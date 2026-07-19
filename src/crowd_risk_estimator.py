"""
src/crowd_risk_estimator.py
===========================
Estimates a Crowd Risk Index (0-100) and classifies it into risk levels
(SAFE, WATCH, HIGH, CRITICAL) along with explaining primary causes and
estimating a data confidence score.

Formula (all terms normalised to [0, 1]):
    Risk = 0.20 * density_term
         + 0.20 * motion_term
         + 0.20 * turbulence_term
         + 0.20 * growth_term            ← timestamp-aware growth rate
         + 0.20 * opposing_flow_term

Output index is smoothed with an EMA to avoid jitter.
"""

import numpy as np
from .history_buffer import HistoryBuffer

MIN_CROWD_DENSITY = 100.0

class CrowdRiskEstimator:
    def __init__(
        self,
        history: HistoryBuffer,
        ema_alpha: float = 0.15,
        density_sat: float = 1500.0,
        motion_sat: float = 6.0,
        turb_sat: float = 4.0,
        growth_window: float = 10.0,
    ):
        self.history = history
        self.ema_alpha = ema_alpha
        self.density_sat = density_sat
        self.motion_sat = motion_sat
        self.turb_sat = turb_sat
        self.growth_window = growth_window
        self._smoothed_risk = 0.0

    def estimate(
        self,
        density_score: float,
        motion_speed: float,
        turbulence: float,
        opposing_score: float = 0.0,
    ) -> dict:
        """
        Calculates Crowd Risk Index, Risk Level, Confidence, and Primary Causes.
        """
        if density_score < MIN_CROWD_DENSITY:
            self._smoothed_risk = 0.0
            return {
                "risk_index": 0.0,
                "risk_level": "SAFE",
                "confidence": 1.0,
                "primary_causes": [],
                "terms": {
                    "density": 0.0,
                    "motion": 0.0,
                    "turbulence": 0.0,
                    "growth": 0.0,
                    "opposing": 0.0,
                },
                "label": "SAFE",
                "label_color": (0, 255, 0),
                "alert_text": "",
                "smooth_prob": 0.0,  # backwards-compatibility mapping
            }

        # 1. Density term
        density_term = float(1.0 - np.exp(-density_score / self.density_sat))

        # 2. Motion term
        motion_term = float(motion_speed / (motion_speed + self.motion_sat))

        # 3. Turbulence term
        turb_term = float(turbulence / (turbulence + self.turb_sat))

        # 4. Growth term (timestamp-aware slope of density)
        entries = self.history.window(self.growth_window)
        if len(entries) >= 3:
            # returns normalized rate of growth per second
            rate = self.history.growth_rate(entries, "density")
            # clip rate to [-0.1, 0.1] (which represents +/-10% growth per second)
            # then normalize to [0, 1]
            growth_norm = np.clip(rate * 5.0, -1.0, 1.0)
            growth_term = float((growth_norm + 1.0) / 2.0)
        else:
            growth_term = 0.5

        # 5. Opposing flow term
        opposing_term = float(np.clip(opposing_score * 2.0, 0.0, 1.0))

        # Weighted sum of components
        raw_risk = (
            0.20 * density_term
            + 0.20 * motion_term
            + 0.20 * turb_term
            + 0.20 * growth_term
            + 0.20 * opposing_term
        )
        raw_risk = float(np.clip(raw_risk, 0.0, 1.0))

        # EMA smoothing
        self._smoothed_risk = (
            self.ema_alpha * raw_risk
            + (1.0 - self.ema_alpha) * self._smoothed_risk
        )
        smoothed_risk = self._smoothed_risk
        risk_index = float(np.round(smoothed_risk * 100, 1))

        # Classify risk level
        risk_level, label_color = self._classify(risk_index)

        # Primary Causes explanation
        primary_causes = []
        if density_term > 0.45:
            primary_causes.append("high crowd density")
        if motion_term > 0.45:
            primary_causes.append("rapid crowd movement")
        if turb_term > 0.45:
            primary_causes.append("high motion turbulence")
        if growth_term > 0.65:
            primary_causes.append("rapid density increase")
        if opposing_term > 0.35:
            primary_causes.append("opposing pedestrian flow")

        # Confidence heuristic
        history_len = len(entries)
        if history_len < 3:
            confidence = 0.50
        else:
            sample_confidence = min(0.70 + 0.02 * history_len, 0.90)
            inconsistency = abs(density_term - motion_term) * 0.15
            confidence = float(np.clip(sample_confidence - inconsistency, 0.50, 0.95))

        # Alert text for UI ticker
        alert_text = self._build_alert(risk_index, risk_level)

        return {
            "risk_index": risk_index,
            "risk_level": risk_level,
            "confidence": float(np.round(confidence, 2)),
            "primary_causes": primary_causes,
            "terms": {
                "density": density_term,
                "motion": motion_term,
                "turbulence": turb_term,
                "growth": growth_term,
                "opposing": opposing_term,
            },
            "label": risk_level,
            "label_color": label_color,
            "alert_text": alert_text,
            "smooth_prob": smoothed_risk,  # backwards compatibility
        }

    @staticmethod
    def _classify(index: float) -> tuple:
        if index < 25.0:
            return "SAFE", (0, 255, 0)
        elif index < 45.0:
            return "WATCH", (0, 255, 255)
        elif index < 75.0:
            return "HIGH", (0, 165, 255)
        else:
            return "CRITICAL", (0, 0, 255)

    @staticmethod
    def _build_alert(index: float, level: str) -> str:
        idx_int = int(index)
        if level == "SAFE":
            return ""
        elif level == "WATCH":
            return f"Monitor crowd (Risk Index: {idx_int}/100)"
        elif level == "HIGH":
            return f"⚠ CROWD RISK HIGH ({idx_int}/100) — Deploy personnel"
        else:
            return f"🚨 CROWD RISK CRITICAL ({idx_int}/100) — EVACUATE/RESTRICT ENTRY"
