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
STARTUP_T0 = time.monotonic()
import queue
import threading
import numpy as np
import torch

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
from src.crowd_risk_estimator   import CrowdRiskEstimator
from src.counting_accuracy      import (
    TemporalCountStabilizer,
    allocate_zone_counts,
    detection_zone_counts,
    detections_to_density_map,
)
from src.stream_output          import LatestFrameEncoder, resolve_ffmpeg_path

# ── model setup ───────────────────────────────────────────────────────
from models.model_registry import load_counting_model

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
cpu_hybrid_enabled = os.environ.get("CPU_USE_HYBRID", "0").lower() in (
    "1", "true", "yes", "on"
)


def _load_model():
    load_started = time.monotonic()
    if is_yolo_mode:
        # CCTV counting never calls the density backbone. Skipping it removes
        # hundreds of megabytes of duplicated model state per CCTV worker and
        # materially shortens startup.
        print("[PERFORMANCE] CCTV mode: loading YOLO only (density model skipped).")
        from ultralytics import YOLO
        yolo_path = os.path.join(os.getcwd(), "yolo11n.pt")
        yolo_model = YOLO(yolo_path)
        print(
            f"[INFO] YOLO-only CCTV model ready in "
            f"{time.monotonic() - load_started:.2f}s."
        )
        return {
            "fusion": None,
            "yolo": yolo_model,
            "counter_name": "yolo",
            "counter_label": "YOLO Person Detection",
        }

    requested_model = getattr(config, "DRONE_MODEL", "fusion").lower()
    model_name = requested_model
    csrnet_path = getattr(config, "CSRNET_WEIGHTS_PATH", "")
    fusion_head_path = getattr(config, "FUSION_HEAD_WEIGHTS_PATH", "")
    has_csrnet = bool(csrnet_path and os.path.exists(csrnet_path))
    has_fusion_head = bool(fusion_head_path and os.path.exists(fusion_head_path))
    cpu_fusion_enabled = os.environ.get("CPU_USE_FUSION", "0").lower() in (
        "1", "true", "yes", "on"
    )
    if requested_model == "fusion" and device.type == "cpu" and not cpu_fusion_enabled:
        model_name = "dm_count"
        print(
            "[PERFORMANCE] CPU fast-start enabled: using trained DM-Count "
            "instead of the two-backbone fusion model. Set CPU_USE_FUSION=1 "
            "to force fusion."
        )
    elif requested_model == "fusion" and not has_csrnet:
        model_name = "dm_count"
        print(
            "[ACCURACY] Trained CSRNet checkpoint is missing; "
            "using trained DM-Count only instead of an untrained fusion branch."
        )
    elif requested_model == "fusion" and not has_fusion_head:
        print(
            "[ACCURACY] Using conservative fusion of the trained DM-Count "
            "and CSRNet backbones; no learned fusion-head checkpoint is installed."
        )
    elif requested_model == "csrnet" and not has_csrnet:
        model_name = "dm_count"
        print(
            "[ACCURACY] Trained CSRNet checkpoint is missing; "
            "using trained DM-Count instead of a random CSRNet backend."
        )

    print(f"[INFO] Loading production model: {model_name}...")
    fm = load_counting_model(model_name, device)
    density_ready = time.monotonic()
    print(
        f"[INFO] Model {model_name} ready in "
        f"{density_ready - load_started:.2f}s."
    )

    ym = None
    if device.type == "cuda" or cpu_hybrid_enabled:
        print("[INFO] Loading YOLOv11 model for hybrid detection...")
        from ultralytics import YOLO
        yolo_path = os.path.join(os.getcwd(), "yolo11n.pt")
        ym = YOLO(yolo_path)
        models_ready = time.monotonic()
        print(
            f"[INFO] YOLO model ready in {models_ready - density_ready:.2f}s; "
            f"all models ready in {models_ready - load_started:.2f}s."
        )
    else:
        print(
            "[PERFORMANCE] CPU drone mode: using the density counter only. "
            "Set CPU_USE_HYBRID=1 to add YOLO."
        )
    counter_label = (
        "DM-Count (NWPU)"
        if model_name == "dm_count"
        else "Fusion (DM-Count+CSRNet)"
        if model_name == "fusion"
        else "CSRNet"
    )
    return {
        "fusion": fm,
        "yolo": ym,
        "counter_name": model_name,
        "counter_label": counter_label,
    }


