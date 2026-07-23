import os
import sys
import json
import socket
import time
import subprocess
import importlib.util
import ipaddress
from functools import lru_cache
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import config

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("[LITE SERVER] Shutting down. Terminating managed child processes...")
    for pid, p in list(running_processes.items()):
        try:
            print(f"[LITE SERVER] Terminating process for {pid}...")
            p.terminate()
            p.wait(timeout=1.0)
        except Exception as e:
            print(f"[LITE SERVER] Failed to terminate {pid}: {e}")
        finally:
            _close_process_log(p)
    running_processes.clear()

app = FastAPI(title="AP Police Drone Monitoring Portal (LITE)", lifespan=lifespan)

# Mount static files directly from static
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory cameras database and processes
cameras_db = []
_cameras_by_id = {}
_cameras_by_stream_path = {}
running_processes = {}

# Global counting mode state (True = counting/analytics, False = viewing)
global_counting_mode = True
ANALYTICS_STALE_AFTER_SECONDS = float(
    os.getenv("ANALYTICS_STALE_AFTER_SECONDS", "20")
)


def _rebuild_camera_indexes():
    """Build O(1) indexes for hot stats and MediaMTX callback routes."""
    global _cameras_by_id, _cameras_by_stream_path
    _cameras_by_id = {camera["id"].lower(): camera for camera in cameras_db}
    _cameras_by_stream_path = {}
    for camera in cameras_db:
        source_path = (camera.get("source_stream_path") or "").strip("/")
        output_path = (camera.get("stream_path") or "").strip("/")
        if source_path:
            _cameras_by_stream_path[source_path] = (camera, "source")
        if output_path:
            _cameras_by_stream_path[output_path] = (camera, "output")

def load_cameras():
    global cameras_db
    cameras_db = []
    video_files = ["Kumbh.mp4", "mecca.mp4", "stadium.mp4", "concert.mp4", "Crowd.mp4"]
    
    # Load 40 Drone Feeds (using Fusion model)
    for i in range(1, 41):
        v_file = video_files[(i - 1) % len(video_files)]
        fallback_path = f"Videos/{v_file}" if os.path.exists(os.path.join(os.getcwd(), "Videos", v_file)) else "Videos/Crowd.mp4"
        cameras_db.append({
            "id": f"drone-{i}",
            "name": f"DRONE {i}",
            "location": "Pushkaralu" if i % 2 == 0 else "Rjy",
            "category": "DRONE",
            # Raw publishers use live/*; analyzed video is republished to
            # analyzed/* so a worker never reads from its own output.
            "source_stream_path": f"live/drone{i}",
            "stream_path": f"analyzed/drone{i}",
            # Sample footage is available only through the explicit Test Sample
            # action. The normal Connect Feed path always waits for real video.
            "fallback_video": fallback_path,
            "source_kind": "live_publish",
            "enabled": True,
            "status": "offline",
            "error_type": "stream not found",
            "connection_message": "Video feed disconnected",
            "source_online": False,
            "output_online": False,
            "playback_stream_path": f"live/drone{i}",
            "analytics_status": "idle",
            "people_count": 0,
            "comp_zone": "SAFE",
            "pressure": 0.0,
            "stampede_prob": 0.0,
            "risk_index": 0.0,
            "risk_level": "SAFE",
            "confidence": 1.0,
            "primary_causes": [],
            "motion_speed": 0.0,
            "turbulence": 0.0,
            "hotspot_alert": "",
            "opposing_alert": "",
            "gps_alerts": [],
            "zone_scores": None,
            "analytics_seq": -1,
            "stats_updated_at": None,
        })

    # Load 20 CCTV Feeds (using YOLOv11 model)
    for i in range(1, 21):
        v_file = video_files[(i - 1) % len(video_files)]
        fallback_path = f"Videos/{v_file}" if os.path.exists(os.path.join(os.getcwd(), "Videos", v_file)) else "Videos/Crowd.mp4"
        cameras_db.append({
            "id": f"cctv-{i}",
            "name": f"CCTV CAMERA {i}",
            "location": "Pushkaralu" if i % 2 == 0 else "Rjy",
            "category": "CCTV",
            "source_stream_path": f"live/cctv{i}",
            "stream_path": f"analyzed/cctv{i}",
            "fallback_video": fallback_path,
            "source_kind": "live_publish",
            "enabled": True,
            "status": "offline",
            "error_type": "stream not found",
            "connection_message": "Video feed disconnected",
            "source_online": False,
            "output_online": False,
            "playback_stream_path": f"live/cctv{i}",
            "analytics_status": "idle",
            "people_count": 0,
            "comp_zone": "SAFE",
            "pressure": 0.0,
            "stampede_prob": 0.0,
            "risk_index": 0.0,
            "risk_level": "SAFE",
            "confidence": 1.0,
            "primary_causes": [],
            "motion_speed": 0.0,
            "turbulence": 0.0,
            "hotspot_alert": "",
            "opposing_alert": "",
            "gps_alerts": [],
            "zone_scores": None,
            "analytics_seq": -1,
            "stats_updated_at": None,
        })

    _rebuild_camera_indexes()

