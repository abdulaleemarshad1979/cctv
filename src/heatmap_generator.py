import cv2
import cv2
import numpy as np

_last_dmap = None
_cached_heatmap_bgr = None
_cached_dmap_norm = None

def apply_heatmap(display_frame, dmap_np, alpha=0.45, state=None):
    global _last_dmap, _cached_heatmap_bgr, _cached_dmap_norm
    if dmap_np is None:
        return display_frame

    h, w = display_frame.shape[:2]
    
    # Run heavy operations at a lower resolution to keep display loop FPS fluid
    w_small = min(w, 320)
    h_small = int(h * w_small / w)

    # Check if we can reuse the cached heatmap and norm mask
    if _last_dmap is dmap_np and _cached_heatmap_bgr is not None and _cached_dmap_norm is not None:
        heatmap_bgr_small = _cached_heatmap_bgr
        dmap_norm_small = _cached_dmap_norm
    else:
        dmap_raw = np.clip(dmap_np, 0, None)
        positive = dmap_raw[dmap_raw > 0]
        
        # Scale the degraded pixel threshold to match raw density map resolution
        min_pixels_raw = max(2, int(50 * dmap_raw.size / (w * h)))
        is_degraded = (positive.size < min_pixels_raw)

        import config
        smoothing_factor = getattr(config, "HEATMAP_SMOOTHING_FACTOR", 0.35)

        if is_degraded:
            if state is not None and "last_heatmap_norm" in state:
                dmap_norm_small = state["last_heatmap_norm"]
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

            dmap_norm_raw = np.clip((dmap_raw - p_low) / (p_high - p_low), 0, 1)
            # Interpolate the normalized map to small working dimensions
            dmap_norm_small = cv2.resize(dmap_norm_raw, (w_small, h_small), interpolation=cv2.INTER_LINEAR)
            dmap_norm_small = np.clip(dmap_norm_small, 0, 1)
            
            if state is not None:
                state["last_heatmap_norm"] = dmap_norm_small

        dmap_u8_small = (dmap_norm_small * 255).astype(np.uint8)
        cmap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)
        heatmap_bgr_small = cv2.applyColorMap(dmap_u8_small, cmap)

        # Cache the results
        _last_dmap = dmap_np
        _cached_heatmap_bgr = heatmap_bgr_small
        _cached_dmap_norm = dmap_norm_small

    # 1. Resize display_frame to the small working resolution
    small_frame = cv2.resize(display_frame, (w_small, h_small), interpolation=cv2.INTER_AREA)

    # 2. Perform alpha blending at the low resolution
    w_blend = (dmap_norm_small * alpha)[..., np.newaxis]
    diff = heatmap_bgr_small.astype(np.float32) - small_frame
    diff *= w_blend
    blended_small = cv2.convertScaleAbs(small_frame + diff)

    # 3. Create a mask of where the heatmap is active
    active_mask_small = (dmap_norm_small > 0.01).astype(np.uint8) * 255

    # 4. Resize blended image and active mask back to original resolution
    blended_large = cv2.resize(blended_small, (w, h), interpolation=cv2.INTER_LINEAR)
    active_mask_large = cv2.resize(active_mask_small, (w, h), interpolation=cv2.INTER_NEAREST)

    # 5. Overlay only the active heatmap region on the original high-quality frame
    out_frame = display_frame.copy()
    mask_bool = active_mask_large > 0
    out_frame[mask_bool] = blended_large[mask_bool]

    if state is not None and state.get("degraded_alert"):
        cv2.putText(out_frame, "SIGNAL DEGRADED (HEATMAP STALE)", (10, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 140, 255), 1, cv2.LINE_AA)

    return out_frame