def _detect_people(frame):
    """Detect people and return ``(box, confidence)`` pairs."""
    yolo = model["yolo"]
    yolo_imgsz = (
        getattr(config, "YOLO_IMG_SIZE", 960)
        if getattr(config, "YOLO_HIGH_ACCURACY", True)
        else 640
    )
    results = yolo(
        frame,
        classes=[0],
        imgsz=yolo_imgsz,
        conf=getattr(config, "YOLO_CONFIDENCE", 0.20),
        iou=getattr(config, "YOLO_IOU", 0.55),
        max_det=getattr(config, "YOLO_MAX_DETECTIONS", 3000),
        agnostic_nms=True,
        verbose=False,
    )

    if not results or results[0].boxes is None:
        return []

    return [
        (box.xyxy[0].detach().cpu().tolist(), float(box.conf[0]))
        for box in results[0].boxes
        if int(box.cls[0]) == 0
    ]


model = None
model_load_error = None
model_loaded = threading.Event()

def async_load_model():
    global model, model_load_error
    try:
        model = _load_model()
    except Exception as e:
        model_load_error = e
        print(f"[ERROR] Failed to load model asynchronously: {e}")
    finally:
        model_loaded.set()

threading.Thread(target=async_load_model, daemon=True, name="ModelLoader").start()

# Allocate normalization tensors once. Creating and transferring two tensors
# for every inference pass adds avoidable allocator and device overhead.
_NORMALIZE_MEAN = torch.tensor(
    [0.485, 0.456, 0.406], dtype=torch.float32, device=device
).view(1, 3, 1, 1)
_NORMALIZE_STD = torch.tensor(
    [0.229, 0.224, 0.225], dtype=torch.float32, device=device
).view(1, 3, 1, 1)