load_cameras()

# Pydantic models for webhooks
class ModeRequest(BaseModel):
    mode: str

class StateRequest(BaseModel):
    path: str
    status: str

class StartRequest(BaseModel):
    source_url: Optional[str] = None
    use_sample: bool = False

class StatsUpdate(BaseModel):
    drone_id: str
    density_score: float
    comp_zone: str
    status: Optional[str] = None
    pressure: Optional[float] = 0.0
    stampede_prob: Optional[float] = 0.0
    risk_index: Optional[float] = 0.0
    risk_level: Optional[str] = "SAFE"
    confidence: Optional[float] = 1.0
    primary_causes: Optional[list[str]] = Field(default_factory=list)
    motion_speed: Optional[float] = 0.0
    turbulence: Optional[float] = 0.0
    hotspot_alert: Optional[str] = ""
    opposing_alert: Optional[str] = ""
    gps_alerts: Optional[list] = Field(default_factory=list)
    zone_scores: Optional[list] = None
    analytics_active: Optional[bool] = True
    analytics_seq: Optional[int] = None


def reset_camera_analytics(camera):
    """Clear every field that belongs exclusively to Counting Mode."""
    camera.update({
        "people_count": 0,
        "comp_zone": "SAFE",
        "pressure": 0.0,
        "stampede_prob": 0.0,
        "risk_index": 0.0,
        "risk_level": "SAFE",
        "confidence": 1.0,
        "primary_causes": [],
        "motion_speed": 0.0,
        "turbulence": 0.0,
        "hotspot_alert": "",
        "opposing_alert": "",
        "gps_alerts": [],
        "zone_scores": None,
    })


def find_camera_by_stream_path(path):
    """Return (camera, path role) for a MediaMTX publish path."""
    normalized = (path or "").strip("/")
    return _cameras_by_stream_path.get(normalized, (None, None))


def normalize_camera_id(camera_id):
    """Normalize legacy IDs such as ``drone1`` to ``drone-1``."""
    normalized = (camera_id or "").lower().strip()
    if normalized.startswith("drone") and "-" not in normalized:
        normalized = normalized.replace("drone", "drone-", 1)
    elif normalized.startswith("cctv") and "-" not in normalized:
        normalized = normalized.replace("cctv", "cctv-", 1)
    return normalized


def find_camera_by_id(camera_id):
    return _cameras_by_id.get(normalize_camera_id(camera_id))


def is_stream_running(camera_id):
    process = running_processes.get(camera_id)
    if not process:
        return False
    try:
        return process.poll() is None
    except (AttributeError, OSError):
        return True


def get_worker_dependency_error():
    """Return a useful preflight error without importing either large package."""
    missing = [
        package
        for package in ("cv2", "torch")
        if importlib.util.find_spec(package) is None
    ]
    if not missing:
        return None
    display_names = {"cv2": "opencv-python", "torch": "torch"}
    packages = ", ".join(display_names[name] for name in missing)
    return (
        f"Counting is unavailable because this Python is missing: {packages}. "
        "Restart with run_lite.bat after installing requirements.txt."
    )


