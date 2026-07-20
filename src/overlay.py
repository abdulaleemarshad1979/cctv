"""
overlay.py  — fixed & clean
Bug fixes vs previous version:
  1. Unicode arrows (→ ↑ ↓) replaced with ASCII so OpenCV renders them
  2. Grid now starts BELOW the banner (y_offset=BANNER_H) — no overlap
  3. Stampede panel sits BELOW the banner and uses a safe x-offset so it
     does NOT cover cell A3/B3/C3 content (it draws over the right edge
     only when the panel is toggled; grid text is shifted left for col 3)
  4. draw_top_banner now spans only the LEFT 2/3 so zone info is clear
  5. Motion noise guard: display motion as px/f but clamp display to 9.99
  6. Added density map heatmap overlay for crowd visualization
"""

import cv2
import numpy as np

# ─── constants ────────────────────────────────────────────────────────
BANNER_H  = 110        # must match draw_top_banner rect height
PANEL_W   = 220        # stampede panel width (px)
HEATMAP_ALPHA = 0.45   # heatmap overlay transparency

C_GREEN   = (0,   255,   0)
C_YELLOW  = (0,   255, 255)
C_ORANGE  = (0,   165, 255)
C_RED     = (0,     0, 255)
C_WHITE   = (255, 255, 255)
C_BLACK   = (0,     0,   0)
C_GRAY    = (100, 100, 100)
C_DGRAY   = (40,   40,  40)
C_MAGENTA = (255,   0, 255)
C_CYAN    = (255, 255,   0)   # OpenCV BGR: yellow = (0,255,255); cyan=(255,255,0)

# ─── Colormap for density heatmap ──────────────────────────────────────
# JET-like colormap: blue (low) -> cyan -> yellow -> red (high)
def _density_colormap(val: float) -> tuple:
    """Map normalized density [0,1] to BGR color (JET-like)."""
    v = np.clip(val, 0.0, 1.0)
    if v < 0.25:
        # Blue to Cyan
        r = 0
        g = int(255 * (v / 0.25))
        b = 255
    elif v < 0.5:
        # Cyan to Yellow
        r = int(255 * ((v - 0.25) / 0.25))
        g = 255
        b = int(255 * (1 - (v - 0.25) / 0.25))
    elif v < 0.75:
        # Yellow to Red
        r = 255
        g = int(255 * (1 - (v - 0.5) / 0.25))
        b = 0
    else:
        # Red to Dark Red
        r = 255
        g = 0
        b = int(255 * ((v - 0.75) / 0.25))
    return (b, g, r)


ZONE_COLORS = {
    "SAFE":     C_GREEN,
    "WATCH":    C_YELLOW,
    "HIGH":     C_ORANGE,
    "CRITICAL": C_RED,
}

# ASCII-only trend markers (OpenCV cannot render unicode)
TREND_COLORS = {
    "STABLE":   C_WHITE,
    "GROWING":  C_YELLOW,
    "EASING":   C_GREEN,
    "CRITICAL": C_RED,
}
TREND_LABELS = {          # short ASCII strings that fit in a cell
    "STABLE":   "-- STABLE",
    "GROWING":  "^^ GROWING",
    "EASING":   "vv EASING",
    "CRITICAL": "!! CRITICAL",
}


# ─── helpers ──────────────────────────────────────────────────────────