def _preprocess(bgr):
    small = cv2.resize(bgr, (config.INFER_WIDTH, config.INFER_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    if getattr(config, "CLEAN_INPUT_OVERLAYS", True):
        small = density_filter.suppress_broadcast_overlays(small)
    rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    
    # Direct numpy to normalized tensor conversion (no PIL round-trip)
    tensor_np = torch.from_numpy(rgb).permute(2, 0, 1).float().unsqueeze(0).to(device) / 255.0
    return (tensor_np - _NORMALIZE_MEAN) / _NORMALIZE_STD

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
        old_array = (
            torch.from_numpy(dummy_rgb)
            .permute(2, 0, 1)
            .float()
            .unsqueeze(0)
            .to(device)
            / 255.0
        )
        old_val = (old_array - _NORMALIZE_MEAN) / _NORMALIZE_STD
        
        diff = torch.max(torch.abs(old_val - new_val)).item()
        if torch.allclose(old_val, new_val, atol=1e-5):
            print(f"[PREPROCESS TEST] Numerical equivalence confirmed! Max absolute diff: {diff:.2e}")
        else:
            print(f"[PREPROCESS TEST] WARNING: Mismatch between PIL and direct tensor preprocessing! Max absolute diff: {diff:.2e}")
    except Exception as e:
        print(f"[PREPROCESS TEST] WARNING: Failed to run verification test: {e}")


def _infer(tensor):
    with torch.inference_mode():
        fm = model["fusion"]
        if AMP_ENABLED:
            with torch.cuda.amp.autocast(dtype=torch.float16):
                out = fm(tensor)
        else:
            out = fm(tensor)
    dm = out[0] if isinstance(out, (tuple, list)) else out
    return dm


def _infer_density_map(frame):
    """Run DM/fusion inference with horizontal-flip test-time augmentation."""
    tensor = _preprocess(frame)
    if getattr(config, "COUNT_FLIP_TTA", True):
        batch = torch.cat((tensor, torch.flip(tensor, dims=[3])), dim=0)
        dmaps = _infer(batch).detach().float()
        original = dmaps[0]
        mirrored = torch.flip(dmaps[1], dims=[2])
        dmap = 0.5 * (original + mirrored)
    else:
        dmap = _infer(tensor).squeeze(0).detach().float()
    return dmap.squeeze().cpu().numpy()


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
    interpolation = (
        cv2.INTER_AREA
        if target[0] < w or target[1] < h
        else cv2.INTER_LINEAR
    )
    return cv2.resize(frame, target, interpolation=interpolation)


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
_is_simulation = False
_counting_mode_active = os.environ.get("COUNTING_MODE_ACTIVE", "1").lower() in (
    "1", "true", "yes", "on"
)

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
_analytics_seq   = 0

_frame_q = queue.Queue(maxsize=1)
_stats_q = queue.Queue(maxsize=1)
_health_stats = {"reconnects": 0, "drop_rate_%": 0.0, "live_fps": 0.0}


def _mode_sync_worker():
    """Keep the video worker's mode in sync independently of stats posts."""
    global _counting_mode_active

    mode_url = os.environ.get("MODE_STATUS_URL", "").strip()
    if not mode_url:
        return

    try:
        import requests
    except ImportError:
        print("[MODE SYNC] WARNING: 'requests' library not installed — mode sync disabled")
        return

    while not _stop.is_set():
        try:
            response = requests.get(mode_url, timeout=2.0)
            response.raise_for_status()
            active = bool(response.json().get("counting_mode", True))
            with _lock:
                _counting_mode_active = active
        except Exception:
            pass
        _stop.wait(0.5)


def _replace_latest(target_queue, item):
    """Put one value without blocking, discarding an older pending value."""
    while not _stop.is_set():
        try:
            target_queue.put_nowait(item)
            return
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                pass


def _stats_poster_worker():
    """Send only the newest analytics payload from one bounded worker."""
    global _counting_mode_active

    stats_url = os.environ.get("DJANGO_UPDATE_URL", "").strip()
    if not stats_url:
        return
    try:
        import requests
    except ImportError:
        print("[STATS] WARNING: 'requests' is unavailable; stats posting disabled")
        return

    session = requests.Session()
    last_error_log = 0.0
    while not _stop.is_set():
        try:
            payload = _stats_q.get(timeout=0.25)
        except queue.Empty:
            continue

        try:
            response = session.post(stats_url, json=payload, timeout=0.5)
            response.raise_for_status()
            response_data = response.json()
            if "counting_mode" in response_data:
                with _lock:
                    _counting_mode_active = bool(response_data["counting_mode"])
        except Exception as exc:
            now = time.monotonic()
            if now - last_error_log >= 10.0:
                print(f"[STATS] POST failed: {exc}")
                last_error_log = now


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
            drone_id_env = os.environ.get("DRONE_ID", "drone1")
            if drone_id_env not in ("drone-1", "drone1") and config.ALLOW_DEMO_FALLBACK and not fallback_active and isinstance(source, str) and source.startswith(("rtsp://", "rtsps://", "rtmp://", "rtmps://", "http://", "https://")):
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
                    with _lock:
                        global _is_simulation
                        _is_simulation = True
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
        last_health_check = 0.0
        health = sh.health_report()

        while not _stop.is_set():
            ok, frame = sh.read_frame()
            if not ok:
                print("[INFO] Stream ended/disconnected. Reconnecting...")
                break

            idx += 1
            ts  = getattr(sh, "latest_frame_ts", 0.0) or time.monotonic()
            now = time.monotonic()
            if now - last_health_check >= 1.0:
                health = sh.health_report()
                last_health_check = now
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
                # The capture frame is immutable after publication. Retaining
                # its reference avoids a second multi-megabyte copy; the queue
                # remains bounded to one latest inference candidate.
                _replace_latest(_frame_q, (frame, ts))

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
    global _analytics_seq
    global _yolo_boxes

    # Wait for the asynchronously loaded model to be ready
    model_loaded.wait()
    if model is None:
        print(f"[ERROR] Inference disabled because model loading failed: {model_load_error}")
        return

    last_t   = time.perf_counter()
    zm       = zone_monitor.ZoneMonitor()

    # Move stateful analytics components here so they run on this worker thread
    motion_anal = optical_flow.CrowdMotionAnalyzer()
    risk_track  = risk_engine.RiskEngineTracker()
    history     = HistoryBuffer(max_seconds=30.0, fps_estimate=5.0)
    hs_tracker  = HotspotTracker(history)
    opp_det     = OpposingFlowDetector()
    risk_est    = CrowdRiskEstimator(history)
    pressure_smooth = 0.0
    count_stabilizer = TemporalCountStabilizer(
        window_size=getattr(config, "COUNT_TEMPORAL_WINDOW", 3),
        ema_alpha=getattr(config, "COUNT_EMA_ALPHA", 0.65),
        calibration_scale=getattr(config, "COUNT_CALIBRATION_SCALE", 1.0),
        calibration_bias=getattr(config, "COUNT_CALIBRATION_BIAS", 0.0),
    )
    first_result_pending = True

    while not _stop.is_set():
        try:
            frame, _ = _frame_q.get(timeout=0.25)
        except queue.Empty:
            continue

        # Viewing Mode is deliberately video-only. Do not run YOLO, the
        # DM-Count/CSRNet fusion model, motion analytics, or risk analytics.
        with _lock:
            counting_mode_active = _counting_mode_active
        if not counting_mode_active:
            count_stabilizer.reset()
            continue

        t0       = time.perf_counter()
        if is_yolo_mode:
            # CCTV feeds use YOLO person detection as their count source.
            tracked_people = _detect_people(frame)
            yolo_boxes_val = [
                box for box, confidence in tracked_people
                if confidence >= getattr(config, "YOLO_DOT_CONFIDENCE", 0.10)
            ]
            count_boxes_val = [
                box for box, confidence in tracked_people
                if confidence >= getattr(config, "YOLO_COUNT_CONFIDENCE", 0.30)
            ]
            dmap_np = detections_to_density_map(
                count_boxes_val,
                (config.INFER_HEIGHT, config.INFER_WIDTH),
                frame.shape[:2],
            )
        else:
            # 1) Fusion density map (kept for risk visualization)
            dmap_np = _infer_density_map(frame)
            dmap_np  = density_filter.clean_density_map(
                dmap_np,
                # The input image was already cleaned before inference. Do not
                # delete predicted density afterwards: its integral is the count.
                source_frame_bgr=None,
                speckle_ratio=getattr(config, "DENSITY_SPECKLE_RATIO", 0.015),
            )

            # 2) YOLOv11 for human detections (used for counting + per-zone headcounts)
            tracked_people = (
                _detect_people(frame) if model["yolo"] is not None else []
            )
            yolo_boxes_val = [
                box for box, confidence in tracked_people
                if confidence >= getattr(config, "YOLO_DOT_CONFIDENCE", 0.10)
            ]
            count_boxes_val = [
                box for box, confidence in tracked_people
                if confidence >= getattr(config, "YOLO_COUNT_CONFIDENCE", 0.30)
            ]

        # Only high-confidence detections influence the numeric count. The
        # lower threshold is visual-only so tiny aerial people still get dots.
        people_count = float(len(count_boxes_val))


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
        zv, zc         = risk_engine.get_risk_zone(
            rs,
            config.SAFE_THRESHOLD,
            config.WATCH_THRESHOLD,
            config.HIGH_THRESHOLD,
        )
        scores, _      = zm.analyze_zones(dmap_np)

        # ── Per-zone headcount from YOLO dots (always) ──
        zone_headcounts = detection_zone_counts(
            count_boxes_val,
            frame.shape[:2],
        )

        # The trained density model counts occluded people; YOLO supplies a
        # hard detected-person floor. Stabilization removes one-frame spikes.
        if not is_yolo_mode:
            ds = count_stabilizer.update(float(ds_metrics), people_count)
            scores = allocate_zone_counts(scores, zone_headcounts, ds)
        else:
            ds = count_stabilizer.update(people_count, people_count)
            scores = allocate_zone_counts(zone_headcounts, zone_headcounts, ds)


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
        sp_result    = {"risk_index": 0.0, "risk_level": "SAFE", "confidence": 1.0,
                        "primary_causes": [], "label": "SAFE", "label_color": (0, 255, 0),
                        "alert_text": "", "smooth_prob": 0.0, "terms": {}}

        if scores is not None and crowd_present:
            history.push(
                timestamp=time.monotonic(),
                density_score=ds, peak_density=pd,
                hotspot_ratio=hr, motion_speed=motion_speed,
                turbulence=turbulence, composite_risk=comp_risk,
                zone_scores=scores, zone_motions=speed_grid,
            )
            hs_result = hs_tracker.update(scores)
            sp_result = risk_est.estimate(
                density_score=ds,
                motion_speed=motion_speed,
                turbulence=turbulence,
                opposing_score=opp_result.get("max_score", 0.0),
            )

            # GPS alerts for HIGH/CRITICAL cells
            geo_alerts = []
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
                        geo_alerts.append(ga)
            if geo_alerts:
                geo_alert.dispatch(geo_alerts)


        elapsed = time.perf_counter() - t0
        if first_result_pending:
            print(
                f"[PERFORMANCE] First real count ready in "
                f"{time.monotonic() - STARTUP_T0:.2f}s "
                f"(first inference {elapsed:.2f}s, "
                f"input {config.INFER_WIDTH}x{config.INFER_HEIGHT}, "
                f"counter {model.get('counter_name', 'unknown')})."
            )
            first_result_pending = False
        now     = time.perf_counter()
        fps     = 1.0 / max(now - last_t, 1e-6)
        last_t  = now

        with _lock:
            # dmap_np is replaced, never mutated, on the next worker pass.
            # Publishing the array directly avoids one full density-map copy.
            _dmap            = dmap_np
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
            _yolo_boxes      = yolo_boxes_val
            _analytics_ts    = time.monotonic()
            _analytics_seq  += 1


threading.Thread(target=_producer,          daemon=True, name="Producer").start()
threading.Thread(target=_inference_worker,  daemon=True, name="Inference").start()
threading.Thread(target=_mode_sync_worker,   daemon=True, name="ModeSync").start()
threading.Thread(target=_stats_poster_worker, daemon=True, name="StatsPoster").start()

# ── display window ────────────────────────────────────────────────────
HEADLESS = os.environ.get("HEADLESS", "0") == "1"
RENDER_VIDEO_OVERLAYS = os.environ.get(
    "RENDER_VIDEO_OVERLAYS", "0" if HEADLESS else "1"
).lower() in ("1", "true", "yes", "on")
PROFILE_PIPELINE = os.environ.get("PROFILE_PIPELINE", "0").lower() in (
    "1", "true", "yes", "on"
)
PERF_METRICS_ENABLED = os.environ.get("PERF_METRICS_ENABLED", "0").lower() in (
    "1", "true", "yes", "on"
)

if not HEADLESS:
    cv2.namedWindow(config.WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(config.WINDOW_NAME, config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
print("[INFO] Keys: q=quit  g=toggle-grid  h=toggle-heatmap  r=record  l=CSV  s=stampede-panel")

# ── component instances ───────────────────────────────────────────────
show_stampede    = True
show_grid        = True
show_heatmap     = False
recording        = False

crowd_log   = logger.CrowdLogger(config.CSV_LOG_PATH, enabled=config.WRITE_CSV_LOG)
video_writer = None
rtmp_target = os.environ.get("RTMP_TARGET")
stream_encoder = None
stream_retry_after = 0.0
alert_first_shown_times = {}
last_cap_ts = 0.0
last_metrics_log_time = 0.0
last_posted_analytics_seq = -1

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
t_stream_sum = 0.0
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
        is_simulation_val = _is_simulation
        yolo_boxes    = _yolo_boxes
        counting_mode_active = _counting_mode_active
        
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
        analytics_seq   = _analytics_seq

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
        if not HEADLESS:
            cv2.imshow(config.WINDOW_NAME, placeholder)
            key = cv2.waitKey(20) & 0xFF
        else:
            _stop.wait(0.02)
            key = 255
        if key in (ord('q'), ord('Q'), 27):
            _stop.set()
        continue

    frame, cap_ts = fd

    # Publish a newly completed inference immediately. Previously this lived
    # inside the one-second telemetry timer, adding up to a full second of
    # avoidable dashboard latency.
    if (
        counting_mode_active
        and analytics_seq > 0
        and analytics_seq != last_posted_analytics_seq
    ):
        last_posted_analytics_seq = analytics_seq
        drone_id_env = os.environ.get("DRONE_ID")
        if drone_id_env:
            _replace_latest(_stats_q, {
                "drone_id": drone_id_env,
                "density_score": float(density_score),
                "comp_zone": comp_zone,
                "status": "online",
                "pressure": float(pressure_smooth),
                "stampede_prob": float(sp_result.get("smooth_prob", 0.0)),
                "risk_index": float(sp_result.get("risk_index", 0.0)),
                "risk_level": sp_result.get("risk_level", "SAFE"),
                "confidence": float(sp_result.get("confidence", 1.0)),
                "primary_causes": sp_result.get("primary_causes", []),
                "motion_speed": float(motion_speed),
                "turbulence": float(turbulence),
                "hotspot_alert": hs_result.get("alert_text", ""),
                "opposing_alert": opp_result.get("alert_text", ""),
                "gps_alerts": [],
                "zone_scores": (
                    zone_scores.tolist() if zone_scores is not None else None
                ),
                "analytics_active": True,
                "analytics_seq": int(analytics_seq),
            })

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
    if counting_mode_active and RENDER_VIDEO_OVERLAYS:
        overlay.draw_top_banner(disp, comp_zone, comp_color, pressure_smooth)
        
        # ── draw YOLO dots (mapping humans) ──
        if yolo_boxes:
            h_orig, w_orig = frame.shape[:2]
            h_disp, w_disp = disp.shape[:2]
            scale_y = h_disp / h_orig
            scale_x = w_disp / w_orig
            for box in yolo_boxes:
                x1 = int(box[0] * scale_x)
                y1 = int(box[1] * scale_y)
                x2 = int(box[2] * scale_x)
                y2 = int(box[3] * scale_y)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                # Draw a solid green dot with a black border to map the human
                cv2.circle(disp, (cx, cy), 6, (0, 255, 0), -1)
                cv2.circle(disp, (cx, cy), 7, (0, 0, 0), 1)

        # Draw model indicator below banner
        counter_label = model.get("counter_label", "Density Counter") if isinstance(model, dict) else "Density Counter"
        if is_yolo_mode:
            model_label = "YOLO Person Detection"
        elif model.get("yolo") is None:
            model_label = counter_label
        else:
            model_label = f"YOLO Detection + {counter_label}"
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

    # Simulation Watermark overlay if active (always draw for simulation feeds for security compliance)
    if is_simulation_val:
        # Draw a semi-transparent warning watermark banner at the bottom
        cv2.rectangle(disp, (0, config.DISPLAY_HEIGHT - 65), (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT - 35), (0, 0, 180), -1)
        cv2.putText(disp, "SIMULATION - NOT A LIVE CAMERA", (config.DISPLAY_WIDTH // 2 - 250, config.DISPLAY_HEIGHT - 43),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    if counting_mode_active and RENDER_VIDEO_OVERLAYS:
        if show_grid:
            overlay.draw_grid_3x3(
                disp,
                zone_scores     = zone_scores,
                zone_motions    = speed_grid,
                trend_matrix    = hs_result.get("trend_matrix"),
                opposing_danger = opp_result.get("danger_grid"),
                panel_visible   = show_stampede,
                density_map     = dmap,
                show_heatmap    = show_heatmap,
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
                cv2.VideoWriter_fourcc(*"mp4v"),
                config.OUTPUT_STREAM_FPS,
                (w2, h2),
            )
        video_writer.write(disp)

    if crowd_log.enabled:
        crowd_log.log(comp_zone, comp_risk, density_score,
                      peak_density, hotspot_ratio, infer_t, age, cur_stride)

    # ── RTMP Streaming ────────────────────────────────────────────
    # FFmpeg runs on its own fixed-cadence thread. submit() only swaps
    # the latest frame reference and therefore cannot stall this loop.
    t_stream_start = time.perf_counter()
    if rtmp_target:
        now = time.monotonic()
        if stream_encoder is not None and not stream_encoder.is_running:
            print(f"[STREAM] Encoder stopped: {stream_encoder.last_error or 'unknown error'}")
            stream_encoder.stop()
            stream_encoder = None
            stream_retry_after = now + 2.0

        if stream_encoder is None and now >= stream_retry_after:
            ffmpeg_path = resolve_ffmpeg_path(config.BASE_DIR)
            if ffmpeg_path is None:
                print("[STREAM] ERROR: FFmpeg was not found; browser output is disabled.")
                rtmp_target = None
            else:
                try:
                    stream_encoder = LatestFrameEncoder(
                        ffmpeg_path,
                        rtmp_target,
                        fps=config.OUTPUT_STREAM_FPS,
                    )
                    stream_encoder.start(disp.shape)
                    print(
                        f"[STREAM] Pushing {config.OUTPUT_STREAM_FPS:g} FPS "
                        f"latest-frame output to: {rtmp_target}"
                    )
                except Exception as exc:
                    print(f"[STREAM] Failed to start encoder: {exc}")
                    stream_encoder = None
                    stream_retry_after = now + 2.0

        if stream_encoder is not None and stream_encoder.is_running:
            try:
                stream_encoder.submit(disp)
            except (RuntimeError, ValueError) as exc:
                print(f"[STREAM] Frame submit failed: {exc}")
    t_stream_dur = time.perf_counter() - t_stream_start

    t_imshow_start = time.perf_counter()
    if not HEADLESS:
        cv2.imshow(config.WINDOW_NAME, disp)
        key = cv2.waitKey(1) & 0xFF
    else:
        key = 255
    t_imshow_dur = time.perf_counter() - t_imshow_start
    
    # Log performance metrics once per second
    now_m = time.monotonic()
    if PERF_METRICS_ENABLED and now_m - last_metrics_log_time >= 1.0:
        last_metrics_log_time = now_m
        if fd is not None:
            try:
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
            except Exception as e:
                print(f"[WARN] perf_metrics log failed: {e}")

    # Accumulate timing stats
    t_acq_sum += t_acq_dur if PROFILE_PIPELINE else 0.0
    t_resize_sum += t_resize_dur if PROFILE_PIPELINE else 0.0
    t_motion_sum += t_motion_dur if PROFILE_PIPELINE else 0.0
    t_opposing_sum += t_opposing_dur if PROFILE_PIPELINE else 0.0
    t_overlay_sum += t_overlay_dur if PROFILE_PIPELINE else 0.0
    t_stream_sum += t_stream_dur if PROFILE_PIPELINE else 0.0
    t_imshow_sum += t_imshow_dur if PROFILE_PIPELINE else 0.0
    
    profile_frame_count += 1 if PROFILE_PIPELINE else 0
    if profile_frame_count >= 30:
        print(f"\n[PROFILER] Display Loop Breakdown (rolling avg over last 30 frames):")
        print(f"  - frame acquisition / lock read: {t_acq_sum / 30 * 1000:.2f} ms")
        print(f"  - _resize_for_display():          {t_resize_sum / 30 * 1000:.2f} ms")
        print(f"  - motion_anal.analyze_motion():   {t_motion_sum / 30 * 1000:.2f} ms")
        print(f"  - opposing flow detection:        {t_opposing_sum / 30 * 1000:.2f} ms")
        print(f"  - overlay/text drawing:           {t_overlay_sum / 30 * 1000:.2f} ms")
        print(f"  - non-blocking stream submit:     {t_stream_sum / 30 * 1000:.2f} ms")
        print(f"  - cv2.imshow() + cv2.waitKey():   {t_imshow_sum / 30 * 1000:.2f} ms")
        total_tracked = (
            t_acq_sum + t_resize_sum + t_motion_sum + t_opposing_sum +
            t_overlay_sum + t_stream_sum + t_imshow_sum
        )
        print(f"  - Total tracked display time:     {total_tracked / 30 * 1000:.2f} ms (FPS: {30 / total_tracked:.1f})")
        print("-" * 50)
        
        # Reset counters
        t_acq_sum = 0.0
        t_resize_sum = 0.0
        t_motion_sum = 0.0
        t_opposing_sum = 0.0
        t_overlay_sum = 0.0
        t_stream_sum = 0.0
        t_imshow_sum = 0.0
        profile_frame_count = 0

    if   key == ord("q"): _stop.set(); break
    elif key == ord("g"): show_grid       = not show_grid
    elif key == ord("h"): show_heatmap    = not show_heatmap
    elif key == ord("s"): show_stampede   = not show_stampede
    elif key == ord("r"):
        recording = not recording
        if not recording and video_writer:
            video_writer.release(); video_writer = None
    elif key == ord("l"):
        crowd_log.enabled = not crowd_log.enabled
        if not crowd_log.enabled: crowd_log.close()

# ── cleanup ───────────────────────────────────────────────────────────
if stream_encoder is not None:
    stream_encoder.stop()
cv2.destroyAllWindows()
if video_writer: video_writer.release()
crowd_log.close()
print("[INFO] Done.")