def update_camera_playback_path(camera):
    """Prefer raw video while analysis is disabled, unavailable, or starting."""
    use_raw = camera.get("source_online") and (
        not global_counting_mode or not camera.get("output_online")
    )
    camera["playback_stream_path"] = (
        camera.get("source_stream_path") if use_raw else camera.get("stream_path")
    )


def _close_process_log(process):
    log_file = getattr(process, "_log_file", None)
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass


def _worker_exit_message(process):
    """Summarize a crash without exposing a camera URL or a large traceback."""
    log_path = getattr(process, "_log_path", None)
    if log_path:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                for line in reversed(log_file.readlines()[-40:]):
                    marker = "ModuleNotFoundError: No module named "
                    if marker in line:
                        package = line.split(marker, 1)[1].strip().strip("'\"")
                        return (
                            f"Video processor stopped because Python package '{package}' "
                            "is missing. Restart with run_lite.bat after installing requirements.txt."
                        )
        except OSError:
            pass
    return (
        f"Video processor stopped (exit code {process.returncode}). "
        "See the camera log in outputs/logs."
    )


def start_live_analysis(camera):
    """Analyze a raw publisher while always preserving raw video as fallback."""
    source_path = camera.get("source_stream_path")
    if not source_path or not camera.get("source_online") or not camera.get("enabled", True):
        return None

    camera["status"] = "online"
    camera["error_type"] = None
    update_camera_playback_path(camera)

    if not global_counting_mode:
        camera["analytics_status"] = "disabled"
        camera["connection_message"] = "Live video connected in Viewing Mode."
        if is_stream_running(camera["id"]):
            stop_stream(camera["id"])
        return None

    if is_stream_running(camera["id"]):
        camera["analytics_status"] = "starting"
        return None

    dependency_error = get_worker_dependency_error()
    if dependency_error:
        camera["analytics_status"] = "error"
        camera["error_type"] = "analysis unavailable"
        camera["connection_message"] = f"Live video connected. {dependency_error}"
        return None

    camera["analytics_status"] = "starting"
    camera["connection_message"] = "Live video connected. Counting is starting..."
    reset_camera_analytics(camera)
    source_url = f"rtsp://127.0.0.1:8554/{source_path.strip('/')}"
    print(f"[LITE SERVER] Starting analysis for {camera['id']} from {source_path}")
    try:
        return start_stream(camera, source_url)
    except Exception as exc:
        camera["analytics_status"] = "error"
        camera["error_type"] = "analysis failed to start"
        camera["connection_message"] = f"Live video connected. Counting failed to start: {exc}"
        update_camera_playback_path(camera)
        return None


def reset_analytics_freshness(camera):
    """Require the next result to come from a newly analyzed frame."""
    camera["analytics_seq"] = -1
    camera["stats_updated_at"] = None


def refresh_camera_health():
    """Detect worker exits and never present an old inference result as live."""
    now = time.time()
    for camera in cameras_db:
        process = running_processes.get(camera["id"])
        if process and process.poll() is not None:
            running_processes.pop(camera["id"], None)
            _close_process_log(process)
            camera["output_online"] = False
            camera["analytics_status"] = "error"
            camera["error_type"] = "video processor stopped"
            crash_message = _worker_exit_message(process)
            if camera.get("source_online") and camera.get("enabled", True):
                camera["status"] = "online"
                camera["connection_message"] = f"Live video connected. {crash_message}"
            else:
                camera["status"] = "offline"
                camera["connection_message"] = crash_message
            update_camera_playback_path(camera)

        updated_at = camera.get("stats_updated_at")
        camera["analytics_age_seconds"] = (
            round(max(0.0, now - updated_at), 1) if updated_at else None
        )
        if (
            global_counting_mode
            and camera.get("analytics_status") == "active"
            and updated_at
            and now - updated_at > ANALYTICS_STALE_AFTER_SECONDS
        ):
            camera["analytics_status"] = "stale"
            reset_camera_analytics(camera)


