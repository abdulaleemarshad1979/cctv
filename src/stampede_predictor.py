"""
stampede_predictor.py
Combines density, motion, turbulence, hotspot growth, and opposing flow
into a single stampede probability score.

# Formula (all terms normalised to [0, 1]):
#     P = 0.20 * density_term           ← was 0.30
#       + 0.20 * motion_term
#       + 0.20 * turbulence_term
#       + 0.20 * growth_term            ← rate-of-change of density
#       + 0.20 * opposing_flow_term     ← was 0.10

Output probability is smoothed with an EMA to avoid jitter.
"""

import numpy as np
from .history_buffer import HistoryBuffer

MIN_CROWD_DENSITY = 100.0


# Probability thresholds → labels
PROB_SAFE     = 0.25
PROB_WATCH    = 0.45
PROB_HIGH     = 0.65
PROB_CRITICAL = 0.80


class StampedePredictor:

    def __init__(
        self,
        history: HistoryBuffer,
        ema_alpha: float = 0.15,          # smoothing factor; lower = smoother
        density_sat: float = 1500.0,      # density_score at which term → 0.63
        motion_sat: float  = 6.0,         # px/frame at which term → 0.50
        turb_sat: float    = 4.0,         # std-dev at which term → 0.50
        growth_window: float = 10.0,      # seconds used for growth trend
    ):
        self.history       = history
        self.ema_alpha     = ema_alpha
        self.density_sat   = density_sat
        self.motion_sat    = motion_sat
        self.turb_sat      = turb_sat
        self.growth_window = growth_window

        self._smoothed_prob: float = 0.0

    # ------------------------------------------------------------------
    def predict(
        self,
        density_score:    float,
        motion_speed:     float,
        turbulence:       float,
        opposing_score:   float = 0.0,   # max cell opposing-flow score [0,1]
    ) -> dict:
        """
        Call once per inference cycle (after history.push()).

        Returns
        -------
        {
          "raw_prob":      float  [0, 1]
          "smooth_prob":   float  [0, 1]  (EMA-smoothed)
          "label":         str    SAFE / WATCH / HIGH / CRITICAL
          "label_color":   tuple  BGR
          "alert_text":    str
          "terms":         dict   individual component scores
        }
        """
        if density_score < MIN_CROWD_DENSITY:
            self._smoothed_prob = 0.0
            return {
                "raw_prob":    0.0,
                "smooth_prob": 0.0,
                "label":       "SAFE",
                "label_color": (0, 255, 0),
                "alert_text":  "",
                "terms": {
                    "density":   0.0,
                    "motion":    0.0,
                    "turbulence": 0.0,
                    "growth":    0.0,
                    "opposing":  0.0,
                },
            }

        # 1. Density term
        density_term = 1.0 - np.exp(-density_score / self.density_sat)

        # 2. Motion term
        motion_term = motion_speed / (motion_speed + self.motion_sat)

        # 3. Turbulence term
        turb_term = turbulence / (turbulence + self.turb_sat)

        # 4. Growth term — slope of density in history window
        d_series = self.history.density_series(self.growth_window)
        if len(d_series) >= 3:
            xs    = np.arange(len(d_series), dtype=float)
            slope = float(np.polyfit(xs, d_series, 1)[0])
            mean  = float(np.mean(d_series)) + 1e-6
            # normalised growth/sample; clip to [-1, 1] then map to [0, 1]
            growth_norm = np.clip(slope / mean, -1.0, 1.0)
            growth_term = (growth_norm + 1.0) / 2.0
        else:
            growth_term = 0.5          # neutral when no history yet

        # 5. Opposing flow term (scaled from minority-flow range [0, 0.5] to [0, 1])
        opposing_term = float(np.clip(opposing_score * 2.0, 0.0, 1.0))

        # Weighted sum
        raw_prob = (
            0.20 * density_term      # was 0.30
            + 0.20 * motion_term
            + 0.20 * turb_term
            + 0.20 * growth_term
            + 0.20 * opposing_term   # was 0.10
        )
        raw_prob = float(np.clip(raw_prob, 0.0, 1.0))

        # EMA smoothing
        self._smoothed_prob = (
            self.ema_alpha * raw_prob
            + (1.0 - self.ema_alpha) * self._smoothed_prob
        )
        smooth_prob = self._smoothed_prob

        label, color = self._classify(smooth_prob)
        alert_text   = self._build_alert(smooth_prob, label)

        return {
            "raw_prob":    raw_prob,
            "smooth_prob": smooth_prob,
            "label":       label,
            "label_color": color,
            "alert_text":  alert_text,
            "terms": {
                "density":   density_term,
                "motion":    motion_term,
                "turbulence": turb_term,
                "growth":    growth_term,
                "opposing":  opposing_term,
            },
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _classify(prob: float):
        if prob < PROB_SAFE:
            return "SAFE",     (0, 255, 0)
        if prob < PROB_WATCH:
            return "WATCH",    (0, 255, 255)
        if prob < PROB_HIGH:
            return "HIGH",     (0, 165, 255)
        if prob < PROB_CRITICAL:
            return "CRITICAL", (0, 0, 255)
        return "CRITICAL",     (0, 0, 255)

    @staticmethod
    def _build_alert(prob: float, label: str) -> str:
        pct = int(prob * 100)
        if label == "SAFE":
            return ""
        if label == "WATCH":
            return f"Monitor crowd ({pct}%)"
        if label == "HIGH":
            return f"⚠ CROWD RISK HIGH ({pct}%) — Deploy personnel"
        return f"🚨 STAMPEDE RISK CRITICAL ({pct}%) — EVACUATE"
