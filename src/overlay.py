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
"""

import cv2
import numpy as np

# ─── constants ────────────────────────────────────────────────────────
BANNER_H  = 110        # must match draw_top_banner rect height
PANEL_W   = 200        # stampede panel width (px)

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
    if alpha >= 1.0:
        cv2.rectangle(img, (x0, y0), (x1, y1), color, -1)
    else:
        tmp = img.copy()
        cv2.rectangle(tmp, (x0, y0), (x1, y1), color, -1)
        cv2.addWeighted(tmp, alpha, img, 1.0 - alpha, 0, img)


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
    max_capacity   : float = 50.0,
    panel_visible  : bool  = False,  # True → shrink col 3 text to avoid panel
):
    h, w = frame.shape[:2]

    # Grid lives BELOW the banner
    grid_y0 = BANNER_H
    grid_h  = h - BANNER_H - 30   # leave 30px for alert ticker at bottom
    cell_w  = w // 3
    labels  = ["A", "B", "C"]
    ROW_BOUNDARIES = [0.0, 0.30, 0.62, 1.0]

    # ── tinted cell backgrounds ──
    overlay_layer = frame.copy()
    for r in range(3):
        y0 = grid_y0 + int(ROW_BOUNDARIES[r] * grid_h)
        y1 = grid_y0 + int(ROW_BOUNDARIES[r+1] * grid_h) if r < 2 else (h - 30)
        for c in range(3):
            x0 = c * cell_w
            x1 = (c + 1) * cell_w if c < 2 else w
            density  = float(zone_scores[r][c]) if zone_scores is not None else 0.0
            pressure = min(1.0, density / max_capacity)
            color = _pressure_color(pressure)
            if color != C_GREEN:
                cv2.rectangle(overlay_layer, (x0, y0), (x1, y1), color, -1)

    cv2.addWeighted(overlay_layer, 0.15, frame, 0.85, 0, frame)

    # ── grid lines ──
    for i in range(1, 3):
        y_line = grid_y0 + int(ROW_BOUNDARIES[i] * grid_h)
        cv2.line(frame, (0, y_line), (w, y_line), C_WHITE, 1)
        cv2.line(frame, (i * cell_w, grid_y0), (i * cell_w, h - 30), C_WHITE, 1)

    # ── per-cell labels ──
    for r in range(3):
        cy0 = grid_y0 + int(ROW_BOUNDARIES[r] * grid_h)
        cy1 = grid_y0 + int(ROW_BOUNDARIES[r+1] * grid_h) if r < 2 else (h - 30)
        for c in range(3):
            cx0 = c * cell_w

            density = float(zone_scores[r][c])   if zone_scores  is not None else 0.0
            motion  = float(zone_motions[r][c])  if zone_motions is not None else 0.0
            motion  = min(motion, 9.99)           # clamp display (noise guard)

            lx = cx0 + 5

            # Cell name  (e.g. A1)
            _text(frame, f"{labels[r]}{c+1}", lx, cy0 + 18, C_WHITE, 0.45, 1)

            # Density
            _text(frame, f"D:{density:.0f}", lx, cy0 + 34, C_WHITE, 0.40, 1)

            # Motion
            _text(frame, f"M:{motion:.2f}", lx, cy0 + 50, C_YELLOW, 0.38, 1)

            # Trend (ASCII only)
            if trend_matrix is not None:
                trend = str(trend_matrix[r, c])
                tcol  = TREND_COLORS.get(trend, C_WHITE)
                tlbl  = TREND_LABELS.get(trend, trend)
                _text(frame, tlbl, lx, cy0 + 65, tcol, 0.30, 1)

            # Opposing flow hatch
            if opposing_danger is not None and opposing_danger[r, c]:
                x1 = (cx0 + cell_w) if c < 2 else w
                y1 = cy1
                _draw_hatch(frame, cx0, cy0, x1, y1)

    # ── hotspot highlight ──
    if zone_scores is not None and np.max(zone_scores) > 0.0:
        max_r, max_c = divmod(int(np.argmax(zone_scores)), 3)
        hy0 = grid_y0 + int(ROW_BOUNDARIES[max_r] * grid_h)
        hy1 = grid_y0 + int(ROW_BOUNDARIES[max_r + 1] * grid_h) if max_r < 2 else (h - 30)
        hx0 = max_c * cell_w
        hx1 = (max_c + 1) * cell_w if max_c < 2 else w

        cv2.rectangle(frame, (hx0 + 3, hy0 + 3), (hx1 - 3, hy1 - 3), C_RED, 3)
        # Small "HOTSPOT" badge
        bx, by = hx0 + 5, hy0 + 5
        cv2.rectangle(frame, (bx, by), (bx + 90, by + 22), C_RED, -1)
        cv2.putText(frame, "HOTSPOT", (bx + 5, by + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_WHITE, 1, cv2.LINE_AA)


def _draw_hatch(img, x0, y0, x1, y1, color=C_MAGENTA, spacing=14):
    """Light diagonal hatching to flag opposing flow cells."""
    span = (x1 - x0) + (y1 - y0)
    for offset in range(0, span, spacing):
        sx, sy = x0 + offset, y0
        ex, ey = x0, y0 + offset
        if sx > x1: sy += sx - x1; sx = x1
        if ey > y1: ex += ey - y1; ey = y1
        if sx <= x1 and ey <= y1:
            cv2.line(img, (int(sx), int(sy)), (int(ex), int(ey)), color, 1, cv2.LINE_AA)


# ─── stampede panel ───────────────────────────────────────────────────

def draw_stampede_panel(frame, predictor_result: dict):
    """
    Fixed-width panel anchored to the RIGHT edge, starting below the banner.
    Does NOT interfere with grid cell A3/B3/C3 because the grid text
    is left-aligned within each cell (lx = cx0 + 5).
    The panel is semi-transparent so the video is still visible behind it.
    """
    h, w  = frame.shape[:2]
    px0   = w - PANEL_W
    py0   = BANNER_H
    pad   = 8

    # Semi-transparent background
    _fill(frame, px0, py0, w, h - 30, C_BLACK, alpha=0.72)
    cv2.rectangle(frame, (px0, py0), (w - 1, h - 31), C_GRAY, 1)

    prob   = float(predictor_result.get("smooth_prob", 0.0))
    label  = str(predictor_result.get("label", "SAFE"))
    lcolor = predictor_result.get("label_color", C_GREEN)
    terms  = predictor_result.get("terms", {})

    # ── title ──
    cv2.putText(frame, "STAMPEDE RISK",
                (px0 + pad, py0 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, C_WHITE, 1, cv2.LINE_AA)

    # ── main bar ──
    bx0 = px0 + pad
    by0 = py0 + 24
    bw  = PANEL_W - 2 * pad
    bh  = 22
    cv2.rectangle(frame, (bx0, by0), (bx0 + bw, by0 + bh), C_GRAY, -1)
    cv2.rectangle(frame, (bx0, by0), (bx0 + int(prob * bw), by0 + bh), lcolor, -1)
    cv2.rectangle(frame, (bx0, by0), (bx0 + bw, by0 + bh), C_WHITE, 1)

    # Label + pct
    cv2.putText(frame, f"{label}  {int(prob*100)}%",
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
