"""Accuracy helpers for stable, internally consistent crowd counts."""

from collections import deque

import cv2
import numpy as np


class TemporalCountStabilizer:
    """Robustly combine a density estimate with YOLO's detected-person floor.

    The density model remains the primary counter for occluded crowds. YOLO
    guarantees that individually detected people are never lost, while a
    short median window and EMA suppress one-frame spikes.
    """

    def __init__(
        self,
        window_size=3,
        ema_alpha=0.65,
        calibration_scale=1.0,
        calibration_bias=0.0,
    ):
        self.window_size = max(1, int(window_size))
        self.ema_alpha = float(np.clip(ema_alpha, 0.0, 1.0))
        self.calibration_scale = max(0.0, float(calibration_scale))
        self.calibration_bias = float(calibration_bias)
        self._history = deque(maxlen=self.window_size)
        self._value = None

    def reset(self):
        self._history.clear()
        self._value = None

    def update(self, density_count, yolo_count):
        calibrated_density = max(
            0.0,
            float(density_count) * self.calibration_scale + self.calibration_bias,
        )
        detected_people = max(0.0, float(yolo_count))
        raw_count = max(calibrated_density, detected_people)
        self._history.append(raw_count)

        target = float(np.median(np.asarray(self._history, dtype=np.float32)))
        if self._value is None:
            self._value = target
        else:
            self._value += self.ema_alpha * (target - self._value)

        # Never report fewer people than YOLO can directly see now.
        self._value = max(self._value, detected_people)
        return self._value


def detections_to_density_map(boxes, map_shape, frame_shape, sigma=2.0):
    """Convert detection centers to a normalized pseudo-density map.

    The previous implementation visited a 13x13 Python loop around every
    detection (O(detections * radius^2) Python operations). This version bins
    all centers once and performs the Gaussian work in optimized OpenCV code.
    Its retained memory is one ``map_shape`` float array.
    """
    map_h, map_w = (int(map_shape[0]), int(map_shape[1]))
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    if map_h <= 0 or map_w <= 0 or frame_h <= 0 or frame_w <= 0:
        raise ValueError("map and frame dimensions must be positive")

    density = np.zeros((map_h, map_w), dtype=np.float32)
    coordinates = np.asarray(boxes, dtype=np.float32)
    if coordinates.size == 0:
        return density
    coordinates = coordinates.reshape(-1, 4)

    center_x = (coordinates[:, 0] + coordinates[:, 2]) * 0.5
    center_y = (coordinates[:, 1] + coordinates[:, 3]) * 0.5
    valid = np.isfinite(center_x) & np.isfinite(center_y)
    if not np.any(valid):
        return density
    map_x = np.floor(center_x[valid] * (map_w / frame_w)).astype(np.intp)
    map_y = np.floor(center_y[valid] * (map_h / frame_h)).astype(np.intp)
    map_x = np.clip(map_x, 0, map_w - 1)
    map_y = np.clip(map_y, 0, map_h - 1)

    np.add.at(density, (map_y, map_x), 1.0)
    density = cv2.GaussianBlur(
        density,
        ksize=(0, 0),
        sigmaX=max(0.1, float(sigma)),
        sigmaY=max(0.1, float(sigma)),
        borderType=cv2.BORDER_CONSTANT,
    )
    density_sum = float(density.sum())
    if density_sum > 0.0:
        density *= float(np.count_nonzero(valid)) / density_sum
    return density


