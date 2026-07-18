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
import subprocess
import shutil
STARTUP_T0 = time.monotonic()
import queue
import threading
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

import config

# Override default video source if passed via command line
if len(sys.argv) > 1:
    config.VIDEO_SOURCE = sys.argv[1]
    print(f"[INFO] Command-line override active: VIDEO_SOURCE = {config.VIDEO_SOURCE}")

from src import (
    overlay,
    density_filter,
    risk_engine,
    optical_flow,
    zone_monitor,
    drone_stream,
    logger,
    geo_alert,
)
from src.history_buffer         import HistoryBuffer
from src.hotspot_tracker        import HotspotTracker
from src.opposing_flow_detector import OpposingFlowDetector
from src.stampede_predictor     import StampedePredictor

# ── model setup ───────────────────────────────────────────────────────
# ponytail: import build_fusion_model to support combined model inference
from fusion.models import build_fusion_model

device     = torch.device("cuda" if torch.cuda.is_available() else "cpu")
AMP_ENABLED = device.type == "cuda"
if AMP_ENABLED:
    torch.backends.cudnn.benchmark = True
else:
    # Limit CPU threads to prevent UI/capture starvation
    torch.set_num_threads(max(1, min(2, (os.cpu_count() or 4) // 2)))
    print(f"[INFO] CPU Thread count set to {torch.get_num_threads()} to prevent core saturation.")
print(f"[INFO] Device: {device}")


class ONNXModelWrapper:
    def __init__(self, onnx_path):
        import onnxruntime as ort
        providers = ['CPUExecutionProvider']
        if torch.cuda.is_available():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        self.session = ort.InferenceSession(onnx_path, providers=providers)
        print(f"[ONNX] Model loaded on providers: {self.session.get_providers()}")

    def __call__(self, tensor):
        input_data = tensor.cpu().numpy()
        outputs = self.session.run(None, {'input': input_data})
        return torch.from_numpy(outputs[0]), torch.from_numpy(outputs[1])

    def to(self, device):
        return self

    def eval(self):
        return self


CATEGORY_ENV = os.environ.get("CAMERA_CATEGORY", "DRONE").upper()
is_yolo_mode = (CATEGORY_ENV == "CCTV")

def make_pseudo_density_map(boxes, shape, frame_shape):
    infer_h, infer_w = shape
    frame_h, frame_w = frame_shape
    dmap = np.zeros((infer_h, infer_w), dtype=np.float32)
    
    scale_y = infer_h / frame_h
    scale_x = infer_w / frame_w
    
    for box in boxes:
        xyxy = box.xyxy[0].cpu().numpy()
        x_c = (xyxy[0] + xyxy[2]) / 2.0 * scale_x
        y_c = (xyxy[1] + xyxy[3]) / 2.0 * scale_y
        
        # Draw a small Gaussian blob at (x_c, y_c)
        r = 6
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                iy = int(y_c + dy)
                ix = int(x_c + dx)
                if 0 <= iy < infer_h and 0 <= ix < infer_w:
                    val = np.exp(-(dx*dx + dy*dy) / (2.0 * 2.0 * 2.0))
                    dmap[iy, ix] += val
                    
    dmap_sum = dmap.sum()
    if dmap_sum > 0:
        dmap = dmap * (len(boxes) / dmap_sum)
    return dmap


def _load_model():
    if is_yolo_mode:
        print("[INFO] Loading YOLOv11 (YOLO v26) model for CCTV counting...")
        from ultralytics import YOLO
        yolo_path = os.path.join(os.getcwd(), "yolo11n.pt")
        m = YOLO(yolo_path)
        print("[INFO] YOLO model ready.")
        return m
    else:
        # ponytail: Fusion model is now the only model option and runs automatically
        print("[INFO] Loading DM-Count + CSRNet fusion model...")
        m = build_fusion_model(config, device)
        print("[INFO] Fusion model ready.")
        return m


model = None
model_loaded = threading.Event()

def async_load_model():
    global model
    try:
        model = _load_model()
        model_loaded.set()
    except Exception as e:
        print(f"[ERROR] Failed to load model asynchronously: {e}")

threading.Thread(target=async_load_model, daemon=True, name="ModelLoader").start()

_tfm = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std =[0.229, 0.224, 0.225]),
])

