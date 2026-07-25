"""Prepare representative video frames for manual crowd-count verification."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np


FIELDS = (
    "camera_id",
    "predicted_count",
    "actual_count",
    "angle",
    "source_video",
    "video_second",
    "frame_path",
)


def _select_rows(rows, sample_count):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["source_video"]].append(row)
    representatives = [
        sorted(group, key=lambda row: float(row["video_second"]))[len(group) // 2]
        for group in grouped.values()
    ]
    representatives.sort(key=lambda row: float(row["raw_observed_count"]))
    if len(representatives) <= sample_count:
        return representatives
    indices = np.linspace(0, len(representatives) - 1, sample_count)
    return [representatives[int(round(index))] for index in indices]


def main():
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        type=Path,
        default=project_root / "outputs" / "video_count_history.csv",
    )
    parser.add_argument("--videos", type=Path, default=project_root / "Videos")
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root / "outputs" / "verified_counts.csv",
    )
    parser.add_argument(
        "--frames",
        type=Path,
        default=project_root / "outputs" / "count_calibration_frames",
    )
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--camera-id", default="default")
    args = parser.parse_args()

    with args.history.open(newline="", encoding="utf-8") as handle:
        selected = _select_rows(list(csv.DictReader(handle)), max(10, args.samples))

    args.frames.mkdir(parents=True, exist_ok=True)
    output_rows = []
    for index, row in enumerate(selected, start=1):
        video_path = args.videos / row["source_video"]
        second = float(row["video_second"])
        capture = cv2.VideoCapture(str(video_path))
        capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000.0)
        ok, frame = capture.read()
        capture.release()
        if not ok:
            print(f"Skipped unreadable frame: {video_path} at {second:g}s")
            continue

        frame_name = f"{index:03d}_{video_path.stem}_{second:g}s.jpg"
        frame_path = args.frames / frame_name
        if not cv2.imwrite(str(frame_path), frame):
            raise OSError(f"could not write {frame_path}")
        output_rows.append(
            {
                "camera_id": args.camera_id,
                "predicted_count": row["raw_observed_count"],
                "actual_count": "",
                "angle": "",
                "source_video": row["source_video"],
                "video_second": row["video_second"],
                "frame_path": str(frame_path.relative_to(project_root)),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    temporary.replace(args.output)
    print(f"Prepared {len(output_rows)} frames and {args.output}")
    print("Fill actual_count and angle, then run tools/calibrate_counting.py.")
    return 0 if len(output_rows) >= 10 else 2


if __name__ == "__main__":
    raise SystemExit(main())