def detection_zone_counts(boxes, frame_shape, rows=3, columns=3):
    """Count detection centers into a grid in O(number of detections)."""
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    rows, columns = int(rows), int(columns)
    if frame_h <= 0 or frame_w <= 0 or rows <= 0 or columns <= 0:
        raise ValueError("frame and grid dimensions must be positive")

    result = np.zeros((rows, columns), dtype=np.float32)
    coordinates = np.asarray(boxes, dtype=np.float32)
    if coordinates.size == 0:
        return result
    coordinates = coordinates.reshape(-1, 4)

    center_x = (coordinates[:, 0] + coordinates[:, 2]) * 0.5
    center_y = (coordinates[:, 1] + coordinates[:, 3]) * 0.5
    valid = np.isfinite(center_x) & np.isfinite(center_y)
    grid_x = np.floor(center_x[valid] * (columns / frame_w)).astype(np.intp)
    grid_y = np.floor(center_y[valid] * (rows / frame_h)).astype(np.intp)
    grid_x = np.clip(grid_x, 0, columns - 1)
    grid_y = np.clip(grid_y, 0, rows - 1)
    np.add.at(result, (grid_y, grid_x), 1.0)
    return result


def allocate_zone_counts(density_scores, yolo_scores, total_count):
    """Return integer 3x3 zone counts that sum exactly to the total count.

    Every YOLO detection is preserved in its zone. The density model then
    distributes the remaining (typically occluded) people using its spatial
    density map and a largest-remainder allocation.
    """

    density = np.clip(np.asarray(density_scores, dtype=np.float64), 0.0, None)
    detected = np.rint(
        np.clip(np.asarray(yolo_scores, dtype=np.float64), 0.0, None)
    ).astype(np.int64)

    if density.shape != detected.shape:
        raise ValueError("density_scores and yolo_scores must have the same shape")

    requested_total = max(0, int(round(float(total_count))))
    detected_total = int(detected.sum())
    final_total = max(requested_total, detected_total)
    remaining = final_total - detected_total

    result = detected.copy()
    if remaining <= 0:
        return result.astype(np.float32)

    weights = density.copy()
    weight_sum = float(weights.sum())
    if weight_sum <= 0.0:
        weights = np.ones_like(weights, dtype=np.float64)
        weight_sum = float(weights.sum())

    exact_extra = weights / weight_sum * remaining
    extra = np.floor(exact_extra).astype(np.int64)
    result += extra

    leftover = remaining - int(extra.sum())
    if leftover > 0:
        order = np.argsort((exact_extra - extra).ravel())[::-1]
        flat = result.ravel()
        for index in order[:leftover]:
            flat[index] += 1

    return result.astype(np.float32)


def fit_linear_calibration(predicted_counts, actual_counts):
    """Fit ``actual ≈ predicted * scale + bias`` from verified samples."""
    predicted = np.asarray(predicted_counts, dtype=np.float64)
    actual = np.asarray(actual_counts, dtype=np.float64)
    if predicted.shape != actual.shape or predicted.ndim != 1:
        raise ValueError("predicted_counts and actual_counts must be 1-D and equal")
    if predicted.size < 3:
        raise ValueError("at least three verified samples are required")
    if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(actual)):
        raise ValueError("calibration counts must be finite")
    if np.any(predicted < 0) or np.any(actual < 0):
        raise ValueError("calibration counts must be non-negative")

    design = np.column_stack((predicted, np.ones_like(predicted)))
    scale, bias = np.linalg.lstsq(design, actual, rcond=None)[0]
    return max(0.0, float(scale)), float(bias)


def count_accuracy_percent(predicted_counts, actual_counts):
    """Return a bounded count accuracy based on mean absolute % error."""
    predicted = np.asarray(predicted_counts, dtype=np.float64)
    actual = np.asarray(actual_counts, dtype=np.float64)
    if predicted.shape != actual.shape or predicted.size == 0:
        raise ValueError("predicted_counts and actual_counts must be non-empty and equal")
    if np.any(actual < 0):
        raise ValueError("actual counts must be non-negative")
    # A denominator of one keeps empty-scene false positives visible instead
    # of dropping those important samples from the accuracy score.
    denominator = np.maximum(actual, 1.0)
    mape = np.mean(np.abs(predicted - actual) / denominator) * 100.0
    if mape < 1e-10:
        return 100.0
    return float(np.clip(100.0 - mape, 0.0, 100.0))