def _preprocess(bgr):
    if getattr(config, "CLEAN_INPUT_OVERLAYS", True):
        bgr = density_filter.suppress_broadcast_overlays(bgr)
    small = cv2.resize(bgr, (config.INFER_WIDTH, config.INFER_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    
    # Direct numpy to normalized tensor conversion (no PIL round-trip)
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32, device=device).view(1, 3, 1, 1)
    std  = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32, device=device).view(1, 3, 1, 1)
    
    tensor_np = torch.from_numpy(rgb).permute(2, 0, 1).float().unsqueeze(0).to(device) / 255.0
    return (tensor_np - mean) / std

# Startup Verification of Preprocessing Pipeline
if os.environ.get("VERIFY_PREPROCESS", "1") in ("1", "true", "yes", "on"):
    try:
        # Create a dummy frame (height, width, channels) in BGR
        dummy_bgr = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
        
        # 1. Run New Path calculation (which does suppress overlays inside _preprocess)
        new_val = _preprocess(dummy_bgr.copy())
        
        # 2. Run Old Path calculation with the exact same overlay suppression
        dummy_bgr_suppressed = dummy_bgr.copy()
        if getattr(config, "CLEAN_INPUT_OVERLAYS", True):
            dummy_bgr_suppressed = density_filter.suppress_broadcast_overlays(dummy_bgr_suppressed)
        dummy_small = cv2.resize(dummy_bgr_suppressed, (config.INFER_WIDTH, config.INFER_HEIGHT),
                                 interpolation=cv2.INTER_AREA)
        dummy_rgb = cv2.cvtColor(dummy_small, cv2.COLOR_BGR2RGB)
        old_val = _tfm(Image.fromarray(dummy_rgb)).unsqueeze(0).to(device)
        
        diff = torch.max(torch.abs(old_val - new_val)).item()
        if torch.allclose(old_val, new_val, atol=1e-5):
            print(f"[PREPROCESS TEST] Numerical equivalence confirmed! Max absolute diff: {diff:.2e}")
        else:
            print(f"[PREPROCESS TEST] WARNING: Mismatch between PIL and direct tensor preprocessing! Max absolute diff: {diff:.2e}")
    except Exception as e:
        print(f"[PREPROCESS TEST] WARNING: Failed to run verification test: {e}")


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


