import cv2
import numpy as np

def apply_heatmap(display_frame, dmap_np, alpha=0.45):
    if dmap_np is None:
        return display_frame

    h, w = display_frame.shape[:2]
    dmap_full = cv2.resize(dmap_np, (w, h), interpolation=cv2.INTER_CUBIC)
    dmap_full = np.clip(dmap_full, 0, None)

    positive = dmap_full[dmap_full > 0]
    if positive.size == 0:
        return display_frame

    p_low = np.percentile(positive, 65)
    p_high = np.percentile(positive, 99.7)

    if p_high <= p_low + 1e-8:
        return display_frame

    dmap_norm = np.clip((dmap_full - p_low) / (p_high - p_low), 0, 1)
    dmap_u8 = (dmap_norm * 255).astype(np.uint8)

    cmap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)
    heatmap_bgr = cv2.applyColorMap(dmap_u8, cmap).astype(np.float32)

    frame_f = display_frame.astype(np.float32)
    blend_w = (dmap_norm * alpha)[..., np.newaxis]

    blended = frame_f * (1.0 - blend_w) + heatmap_bgr * blend_w
    return np.clip(blended, 0, 255).astype(np.uint8)
