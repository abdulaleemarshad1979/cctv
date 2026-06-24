"""
opposing_flow_detector.py
Detects opposing / convergent crowd flow within each 3×3 zone.
Opposing flow (people moving in opposite directions in the same cell)
is one of the strongest precursors to crowd crush / stampede.
"""

import cv2
import numpy as np


# How much of a cell must move in "opposite" direction to count
OPPOSING_RATIO_THRESHOLD = 0.20   # 20 % of pixels moving against majority
OPPOSING_DANGER_RATIO    = 0.35   # 35 % → dangerous


class OpposingFlowDetector:
    """
    Given an optical-flow field (H×W×2), analyse each 3×3 cell for
    opposing flow in both x and y axes.

    Opposing flow score per cell ∈ [0, 1]:
        0   = perfectly uni-directional
        1   = perfectly opposing (50/50 split)
    """

    def analyze(
        self,
        flow: np.ndarray,          # shape (H, W, 2)  from cv2.calcOpticalFlowFarneback
        magnitude_threshold: float = 0.3,  # ignore sub-pixel noise
    ) -> dict:
        """
        Returns
        -------
        {
          "opposing_score_grid": np.ndarray (3,3) float in [0,1]
          "danger_grid":         np.ndarray (3,3) bool
          "max_score":           float
          "any_dangerous":       bool
          "alert_text":          str
        }
        """
        fh, fw = flow.shape[:2]
        cell_h = fh // 3
        cell_w = fw // 3

        flow_x = flow[..., 0]
        flow_y = flow[..., 1]
        mag    = np.sqrt(flow_x ** 2 + flow_y ** 2)

        score_grid  = np.zeros((3, 3))
        danger_grid = np.zeros((3, 3), dtype=bool)

        for r in range(3):
            for c in range(3):
                r0 = r * cell_h
                r1 = (r + 1) * cell_h if r < 2 else fh
                c0 = c * cell_w
                c1 = (c + 1) * cell_w if c < 2 else fw

                cell_mag = mag[r0:r1, c0:c1]
                cell_fx  = flow_x[r0:r1, c0:c1]
                cell_fy  = flow_y[r0:r1, c0:c1]

                # Only consider pixels with meaningful motion
                mask = cell_mag > magnitude_threshold
                if mask.sum() < 10:
                    continue

                fx_valid = cell_fx[mask]
                fy_valid = cell_fy[mask]

                # Opposing score = fraction of pixels moving against majority
                # Axis X
                pos_x = (fx_valid > 0).sum()
                neg_x = (fx_valid < 0).sum()
                total = len(fx_valid)
                minority_x = min(pos_x, neg_x) / max(total, 1)

                # Axis Y
                pos_y = (fy_valid > 0).sum()
                neg_y = (fy_valid < 0).sum()
                minority_y = min(pos_y, neg_y) / max(total, 1)

                # Combined score: worst axis
                opp_score = float(max(minority_x, minority_y))
                score_grid[r, c]  = opp_score
                danger_grid[r, c] = opp_score >= OPPOSING_DANGER_RATIO

        max_score     = float(score_grid.max())
        any_dangerous = bool(danger_grid.any())

        # Build alert text
        cell_labels = ["A", "B", "C"]
        danger_cells = [
            f"{cell_labels[r]}{c + 1}"
            for r in range(3)
            for c in range(3)
            if danger_grid[r, c]
        ]
        alert_text = (
            "OPPOSING FLOW DETECTED: " + ", ".join(danger_cells)
            if danger_cells else ""
        )

        return {
            "opposing_score_grid": score_grid,
            "danger_grid":         danger_grid,
            "max_score":           max_score,
            "any_dangerous":       any_dangerous,
            "alert_text":          alert_text,
        }


# -----------------------------------------------------------------------
# Convenience: extract raw flow from the Farneback result already computed
# in optical_flow.py so we don't recompute it.
# optical_flow.CrowdMotionAnalyzer should expose `last_flow` — add this
# one-liner to that class:  self.last_flow = flow   (after computing it)
# -----------------------------------------------------------------------
def extract_flow_from_analyzer(motion_analyzer) -> np.ndarray | None:
    """Return the last optical flow computed by CrowdMotionAnalyzer, or None."""
    return getattr(motion_analyzer, "last_flow", None)
