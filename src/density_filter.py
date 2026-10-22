import cv2
import numpy as np


_OVERLAY_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
# Rows of context kept around a band: closing and dilation (2 rows each) and
# the inpainting radius (3) then see exactly what they see in the full frame.
_OVERLAY_BAND_PAD = 12


def _overlay_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)

    bright_sat = (sat > 95) & (val > 135)
    overlay_hues = (
        (hue < 12) | (hue > 168) |          # red
        ((hue > 24) & (hue < 42)) |         # yellow
        ((hue > 42) & (hue < 88)) |         # green
        ((hue > 88) & (hue < 105))          # cyan
    )
    return (bright_sat & overlay_hues).astype(np.uint8) * 255


def suppress_broadcast_overlays(frame_bgr: np.ndarray) -> np.ndarray:
    """
    Remove bright saturated stream text/graphics before crowd inference.
    Keeps normal video intact, but inpaints neon red/green/yellow/cyan overlays
    such as RTMP/Larix watermarks that can look like dense crowds to DM-Count.

    Only the top and bottom 15% margins are cleaned (bright clothes in the
    crowd must not be smeared), so only those bands are examined: about a
    third of the colour conversion and masking work of the whole frame, with
    output identical to cleaning the whole frame with the middle masked out.
    Returns the input itself when there is nothing to remove.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return frame_bgr

    height = frame_bgr.shape[0]
    margin_h = int(height * 0.15)
    bands = [(0, height)] if margin_h <= 0 else [(0, margin_h), (height - margin_h, height)]
    found = []
    total = 0
    for top, bottom in bands:
        mask = _overlay_mask(frame_bgr[top:bottom])
        pixels = cv2.countNonZero(mask)
        total += pixels
        if pixels:
            found.append((top, bottom, mask))
    if total < 64:
        return frame_bgr

    cleaned = frame_bgr.copy()
    for top, bottom, band_mask in found:
        lo = max(0, top - _OVERLAY_BAND_PAD)
        hi = min(height, bottom + _OVERLAY_BAND_PAD)
        mask = np.zeros((hi - lo, frame_bgr.shape[1]), dtype=np.uint8)
        mask[top - lo:bottom - lo] = band_mask
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _OVERLAY_KERNEL, iterations=1)
        mask = cv2.dilate(mask, _OVERLAY_KERNEL, iterations=1)
        cleaned[lo:hi] = cv2.inpaint(cleaned[lo:hi], mask, 3, cv2.INPAINT_TELEA)
    return cleaned


def clean_density_map(
    dmap_np: np.ndarray,
    source_frame_bgr: np.ndarray | None = None,
    speckle_ratio: float = 0.015,
) -> np.ndarray:
    """
    Clip negative values, remove tiny density speckles, and zero density that
    lands directly on bright saturated broadcast graphics.
    """
    if dmap_np is None:
        return dmap_np

    dmap = np.clip(dmap_np.astype(np.float32, copy=True), 0, None)
    peak = float(dmap.max()) if dmap.size else 0.0
    if peak <= 0.0:
        return dmap

    if speckle_ratio > 0.0:
        # absolute floor = 0.002, absolute cap = 0.05
        threshold = max(0.002, min(peak * speckle_ratio, 0.05))
        dmap[dmap < threshold] = 0.0

    if source_frame_bgr is None or source_frame_bgr.size == 0:
        return dmap

    # ponytail: downsample frame to density map resolution first for ~6x faster mask computation
    small_bgr = cv2.resize(source_frame_bgr, (dmap.shape[1], dmap.shape[0]), interpolation=cv2.INTER_LINEAR)
    hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    bright_sat = (sat > 95) & (val > 135)
    overlay_hues = (
        (hue < 12) | (hue > 168) |
        ((hue > 24) & (hue < 42)) |
        ((hue > 42) & (hue < 88)) |
        ((hue > 88) & (hue < 105))
    )
    mask = (bright_sat & overlay_hues).astype(np.uint8)
    # Only apply overlay suppression to top and bottom 15% margins
    h_mask = mask.shape[0]
    margin_h = int(h_mask * 0.15)
    if margin_h > 0:
        mask[margin_h : h_mask - margin_h, :] = 0

    dmap[mask > 0] = 0.0

    return dmap
