"""Train and approve the optional crowd-count LSTM from forecast history."""

from __future__ import annotations

import argparse
import copy
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.crowd_forecast import HORIZONS
from src.lstm_forecast import (
    FORMAT_VERSION,
    CountLSTM,
    decode_predictions,
    encode_window,
)


def load_sequences(path, sample_interval_seconds):
    grouped = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row.get("observed_count"):
                continue
            timestamp = datetime.fromisoformat(
                row["timestamp_utc"].replace("Z", "+00:00")
            ).timestamp()
            key = (row["camera_id"], row.get("session_id") or "0")
            grouped[key].append((timestamp, max(0.0, float(row["observed_count"]))))

    sequences = []
    max_gap = sample_interval_seconds * 1.5
    for points in grouped.values():
        current = []
        for point in sorted(points):
            if current and point[0] - current[-1][0] > max_gap:
                sequences.append([value for _, value in current])
                current = []
            current.append(point)
        if current:
            sequences.append([value for _, value in current])
    return sequences


def _examples(sequences, window_size, horizon_steps, validation):
    features, targets, masks, centers, scales = [], [], [], [], []
    for sequence in sequences:
        split = max(window_size + 1, int(len(sequence) * 0.8))
        start = split if validation else window_size
        stop = len(sequence) if validation else min(split, len(sequence))
        target_limit = len(sequence) if validation else split
        for end in range(start, stop):
            encoded = encode_window(sequence[:end], window_size)
            if encoded is None:
                continue
            x, center, scale = encoded
            y = np.zeros(len(horizon_steps), dtype=np.float32)
            mask = np.zeros(len(horizon_steps), dtype=np.float32)
            for index, steps in enumerate(horizon_steps):
                target_index = end + steps - 1
                if target_index < target_limit:
                    y[index] = (
                        np.log1p(sequence[target_index]) - center
                    ) / scale
                    mask[index] = 1.0
            if mask.any():
                features.append(x)
                targets.append(y)
                masks.append(mask)
                centers.append(center)
                scales.append(scale)
    if not features:
        return None
    return tuple(
        np.asarray(values, dtype=np.float32)
        for values in (features, targets, masks, centers, scales)
    )


def accuracy_by_horizon(model, dataset, device):
    x, y, mask, centers, scales = dataset
    model.eval()
    with torch.no_grad():
        predicted = model(torch.from_numpy(x).to(device)).cpu().numpy()

    metrics = []
    for index in range(y.shape[1]):
        selected = mask[:, index].astype(bool)
        count = int(selected.sum())
        if not count:
            metrics.append((None, 0))
            continue
        actual = decode_predictions(
            y[selected, index], centers[selected], scales[selected]
        )
        forecast = decode_predictions(
            predicted[selected, index], centers[selected], scales[selected]
        )
        error = np.minimum(
            1.0, np.abs(forecast - actual) / np.maximum(actual, 1.0)
        )
        metrics.append((100.0 * (1.0 - float(np.mean(error))), count))
    return metrics