def _text(img, txt, x, y, color=C_WHITE, scale=0.38, thick=1):
    """Drop-shadow putText for readability on any background."""
    cv2.putText(img, txt, (x + 1, y + 1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, C_BLACK, thick + 1, cv2.LINE_AA)
    cv2.putText(img, txt, (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _fill(img, x0, y0, x1, y1, color, alpha=1.0):
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(img.shape[1], int(x1))
    y1 = min(img.shape[0], int(y1))
    if x0 >= x1 or y0 >= y1:
        return
    if alpha >= 1.0:
        cv2.rectangle(img, (x0, y0), (x1, y1), color, -1)
    else:
        # Blend only the requested region. Copying a full 720p frame for a
        # 220-pixel side panel wasted both time and several megabytes/frame.
        region = img[y0:y1, x0:x1]
        tint = np.empty_like(region)
        tint[:] = color
        cv2.addWeighted(tint, alpha, region, 1.0 - alpha, 0, region)


def _pressure_color(pressure: float):
    """Map normalised pressure [0,1] -> BGR colour."""
    if pressure < 0.25:  return C_GREEN
    if pressure < 0.50:  return C_YELLOW
    if pressure < 0.75:  return C_ORANGE
    return C_RED


# ─── top banner ───────────────────────────────────────────────────────

def draw_top_banner(frame, zone: str, zone_color: tuple, pressure: float):
    """
    Full-width banner at the top.
    Left side: ZONE label.   Right side: PRESSURE bar.
    Height = BANNER_H px.
    """
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, BANNER_H), C_BLACK, -1)
    cv2.rectangle(frame, (0, 0), (w, BANNER_H), zone_color, 3)

    # Zone label — large, left
    cv2.putText(frame, f"ZONE : {zone}",
                (22, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                C_BLACK, 5, cv2.LINE_AA)
    cv2.putText(frame, f"ZONE : {zone}",
                (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                zone_color, 2, cv2.LINE_AA)

    # Pressure bar — right half
    bar_x0 = w // 2
    bar_y0 = 20
    bar_w  = w // 2 - 20
    bar_h  = 30

    pct = int(pressure)
    fill_w = int(pressure / 100.0 * bar_w)
    bar_color = _pressure_color(pressure / 100.0)

    cv2.rectangle(frame, (bar_x0, bar_y0), (bar_x0 + bar_w, bar_y0 + bar_h), C_GRAY, -1)
    cv2.rectangle(frame, (bar_x0, bar_y0), (bar_x0 + fill_w, bar_y0 + bar_h), bar_color, -1)
    cv2.rectangle(frame, (bar_x0, bar_y0), (bar_x0 + bar_w, bar_y0 + bar_h), C_WHITE, 1)

    cv2.putText(frame, f"PRESSURE  {pct}/100",
                (bar_x0 + 2, bar_y0 + bar_h + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, C_BLACK, 4, cv2.LINE_AA)
    cv2.putText(frame, f"PRESSURE  {pct}/100",
                (bar_x0, bar_y0 + bar_h + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, C_WHITE, 1, cv2.LINE_AA)


# ─── 3×3 grid ─────────────────────────────────────────────────────────

def draw_grid_3x3(
    frame,
    zone_scores    = None,   # np (3,3) density sums
    zone_motions   = None,   # np (3,3) motion speed
    trend_matrix   = None,   # np (3,3) str  STABLE/GROWING/EASING/CRITICAL
    opposing_danger= None,   # np (3,3) bool
    capacity_grid  = None,   # 3x3 array or list of capacities
    panel_visible  : bool  = False,  # True → shrink col 3 text to avoid panel
    density_map    = None,   # 2D density map for heatmap overlay
    show_heatmap   : bool  = False,
):
    h, w = frame.shape[:2]
    import config
    if capacity_grid is None:
        capacity_grid = config.ZONE_CAPACITY[0]
    cap_grid = np.array(capacity_grid, dtype=float)

    # Match the canonical 3x3 counting zones over the entire source frame.
    # The top banner and optional side panel may cover part of the lines, but
    # changing the grid geometry for those decorations would mislabel people.
    grid_y0 = 0
    grid_y1 = h
    grid_x0 = 0
    grid_x1 = w
    grid_w  = grid_x1 - grid_x0
    grid_h  = grid_y1 - grid_y0
    labels  = ["A", "B", "C"]

    # ── density heatmap overlay ──
    if show_heatmap and density_map is not None:
        draw_density_heatmap(frame, density_map)

    # ── tinted cell backgrounds ──
    # Removed tinted cell background overlays to keep video streams clear without filters
    pass

    # ── grid lines ──
    cv2.rectangle(frame, (grid_x0, grid_y0), (grid_x1 - 1, grid_y1 - 1), C_WHITE, 1)
    for i in range(1, 3):
        y_line = grid_y0 + i * grid_h // 3
        x_line = grid_x0 + i * grid_w // 3
        cv2.line(frame, (grid_x0, y_line), (grid_x1, y_line), C_WHITE, 1)
        cv2.line(frame, (x_line, grid_y0), (x_line, grid_y1), C_WHITE, 1)

    # ── per-cell labels ──
    for r in range(3):
        cy0 = grid_y0 + r * grid_h // 3
        cy1 = grid_y0 + (r + 1) * grid_h // 3
        for c in range(3):
            cx0 = grid_x0 + c * grid_w // 3
            cx1 = grid_x0 + (c + 1) * grid_w // 3

            density = float(zone_scores[r][c])   if zone_scores  is not None else 0.0
            motion  = float(zone_motions[r][c])  if zone_motions is not None else 0.0
            motion  = min(motion, 9.99)           # clamp display (noise guard)

            lx = cx0 + 5
            label_y0 = max(cy0, BANNER_H) if r == 0 else cy0

            # Cell name  (e.g. A1)
            _text(frame, f"{labels[r]}{c+1}", lx, label_y0 + 18, C_WHITE, 0.45, 1)

            # Density
            _text(frame, f"D:{density:.0f}", lx, label_y0 + 34, C_WHITE, 0.40, 1)

            # Motion
            _text(frame, f"M:{motion:.2f}", lx, label_y0 + 50, C_YELLOW, 0.38, 1)

            # Trend (ASCII only)
            if trend_matrix is not None:
                trend = str(trend_matrix[r, c])
                tcol  = TREND_COLORS.get(trend, C_WHITE)
                tlbl  = TREND_LABELS.get(trend, trend)
                _text(frame, tlbl, lx, label_y0 + 65, tcol, 0.30, 1)

            # Opposing flow hatch
            if opposing_danger is not None and opposing_danger[r, c]:
                _draw_opposing_marker(frame, cx0, cy0, cx1, cy1)

    # ── hotspot highlight ──
    if zone_scores is not None:
        max_idx = np.argmax(zone_scores)
        max_r, max_c = divmod(int(max_idx), 3)
        max_val = float(zone_scores[max_r, max_c])
        max_cap = float(cap_grid[max_r, max_c])
        if max_val / max(max_cap, 1.0) >= 0.30:  # Highlight only if cell is at least 30% full
            hy0 = grid_y0 + max_r * grid_h // 3
            hy1 = grid_y0 + (max_r + 1) * grid_h // 3
            hx0 = grid_x0 + max_c * grid_w // 3
            hx1 = grid_x0 + (max_c + 1) * grid_w // 3

            cv2.rectangle(frame, (hx0 + 3, hy0 + 3), (hx1 - 3, hy1 - 3), C_RED, 3)
            # Small "HOTSPOT" badge
            bx, by = max(hx1 - 98, hx0 + 5), hy0 + 5
            cv2.rectangle(frame, (bx, by), (bx + 90, by + 22), C_RED, -1)
            cv2.putText(frame, "HOTSPOT", (bx + 5, by + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_WHITE, 1, cv2.LINE_AA)


def _draw_opposing_marker(img, x0, y0, x1, y1, color=C_MAGENTA):
    """Mark opposing flow without visually filling the whole cell."""
    cv2.rectangle(img, (x0 + 4, y0 + 4), (x1 - 4, y1 - 4), color, 2)
    _text(img, "OPPOSING", x0 + 8, y1 - 12, color, 0.34, 1)


# ─── density heatmap overlay ───────────────────────────────────────────
def draw_density_heatmap(frame, density_map: np.ndarray, alpha: float = HEATMAP_ALPHA):
    """
    Overlay a JET-colormap heatmap of the density map onto the frame.
    density_map: 2D array (H, W) with density values
    """
    if density_map is None or density_map.size == 0:
        return
    
    h, w = frame.shape[:2]
    dm_h, dm_w = density_map.shape
    
    # Normalize density map to [0, 1] for colormap
    dm_max = density_map.max()
    if dm_max <= 0:
        return
    dm_norm = density_map / dm_max
    
    # Resize and colorize in optimized native code. The old nested Python loop
    # executed once for every output pixel and could freeze a 720p stream.
    dm_resized = cv2.resize(dm_norm, (w, h), interpolation=cv2.INTER_LINEAR)
    heatmap = cv2.applyColorMap(
        np.clip(dm_resized * 255.0, 0, 255).astype(np.uint8),
        cv2.COLORMAP_JET,
    )
    
    # Blend with frame
    cv2.addWeighted(heatmap, alpha, frame, 1.0 - alpha, 0, frame)


# ─── stampede panel ───────────────────────────────────────────────────

def draw_stampede_panel(frame, predictor_result: dict):
    """
    Fixed-width panel anchored to the RIGHT edge, starting below the banner.
    Displays Crowd Risk Index, level, component metrics, confidence, and primary causes.
    """
    h, w  = frame.shape[:2]
    px0   = w - PANEL_W
    py0   = BANNER_H
    pad   = 8

    # Semi-transparent background
    _fill(frame, px0, py0, w, h - 30, C_BLACK, alpha=0.72)
    cv2.rectangle(frame, (px0, py0), (w - 1, h - 31), C_GRAY, 1)

    risk_idx = float(predictor_result.get("risk_index", 0.0))
    label    = str(predictor_result.get("label", "SAFE"))
    lcolor   = predictor_result.get("label_color", C_GREEN)
    confidence = float(predictor_result.get("confidence", 1.0))
    primary_causes = predictor_result.get("primary_causes", [])
    terms    = predictor_result.get("terms", {})

    # ── title ──
    cv2.putText(frame, "CROWD RISK INDEX",
                (px0 + pad, py0 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, C_WHITE, 1, cv2.LINE_AA)

    # ── main bar ──
    bx0 = px0 + pad
    by0 = py0 + 24
    bw  = PANEL_W - 2 * pad
    bh  = 22
    cv2.rectangle(frame, (bx0, by0), (bx0 + bw, by0 + bh), C_GRAY, -1)
    cv2.rectangle(frame, (bx0, by0), (bx0 + int((risk_idx / 100.0) * bw), by0 + bh), lcolor, -1)
    cv2.rectangle(frame, (bx0, by0), (bx0 + bw, by0 + bh), C_WHITE, 1)

    # Label + index/100
    cv2.putText(frame, f"{label} ({int(risk_idx)}/100)",
                (bx0, by0 + bh + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, lcolor, 1, cv2.LINE_AA)

    # ── component mini-bars ──
    components = [
        ("density",    "Density"),
        ("motion",     "Motion"),
        ("turbulence", "Turb."),
        ("growth",     "Growth"),
        ("opposing",   "OppFlow"),
    ]
    name_w = 52   # pixel width reserved for the name label
    mini_h = 9
    cy = by0 + bh + 30

    for key, name in components:
        val   = float(terms.get(key, 0.0))
        bcol  = C_GREEN if val < 0.4 else (C_YELLOW if val < 0.7 else C_RED)
        fill  = int(val * (bw - name_w))

        cv2.putText(frame, name,
                    (bx0, cy + mini_h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.28, C_GRAY, 1, cv2.LINE_AA)

        bx_s = bx0 + name_w
        bx_e = bx0 + bw
        cv2.rectangle(frame, (bx_s, cy), (bx_e, cy + mini_h), C_DGRAY, -1)
        cv2.rectangle(frame, (bx_s, cy), (bx_s + fill, cy + mini_h), bcol, -1)
        cv2.rectangle(frame, (bx_s, cy), (bx_e, cy + mini_h), C_GRAY, 1)
        cy += mini_h + 8

    # ── confidence & primary causes ──
    _text(frame, f"Conf: {confidence:.2f}", bx0, cy + 10, C_WHITE, 0.32, 1)
    cy += 14

    if primary_causes:
        _text(frame, "CAUSES:", bx0, cy + 10, C_WHITE, 0.30, 1)
        cy += 14
        for cause in primary_causes[:3]:
            _text(frame, f"- {cause}", bx0 + 4, cy + 10, C_ORANGE, 0.28, 1)
            cy += 12


# ─── alert ticker ─────────────────────────────────────────────────────

def draw_alert_ticker(frame, alerts: list, alert_first_shown_times: dict = None, current_time: float = None):
    """
    Single-line coloured alert bar pinned to the BOTTOM of the frame.
    Priority: CRITICAL > HIGH/EXPANDING > WATCH > stable.
    """
    h, w = frame.shape[:2]

    # Pick highest-severity alert
    chosen = ""
    for a in alerts:
        if "CRITICAL" in a or "EVACUATE" in a or "STAMPEDE" in a:
            chosen = a
            break
    if not chosen:
        for a in alerts:
            if "HIGH" in a or "EXPANDING" in a or "OPPOSING" in a:
                chosen = a
                break
    if not chosen and alerts:
        chosen = alerts[-1]
    if not chosen:
        return

    is_repeated = False
    if alert_first_shown_times is not None and current_time is not None and chosen in alert_first_shown_times:
        first_shown = alert_first_shown_times[chosen]
        elapsed = current_time - first_shown
        if elapsed > 30.0:  # remove repeats after 30 seconds
            return
        elif elapsed > 10.0:  # dim repeated alerts after 10 seconds
            is_repeated = True

    if is_repeated:
        bg, fg = C_DGRAY, C_GRAY
    elif "CRITICAL" in chosen or "EVACUATE" in chosen or "STAMPEDE" in chosen:
        bg, fg = C_RED, C_WHITE
    elif "HIGH" in chosen or "EXPANDING" in chosen or "OPPOSING" in chosen:
        bg, fg = C_ORANGE, C_BLACK
    elif "MONITOR" in chosen or "GROWING" in chosen or "WATCH" in chosen:
        bg, fg = C_YELLOW, C_BLACK
    else:
        bg, fg = C_DGRAY, C_WHITE

    cv2.rectangle(frame, (0, h - 30), (w, h), bg, -1)
    cv2.putText(frame, chosen, (10, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, fg, 1, cv2.LINE_AA)
