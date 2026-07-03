"""
hotspot_tracker.py
Tracks density hotspots across frames and flags cells that are
consistently growing — the first warning sign before a stampede.
"""

import time
import numpy as np
from .history_buffer import HistoryBuffer


# Labels shown on the grid overlay
TREND_STABLE   = "STABLE"
TREND_GROWING  = "GROWING"
TREND_SHRINKING= "EASING"
TREND_CRITICAL = "CRITICAL"


class HotspotTracker:
    """
    Uses a HistoryBuffer to:
      1. Rank the top-N hotspot cells by density.
      2. Compute per-cell trend labels (GROWING / EASING / STABLE).
      3. Emit a HOTSPOT EXPANDING alert when a top cell grows fast.
    """

    def __init__(
        self,
        history: HistoryBuffer,
        top_n: int = 3,
        growth_threshold: float = 0.015,   # normalised growth/sample to flag GROWING
        critical_threshold: float = 0.04,  # to flag CRITICAL
        trend_window: float = 8.0,         # seconds of history for trend
        min_density: float = 30.0,          # ignore tiny model noise
    ):
        self.history          = history
        self.top_n            = top_n
        self.growth_threshold = growth_threshold
        self.critical_threshold = critical_threshold
        self.trend_window     = trend_window
        self.min_density      = min_density

    # ------------------------------------------------------------------
    def update(self, zone_scores: np.ndarray) -> dict:
        """
        Call once per inference cycle (after history.push()).

        Returns
        -------
        {
          "hotspots":      list of (r, c, density) sorted by density desc
          "trend_matrix":  3x3 str array with STABLE/GROWING/EASING/CRITICAL
          "growth_matrix": 3x3 float of normalised growth rates
          "expanding":     bool  — True if any top cell is GROWING/CRITICAL
          "alert_text":    str   — human-readable alert or ""
        }
        """
        growth_matrix = self.history.zone_growth_matrix(self.trend_window)
        trend_matrix  = self._label_trends(growth_matrix)
        trend_matrix  = np.where(zone_scores >= self.min_density, trend_matrix, TREND_STABLE)

        # Rank cells by current density
        flat_idx  = np.argsort(zone_scores.ravel())[::-1]
        hotspots  = [
            (int(i // 3), int(i % 3), float(zone_scores.ravel()[i]))
            for i in flat_idx[: self.top_n]
            if zone_scores.ravel()[i] >= self.min_density
        ]

        # Check if any top-N hotspot cell is expanding
        expanding   = False
        alert_parts = []
        cell_labels = ["A", "B", "C"]

        for r, c, density in hotspots:
            label = trend_matrix[r, c]
            if label in (TREND_GROWING, TREND_CRITICAL):
                expanding = True
                cell_name = f"{cell_labels[r]}{c + 1}"
                alert_parts.append(f"{cell_name} {label}")

        alert_text = "HOTSPOT EXPANDING: " + ", ".join(alert_parts) if alert_parts else ""

        return {
            "hotspots":      hotspots,
            "trend_matrix":  trend_matrix,
            "growth_matrix": growth_matrix,
            "expanding":     expanding,
            "alert_text":    alert_text,
        }

    # ------------------------------------------------------------------
    def _label_trends(self, growth_matrix: np.ndarray) -> np.ndarray:
        labels = np.full((3, 3), TREND_STABLE, dtype=object)
        for r in range(3):
            for c in range(3):
                g = growth_matrix[r, c]
                if g >= self.critical_threshold:
                    labels[r, c] = TREND_CRITICAL
                elif g >= self.growth_threshold:
                    labels[r, c] = TREND_GROWING
                elif g <= -self.growth_threshold:
                    labels[r, c] = TREND_SHRINKING
                else:
                    labels[r, c] = TREND_STABLE
        return labels
