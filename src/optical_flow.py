"""
optical_flow.py  — Pushkaralu edition
Fixes vs previous version:
  1. Exposes self.last_flow for OpposingFlowDetector reuse
  2. Motion noise guard: uses MEDIAN instead of mean per cell
  3. Global speed also uses median
  4. Turbulence computed only on pixels with motion > threshold
  5. NEW: Temporal EMA smoothing per cell — kills one-frame boat/wave spikes
  6. NEW: Consistency gate — cell motion only counted if active pixels
     exceed MIN_ACTIVE_RATIO of the cell (water cells fail this)
  7. NEW: Frame-to-frame camera shake compensation via global median subtraction
"""

import cv2
import numpy as np

# Pixels below this magnitude are considered static (noise floor)
MOTION_NOISE_FLOOR = 0.5       # raised from 0.4 — tighter for outdoor aerial

# A cell must have at least this fraction of pixels moving to count
# Water/empty cells have scattered random motion, not coherent movement
MIN_ACTIVE_RATIO   = 0.08      # 8% of cell pixels must be above noise floor

# EMA smoothing per cell — kills single-frame spikes from waves/boats
# Lower = more smoothing (slower response), Higher = faster response
MOTION_EMA_ALPHA   = 0.35

# Camera shake compensation: subtract global median flow vector to remove it.
# This removes drone-drift motion that affects every cell equally
COMPENSATE_SHAKE   = True

import config
import torch

if torch.cuda.is_available():
    FLOW_W, FLOW_H = 480, 270   # raised flow resolution to 1/4 of display resolution
    FARNEBACK_WINSIZE = 25
else:
    FLOW_W, FLOW_H = 320, 180   # optimized resolution on CPU to run smoothly
    FARNEBACK_WINSIZE = 15

# Detect CUDA support in OpenCV
OPENCV_CUDA_AVAILABLE = False
try:
    if hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0:
        OPENCV_CUDA_AVAILABLE = True
except Exception:
    pass

OPTICAL_FLOW_GPU_ACTIVE = OPENCV_CUDA_AVAILABLE and getattr(config, "OPTICAL_FLOW_GPU", False)


class CrowdMotionAnalyzer:
    def __init__(self):
        self.prev_gray   = None
        self.last_flow   = None          # exposed for OpposingFlowDetector
        self._ema_grid   = np.zeros((3, 3))   # smoothed per-cell speeds
        
        # GPU/CUDA optical flow helper setup
        self.gpu_flow_obj = None
        if OPTICAL_FLOW_GPU_ACTIVE:
            try:
                self.gpu_flow_obj = cv2.cuda_FarnebackOpticalFlow.create(
                    numLevels=3,
                    pyrScale=0.5,
                    fastPyramids=False,
                    winSize=FARNEBACK_WINSIZE,
                    numIters=3,
                    polyN=5,
                    polySigma=1.2,
                    flags=0
                )
                self.gpu_prev = cv2.cuda_GpuMat()
                self.gpu_curr = cv2.cuda_GpuMat()
                print(f"[OPTICAL FLOW] Using OpenCV CUDA GPU path.")
            except Exception as e:
                print(f"[OPTICAL FLOW] Failed to initialize OpenCV CUDA path: {e}. Falling back to CPU.")
                self.gpu_flow_obj = None
        
        if self.gpu_flow_obj is None:
            print(f"[OPTICAL FLOW] Using CPU path.")

    def analyze_motion(self, frame_bgr: np.ndarray):
        """
        Returns
        -------
        speed_grid   : np.ndarray (3,3) — EMA-smoothed MEDIAN speed per cell [px/frame]
        global_speed : float            — median speed across crowd cells only
        turbulence   : float            — std-dev of active-pixel speeds
        """
        small = cv2.resize(frame_bgr, (FLOW_W, FLOW_H), interpolation=cv2.INTER_AREA)
        gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        if self.prev_gray is None:
            self.prev_gray = gray
            self.last_flow = np.zeros((FLOW_H, FLOW_W, 2), dtype=np.float32)
            return np.zeros((3, 3)), 0.0, 0.0

        if self.gpu_flow_obj is not None:
            try:
                self.gpu_prev.upload(self.prev_gray)
                self.gpu_curr.upload(gray)
                gpu_flow = self.gpu_flow_obj.calc(self.gpu_prev, self.gpu_curr, None)
                flow = gpu_flow.download()
            except Exception as e:
                # Fallback to CPU in case of runtime CUDA failure
                flow = cv2.calcOpticalFlowFarneback(
                    self.prev_gray, gray, None,
                    0.5, 3, FARNEBACK_WINSIZE, 3, 5, 1.2, 0
                )
        else:
            flow = cv2.calcOpticalFlowFarneback(
                self.prev_gray, gray, None,
                0.5,
                3,
                FARNEBACK_WINSIZE,
                3,
                5,
                1.2,
                0,
            )
        self.prev_gray = gray

        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)

        # ── Camera shake compensation ──────────────────────────────
        # If the drone drifts, EVERY pixel gets the same offset.
        # Subtract the global median flow vector to remove it.
        if COMPENSATE_SHAKE:
            global_fx_median = float(np.median(flow[..., 0]))
            global_fy_median = float(np.median(flow[..., 1]))
            comp_flow = flow.copy()
            comp_flow[..., 0] -= global_fx_median
            comp_flow[..., 1] -= global_fy_median
            magnitude = np.sqrt(comp_flow[..., 0] ** 2 + comp_flow[..., 1] ** 2)
            self.last_flow = comp_flow
        else:
            self.last_flow = flow

        # ── Global metrics ─────────────────────────────────────────
        active = magnitude[magnitude > MOTION_NOISE_FLOOR]
        global_speed = float(np.median(active)) if active.size else 0.0
        turbulence   = float(np.std(active))    if active.size else 0.0

        # ── Per-cell speed grid (3×3) ──────────────────────────────
        fh, fw     = flow.shape[:2]
        ch, cw     = fh // 3, fw // 3
        raw_grid   = np.zeros((3, 3))

        for r in range(3):
            for c in range(3):
                r0 = r * ch;       r1 = (r + 1) * ch if r < 2 else fh
                c0 = c * cw;       c1 = (c + 1) * cw if c < 2 else fw

                cell      = magnitude[r0:r1, c0:c1]
                total_px  = cell.size

                active_cell = cell[cell > MOTION_NOISE_FLOOR]
                active_ratio = active_cell.size / max(total_px, 1)

                # Consistency gate: if fewer than MIN_ACTIVE_RATIO pixels
                # are moving, treat cell as water/empty — set to zero.
                if active_ratio < MIN_ACTIVE_RATIO:
                    raw_grid[r, c] = 0.0
                else:
                    raw_grid[r, c] = float(np.median(active_cell))

        # ── EMA smoothing — kills wave/boat one-frame spikes ───────
        self._ema_grid = (
            MOTION_EMA_ALPHA * raw_grid
            + (1.0 - MOTION_EMA_ALPHA) * self._ema_grid
        )

        return self._ema_grid.copy(), global_speed, turbulence
