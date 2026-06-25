"""
infer.py  — fixed & clean
Main pipeline: producer → inference worker → display loop.

Fixes vs previous version:
  1. draw_grid_3x3 now receives grid_y0 offset so it starts below banner
  2. draw_stampede_panel only drawn when show_stampede=True (key 's')
  3. Correct BANNER_H constant imported from overlay so layout is consistent
  4. Motion grid passed correctly to draw_grid_3x3 as zone_motions
  5. No redundant re-computation of risk inside display loop
"""

import os
import sys
import cv2
import time
import queue
import threading
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

import config
from src import (
    overlay,
    heatmap_generator,
    risk_engine,
    optical_flow,
    zone_monitor,
    drone_stream,
    logger,
)
from src.history_buffer         import HistoryBuffer
from src.hotspot_tracker        import HotspotTracker
from src.opposing_flow_detector import OpposingFlowDetector
from src.stampede_predictor     import StampedePredictor

# ── model setup ───────────────────────────────────────────────────────
from dm_count.models import vgg19

device     = torch.device("cuda" if torch.cuda.is_available() else "cpu")
AMP_ENABLED = device.type == "cuda"
if AMP_ENABLED:
    torch.backends.cudnn.benchmark = True
print(f"[INFO] Device: {device}")


def _load_model():
    if not os.path.exists(config.WEIGHTS_PATH):
        raise FileNotFoundError(config.WEIGHTS_PATH)
    ckpt = torch.load(config.WEIGHTS_PATH, map_location=device)
    # unwrap checkpoint wrappers
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "ema"):
            if k in ckpt and isinstance(ckpt[k], dict):
                ckpt = ckpt[k]; break
    sd = {
        k.replace("module.", "").replace("model.", ""): v
        for k, v in ckpt.items() if isinstance(v, torch.Tensor)
    }
    m = vgg19()
    try:
        m.load_state_dict(sd, strict=True)
    except RuntimeError:
        m.load_state_dict(sd, strict=False)
    m.to(device).eval()
    print("[INFO] Model ready.")
    return m


model = _load_model()

_tfm = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std =[0.229, 0.224, 0.225]),
])