@lru_cache(maxsize=1)
def get_lan_ip():
    """Return the default-route address camera apps can actually reach."""
    configured_host = os.getenv("CCTV_PUBLISH_HOST", "").strip()
    if configured_host:
        return configured_host

    # A UDP connect chooses the operating system's default route without
    # sending application data. This avoids selecting VirtualBox/VPN adapters.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect(("8.8.8.8", 80))
            route_ip = route_socket.getsockname()[0]
            address = ipaddress.ip_address(route_ip)
            if not address.is_loopback and not address.is_link_local:
                return route_ip
    except (OSError, ValueError):
        pass

    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
        for candidate in addresses:
            address = ipaddress.ip_address(candidate)
            if not address.is_loopback and not address.is_link_local:
                return candidate
    except (OSError, ValueError):
        pass
    return "127.0.0.1"

def generate_mediamtx_config(port: int):
    config_content = f"""# MediaMTX Low-Latency Configuration (Auto-generated)
rtsp: yes
rtspAddress: :8554
protocols: [udp, multicast, tcp]
rtpAddress: :8002
rtcpAddress: :8003

rtmp: yes
rtmpAddress: :1935

srt: yes
srtAddress: :8890

webrtc: yes
webrtcAddress: :8889

hls: yes
hlsAddress: :8088
hlsVariant: lowLatency
hlsSegmentCount: 3
hlsSegmentDuration: 1s
hlsPartDuration: 200ms

readTimeout: 5s
writeTimeout: 5s

paths:
  all:
    runOnReady: 'curl -X POST http://127.0.0.1:{port}/cameras/state -H "Content-Type: application/json" -d "{{\\"path\\":\\"$MTX_PATH\\", \\"status\\":\\"online\\"}}"'
    runOnNotReady: 'curl -X POST http://127.0.0.1:{port}/cameras/state -H "Content-Type: application/json" -d "{{\\"path\\":\\"$MTX_PATH\\", \\"status\\":\\"offline\\"}}"'
"""
    with open("mediamtx.yml", "w") as f:
        f.write(config_content.strip())

