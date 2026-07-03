import cv2
import numpy as np

def apply_heatmap(display_frame, dmap_np, alpha=0.45, state=None):
    if dmap_np is None:
        return display_frame

    h, w = display_frame.shape[:2]
    dmap_full = cv2.resize(dmap_np, (w, h), interpolation=cv2.INTER_CUBIC)
    dmap_full = np.clip(dmap_full, 0, None)

    positive = dmap_full[dmap_full > 0]
    
    # Check for degraded frame (less than 50 positive pixels)
    min_pixels = 50
    is_degraded = (positive.size < min_pixels)

    import config
    smoothing_factor = getattr(config, "HEATMAP_SMOOTHING_FACTOR", 0.35)

    if is_degraded:
        if state is not None and "last_heatmap_norm" in state:
            dmap_norm = state["last_heatmap_norm"]
            state["degraded_alert"] = True
        else:
            return display_frame
    else:
        p_low = np.percentile(positive, 65)
        p_high = np.percentile(positive, 99.7)

        if p_high <= p_low + 1e-8:
            return display_frame

        if state is not None:
            state["degraded_alert"] = False
            if "p_low" in state and "p_high" in state:
                p_low = smoothing_factor * p_low + (1.0 - smoothing_factor) * state["p_low"]
                p_high = smoothing_factor * p_high + (1.0 - smoothing_factor) * state["p_high"]
            state["p_low"] = p_low
            state["p_high"] = p_high

        dmap_norm = np.clip((dmap_full - p_low) / (p_high - p_low), 0, 1)
        if state is not None:
            state["last_heatmap_norm"] = dmap_norm

    dmap_u8 = (dmap_norm * 255).astype(np.uint8)

    cmap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)
    heatmap_bgr = cv2.applyColorMap(dmap_u8, cmap).astype(np.float32)

    frame_f = display_frame.astype(np.float32)
    blend_w = (dmap_norm * alpha)[..., np.newaxis]

    blended = frame_f * (1.0 - blend_w) + heatmap_bgr * blend_w
    out_frame = np.clip(blended, 0, 255).astype(np.uint8)

    if state is not None and state.get("degraded_alert"):
        cv2.putText(out_frame, "SIGNAL DEGRADED (HEATMAP STALE)", (10, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 140, 255), 1, cv2.LINE_AA)

    return out_frame
