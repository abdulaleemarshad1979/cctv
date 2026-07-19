"""
history_buffer.py
Circular buffer that stores per-frame crowd metrics over a sliding time window.
Used by hotspot_tracker and stampede_predictor as input.
"""

from collections import deque
import time
import numpy as np


class HistoryBuffer:
    """
    Stores the last N seconds of crowd metrics.
    Each entry is a dict snapshot of one inference cycle.
    """

    def __init__(self, max_seconds: float = 30.0, fps_estimate: float = 5.0):
        # We store at most max_seconds * fps_estimate frames
        maxlen = int(max_seconds * fps_estimate)
        self._buf: deque = deque(maxlen=maxlen)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def push(
        self,
        timestamp: float,
        density_score: float,
        peak_density: float,
        hotspot_ratio: float,
        motion_speed: float,
        turbulence: float,
        composite_risk: float,
        zone_scores: np.ndarray,       # shape (3,3)
        zone_motions: np.ndarray,      # shape (3,3)
    ) -> None:
        self._buf.append(
            {
                "t":              timestamp,
                "density":        density_score,
                "peak":           peak_density,
                "hotspot_ratio":  hotspot_ratio,
                "motion":         motion_speed,
                "turbulence":     turbulence,
                "risk":           composite_risk,
                "zone_scores":    zone_scores.copy(),
                "zone_motions":   zone_motions.copy(),
            }
        )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._buf)

    def is_empty(self) -> bool:
        return len(self._buf) == 0

    def clear(self) -> None:
        """Clear all historical buffer records."""
        self._buf.clear()

    def latest(self) -> dict | None:
        return self._buf[-1] if self._buf else None

    def window(self, seconds: float) -> list[dict]:
        """Return all entries within the last `seconds` seconds."""
        if self.is_empty():
            return []
        cutoff = self._buf[-1]["t"] - seconds
        return [e for e in self._buf if e["t"] >= cutoff]

    # ------------------------------------------------------------------
    # Metric series helpers
    # ------------------------------------------------------------------
    def density_series(self, seconds: float = 10.0) -> np.ndarray:
        return np.array([e["density"] for e in self.window(seconds)])

    def risk_series(self, seconds: float = 10.0) -> np.ndarray:
        return np.array([e["risk"] for e in self.window(seconds)])

    def motion_series(self, seconds: float = 10.0) -> np.ndarray:
        return np.array([e["motion"] for e in self.window(seconds)])

    def turbulence_series(self, seconds: float = 10.0) -> np.ndarray:
        return np.array([e["turbulence"] for e in self.window(seconds)])

    def zone_density_series(self, r: int, c: int, seconds: float = 10.0) -> np.ndarray:
        """Time series of density for a single 3×3 cell."""
        return np.array([e["zone_scores"][r, c] for e in self.window(seconds)])

    # ------------------------------------------------------------------
    # Growth rate helpers
    # ------------------------------------------------------------------
    def growth_rate(self, entries: list[dict], key) -> float:
        """
        Linear regression slope of a metric series, normalised by its mean,
        using real timestamps of entries.
        Returns fraction-per-second growth (positive = increasing, negative = decreasing).
        """
        if len(entries) < 3:
            return 0.0

        times = np.array([entry["t"] for entry in entries], dtype=np.float64)
        if isinstance(key, tuple):
            values = np.array([entry[key[0]][key[1], key[2]] for entry in entries], dtype=np.float64)
        else:
            values = np.array([entry[key] for entry in entries], dtype=np.float64)

        times -= times[0]

        if times[-1] <= 0:
            return 0.0

        slope = float(np.polyfit(times, values, 1)[0])
        mean_value = max(float(np.mean(values)), 1e-6)

        return float(slope / mean_value)

    def zone_growth_matrix(self, seconds: float = 10.0) -> np.ndarray:
        """3×3 matrix of normalised growth rates for each cell."""
        result = np.zeros((3, 3))
        entries = self.window(seconds)
        if len(entries) < 3:
            return result
        for r in range(3):
            for c in range(3):
                result[r, c] = self.growth_rate(entries, ("zone_scores", r, c))
        return result