def cleanup_and_start_mediamtx():
    # ponytail: generate dynamic config file to align backend ports
    generate_mediamtx_config(config.BACKEND_PORT)

    # Reuse an already healthy local MediaMTX instance. This prevents duplicate
    # launch attempts after a dashboard restart and avoids briefly dropping all
    # live camera publishers.
    required_ports = (1935, 8554, 8889, 8088)
    ports_ready = True
    for media_port in required_ports:
        try:
            with socket.create_connection(("127.0.0.1", media_port), timeout=0.2):
                pass
        except OSError:
            ports_ready = False
            break
    if ports_ready:
        print("[LITE SERVER] Reusing the running MediaMTX service.")
        return

    mediamtx_bin = "mediamtx.exe" if os.name == "nt" else "mediamtx"
    mediamtx_path = os.path.join(os.getcwd(), mediamtx_bin)
    if not os.path.exists(mediamtx_path) and os.name != "nt":
        import shutil
        mediamtx_path = shutil.which("mediamtx") or "mediamtx"

    mediamtx_config = os.path.join(os.getcwd(), "mediamtx.yml")
    if mediamtx_path:
        try:
            print(f"[LITE SERVER] Starting MediaMTX ({mediamtx_path}) with configuration...")
            cmd = [mediamtx_path]
            if os.path.exists(mediamtx_config):
                cmd.append(mediamtx_config)
            p = subprocess.Popen(
                cmd,
                cwd=os.getcwd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            running_processes["mediamtx"] = p
            time.sleep(1.0)
        except Exception as e:
            print(f"[LITE SERVER] Failed to start MediaMTX: {e}")

cleanup_and_start_mediamtx()

def get_default_source_for_camera(camera):
    return f"rtsp://127.0.0.1:8554/{camera['source_stream_path']}"


def get_sample_source_for_camera(camera):
    """Return bundled footage only when the user explicitly requests a test."""
    configured = camera.get("fallback_video")
    if configured and os.path.exists(os.path.join(os.getcwd(), configured)):
        return configured

    video_dir = os.path.join(os.getcwd(), "Videos")
    video_files = []
    if os.path.exists(video_dir):
        video_files = [f for f in os.listdir(video_dir) if f.endswith(".mp4")]
    
    if video_files:
        video_files.sort()
        for vf in video_files:
            name_no_ext = os.path.splitext(vf)[0].lower()
            if (name_no_ext in camera["name"].lower() or 
                name_no_ext in camera["id"].lower() or 
                name_no_ext in camera.get("location", "").lower()):
                return os.path.join("Videos", vf)
                
        camera_index = sum(ord(c) for c in camera["id"])
        selected_video = video_files[camera_index % len(video_files)]
        return os.path.join("Videos", selected_video)

    raise HTTPException(status_code=404, detail="No bundled sample video is available")


def get_analyzed_publish_target(camera):
    """Return the credential-free local RTMP destination for one analyzer."""
    return f"rtmp://127.0.0.1:1935/{camera['stream_path']}"


def start_stream(camera, source_url):
    drone_id = camera["id"]
    dependency_error = get_worker_dependency_error()
    if dependency_error:
        raise RuntimeError(dependency_error)

    stop_stream(drone_id)
    reset_camera_analytics(camera)
    reset_analytics_freshness(camera)

    normalized_source = str(source_url).lower()
    if normalized_source.startswith(("videos/", "videos\\")) or os.path.isfile(str(source_url)):
        camera["source_kind"] = "sample"
    elif normalized_source.startswith("rtsp://127.0.0.1:8554/"):
        camera["source_kind"] = "live_publish"
    else:
        camera["source_kind"] = "live_url"

    # Drone/CCTV publishing is intentionally open for the current local
    # deployment phase. There are no user/password query parameters.
    rtmp_target = get_analyzed_publish_target(camera)

    infer_script = os.path.join(os.getcwd(), "infer.py")
    cmd = [sys.executable, infer_script, source_url]

    env = os.environ.copy()
    env["HEADLESS"] = "1"
    env["RTMP_TARGET"] = rtmp_target
    env["DRONE_ID"] = str(drone_id)
    env["CAMERA_CATEGORY"] = camera.get("category", "DRONE")
    env["DJANGO_UPDATE_URL"] = f"{config.BACKEND_URL}/cameras/update_stats"
    env["MODE_STATUS_URL"] = f"{config.BACKEND_URL}/get_mode"
    env["COUNTING_MODE_ACTIVE"] = "1" if global_counting_mode else "0"
    env["VERIFY_PREPROCESS"] = "0"  # Skip startup self-test in headless workers
    env["PYTHONUNBUFFERED"] = "1"
    env["DISPLAY_WIDTH"] = str(config.WEB_STREAM_WIDTH)
    env["DISPLAY_HEIGHT"] = str(config.WEB_STREAM_HEIGHT)
    env["OUTPUT_STREAM_FPS"] = f"{config.OUTPUT_STREAM_FPS:g}"
    # The browser already renders the detailed count/risk HUD. Keeping the
    # encoded video clean removes dozens of text draws from every video frame.
    env["RENDER_VIDEO_OVERLAYS"] = "0"

    # Log subprocess output to files for debugging (instead of silently discarding)
    log_dir = os.path.join(os.getcwd(), "outputs", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{drone_id}.log")
    log_file = open(log_path, "w")

    try:
        p = subprocess.Popen(
            cmd,
            cwd=os.getcwd(),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        log_file.close()
        raise
    # Attach the log file handle so we can close it on stop
    p._log_file = log_file
    p._log_path = log_path
    running_processes[drone_id] = p
    print(f"[LITE SERVER] Started {drone_id} (pid={p.pid}), log: {log_path}")
    return p.pid


def stop_stream(drone_id):
    p = running_processes.pop(drone_id, None)
    if not p:
        return False
    try:
        p.terminate()
        p.wait(timeout=2)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    _close_process_log(p)
    return True



# --- Endpoints ---

@app.get("/", response_class=FileResponse)
def index():
    """Serve the static HTML page directly."""
    return FileResponse("lite_dashboard.html")

@app.get("/health")
def health_check():
    dependency_error = get_worker_dependency_error()
    return {
        "status": "healthy",
        "service": "lite-cctv-backend",
        "counting_available": dependency_error is None,
        "counting_message": dependency_error,
    }

@app.get("/cameras")
def get_cameras():
    refresh_camera_health()
    publish_host = get_lan_ip()
    for camera in cameras_db:
        camera["publish_url"] = (
            f"rtmp://{publish_host}:1935/{camera['source_stream_path']}"
        )
        update_camera_playback_path(camera)
    return cameras_db

@app.post("/cameras/state")
def update_camera_state(req: StateRequest):
    path = req.path.strip("/")
    matched_camera, path_role = find_camera_by_stream_path(path)

    if not matched_camera:
        return {"status": "ignored", "path": path}

    is_online = req.status.lower() == "online"

    if path_role == "source" and is_online:
        matched_camera["source_online"] = True
        matched_camera["source_kind"] = "live_publish"
        if matched_camera.get("enabled", True):
            matched_camera["status"] = "online"
            matched_camera["error_type"] = None
            start_live_analysis(matched_camera)
        else:
            matched_camera["status"] = "offline"
            matched_camera["connection_message"] = "Video is available but disconnected by the operator."
        update_camera_playback_path(matched_camera)
    elif path_role == "source":
        matched_camera["source_online"] = False
        matched_camera["output_online"] = False
        matched_camera["status"] = "offline"
        matched_camera["error_type"] = "stream not found"
        matched_camera["connection_message"] = "The camera stopped sending video."
        matched_camera["analytics_status"] = "idle"
        stop_stream(matched_camera["id"])
        reset_camera_analytics(matched_camera)
        reset_analytics_freshness(matched_camera)
        update_camera_playback_path(matched_camera)
    elif is_online:
        matched_camera["output_online"] = True
        if matched_camera.get("enabled", True):
            matched_camera["status"] = "online"
            matched_camera["error_type"] = None
            matched_camera["connection_message"] = (
                "Live video and counting connected."
                if global_counting_mode
                else "Live video connected in Viewing Mode."
            )
        update_camera_playback_path(matched_camera)
    else:
        matched_camera["output_online"] = False
        if matched_camera.get("source_online") and matched_camera.get("enabled", True):
            matched_camera["status"] = "online"
            matched_camera["connection_message"] = "Live video connected; using the raw feed."
            if global_counting_mode and not is_stream_running(matched_camera["id"]):
                start_live_analysis(matched_camera)
            elif not global_counting_mode:
                matched_camera["analytics_status"] = "disabled"
        else:
            matched_camera["status"] = "offline"
            matched_camera["error_type"] = "stream not found"
            matched_camera["analytics_status"] = "idle"
            if matched_camera.get("enabled", True):
                matched_camera["connection_message"] = "Video feed disconnected"
        update_camera_playback_path(matched_camera)

    return {"status": "updated", "camera_id": matched_camera["id"], "state": {
        "status": matched_camera["status"],
        "error_type": matched_camera["error_type"],
        "connection_message": matched_camera.get("connection_message"),
        "source_online": matched_camera.get("source_online"),
        "output_online": matched_camera.get("output_online"),
        "playback_stream_path": matched_camera.get("playback_stream_path"),
    }}

@app.post("/cameras/{drone_id}/start")
def start_camera(drone_id: str, req: StartRequest):
    drone_id = normalize_camera_id(drone_id)
    matched_camera = find_camera_by_id(drone_id)

    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    source_url = req.source_url.strip() if req.source_url else None
    matched_camera["enabled"] = True
    matched_camera["error_type"] = None
    matched_camera["connection_message"] = None
    publish_url = (
        f"rtmp://{get_lan_ip()}:1935/{matched_camera['source_stream_path']}"
    )
    matched_camera["publish_url"] = publish_url

    # A blank URL means an external camera app will publish to live/*. Do not
    # start a processor that repeatedly opens a stream which does not exist yet.
    if not req.use_sample and not source_url:
        stop_stream(drone_id)
        matched_camera["source_kind"] = "live_publish"
        matched_camera["output_online"] = False
        reset_camera_analytics(matched_camera)
        reset_analytics_freshness(matched_camera)
        if matched_camera.get("source_online"):
            matched_camera["status"] = "online"
            start_live_analysis(matched_camera)
        else:
            matched_camera["status"] = "connecting"
            matched_camera["analytics_status"] = (
                "waiting_for_source" if global_counting_mode else "disabled"
            )
            matched_camera["connection_message"] = (
                f"Waiting for the camera to publish to {publish_url}"
            )
        update_camera_playback_path(matched_camera)
        return {
            "status": matched_camera["status"],
            "drone_id": drone_id,
            "pid": None,
            "publish_url": publish_url,
            "camera": matched_camera,
        }

    if req.use_sample:
        source_url = get_sample_source_for_camera(matched_camera)
        matched_camera["source_kind"] = "sample"
        connecting_message = "Starting bundled sample video..."
    else:
        matched_camera["source_kind"] = "live_url"
        connecting_message = "Opening the camera URL..."

    matched_camera["source_online"] = False
    matched_camera["output_online"] = False
    matched_camera["status"] = "connecting"
    matched_camera["connection_message"] = connecting_message
    matched_camera["analytics_status"] = (
        "starting" if global_counting_mode else "disabled"
    )
    update_camera_playback_path(matched_camera)

    try:
        pid = start_stream(matched_camera, source_url)
    except Exception as exc:
        matched_camera["status"] = "offline"
        matched_camera["analytics_status"] = "error"
        matched_camera["error_type"] = "video processor failed to start"
        matched_camera["connection_message"] = str(exc)
        status_code = 503 if get_worker_dependency_error() else 500
        raise HTTPException(status_code=status_code, detail=str(exc))

    return {
        "status": "connecting",
        "drone_id": drone_id,
        "pid": pid,
        "camera": matched_camera,
    }

@app.post("/cameras/{drone_id}/stop")
def stop_camera(drone_id: str):
    drone_id = normalize_camera_id(drone_id)
    matched_camera = find_camera_by_id(drone_id)

    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    stop_stream(drone_id)
    matched_camera["enabled"] = False
    matched_camera["status"] = "offline"
    matched_camera["error_type"] = None
    matched_camera["connection_message"] = "Disconnected by the operator."
    matched_camera["output_online"] = False
    matched_camera["analytics_status"] = "idle"
    reset_camera_analytics(matched_camera)
    reset_analytics_freshness(matched_camera)
    update_camera_playback_path(matched_camera)
    return {"status": "stopped", "drone_id": drone_id}

@app.post("/cameras/update_stats")
def update_stats(data: StatsUpdate):
    drone_id = normalize_camera_id(data.drone_id)
    matched_camera = find_camera_by_id(drone_id)

    if not matched_camera:
        return {"status": "ignored", "drone_id": drone_id}
    if not matched_camera.get("enabled", True):
        return {"status": "ignored", "drone_id": drone_id, "reason": "camera disabled"}

    matched_camera["status"] = "online"
    matched_camera["error_type"] = None

    # Viewing Mode is video-only. Ignore stale/in-flight analytics, and when
    # Counting Mode is re-enabled wait for a fresh result from the worker.
    if not global_counting_mode or not data.analytics_active:
        matched_camera["analytics_status"] = (
            "disabled" if not global_counting_mode else "starting"
        )
        matched_camera["connection_message"] = "Live video connected in Viewing Mode."
        reset_camera_analytics(matched_camera)
        update_camera_playback_path(matched_camera)
        return {"status": "success", "counting_mode": global_counting_mode}

    # Workers render video faster than they finish an inference pass. They may
    # therefore resend the same result several times. Only a newer sequence is
    # allowed to refresh the count or its freshness timestamp.
    if data.analytics_seq is not None:
        previous_seq = matched_camera.get("analytics_seq", -1)
        if data.analytics_seq <= previous_seq:
            return {
                "status": "duplicate",
                "counting_mode": global_counting_mode,
            }
        matched_camera["analytics_seq"] = data.analytics_seq

    matched_camera["analytics_status"] = "active"
    matched_camera["connection_message"] = "Live video and counting connected."
    matched_camera["stats_updated_at"] = time.time()
    matched_camera["people_count"] = max(0, int(round(data.density_score)))
    matched_camera["comp_zone"] = data.comp_zone
    matched_camera["pressure"] = data.pressure
    
    # Backwards compatibility check
    matched_camera["risk_index"] = data.risk_index if data.risk_index is not None else (data.stampede_prob * 100.0)
    matched_camera["risk_level"] = data.risk_level if data.risk_level is not None else data.comp_zone
    matched_camera["confidence"] = data.confidence if data.confidence is not None else 1.0
    matched_camera["primary_causes"] = data.primary_causes if data.primary_causes is not None else []
    
    if data.risk_index is not None:
        matched_camera["stampede_prob"] = data.risk_index / 100.0
    else:
        matched_camera["stampede_prob"] = data.stampede_prob
        
    matched_camera["motion_speed"] = data.motion_speed
    matched_camera["turbulence"] = data.turbulence
    matched_camera["hotspot_alert"] = data.hotspot_alert
    matched_camera["opposing_alert"] = data.opposing_alert
    matched_camera["gps_alerts"] = data.gps_alerts
    matched_camera["zone_scores"] = data.zone_scores
    update_camera_playback_path(matched_camera)

    return {"status": "success", "counting_mode": global_counting_mode}

@app.post("/set_mode")
def set_mode(req: ModeRequest):
    global global_counting_mode
    mode = req.mode.lower().strip()
    if mode == "counting":
        global_counting_mode = True
        for camera in cameras_db:
            if not camera.get("enabled", True):
                continue
            reset_camera_analytics(camera)
            reset_analytics_freshness(camera)
            if camera.get("source_online") and camera.get("source_kind") == "live_publish":
                start_live_analysis(camera)
            elif camera.get("status") == "online":
                camera["analytics_status"] = "starting"
                camera["connection_message"] = "Live video connected. Counting is starting..."
            elif camera.get("status") == "connecting":
                camera["analytics_status"] = "waiting_for_source"
            update_camera_playback_path(camera)
    elif mode == "viewing" or mode == "normal":
        global_counting_mode = False
        for camera in cameras_db:
            if (
                camera.get("source_kind") == "live_publish"
                and camera.get("source_online")
            ):
                stop_stream(camera["id"])
                if camera.get("enabled", True):
                    camera["status"] = "online"
                    camera["connection_message"] = "Live video connected in Viewing Mode."
            if camera.get("status") in ("online", "connecting"):
                camera["analytics_status"] = "disabled"
            reset_camera_analytics(camera)
            reset_analytics_freshness(camera)
            update_camera_playback_path(camera)
    else:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'counting' or 'viewing'.")
    print(f"[LITE SERVER] Global mode set to: {'counting' if global_counting_mode else 'viewing'}")
    dependency_error = get_worker_dependency_error()
    return {
        "status": "success",
        "counting_mode": global_counting_mode,
        "counting_available": dependency_error is None,
        "counting_message": dependency_error,
    }

@app.get("/get_mode")
def get_mode():
    return {"counting_mode": global_counting_mode}


@app.get("/stream/{path:path}")
async def proxy_stream(path: str):
    # Redirect directly to MediaMTX HLS - simpler and more reliable
    target_url = f"http://127.0.0.1:8088/{path}"
    return RedirectResponse(url=target_url, status_code=307)

if __name__ == "__main__":
    import uvicorn
    # Reloading starts a second interpreter and can duplicate MediaMTX/worker
    # processes. Keep it opt-in for development, off for smooth operation.
    reload_enabled = os.environ.get("BACKEND_RELOAD", "0").lower() in (
        "1", "true", "yes", "on"
    )
    host_ip = os.environ.get("BACKEND_HOST", "0.0.0.0")  # ponytail: bind 0.0.0.0 to accept external & LAN traffic
    uvicorn.run(
        "lite_server:app" if reload_enabled else app,
        host=host_ip,
        port=config.BACKEND_PORT,
        reload=reload_enabled,
    )
