import cv2
import numpy as np


def suppress_broadcast_overlays(frame_bgr: np.ndarray) -> np.ndarray:
    """
    Remove bright saturated stream text/graphics before crowd inference.
    Keeps normal video intact, but inpaints neon red/green/yellow/cyan overlays
    such as RTMP/Larix watermarks that can look like dense crowds to DM-Count.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return frame_bgr

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)

    bright_sat = (sat > 95) & (val > 135)
    overlay_hues = (
        (hue < 12) | (hue > 168) |          # red
        ((hue > 24) & (hue < 42)) |         # yellow
        ((hue > 42) & (hue < 88)) |         # green
        ((hue > 88) & (hue < 105))          # cyan
    )
    mask = (bright_sat & overlay_hues).astype(np.uint8) * 255

    if cv2.countNonZero(mask) < 64:
        return frame_bgr

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=1)

    return cv2.inpaint(frame_bgr, mask, 3, cv2.INPAINT_TELEA)


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
        dmap[dmap < peak * speckle_ratio] = 0.0

    if source_frame_bgr is None or source_frame_bgr.size == 0:
        return dmap

    hsv = cv2.cvtColor(source_frame_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    bright_sat = (sat > 95) & (val > 135)
    overlay_hues = (
        (hue < 12) | (hue > 168) |
        ((hue > 24) & (hue < 42)) |
        ((hue > 42) & (hue < 88)) |
        ((hue > 88) & (hue < 105))
    )
    mask = (bright_sat & overlay_hues).astype(np.uint8)
    mask = cv2.resize(mask, (dmap.shape[1], dmap.shape[0]), interpolation=cv2.INTER_AREA)
    dmap[mask > 0] = 0.0

    return dmap