def _mean_available_accuracy(metrics):
    values = [accuracy for accuracy, _ in metrics if accuracy is not None]
    return float(np.mean(values)) if values else float("-inf")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "history",
        nargs="*",
        default=[os.path.join("outputs", "crowd_history.csv")],
        help="One or more forecast-history CSV files.",
    )
    parser.add_argument("--output", default=os.path.join("models", "crowd_lstm.pt"))
    parser.add_argument("--sample-interval", type=float, default=15.0)
    parser.add_argument("--window", type=int, default=20)
    parser.add_argument("--hidden-size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument(
        "--loss-weighting",
        choices=("balanced", "sample"),
        default="balanced",
    )
    parser.add_argument("--target-accuracy", type=float, default=90.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-training-samples", type=int, default=64)
    parser.add_argument("--min-validation-samples", type=int, default=30)
    args = parser.parse_args()

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    horizon_steps = [
        max(1, int(round(seconds / args.sample_interval)))
        for _, seconds in HORIZONS
    ]
    sequences = []
    for history_path in args.history:
        sequences.extend(load_sequences(history_path, args.sample_interval))
    train = _examples(sequences, args.window, horizon_steps, validation=False)
    validation = _examples(sequences, args.window, horizon_steps, validation=True)
    if train is None or validation is None:
        print("Not enough continuous history to build training and validation data.")
        return 2

    x, y, mask, _, _ = train
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = CountLSTM(len(HORIZONS), args.hidden_size).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    loader = DataLoader(
        TensorDataset(
            torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(mask)
        ),
        batch_size=args.batch_size,
        shuffle=True,
    )

    best_epoch = 0
    best_metrics = accuracy_by_horizon(model, validation, device)
    best_score = _mean_available_accuracy(best_metrics)
    best_state = copy.deepcopy(model.state_dict())
    for epoch in range(1, max(1, args.epochs) + 1):
        model.train()
        for batch_x, batch_y, batch_mask in loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            batch_mask = batch_mask.to(device)
            optimizer.zero_grad()
            loss = nn.functional.smooth_l1_loss(
                model(batch_x), batch_y, reduction="none"
            )
            if args.loss_weighting == "sample":
                loss = (loss * batch_mask).sum() / batch_mask.sum().clamp_min(1.0)
            else:
                horizon_counts = batch_mask.sum(dim=0)
                horizon_loss = (
                    (loss * batch_mask).sum(dim=0)
                    / horizon_counts.clamp_min(1.0)
                )
                loss = horizon_loss[horizon_counts > 0].mean()
            loss.backward()
            optimizer.step()

        if epoch % 5 == 0 or epoch == args.epochs:
            candidate_metrics = accuracy_by_horizon(model, validation, device)
            candidate_score = _mean_available_accuracy(candidate_metrics)
            if candidate_score > best_score:
                best_epoch = epoch
                best_score = candidate_score
                best_metrics = candidate_metrics
                best_state = copy.deepcopy(model.state_dict())

    model.load_state_dict(best_state)
    metrics = best_metrics
    training_counts = train[2].sum(axis=0).astype(int)
    approved = True
    print(f"LSTM held-out validation (selected epoch {best_epoch}):")
    evaluated_horizons = 0
    for index, ((label, _), (accuracy, validation_count)) in enumerate(
        zip(HORIZONS, metrics)
    ):
        text = "unavailable" if accuracy is None else f"{accuracy:.1f}%"
        print(
            f"  {label:>4}: {text}, train={training_counts[index]}, "
            f"validation={validation_count}"
        )
        if validation_count >= args.min_validation_samples and accuracy is not None:
            evaluated_horizons += 1
            if accuracy < args.target_accuracy:
                approved = False

    if evaluated_horizons == 0:
        approved = False

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    torch.save(
        {
            "format_version": FORMAT_VERSION,
            "approved": approved,
            "state_dict": model.cpu().state_dict(),
            "window_size": args.window,
            "hidden_size": args.hidden_size,
            "selected_epoch": best_epoch,
            "loss_weighting": args.loss_weighting,
            "seed": args.seed,
            "target_accuracy_percent": args.target_accuracy,
            "horizons_seconds": [seconds for _, seconds in HORIZONS],
            "sample_interval_seconds": args.sample_interval,
            "validation_accuracy": {
                label: None if accuracy is None else round(accuracy, 3)
                for (label, _), (accuracy, _) in zip(HORIZONS, metrics)
            },
            "training_samples": {
                label: int(count)
                for (label, _), count in zip(HORIZONS, training_counts)
            },
            "validation_samples": {
                label: int(count)
                for (label, _), (_, count) in zip(HORIZONS, metrics)
            },
        },
        args.output,
    )
    if approved:
        print(f"Approved checkpoint saved to {args.output}")
    else:
        print(
            f"Shadow checkpoint saved to {args.output}; it will be scored "
            f"online but cannot be selected below {args.target_accuracy:g}%."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
