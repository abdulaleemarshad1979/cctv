"""Fit and validate per-camera crowd-count calibration profiles.

CSV columns:
    camera_id,predicted_count,actual_count,angle

Use at least 30 verified morning/daylight frames per camera, covering the
high-aerial, oblique, and close viewpoints expected in operation.
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.counting_accuracy import count_accuracy_percent, fit_linear_calibration


def read_samples(csv_path):
    grouped = defaultdict(list)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"camera_id", "predicted_count", "actual_count", "angle"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing CSV columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            camera_id = row["camera_id"].strip().lower()
            angle = row["angle"].strip().lower()
            if not camera_id or not angle:
                raise ValueError(f"line {line_number}: camera_id and angle are required")
            grouped[camera_id].append(
                (
                    float(row["predicted_count"]),
                    float(row["actual_count"]),
                    angle,
                )
            )
    return grouped


def validate_and_fit(samples, target_accuracy):
    if len(samples) < 10:
        raise ValueError("at least 10 samples are required; 30 or more are recommended")

    # Hold out samples independently within each angle. A global every-fifth
    # split could accidentally omit a rarer drone viewpoint and report a
    # misleading overall pass while never validating that angle.
    indices_by_angle = defaultdict(list)
    for index, row in enumerate(samples):
        indices_by_angle[row[2]].append(index)
    validation_indices = set()
    for angle_indices in indices_by_angle.values():
        selected = angle_indices[4::5]
        if not selected:
            selected = angle_indices[-1:]
        validation_indices.update(selected)

    training = [row for index, row in enumerate(samples) if index not in validation_indices]
    validation = [row for index, row in enumerate(samples) if index in validation_indices]
    if len(training) < 3:
        raise ValueError("at least three non-validation samples are required")

    train_pred = np.asarray([row[0] for row in training], dtype=np.float64)
    train_actual = np.asarray([row[1] for row in training], dtype=np.float64)
    validation_pred = np.asarray([row[0] for row in validation], dtype=np.float64)
    validation_actual = np.asarray([row[1] for row in validation], dtype=np.float64)

    validation_scale, validation_bias = fit_linear_calibration(train_pred, train_actual)
    validation_estimate = np.maximum(
        0.0, validation_pred * validation_scale + validation_bias
    )
    validation_accuracy = count_accuracy_percent(
        validation_estimate, validation_actual
    )

    per_angle = {}
    for angle in sorted({row[2] for row in validation}):
        selected = [index for index, row in enumerate(validation) if row[2] == angle]
        per_angle[angle] = round(
            count_accuracy_percent(
                validation_estimate[selected], validation_actual[selected]
            ),
            2,
        )

    all_pred = np.asarray([row[0] for row in samples], dtype=np.float64)
    all_actual = np.asarray([row[1] for row in samples], dtype=np.float64)
    final_scale, final_bias = fit_linear_calibration(all_pred, all_actual)

    return {
        "scale": round(final_scale, 8),
        "bias": round(final_bias, 8),
        "validated_accuracy_percent": round(validation_accuracy, 2),
        "target_accuracy_percent": float(target_accuracy),
        "target_met": bool(
            validation_accuracy >= target_accuracy
            and all(value >= target_accuracy for value in per_angle.values())
        ),
        "validation_accuracy_by_angle": per_angle,
        "sample_count": len(samples),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_DIR / "models" / "count_calibration.json",
    )
    parser.add_argument("--target", type=float, default=90.0)
    args = parser.parse_args()

    grouped = read_samples(args.csv_path)
    if not grouped:
        raise ValueError("the calibration CSV contains no samples")

    profiles = {}
    all_targets_met = True
    for camera_id, samples in grouped.items():
        profile = validate_and_fit(samples, args.target)
        profiles[camera_id] = profile
        all_targets_met = all_targets_met and profile["target_met"]
        print(
            f"{camera_id}: validation={profile['validated_accuracy_percent']:.2f}% "
            f"target_met={profile['target_met']} angles="
            f"{profile['validation_accuracy_by_angle']}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")
    print(f"Saved calibration profiles to {args.output}")
    if not all_targets_met:
        print(
            "WARNING: The requested accuracy was not reached for every camera/angle. "
            "Add more representative labeled frames or retrain before making the claim."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
