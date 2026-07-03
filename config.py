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
    os.path.join(BASE_DIR, "Videos", "mecca.mp4")
)

# ─── RTSP transport ───────────────────────────────────────────────────
# "tcp" = reliable, ~200 ms lag  |  "udp" = fast, ~50 ms lag
RTSP_TRANSPORT = os.environ.get("RTSP_TRANSPORT", "tcp")

# ─── Model ────────────────────────────────────────────────────────────
WEIGHTS_PATH = os.path.join(
    BASE_DIR, "dm_count", "pretrained_models", "model_nwpu.pth"
)

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

import torch
if torch.cuda.is_available():
    INFER_WIDTH  = 1024
    INFER_HEIGHT = 576
else:
    INFER_WIDTH  = 768
    INFER_HEIGHT = 432

# ─── Adaptive stride ──────────────────────────────────────────────────
INITIAL_INFERENCE_STRIDE = 12
MIN_INFERENCE_STRIDE     = 6
MAX_INFERENCE_STRIDE     = 24

# ─── Visual ───────────────────────────────────────────────────────────
HEATMAP_ALPHA = 0.45
HEATMAP_ENABLED_DEFAULT = os.environ.get("HEATMAP_ENABLED", "1").lower() in ("1", "true", "yes", "on")
WINDOW_NAME   = "Pushkaralu Crowd Risk"

# Remove bright broadcast/watermark colours before counting, then discard tiny
# density-map speckles. This reduces false counts from stream overlays, desks,
# windows, and other static background texture.
CLEAN_INPUT_OVERLAYS = os.environ.get("CLEAN_INPUT_OVERLAYS", "1").lower() in ("1", "true", "yes", "on")
DENSITY_SPECKLE_RATIO = float(os.environ.get("DENSITY_SPECKLE_RATIO", "0.015"))

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
SWARM_DRONE_COUNT = 4

DRONE_SOURCES = [
    'rtsp://127.0.0.1:8554/live/drone1',
    'rtsp://127.0.0.1:8554/live/drone2',
    'rtsp://127.0.0.1:8554/live/drone3',
    'rtsp://127.0.0.1:8554/live/drone4',
]

DRONE_NAMES = [
    'Ghat 1', 'Ghat 2', 'Ghat 3', 'Ghat 4'
]

DRONE_ALTITUDES_M = [30.0, 25.0, 30.0, 20.0]

# GPS bounding boxes: [lat_min, lon_min, lat_max, lon_max]
DRONE_GPS_BOUNDS = [
    [16.9820, 81.7355, 16.9850, 81.7380],   # Drone 1 North
    [16.9800, 81.7375, 16.9825, 81.7400],   # Drone 2 Main
    [16.9775, 81.7390, 16.9805, 81.7415],   # Drone 3 South
    [0.0, 0.0, 0.0, 0.0],                   # Drone 4 Dynamic (update at runtime)
]

# Safe headcount per 3x3 cell per drone (tune from field measurements)
ZONE_CAPACITY = [
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 1
    [[400, 600, 400], [450, 700, 450], [400, 600, 400]],   # Drone 2
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 3
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 4
]

BASELINE_PX_PER_M         = 50.0
ENABLE_ALTITUDE_CORRECTION = False