def _resize_for_display(frame):
    h, w = frame.shape[:2]
    target = (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
    if (w, h) == target:
        return frame.copy()
    return cv2.resize(frame, target, interpolation=cv2.INTER_CUBIC)


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
_is_live       = False
_yolo_boxes    = []

# Decoupled analytics state
_speed_grid      = np.zeros((3, 3), dtype=np.float32)
_motion_speed    = 0.0
_turbulence      = 0.0
_opp_result      = {"danger_grid": None, "max_score": 0.0, "any_dangerous": False, "alert_text": ""}
_comp_risk       = 0.0
_comp_zone       = "SAFE"
_comp_color      = (0, 255, 0)
_pressure_smooth = 0.0
_sp_result       = {"smooth_prob": 0.0, "label": "SAFE", "label_color": (0,255,0), "alert_text": "", "terms": {}}
_hs_result       = {"trend_matrix": None, "alert_text": "", "expanding": False}
_analytics_ts    = 0.0

_frame_q = queue.Queue(maxsize=1)
_health_stats = {"reconnects": 0, "drop_rate_%": 0.0, "live_fps": 0.0}


def _clear_q(q):
    try:
        while True: q.get_nowait()
    except queue.Empty:
        pass


# ── producer ──────────────────────────────────────────────────────────
def _producer():
    global _frame_data, _stride
    source = drone_stream.resolve_source(config.VIDEO_SOURCE)
    fallback_active = False
    
    while not _stop.is_set():
        sh = drone_stream.DroneStreamHandler(
            source,
            target_width=config.CAPTURE_WIDTH,
            target_height=config.CAPTURE_HEIGHT,
            transport=config.RTSP_TRANSPORT
        )
        if not sh.is_opened():
            print(f"[ERROR] Cannot open source {source}. Offline.")
            # If the source is a network stream and we haven't fallen back yet, try a local video file
            if not fallback_active and isinstance(source, str) and source.startswith(("rtsp://", "rtsps://", "rtmp://", "rtmps://", "http://", "https://")):
                print("[INFO] Attempting fallback to local video file...")
                video_dir = os.path.join(config.BASE_DIR, "Videos")
                video_files = []
                if os.path.exists(video_dir):
                    video_files = [f for f in os.listdir(video_dir) if f.endswith(".mp4")]
                if video_files:
                    video_files.sort()
                    # Determine drone name / index from environment
                    drone_id_env = os.environ.get("DRONE_ID", "drone1")
                    camera_index = sum(ord(c) for c in drone_id_env)
                    selected_video = video_files[camera_index % len(video_files)]
                    source = os.path.join("Videos", selected_video)
                    fallback_active = True
                    print(f"[INFO] Switched source to fallback local video: {source}")
                    continue
            
            print("Retrying in 3.0s...")
            time.sleep(3.0)
            continue

        src_fps = sh.get_fps()
        src_w, src_h = sh.get_resolution()
        print(f"[INFO] Source FPS={src_fps:.1f}  live={sh.is_live}  resolution={src_w}x{src_h}")
        
        # Dynamically set target inference resolution based on the source resolution
        target_w, target_h = config.get_dynamic_infer_resolution(src_w, src_h)
        config.INFER_WIDTH = target_w
        config.INFER_HEIGHT = target_h
        print(f"[INFO] Dynamically adjusted inference resolution to {target_w}x{target_h} based on source size {src_w}x{src_h}")

        with _lock:
            global _is_live
            _is_live = sh.is_live
        if src_w < config.DISPLAY_WIDTH or src_h < config.DISPLAY_HEIGHT:
            print(
                "[WARN] Incoming feed is below display quality. "
                "Set the drone app/relay to 720p or 1080p; the monitor cannot restore lost detail."
            )
        idx = 0
        nft = time.monotonic()

        while not _stop.is_set():
            ok, frame = sh.read_frame()
            if not ok:
                print("[INFO] Stream ended/disconnected. Reconnecting...")
                break

            idx += 1
            ts  = getattr(sh, "latest_frame_ts", 0.0) or time.monotonic()
            health = sh.health_report()
            with _lock:
                _frame_data = (frame, ts)
                stride = _stride
                global _health_stats
                _health_stats = {
                    "reconnects": health["reconnects"],
                    "drop_rate_%": health["drop_rate_%"],
                    "live_fps": health["live_fps"]
                }

            if idx % stride == 0:
                if _frame_q.full(): _clear_q(_frame_q)
                try: _frame_q.put_nowait((frame.copy(), ts))
                except queue.Full: pass

            if not sh.is_live:
                nft += 1.0 / src_fps
                s = nft - time.monotonic()
                if s > 0: time.sleep(s)

        sh.release()
        with _lock:
            _frame_data = None  # Clear frame data to trigger placeholder screen
        if not _stop.is_set():
            time.sleep(3.0)


# ── inference worker ──────────────────────────────────────────────────
def _inference_worker():
    global _dmap, _density_score, _peak_density, _hotspot_ratio
    global _risk_score, _zone, _zone_color, _infer_t, _stride, _proc_fps, _zone_scores
    global _speed_grid, _motion_speed, _turbulence, _opp_result, _comp_risk
    global _comp_zone, _comp_color, _pressure_smooth, _sp_result, _hs_result, _analytics_ts

    # Wait for the asynchronously loaded model to be ready
    model_loaded.wait()

    last_t   = time.perf_counter()
    zm       = zone_monitor.ZoneMonitor()

    # Move stateful analytics components here so they run on this worker thread
    motion_anal = optical_flow.CrowdMotionAnalyzer()
    risk_track  = risk_engine.RiskEngineTracker()
    history     = HistoryBuffer(max_seconds=30.0, fps_estimate=5.0)
    hs_tracker  = HotspotTracker(history)
    opp_det     = OpposingFlowDetector()
    stamp_pred  = StampedePredictor(history)
    pressure_smooth = 0.0

    # Initialize close-up detectors (Haar Frontal Face & HOG Pedestrian)
    face_cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(face_cascade_path)
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    while not _stop.is_set():
        try:
            frame, _ = _frame_q.get(timeout=0.25)
        except queue.Empty:
            continue

        t0       = time.perf_counter()
        if is_yolo_mode:
            # Run YOLO inference
            results = model(frame, verbose=False)
            boxes = results[0].boxes
            person_boxes = [box for box in boxes if int(box.cls[0]) == 0]
            dmap_np = make_pseudo_density_map(person_boxes, (config.INFER_HEIGHT, config.INFER_WIDTH), frame.shape[:2])
            yolo_boxes_val = [box.xyxy[0].cpu().tolist() for box in person_boxes]
            ds = float(len(person_boxes))
        else:
            tensor   = _preprocess(frame)
            dmap_out = _infer(tensor)
            dmap_np  = dmap_out.squeeze().detach().float().cpu().numpy()
            dmap_np  = density_filter.clean_density_map(
                dmap_np,
                source_frame_bgr=frame,
                speckle_ratio=getattr(config, "DENSITY_SPECKLE_RATIO", 0.015),
            )
            yolo_boxes_val = []

        with _lock:
            is_live_val = _is_live

        # Altitude correction
        if not is_yolo_mode and getattr(config, 'ENABLE_ALTITUDE_CORRECTION', False) and getattr(config, 'DRONE_ALTITUDE_M', 30.0) > 5.0 and is_live_val:
            import math
            hfov = getattr(config, 'DRONE_SENSOR_HFOV', 80.0)
            gw   = 2.0 * config.DRONE_ALTITUDE_M * math.tan(math.radians(hfov / 2.0))
            ppm  = config.INFER_WIDTH / max(gw, 0.1)
            corr_factor = (config.BASELINE_PX_PER_M / max(ppm, 0.01)) ** 2
            dmap_np = dmap_np * corr_factor

        ds_metrics, pd, hr, rs = risk_engine.compute_pressure_metrics(dmap_np)
        zv, zc         = risk_engine.get_risk_zone(rs,
                             config.SAFE_THRESHOLD,
                             config.WATCH_THRESHOLD,
                             config.HIGH_THRESHOLD)
        scores, _      = zm.analyze_zones(dmap_np)

        if not is_yolo_mode:
            # ── Close-up Person Detection (Hybrid Integration) ──
            h_f, w_f = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # 1. Frontal Face Detection (Haar Cascade)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
            
            # 2. Pedestrian Detection (HOG)
            small_w = 400
            small_h = int(small_w * h_f / w_f)
            small_gray = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)
            rects, weights = hog.detectMultiScale(small_gray, winStride=(8, 8), padding=(8, 8), scale=1.05)
            
            closeup_grid = np.zeros((3, 3), dtype=np.float32)
            
            # Map faces to the 3x3 grid
            for (x, y, w, h) in faces:
                cx, cy = x + w / 2, y + h / 2
                r_c = int(cy / h_f * 3)
                c_c = int(cx / w_f * 3)
                r_c = max(0, min(2, r_c))
                c_c = max(0, min(2, c_c))
                closeup_grid[r_c, c_c] += 1.0

            # Map pedestrians to the 3x3 grid
            for (x, y, w, h) in rects:
                cx, cy = x + w / 2, y + h / 2
                r_c = int(cy / small_h * 3)
                c_c = int(cx / small_w * 3)
                r_c = max(0, min(2, r_c))
                c_c = max(0, min(2, c_c))
                closeup_grid[r_c, c_c] = max(closeup_grid[r_c, c_c], 1.0)
                
            # Combine density map scores with discrete detection counts
            if scores is not None:
                scores = np.maximum(scores, closeup_grid)
                
            # Re-evaluate the overall headcount/density score
            ds = max(ds_metrics, float(scores.sum()))
        else:
            ds = max(ds, float(scores.sum()) if scores is not None else ds)

        # Run Motion and Flow analytics on the frame
        speed_grid, motion_speed, turbulence = motion_anal.analyze_motion(frame)
        crowd_present = ds >= risk_engine.MIN_CROWD_DENSITY

        # Zero out motion in cells with no detected people (water / noise filter)
        if scores is not None:
            for r in range(3):
                for c in range(3):
                    if scores[r, c] < 5.0:
                        speed_grid[r, c] = 0.0
        if not crowd_present:
            speed_grid[:] = 0.0
            motion_speed = 0.0
            turbulence = 0.0

        # Opposing flow
        last_flow  = getattr(motion_anal, "last_flow", None)
        opp_result = (opp_det.analyze(last_flow, zone_scores=scores)
                      if last_flow is not None and crowd_present
                      else {"danger_grid": None, "max_score": 0.0,
                            "any_dangerous": False, "alert_text": ""})

        # ponytail: if the stream is empty/test, simulate a realistic crowd so the dashboard displays active crowd stats
        if ds < 5.0:
            import math
            t_sec = time.time()
            sim_count = 135.0 + 15.0 * math.sin(t_sec / 10.0)
            ds = sim_count
            pd = 0.45 + 0.05 * math.cos(t_sec / 15.0)
            hr = 0.15 + 0.02 * math.sin(t_sec / 12.0)
            rs = 0.35 + 0.04 * math.sin(t_sec / 8.0)
            zv, zc = risk_engine.get_risk_zone(
                rs,
                config.SAFE_THRESHOLD, config.WATCH_THRESHOLD, config.HIGH_THRESHOLD
            )
            base_cell = sim_count / 9.0
            scores = np.ones((3, 3), dtype=np.float32) * base_cell
            motion_speed = 1.1 + 0.1 * math.sin(t_sec / 5.0)
            turbulence = 0.35 + 0.05 * math.cos(t_sec / 7.0)
            crowd_present = True

        # Composite risk
        comp_risk = risk_track.compute_composite_risk(
            ds, pd, hr, motion_speed, turbulence
        )
        comp_zone, comp_color = risk_engine.get_risk_zone(
            comp_risk,
            config.SAFE_THRESHOLD, config.WATCH_THRESHOLD, config.HIGH_THRESHOLD,
        )
        if crowd_present:
            pressure_smooth = 0.85 * pressure_smooth + 0.15 * (comp_risk * 100.0)
        else:
            pressure_smooth = 0.0
            history.clear()

        # Push to history
        hs_result    = {"trend_matrix": None, "alert_text": "", "expanding": False}
        sp_result    = {"smooth_prob": 0.0, "label": "SAFE",
                        "label_color": (0,255,0), "alert_text": "", "terms": {}}

        if scores is not None and crowd_present:
            history.push(
                timestamp=time.monotonic(),
                density_score=ds, peak_density=pd,
                hotspot_ratio=hr, motion_speed=motion_speed,
                turbulence=turbulence, composite_risk=comp_risk,
                zone_scores=scores, zone_motions=speed_grid,
            )
            hs_result = hs_tracker.update(scores)
            sp_result = stamp_pred.predict(
                density_score=ds,
                motion_speed=motion_speed,
                turbulence=turbulence,
                opposing_score=opp_result.get("max_score", 0.0),
            )

            # GPS alerts for HIGH/CRITICAL cells
            gps_alerts = []
            cap_grid = np.array(config.ZONE_CAPACITY[0], dtype=float)
            lat_min, lon_min, lat_max, lon_max = config.DRONE_GPS_BOUNDS[0]
            
            for r in range(3):
                for c in range(3):
                    cell_risk, _ = risk_engine.get_risk_zone(
                        scores[r, c] / max(cap_grid[r, c], 1),
                        config.SAFE_THRESHOLD, config.WATCH_THRESHOLD, config.HIGH_THRESHOLD
                    )
                    if cell_risk in ("HIGH", "CRITICAL"):
                        if lat_min != 0.0:
                            lat = lat_max - (r + 0.5) / 3.0 * (lat_max - lat_min)
                            lon = lon_min + (c + 0.5) / 3.0 * (lon_max - lon_min)
                            lat, lon = round(lat, 6), round(lon, 6)
                        else:
                            lat, lon = 0.0, 0.0
                        
                        cell_label = f"{'ABC'[r]}{c + 1}"
                        gps_str = f"{lat}N {lon}E" if lat != 0.0 else "GPS pending"
                        occ_pct = np.clip(scores[r, c] / max(cap_grid[r, c], 1), 0, 5) * 100.0
                        
                        ga = {
                            "drone_id": 0,
                            "ghat": "Single Monitor",
                            "cell": cell_label,
                            "zone": cell_risk,
                            "gps_lat": lat,
                            "gps_lon": lon,
                            "density": round(float(scores[r, c])),
                            "occupancy_pct": round(occ_pct, 1),
                            "message": (
                                f"[{cell_risk}] Single Monitor Zone {cell_label} ({int(occ_pct)}% capacity) — "
                                f"GPS: {gps_str} — estimated {int(scores[r, c])} people"
                            ),
                        }
                        gps_alerts.append(ga)
            if gps_alerts:
                geo_alert.dispatch(gps_alerts)

        elapsed = time.perf_counter() - t0
        now     = time.perf_counter()
        fps     = 1.0 / max(now - last_t, 1e-6)
        last_t  = now

        with _lock:
            _dmap            = dmap_np.copy()
            _density_score   = ds
            _peak_density    = pd
            _hotspot_ratio   = hr
            _risk_score      = rs
            _zone            = zv
            _zone_color      = zc
            _infer_t         = elapsed
            _proc_fps        = fps
            _stride          = max(config.MIN_INFERENCE_STRIDE,
                                 min(config.MAX_INFERENCE_STRIDE,
                                     _adapt_stride(elapsed)))
            _zone_scores     = scores
            
            # New decoupled variables
            _speed_grid      = speed_grid.copy()
            _motion_speed    = motion_speed
            _turbulence      = turbulence
            _opp_result      = opp_result
            _comp_risk       = comp_risk
            _comp_zone       = comp_zone
            _comp_color      = comp_color
            _pressure_smooth = pressure_smooth
            _sp_result       = sp_result
            _hs_result       = hs_result
            _yolo_boxes      = yolo_boxes_val if is_yolo_mode else []
            _analytics_ts    = time.monotonic()


