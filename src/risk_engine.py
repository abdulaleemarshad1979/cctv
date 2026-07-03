import numpy as np

MIN_CROWD_DENSITY = 5.0

class RiskEngineTracker:
    def __init__(self):
        self.prev_peak_density = 0.0

    def compute_composite_risk(self, density_score, peak_density, hotspot_ratio, motion_speed, turbulence):
        """
        Computes composite risk using density, motion, turbulence, and peak density growth:
        risk = 0.4 * density + 0.3 * motion + 0.2 * turbulence + 0.1 * hotspot_growth
        All terms are normalized to [0, 1].
        """
        if density_score < MIN_CROWD_DENSITY:
            self.prev_peak_density = 0.0
            return 0.0

        # 1. Density term (sigmoid/exp saturation)
        density_term = 1.0 - np.exp(-density_score / 1200.0)

        # 2. Motion term (saturating speed mapping)
        motion_term = motion_speed / (motion_speed + 5.0)

        # 3. Turbulence term (saturating std-dev mapping)
        turbulence_term = turbulence / (turbulence + 3.0)

        # 4. Hotspot growth term (rate of change of peak density)
        if self.prev_peak_density <= 0.0:
            growth = 0.0
        else:
            growth = (peak_density - self.prev_peak_density) / self.prev_peak_density

        self.prev_peak_density = peak_density

        # Normalize growth: map -100% to +100% bounds into [0, 1]
        growth = np.clip(growth, -1.0, 1.0)
        growth_term = (growth + 1.0) / 2.0

        # Composite risk
        risk_score = (
            0.4 * density_term +
            0.3 * motion_term +
            0.2 * turbulence_term +
            0.1 * growth_term
        )

        return float(risk_score)

def compute_pressure_metrics(dmap_np):
    if dmap_np is None:
        return 0.0, 0.0, 0.0, 0.0

    dmap = np.clip(dmap_np, 0, None)
    density_score = float(dmap.sum())
    peak_density = float(dmap.max()) if dmap.size else 0.0

    if density_score < MIN_CROWD_DENSITY:
        return density_score, peak_density, 0.0, 0.0

    positive = dmap[dmap > 0]
    if positive.size == 0:
        hotspot_ratio = 0.0
    else:
        cutoff = np.percentile(positive, 90)
        hotspot_ratio = float((dmap >= cutoff).mean())

    # Smooth, bounded risk score in [0, 1]
    density_term = 1.0 - np.exp(-density_score / 1200.0)
    peak_term = peak_density / (peak_density + 1.0)
    hotspot_term = min(1.0, hotspot_ratio * 4.0)

    risk_score = float(
        0.55 * density_term +
        0.25 * peak_term +
        0.20 * hotspot_term
    )

    return density_score, peak_density, hotspot_ratio, risk_score

def get_risk_zone(risk_score, safe_threshold=0.25, watch_threshold=0.50, high_threshold=0.75):
    if risk_score < safe_threshold:
        return "SAFE", (0, 255, 0)
    if risk_score < watch_threshold:
        return "WATCH", (0, 255, 255)
    if risk_score < high_threshold:
        return "HIGH", (0, 165, 255)
    return "CRITICAL", (0, 0, 255)

def get_crowd_state(display_pressure):
    if display_pressure < 30:
        return "STABLE"
    elif display_pressure < 60:
        return "DENSE"
    elif display_pressure < 80:
        return "HIGH PRESSURE"
    else:
        return "CRITICAL"

def get_alert_level(zone):
    if zone == "SAFE":
        return "NORMAL"
    elif zone == "WATCH":
        return "LOW"
    elif zone == "HIGH":
        return "MEDIUM"
    else:
        return "EMERGENCY"
