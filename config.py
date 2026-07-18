"""
config.py — Pushkaralu Crowd Risk Monitor
==========================================
HOW TO SWITCH BETWEEN VIDEO FILE AND LIVE DRONE:

  Test with local video (default):
      python infer.py

  Live drone — use a shortcut name:
      DRONE=dji_mini3  python infer.py        (Linux/macOS)
      set DRONE=dji_mini3 && python infer.py  (Windows CMD)

  Live drone — paste any RTSP URL:
      CCTV_SOURCE=rtsp://192.168.42.1/live  python infer.py

  Change transport if you have lag:
      RTSP_TRANSPORT=udp CCTV_SOURCE=rtsp://... python infer.py

  List all supported drone presets:
      python drone_stream.py --list

  Test a stream before running:
      python drone_stream.py rtsp://192.168.42.1/live
      python drone_stream.py dji_mini3
"""

import os

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DMCOUNT_DIR = os.path.join(BASE_DIR, "dm_count")

# ─── Video source ─────────────────────────────────────────────────────
# Priority: DRONE env var → CCTV_SOURCE env var → this file path
VIDEO_SOURCE = os.environ.get(
    "CCTV_SOURCE",
    os.path.join(BASE_DIR, "Videos", "Kumbh.mp4")
)

# ─── RTSP transport ───────────────────────────────────────────────────
# "tcp" = reliable, ~200 ms lag  |  "udp" = fast, ~50 ms lag
RTSP_TRANSPORT = os.environ.get("RTSP_TRANSPORT", "tcp")

# ─── Model ────────────────────────────────────────────────────────────
WEIGHTS_PATH = os.path.join(
    BASE_DIR, "dm_count", "pretrained_models", "model_nwpu.pth"
)

# ─── CSRNet + Fusion layer ──────────────────────────────────────────────
# ponytail: Fusion is now the only model and runs automatically
USE_FUSION = True

CSRNET_WEIGHTS_PATH = os.environ.get(
    "CSRNET_WEIGHTS_PATH",
    os.path.join(BASE_DIR, "csrnet", "pretrained_models", "csrnet_shtechA.pth"),
)

# Trained fusion-head checkpoint (see fusion/train_fusion.py). If this file
# doesn't exist, the fusion head runs untrained, which is equivalent to a
# safe fixed 50/50 average of DM-Count and CSRNet.
FUSION_HEAD_WEIGHTS_PATH = os.environ.get(
    "FUSION_HEAD_WEIGHTS_PATH",
    os.path.join(BASE_DIR, "fusion", "pretrained_models", "fusion_head.pth"),
)

# Fallback fixed weights, only used if you explicitly call the model in
# mode="static" instead of the learned gate.
FUSION_DM_WEIGHT  = float(os.environ.get("FUSION_DM_WEIGHT", "0.5"))
FUSION_CSR_WEIGHT = float(os.environ.get("FUSION_CSR_WEIGHT", "0.5"))

# ─── Outputs ──────────────────────────────────────────────────────────
SAVE_ANNOTATED_VIDEO = False
ANNOTATED_VIDEO_PATH = os.path.join(BASE_DIR, "outputs", "annotated_output.mp4")
WRITE_CSV_LOG        = False
CSV_LOG_PATH         = os.path.join(BASE_DIR, "outputs", "crowd_log.csv")

# ─── Display / inference ──────────────────────────────────────────────
DISPLAY_WIDTH  = int(os.environ.get("DISPLAY_WIDTH", "1280"))
DISPLAY_HEIGHT = int(os.environ.get("DISPLAY_HEIGHT", "720"))

# Preferred capture size for webcams/camera backends that accept resolution hints.
# RTSP/RTMP feeds usually keep the resolution chosen by the drone app or relay.
CAPTURE_WIDTH  = int(os.environ.get("CAPTURE_WIDTH", "1280"))
CAPTURE_HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", "720"))

def is_cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False

def get_infer_resolution():
    if not hasattr(get_infer_resolution, "_cached"):
        if is_cuda_available():
            get_infer_resolution._cached = (1024, 576)
        else:
            get_infer_resolution._cached = (512, 288)
    return get_infer_resolution._cached