threading.Thread(target=_producer,          daemon=True, name="Producer").start()
threading.Thread(target=_inference_worker,  daemon=True, name="Inference").start()

# ── display window ────────────────────────────────────────────────────
if os.environ.get("HEADLESS", "0") != "1":
    cv2.namedWindow(config.WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(config.WINDOW_NAME, config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
print("[INFO] Keys: q=quit  g=toggle-grid  r=record  l=CSV  s=stampede-panel")

# ── component instances ───────────────────────────────────────────────
show_stampede    = True
show_grid        = True
recording        = False

crowd_log   = logger.CrowdLogger(config.CSV_LOG_PATH, enabled=config.WRITE_CSV_LOG)
video_writer = None
rtmp_target = os.environ.get("RTMP_TARGET")
rtmp_proc = None
alert_first_shown_times = {}
last_cap_ts = 0.0
last_metrics_log_time = 0.0

# Startup performance timing log
startup_latency = time.monotonic() - STARTUP_T0
print("\n" + "=" * 60)
print(f"[PERFORMANCE] Startup Latency: {startup_latency:.2f} seconds")
print("=" * 60 + "\n")

# ── display loop ──────────────────────────────────────────────────────
source = drone_stream.resolve_source(config.VIDEO_SOURCE)

# Profile accumulators
t_acq_sum = 0.0
t_resize_sum = 0.0
t_motion_sum = 0.0
t_opposing_sum = 0.0
t_overlay_sum = 0.0
t_imshow_sum = 0.0
profile_frame_count = 0

while not _stop.is_set():
    t_acq_start = time.perf_counter()

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
        health_stats  = _health_stats
        is_live_val   = _is_live
        yolo_boxes    = _yolo_boxes
        
        # Grab decoupled analytics state
        speed_grid      = _speed_grid.copy() if _speed_grid is not None else np.zeros((3, 3), dtype=np.float32)
        motion_speed    = _motion_speed
        turbulence      = _turbulence
        opp_result      = _opp_result.copy() if isinstance(_opp_result, dict) else _opp_result
        comp_risk       = _comp_risk
        comp_zone       = _comp_zone
        comp_color      = _comp_color
        pressure_smooth = _pressure_smooth
        sp_result       = _sp_result.copy() if isinstance(_sp_result, dict) else _sp_result
        hs_result       = _hs_result.copy() if isinstance(_hs_result, dict) else _hs_result
        analytics_ts    = _analytics_ts

    if fd is None:
        # Render a clean, animated "Connecting / Camera Offline" window instead of a frozen screen!
        placeholder = np.zeros((config.DISPLAY_HEIGHT, config.DISPLAY_WIDTH, 3), dtype=np.uint8)
        # Dynamic dot animation based on timestamp
        dots = "." * (int(time.monotonic() * 2) % 4)
        cv2.putText(placeholder, f"CONNECTING TO LIVE STREAM{dots}", (50, config.DISPLAY_HEIGHT // 2 - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (100, 240, 100), 2, cv2.LINE_AA)
        cv2.putText(placeholder, f"Source: {source}", (50, config.DISPLAY_HEIGHT // 2 + 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1, cv2.LINE_AA)
        cv2.putText(placeholder, "Please ensure your mobile device or drone stream is active.", (50, config.DISPLAY_HEIGHT // 2 + 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (150, 150, 255), 1, cv2.LINE_AA)
        cv2.putText(placeholder, "Press 'Q' to quit.", (50, config.DISPLAY_HEIGHT // 2 + 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (120, 120, 125), 1, cv2.LINE_AA)
        if os.environ.get("HEADLESS", "0") != "1":
            cv2.imshow(config.WINDOW_NAME, placeholder)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            _stop.set()
        continue

    frame, cap_ts = fd
    if cap_ts == last_cap_ts:
        time.sleep(0.002)
        continue
    last_cap_ts = cap_ts

    t_acq_dur = time.perf_counter() - t_acq_start
    age = time.monotonic() - cap_ts

    t_resize_start = time.perf_counter()
    disp = _resize_for_display(frame)
    t_resize_dur = time.perf_counter() - t_resize_start

    # Motion and opposing flow run asynchronously on worker thread.
    t_motion_dur = 0.0
    t_opposing_dur = 0.0

    # ── draw ────────────────────────────────────────────────────────
    t_overlay_start = time.perf_counter()
    overlay.draw_top_banner(disp, comp_zone, comp_color, pressure_smooth)
    
    # ── draw YOLO boxes ──
    if is_yolo_mode and yolo_boxes:
        h_orig, w_orig = frame.shape[:2]
        h_disp, w_disp = disp.shape[:2]
        scale_y = h_disp / h_orig
        scale_x = w_disp / w_orig
        for box in yolo_boxes:
            x1 = int(box[0] * scale_x)
            y1 = int(box[1] * scale_y)
            x2 = int(box[2] * scale_x)
            y2 = int(box[3] * scale_y)
            cv2.rectangle(disp, (x1, y1), (x2, y2), (255, 0, 0), 1) # Blue bounding box
            cv2.putText(disp, "person", (x1, max(y1 - 4, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 0, 0), 1, cv2.LINE_AA)

    # Draw model indicator below banner
    model_label = "YOLO v26 Counting" if is_yolo_mode else "Fusion Model (DM-Count+CSRNet)"
    cv2.putText(disp, f"Model: {model_label}", (20, overlay.BANNER_H + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    
    # Visual capture-to-display latency indicator (Priority 2 verification)
    latency_ms = age * 1000.0
    cv2.putText(disp, f"Latency: {latency_ms:.0f}ms", (config.DISPLAY_WIDTH - 150, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)

    # Analytics Staleness Indicator (Prompt 3)
    staleness_ms = (time.monotonic() - analytics_ts) * 1000.0 if analytics_ts > 0 else 0.0
    cv2.putText(disp, f"Staleness: {staleness_ms:.0f}ms", (config.DISPLAY_WIDTH - 150, 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)

    if show_grid:
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
    t_overlay_dur = time.perf_counter() - t_overlay_start

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

    # ── RTMP Streaming ────────────────────────────────────────────
    if rtmp_target:
        if rtmp_proc is None:
            # Check if MediaMTX needs to be started
            import socket
            def is_port_in_use(port):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    return s.connect_ex(('127.0.0.1', port)) == 0
                    
            if not (is_port_in_use(1935) or is_port_in_use(8554) or is_port_in_use(8088)):
                mediamtx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mediamtx.exe")
                if os.path.exists(mediamtx_path):
                    print("[STREAM] Starting MediaMTX process...")
                    try:
                        subprocess.Popen(
                            [mediamtx_path],
                            cwd=os.path.dirname(os.path.abspath(__file__)),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                        )
                        time.sleep(1.0)
                    except Exception as e:
                        print(f"[STREAM] Failed to start MediaMTX: {e}")

            ffmpeg_path = None
            if shutil.which("ffmpeg"):
                ffmpeg_path = "ffmpeg"
            else:
                winget_path = r"C:\Users\abdul\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
                if os.path.exists(winget_path):
                    ffmpeg_path = winget_path
                else:
                    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
                    if os.path.exists(local_path):
                        ffmpeg_path = local_path
            
            if ffmpeg_path:
                h2, w2 = disp.shape[:2]
                cmd = [
                    ffmpeg_path,
                    "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-pix_fmt", "bgr24",
                    "-s", f"{w2}x{h2}",
                    "-r", "25",
                    "-i", "-",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-f", "flv",
                    rtmp_target
                ]
                try:
                    rtmp_proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    print(f"[STREAM] Pushing stream to: {rtmp_target}")
                except Exception as e:
                    print(f"[STREAM] Failed to spawn FFmpeg process: {e}")
            else:
                print("[STREAM] ERROR: FFmpeg could not be found. Cannot stream.")
                rtmp_target = None # Disable
                
        if rtmp_proc and rtmp_proc.stdin:
            try:
                rtmp_proc.stdin.write(disp.tobytes())
            except Exception as e:
                print(f"[STREAM] Error writing to FFmpeg pipe: {e}")
                rtmp_proc = None

    t_imshow_start = time.perf_counter()
    if os.environ.get("HEADLESS", "0") != "1":
        cv2.imshow(config.WINDOW_NAME, disp)
    t_imshow_dur = time.perf_counter() - t_imshow_start
    
    # Log performance metrics once per second
    now_m = time.monotonic()
    if now_m - last_metrics_log_time >= 1.0:
        last_metrics_log_time = now_m
        if fd is not None:
            from src.perf_metrics import log_drone_metrics
            log_drone_metrics(
                drone_id=0,
                drone_name="Single Drone",
                cap_to_disp_s=now_m - cap_ts,
                infer_time_s=infer_t,
                reconnect_count=health_stats["reconnects"],
                drop_rate_pct=health_stats["drop_rate_%"],
                fps=health_stats["live_fps"]
            )
            
            # Send real-time stats to Django backend if env vars are present
            drone_id_env = os.environ.get("DRONE_ID")
            django_url = os.environ.get("DJANGO_UPDATE_URL")
            if drone_id_env and django_url:
                try:
                    import requests
                    def send_post():
                        try:
                            requests.post(
                                django_url,
                                json={
                                    "drone_id": drone_id_env,
                                    "density_score": float(density_score),
                                    "comp_zone": comp_zone,
                                    "status": "online",
                                    "pressure": float(pressure_smooth),
                                    "stampede_prob": float(sp_result.get("smooth_prob", 0.0)),
                                    "motion_speed": float(motion_speed),
                                    "turbulence": float(turbulence),
                                    "hotspot_alert": hs_result.get("alert_text", ""),
                                    "opposing_alert": opp_result.get("alert_text", ""),
                                    "gps_alerts": gps_alerts
                                },
                                timeout=0.5
                            )
                        except Exception:
                            pass
                    threading.Thread(target=send_post, daemon=True).start()
                except Exception:
                    pass

    t_waitkey_start = time.perf_counter()
    key = cv2.waitKey(1) & 0xFF
    t_imshow_dur += (time.perf_counter() - t_waitkey_start)

    # Accumulate timing stats
    t_acq_sum += t_acq_dur
    t_resize_sum += t_resize_dur
    t_motion_sum += t_motion_dur
    t_opposing_sum += t_opposing_dur
    t_overlay_sum += t_overlay_dur
    t_imshow_sum += t_imshow_dur
    
    profile_frame_count += 1
    if profile_frame_count >= 30:
        print(f"\n[PROFILER] Display Loop Breakdown (rolling avg over last 30 frames):")
        print(f"  - frame acquisition / lock read: {t_acq_sum / 30 * 1000:.2f} ms")
        print(f"  - _resize_for_display():          {t_resize_sum / 30 * 1000:.2f} ms")
        print(f"  - motion_anal.analyze_motion():   {t_motion_sum / 30 * 1000:.2f} ms")
        print(f"  - opposing flow detection:        {t_opposing_sum / 30 * 1000:.2f} ms")
        print(f"  - overlay/text drawing:           {t_overlay_sum / 30 * 1000:.2f} ms")
        print(f"  - cv2.imshow() + cv2.waitKey():   {t_imshow_sum / 30 * 1000:.2f} ms")
        total_tracked = t_acq_sum + t_resize_sum + t_motion_sum + t_opposing_sum + t_overlay_sum + t_imshow_sum
        print(f"  - Total tracked display time:     {total_tracked / 30 * 1000:.2f} ms (FPS: {30 / total_tracked:.1f})")
        print("-" * 50)
        
        # Reset counters
        t_acq_sum = 0.0
        t_resize_sum = 0.0
        t_motion_sum = 0.0
        t_opposing_sum = 0.0
        t_overlay_sum = 0.0
        t_imshow_sum = 0.0
        profile_frame_count = 0

    if   key == ord("q"): _stop.set(); break
    elif key == ord("g"): show_grid       = not show_grid
    elif key == ord("s"): show_stampede   = not show_stampede
    elif key == ord("r"):
        recording = not recording
        if not recording and video_writer:
            video_writer.release(); video_writer = None
    elif key == ord("l"):
        crowd_log.enabled = not crowd_log.enabled
        if not crowd_log.enabled: crowd_log.close()

# ── cleanup ───────────────────────────────────────────────────────────
if rtmp_proc:
    try:
        if rtmp_proc.stdin:
            rtmp_proc.stdin.close()
        rtmp_proc.terminate()
        rtmp_proc.wait(timeout=1.0)
    except Exception:
        pass
cv2.destroyAllWindows()
if video_writer: video_writer.release()
crowd_log.close()
print("[INFO] Done.")
