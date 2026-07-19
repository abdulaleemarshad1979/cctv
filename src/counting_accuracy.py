"""Accuracy helpers for stable, internally consistent crowd counts."""

from collections import deque

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