def get_dynamic_infer_resolution(src_w, src_h):
    is_cuda = is_cuda_available()
    max_w, max_h = (1024, 576) if is_cuda else (768, 432)
    scale = min(1.0, max_w / src_w, max_h / src_h)
    target_w = max(32, (int(src_w * scale) // 32) * 32)
    target_h = max(32, (int(src_h * scale) // 32) * 32)
    return target_w, target_h

def __getattr__(name):
    if name == "INFER_WIDTH":
        return get_infer_resolution()[0]
    elif name == "INFER_HEIGHT":
        return get_infer_resolution()[1]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

# ─── Adaptive stride ──────────────────────────────────────────────────
INITIAL_INFERENCE_STRIDE = 12
MIN_INFERENCE_STRIDE     = 6
MAX_INFERENCE_STRIDE     = 24

# ─── Optical flow stride ──────────────────────────────────────────────
# Stride for running optical flow in display loop (1 = every frame)
OPTICAL_FLOW_STRIDE = int(os.environ.get("OPTICAL_FLOW_STRIDE", "4"))

# ─── Visual ───────────────────────────────────────────────────────────
WINDOW_NAME   = "Pushkaralu Crowd Risk"
OPTICAL_FLOW_GPU = os.environ.get("OPTICAL_FLOW_GPU", "1").lower() in ("1", "true", "yes", "on")

# Remove bright broadcast/watermark colours before counting, then discard tiny
# density-map speckles. This reduces false counts from stream overlays, desks,
# windows, and other static background texture.
CLEAN_INPUT_OVERLAYS = os.environ.get("CLEAN_INPUT_OVERLAYS", "1").lower() in ("1", "true", "yes", "on")
DENSITY_SPECKLE_RATIO = float(os.environ.get("DENSITY_SPECKLE_RATIO", "0.015"))
INFERENCE_BACKEND = os.environ.get("INFERENCE_BACKEND", "torch").lower()
SWARM_BATCH_INFERENCE = os.environ.get("SWARM_BATCH_INFERENCE", "1" if is_cuda_available() else "0").lower() in ("1", "true", "yes", "on")

# ─── Risk thresholds ──────────────────────────────────────────────────
SAFE_THRESHOLD  = 0.25
WATCH_THRESHOLD = 0.50
HIGH_THRESHOLD  = 0.75

# ─── Drone altitude (for reference / future scale correction) ───
# DJI Air 3S specs: main lens HFOV ≈ 80° (wide), medium lens HFOV ≈ 57°
# Set DRONE_SENSOR_HFOV to 80.0 for the main camera (most common).
DRONE_ALTITUDE_M  = 30.0   # assumed altitude above ground (metres)
DRONE_SENSOR_HFOV = 80.0   # horizontal FOV in degrees — DJI Air 3S main lens
DRONE_CORRECT_DISTORTION = False

# ─── Swarm Config ────────────────────────────────────────────────────
SWARM_DRONE_COUNT = int(os.environ.get("SWARM_DRONE_COUNT", "4"))

CCTV_SOURCES = {
    "drone1":  os.environ.get("CCTV_SOURCE_1",  'rtsp://127.0.0.1:8554/live/drone1'),
    "drone2":  os.environ.get("CCTV_SOURCE_2",  'rtsp://127.0.0.1:8554/live/drone2'),
    "drone3":  os.environ.get("CCTV_SOURCE_3",  'rtsp://127.0.0.1:8554/live/drone3'),
    "drone4":  os.environ.get("CCTV_SOURCE_4",  'rtsp://127.0.0.1:8554/live/drone4'),
    "drone5":  os.environ.get("CCTV_SOURCE_5",  'rtsp://127.0.0.1:8554/live/drone5'),
    "drone6":  os.environ.get("CCTV_SOURCE_6",  'rtsp://127.0.0.1:8554/live/drone6'),
    "drone7":  os.environ.get("CCTV_SOURCE_7",  'rtsp://127.0.0.1:8554/live/drone7'),
    "drone8":  os.environ.get("CCTV_SOURCE_8",  'rtsp://127.0.0.1:8554/live/drone8'),
    "drone9":  os.environ.get("CCTV_SOURCE_9",  'rtsp://127.0.0.1:8554/live/drone9'),
    "drone10": os.environ.get("CCTV_SOURCE_10", 'rtsp://127.0.0.1:8554/live/drone10'),
    "drone11": os.environ.get("CCTV_SOURCE_11", 'rtsp://127.0.0.1:8554/live/drone11'),
    "drone12": os.environ.get("CCTV_SOURCE_12", 'rtsp://127.0.0.1:8554/live/drone12'),
    "drone13": os.environ.get("CCTV_SOURCE_13", 'rtsp://127.0.0.1:8554/live/drone13'),
    "drone14": os.environ.get("CCTV_SOURCE_14", 'rtsp://127.0.0.1:8554/live/drone14'),
    "drone15": os.environ.get("CCTV_SOURCE_15", 'rtsp://127.0.0.1:8554/live/drone15'),
    "drone16": os.environ.get("CCTV_SOURCE_16", 'rtsp://127.0.0.1:8554/live/drone16'),
    "drone17": os.environ.get("CCTV_SOURCE_17", 'rtsp://127.0.0.1:8554/live/drone17'),
    "drone18": os.environ.get("CCTV_SOURCE_18", 'rtsp://127.0.0.1:8554/live/drone18'),
    "drone19": os.environ.get("CCTV_SOURCE_19", 'rtsp://127.0.0.1:8554/live/drone19'),
    "drone20": os.environ.get("CCTV_SOURCE_20", 'rtsp://127.0.0.1:8554/live/drone20'),
    "drone21": os.environ.get("CCTV_SOURCE_21", 'rtsp://127.0.0.1:8554/live/drone21'),
    "drone22": os.environ.get("CCTV_SOURCE_22", 'rtsp://127.0.0.1:8554/live/drone22'),
    "drone23": os.environ.get("CCTV_SOURCE_23", 'rtsp://127.0.0.1:8554/live/drone23'),
    "drone24": os.environ.get("CCTV_SOURCE_24", 'rtsp://127.0.0.1:8554/live/drone24'),
    "drone25": os.environ.get("CCTV_SOURCE_25", 'rtsp://127.0.0.1:8554/live/drone25'),
    "drone26": os.environ.get("CCTV_SOURCE_26", 'rtsp://127.0.0.1:8554/live/drone26'),
    "drone27": os.environ.get("CCTV_SOURCE_27", 'rtsp://127.0.0.1:8554/live/drone27'),
    "drone28": os.environ.get("CCTV_SOURCE_28", 'rtsp://127.0.0.1:8554/live/drone28'),
    "drone29": os.environ.get("CCTV_SOURCE_29", 'rtsp://127.0.0.1:8554/live/drone29'),
    "drone30": os.environ.get("CCTV_SOURCE_30", 'rtsp://127.0.0.1:8554/live/drone30'),
    "drone31": os.environ.get("CCTV_SOURCE_31", 'rtsp://127.0.0.1:8554/live/drone31'),
    "drone32": os.environ.get("CCTV_SOURCE_32", 'rtsp://127.0.0.1:8554/live/drone32'),
    "drone33": os.environ.get("CCTV_SOURCE_33", 'rtsp://127.0.0.1:8554/live/drone33'),
    "drone34": os.environ.get("CCTV_SOURCE_34", 'rtsp://127.0.0.1:8554/live/drone34'),
    "drone35": os.environ.get("CCTV_SOURCE_35", 'rtsp://127.0.0.1:8554/live/drone35'),
    "drone36": os.environ.get("CCTV_SOURCE_36", 'rtsp://127.0.0.1:8554/live/drone36'),
    "drone37": os.environ.get("CCTV_SOURCE_37", 'rtsp://127.0.0.1:8554/live/drone37'),
    "drone38": os.environ.get("CCTV_SOURCE_38", 'rtsp://127.0.0.1:8554/live/drone38'),
    "drone39": os.environ.get("CCTV_SOURCE_39", 'rtsp://127.0.0.1:8554/live/drone39'),
    "drone40": os.environ.get("CCTV_SOURCE_40", 'rtsp://127.0.0.1:8554/live/drone40'),
}

# Compatibility helper: make DRONE_SOURCES a list of the 40 URLs
DRONE_SOURCES = [CCTV_SOURCES[f"drone{i}"] for i in range(1, 41)]

DRONE_NAMES = [f"Drone {i}" for i in range(1, 41)]

DRONE_ALTITUDES_M = [30.0] * 40

# GPS bounding boxes: [lat_min, lon_min, lat_max, lon_max]
DRONE_GPS_BOUNDS = [[16.9820, 81.7355, 16.9850, 81.7380] for _ in range(40)]

# Safe headcount per 3x3 cell per drone (tune from field measurements)
ZONE_CAPACITY = [[[300, 400, 300], [350, 500, 350], [300, 400, 300]] for _ in range(40)]

BASELINE_PX_PER_M         = 50.0
ENABLE_ALTITUDE_CORRECTION = False
