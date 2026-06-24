"""
optical_flow.py  — fixed & clean
Fixes vs previous:
  1. Exposes self.last_flow for OpposingFlowDetector reuse
  2. Motion noise guard: uses MEDIAN instead of mean per cell
     (sparse/water areas have many near-zero pixels that inflate mean)
  3. Global speed also uses median
  4. Turbulence computed only on pixels with motion > threshold
     so water ripple noise does not spike turbulence
"""

import cv2
import numpy as np

# Pixels below this magnitude are considered "static" (noise floor)
MOTION_NOISE_FLOOR = 0.4   # px/frame at 240×135 scale


class CrowdMotionAnalyzer:
    def __init__(self):
        self.prev_gray  = None
        self.last_flow  = None   # exposed for OpposingFlowDetector

    def analyze_motion(self, frame_bgr: np.ndarray):
        """
        Returns
        -------
        speed_grid   : np.ndarray (3,3) — MEDIAN speed per cell [px/frame]
        global_speed : float            — MEDIAN speed across frame
        turbulence   : float            — std-dev of speeds above noise floor
        """
        small = cv2.resize(frame_bgr, (240, 135), interpolation=cv2.INTER_AREA)
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if self.prev_gray is None:
            self.prev_gray = gray
            self.last_flow = np.zeros((135, 240, 2), dtype=np.float32)
            return np.zeros((3, 3)), 0.0, 0.0

        flow = cv2.calcOpticalFlowFarneback(
            self.prev_gray, gray, None,
            0.5,   # pyr_scale
            3,     # levels
            15,    # winsize
            3,     # iterations
            5,     # poly_n
            1.2,   # poly_sigma
            0,     # flags
        )
        self.prev_gray = gray
        self.last_flow = flow   # reused by OpposingFlowDetector

        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)

        # Global metrics — ignore sub-noise pixels
        active = magnitude[magnitude > MOTION_NOISE_FLOOR]
        global_speed = float(np.median(active)) if active.size else 0.0
        turbulence   = float(np.std(active))    if active.size else 0.0

        # Per-cell speed grid (3×3)
        fh, fw     = flow.shape[:2]
        ch, cw     = fh // 3, fw // 3
        speed_grid = np.zeros((3, 3))

        for r in range(3):
            for c in range(3):
                r0 = r * ch;  r1 = (r + 1) * ch if r < 2 else fh
                c0 = c * cw;  c1 = (c + 1) * cw if c < 2 else fw
                cell = magnitude[r0:r1, c0:c1]
                active_cell = cell[cell > MOTION_NOISE_FLOOR]
                speed_grid[r, c] = (
                    float(np.median(active_cell)) if active_cell.size else 0.0
                )

        return speed_grid, global_speed, turbulence
