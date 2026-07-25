"""Extract 15-second crowd-count sequences from the local training videos."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import torch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from models.model_registry import load_counting_model
from src import density_filter


FIELDS = (
    "timestamp_utc",
    "camera_id",
    "session_id",
    "raw_observed_count",
    "observed_count",
    "sample_interval_seconds",
    "source_video",
    "video_second",
)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".m4v")


def _video_samples(path: str, interval_seconds: float):
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        return []
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0.0 else 0.0
    capture.release()
    if duration <= 0.0:
        return []
    # Avoid seeking exactly to EOF, where many codecs return no frame.
    return np.arange(0.0, max(0.0, duration - 0.05), interval_seconds).tolist()


def _read_frame(capture: cv2.VideoCapture, second: float):
    capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000.0)
    ok, frame = capture.read()
    return frame if ok else None


def _preprocess(frames, device):
    tensors = []
    for frame in frames:
        small = cv2.resize(
            frame,
            (config.INFER_WIDTH, config.INFER_HEIGHT),
            interpolation=cv2.INTER_AREA,
        )
        if getattr(config, "CLEAN_INPUT_OVERLAYS", True):
            small = density_filter.suppress_broadcast_overlays(small)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        tensors.append(
            torch.from_numpy(rgb).permute(2, 0, 1).float().div_(255.0)
        )
    batch = torch.stack(tensors).to(device)
    mean = torch.tensor(
        [0.485, 0.456, 0.406], dtype=torch.float32, device=device
    ).view(1, 3, 1, 1)
    std = torch.tensor(
        [0.229, 0.224, 0.225], dtype=torch.float32, device=device
    ).view(1, 3, 1, 1)
    return (batch - mean) / std


def _counts(model, frames, device):
    tensor = _preprocess(frames, device)
    with torch.inference_mode():
        output = model(tensor)
    density_maps = output[0] if isinstance(output, (tuple, list)) else output
    values = []
    for density_map in density_maps.detach().float().cpu().numpy():
        cleaned = density_filter.clean_density_map(
            np.squeeze(density_map),
            source_frame_bgr=None,
            speckle_ratio=getattr(config, "DENSITY_SPECKLE_RATIO", 0.0),
        )
        raw = float(cleaned.sum())
        calibrated = max(
            0.0,
            raw * getattr(config, "COUNT_CALIBRATION_SCALE", 1.0)
            + getattr(config, "COUNT_CALIBRATION_BIAS", 0.0),
        )
        values.append(calibrated)
    return values


def _write_rows(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", default=os.path.join(PROJECT_ROOT, "Videos"))
    parser.add_argument(
        "--output",
        default=os.path.join(PROJECT_ROOT, "outputs", "video_count_history.csv"),
    )
    parser.add_argument("--sample-interval", type=float, default=15.0)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--threads", type=int, default=max(1, min(4, (os.cpu_count() or 4) // 2))
    )
    parser.add_argument("--smoothing-samples", type=int, default=5)
    args = parser.parse_args()

    videos = sorted(
        os.path.join(args.videos, name)
        for name in os.listdir(args.videos)
        if name.lower().endswith(VIDEO_EXTENSIONS)
    )
    if not videos:
        print(f"No videos found in {args.videos}")
        return 2

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cpu":
        torch.set_num_threads(max(1, args.threads))
    print(f"Loading DM-Count on {device} for {len(videos)} videos...", flush=True)
    model = load_counting_model("dm_count", device)
    model.eval()

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    started = time.perf_counter()
    batch_size = max(1, args.batch_size)
    for video_index, path in enumerate(videos, start=1):
        seconds = _video_samples(path, args.sample_interval)
        capture = cv2.VideoCapture(path)
        raw_counts = []
        accepted_seconds = []
        for offset in range(0, len(seconds), batch_size):
            requested = seconds[offset : offset + batch_size]
            pairs = [
                (second, _read_frame(capture, second)) for second in requested
            ]
            pairs = [(second, frame) for second, frame in pairs if frame is not None]
            if not pairs:
                continue
            raw_counts.extend(
                _counts(model, [frame for _, frame in pairs], device)
            )
            accepted_seconds.extend(second for second, _ in pairs)
        capture.release()

        smoothing = deque(maxlen=max(1, args.smoothing_samples))
        name = os.path.basename(path)
        for second, raw_count in zip(accepted_seconds, raw_counts):
            smoothing.append(raw_count)
            observed = float(np.median(np.asarray(smoothing, dtype=np.float32)))
            rows.append(
                {
                    "timestamp_utc": (
                        base_time + timedelta(seconds=second)
                    ).isoformat().replace("+00:00", "Z"),
                    "camera_id": f"video:{name}",
                    "session_id": 0,
                    "raw_observed_count": int(round(raw_count)),
                    "observed_count": int(round(observed)),
                    "sample_interval_seconds": args.sample_interval,
                    "source_video": name,
                    "video_second": round(second, 3),
                }
            )
        elapsed = time.perf_counter() - started
        print(
            f"[{video_index}/{len(videos)}] {name}: "
            f"{len(raw_counts)} samples; total={len(rows)}; {elapsed:.1f}s",
            flush=True,
        )

    _write_rows(args.output, rows)
    print(f"Wrote {len(rows)} samples to {args.output}", flush=True)
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