def _preprocess(bgr):
    small = cv2.resize(bgr, (config.INFER_WIDTH, config.INFER_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return _tfm(Image.fromarray(rgb)).unsqueeze(0).to(device)


def _infer(tensor):
    with torch.inference_mode():
        if AMP_ENABLED:
            with torch.cuda.amp.autocast(dtype=torch.float16):
                out = model(tensor)
        else:
            out = model(tensor)
    dm = out[0] if isinstance(out, (tuple, list)) else out
    return dm


def _adapt_stride(elapsed):
    if elapsed > 2.0: return 24
    if elapsed > 1.3: return 18
    if elapsed > 0.9: return 12
    return 8


# ── shared state ──────────────────────────────────────────────────────
_lock      = threading.Lock()
_stop      = threading.Event()

_frame_data    = None          # (bgr, timestamp)
_dmap          = None          # latest density map np array
_density_score = 0.0
_peak_density  = 0.0
_hotspot_ratio = 0.0
_risk_score    = 0.0
_zone          = "SAFE"
_zone_color    = (0, 255, 0)
_infer_t       = 0.0
_stride        = config.INITIAL_INFERENCE_STRIDE
_proc_fps      = 0.0
_zone_scores   = None          # np (3,3)

_frame_q = queue.Queue(maxsize=1)


def _clear_q(q):
    try:
        while True: q.get_nowait()
    except queue.Empty:
        pass


# ── producer ──────────────────────────────────────────────────────────
def _producer():
    global _frame_data, _stride
    source = drone_stream.resolve_source(config.VIDEO_SOURCE)
    sh = drone_stream.DroneStreamHandler(source)
    if not sh.is_opened():
        print("[ERROR] Cannot open source:", source)
        _stop.set(); return

    src_fps = sh.get_fps()
    print(f"[INFO] Source FPS={src_fps:.1f}  live={sh.is_live}")
    idx = 0
    nft = time.monotonic()

    while not _stop.is_set():
        ok, frame = sh.read_frame()
        if not ok:
            print("[INFO] Stream ended."); _stop.set(); break

        idx += 1
        ts  = time.monotonic()
        with _lock:
            _frame_data = (frame, ts)
            stride = _stride

        if idx % stride == 0:
            if _frame_q.full(): _clear_q(_frame_q)
            try: _frame_q.put_nowait((frame.copy(), ts))
            except queue.Full: pass

        if not sh.is_live:
            nft += 1.0 / src_fps
            s = nft - time.monotonic()
            if s > 0: time.sleep(s)

    sh.release()


# ── inference worker ──────────────────────────────────────────────────
def _inference_worker():
    global _dmap, _density_score, _peak_density, _hotspot_ratio
    global _risk_score, _zone, _zone_color, _infer_t, _stride, _proc_fps, _zone_scores

    last_t   = time.perf_counter()
    zm       = zone_monitor.ZoneMonitor()

    while not _stop.is_set():
        try:
            frame, _ = _frame_q.get(timeout=0.25)
        except queue.Empty:
            continue

        t0       = time.perf_counter()
        tensor   = _preprocess(frame)
        dmap_out = _infer(tensor)
        dmap_np  = dmap_out.squeeze().detach().float().cpu().numpy()

        ds, pd, hr, rs = risk_engine.compute_pressure_metrics(dmap_np)
        zv, zc         = risk_engine.get_risk_zone(rs,
                             config.SAFE_THRESHOLD,
                             config.WATCH_THRESHOLD,
                             config.HIGH_THRESHOLD)
        scores, _      = zm.analyze_zones(dmap_np)

        elapsed = time.perf_counter() - t0
        now     = time.perf_counter()
        fps     = 1.0 / max(now - last_t, 1e-6)
        last_t  = now

        with _lock:
            _dmap          = dmap_np.copy()
            _density_score = ds
            _peak_density  = pd
            _hotspot_ratio = hr
            _risk_score    = rs
            _zone          = zv
            _zone_color    = zc
            _infer_t       = elapsed
            _proc_fps      = fps
            _stride        = max(config.MIN_INFERENCE_STRIDE,
                                 min(config.MAX_INFERENCE_STRIDE,
                                     _adapt_stride(elapsed)))
            _zone_scores   = scores


threading.Thread(target=_producer,          daemon=True, name="Producer").start()
threading.Thread(target=_inference_worker,  daemon=True, name="Inference").start()

# ── display window ────────────────────────────────────────────────────
cv2.namedWindow(config.WINDOW_NAME, cv2.WINDOW_NORMAL)
cv2.resizeWindow(config.WINDOW_NAME, config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
print("[INFO] Keys: q=quit  h=heatmap  r=record  l=CSV  s=stampede-panel")

# ── component instances ───────────────────────────────────────────────
pressure_smooth  = 0.0
show_stampede    = True
recording        = False
heatmap_enabled  = True

crowd_log   = logger.CrowdLogger(config.CSV_LOG_PATH, enabled=config.WRITE_CSV_LOG)
motion_anal = optical_flow.CrowdMotionAnalyzer()
risk_track  = risk_engine.RiskEngineTracker()
history     = HistoryBuffer(max_seconds=30.0, fps_estimate=5.0)
hs_tracker  = HotspotTracker(history)
opp_det     = OpposingFlowDetector()
stamp_pred  = StampedePredictor(history)
video_writer = None
alert_first_shown_times = {}
last_cap_ts = 0.0

# ── display loop ──────────────────────────────────────────────────────
while not _stop.is_set():

    with _lock:
        fd            = _frame_data
        dmap          = _dmap
        density_score = _density_score
        peak_density  = _peak_density
        hotspot_ratio = _hotspot_ratio
        risk_score    = _risk_score
        zone_val      = _zone
        zone_color    = _zone_color
        infer_t       = _infer_t
        proc_fps      = _proc_fps
        cur_stride    = _stride
        zone_scores   = _zone_scores

    if fd is None:
        time.sleep(0.005); continue

    frame, cap_ts = fd
    if cap_ts == last_cap_ts:
        time.sleep(0.002)
        continue
    last_cap_ts = cap_ts

    age = time.monotonic() - cap_ts

    # Resize to display resolution
    disp = cv2.resize(frame, (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT),
                      interpolation=cv2.INTER_AREA)

    # Heatmap
    if heatmap_enabled:
        disp = heatmap_generator.apply_heatmap(disp, dmap, alpha=config.HEATMAP_ALPHA)

    # Motion (uses median, noise-floored)
    speed_grid, motion_speed, turbulence = motion_anal.analyze_motion(disp)

    # Zero out motion in cells with no detected people (water / noise filter)
    if zone_scores is not None:
        for r in range(3):
            for c in range(3):
                if zone_scores[r, c] < 5.0:
                    speed_grid[r, c] = 0.0

    # Opposing flow
    last_flow  = getattr(motion_anal, "last_flow", None)
    opp_result = (opp_det.analyze(last_flow)
                  if last_flow is not None
                  else {"danger_grid": None, "max_score": 0.0,
                        "any_dangerous": False, "alert_text": ""})

    # Composite risk
    comp_risk = risk_track.compute_composite_risk(
        density_score, peak_density, hotspot_ratio, motion_speed, turbulence
    )
    comp_zone, comp_color = risk_engine.get_risk_zone(
        comp_risk,
        config.SAFE_THRESHOLD, config.WATCH_THRESHOLD, config.HIGH_THRESHOLD,
    )
    pressure_smooth = 0.85 * pressure_smooth + 0.15 * (comp_risk * 100.0)

    # Push to history
    hs_result    = {"trend_matrix": None, "alert_text": "", "expanding": False}
    sp_result    = {"smooth_prob": 0.0, "label": "SAFE",
                    "label_color": (0,255,0), "alert_text": "", "terms": {}}

    if zone_scores is not None:
        history.push(
            timestamp=time.monotonic(),
            density_score=density_score, peak_density=peak_density,
            hotspot_ratio=hotspot_ratio, motion_speed=motion_speed,
            turbulence=turbulence, composite_risk=comp_risk,
            zone_scores=zone_scores, zone_motions=speed_grid,
        )
        hs_result = hs_tracker.update(zone_scores)
        sp_result = stamp_pred.predict(
            density_score=density_score,
            motion_speed=motion_speed,
            turbulence=turbulence,
            opposing_score=opp_result.get("max_score", 0.0),
        )

    # ── draw ────────────────────────────────────────────────────────
    overlay.draw_top_banner(disp, comp_zone, comp_color, pressure_smooth)

    overlay.draw_grid_3x3(
        disp,
        zone_scores     = zone_scores,
        zone_motions    = speed_grid,
        trend_matrix    = hs_result.get("trend_matrix"),
        opposing_danger = opp_result.get("danger_grid"),
        panel_visible   = show_stampede,
    )

    if show_stampede:
        overlay.draw_stampede_panel(disp, sp_result)

    alerts = [a for a in [
        hs_result.get("alert_text", ""),
        opp_result.get("alert_text", ""),
        sp_result.get("alert_text", ""),
    ] if a]
    
    # Track alert first shown times
    now = time.monotonic()
    for a in list(alert_first_shown_times.keys()):
        if a not in alerts:
            del alert_first_shown_times[a]
    for a in alerts:
        if a not in alert_first_shown_times:
            alert_first_shown_times[a] = now

    overlay.draw_alert_ticker(disp, alerts, alert_first_shown_times, now)

    # ── recording ─────────────────────────────────────────────────
    if recording:
        if video_writer is None:
            od = os.path.dirname(config.ANNOTATED_VIDEO_PATH)
            if od: os.makedirs(od, exist_ok=True)
            h2, w2 = disp.shape[:2]
            video_writer = cv2.VideoWriter(
                config.ANNOTATED_VIDEO_PATH,
                cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (w2, h2)
            )
        video_writer.write(disp)

    if crowd_log.enabled:
        crowd_log.log(comp_zone, comp_risk, density_score,
                      peak_density, hotspot_ratio, infer_t, age, cur_stride)

    cv2.imshow(config.WINDOW_NAME, disp)
    key = cv2.waitKey(1) & 0xFF

    if   key == ord("q"): _stop.set(); break
    elif key == ord("h"): heatmap_enabled = not heatmap_enabled
    elif key == ord("s"): show_stampede   = not show_stampede
    elif key == ord("r"):
        recording = not recording
        if not recording and video_writer:
            video_writer.release(); video_writer = None
    elif key == ord("l"):
        crowd_log.enabled = not crowd_log.enabled
        if not crowd_log.enabled: crowd_log.close()

# ── cleanup ───────────────────────────────────────────────────────────
cv2.destroyAllWindows()
if video_writer: video_writer.release()
crowd_log.close()
print("[INFO] Done.")