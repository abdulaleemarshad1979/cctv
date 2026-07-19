import os
import sys
import json
import socket
import time
import subprocess
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import config

app = FastAPI(title="AP Police Drone Monitoring Portal (LITE)")

# Mount static files directly from static
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory cameras database and processes
cameras_db = []
running_processes = {}
LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}

# Global counting mode state (True = counting/analytics, False = viewing)
global_counting_mode = True

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
            # Larix keeps publishing Drone 1 to its existing URL.  The
            # inference worker republishes annotated video to a different
            # path so it never reads from and writes to the same stream.
            "source_stream_path": "live/drone1" if i == 1 else None,
            "stream_path": "analyzed/drone1" if i == 1 else f"live/drone{i}",
            "publish_user": f"drone-{i}",
            "publish_pass": config.CAMERA_AUTH_SECRET,
            # Drone 1 is reserved for the real drone stream; no demo video.
            "fallback_video": None if i == 1 else fallback_path,
            "status": "offline",
            "error_type": "stream not found",
            "source_online": False,
            "output_online": False,
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
            "zone_scores": None
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
            "stream_path": f"live/cctv{i}",
            "publish_user": f"cctv-{i}",
            "publish_pass": config.CAMERA_AUTH_SECRET,
            "fallback_video": fallback_path,
            "status": "offline",
            "error_type": "stream not found",
            "source_online": False,
            "output_online": False,
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
            "zone_scores": None
        })

load_cameras()

# Pydantic models for webhooks
class AuthRequest(BaseModel):
    ip: str = ""
    user: str = ""
    password: str = ""
    path: str = ""
    protocol: str = ""
    id: str = ""
    action: str = ""

class ModeRequest(BaseModel):
    mode: str

class StateRequest(BaseModel):
    path: str
    status: str

class StartRequest(BaseModel):
    source_url: Optional[str] = None

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
    primary_causes: Optional[list[str]] = []
    motion_speed: Optional[float] = 0.0
    turbulence: Optional[float] = 0.0
    hotspot_alert: Optional[str] = ""
    opposing_alert: Optional[str] = ""
    gps_alerts: Optional[list] = []
    zone_scores: Optional[list] = None
    analytics_active: Optional[bool] = True


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
    for camera in cameras_db:
        source_path = (camera.get("source_stream_path") or "").strip("/")
        output_path = (camera.get("stream_path") or "").strip("/")
        if source_path and source_path == normalized:
            return camera, "source"
        if output_path == normalized:
            return camera, "output"
    return None, None


def is_stream_running(camera_id):
    process = running_processes.get(camera_id)
    if not process:
        return False
    try:
        return process.poll() is None
    except (AttributeError, OSError):
        return True


def start_live_analysis(camera):
    """Start one analyzer for a raw live source, without duplicating workers."""
    source_path = camera.get("source_stream_path")
    if not source_path or is_stream_running(camera["id"]):
        return None

    camera["analytics_status"] = "starting" if global_counting_mode else "disabled"
    camera["status"] = "connecting"
    camera["error_type"] = None
    reset_camera_analytics(camera)
    source_url = f"rtsp://127.0.0.1:8554/{source_path.strip('/')}"
    print(f"[LITE SERVER] Starting analysis for {camera['id']} from {source_path}")
    return start_stream(camera, source_url)

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

readTimeout: 5s
writeTimeout: 5s

externalAuthenticationURL: http://127.0.0.1:{port}/auth

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

@app.on_event("shutdown")
def shutdown_event():
    print("[LITE SERVER] Shutting down. Terminating managed child processes...")
    for pid, p in list(running_processes.items()):
        try:
            print(f"[LITE SERVER] Terminating process for {pid}...")
            p.terminate()
            p.wait(timeout=1.0)
        except Exception as e:
            print(f"[LITE SERVER] Failed to terminate {pid}: {e}")

def get_default_source_for_camera(camera):
    if camera.get("id") == "drone-1":
        return f"rtsp://127.0.0.1:8554/{camera['source_stream_path']}"

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

    return f"rtsp://127.0.0.1:8554/live/{camera['id']}"

def start_stream(camera, source_url):
    drone_id = camera["id"]
    stop_stream(drone_id)

    user = camera.get("publish_user", "")
    password = camera.get("publish_pass", "")
    stream_path = camera["stream_path"]
    
    if user and password:
        rtmp_target = f"rtmp://127.0.0.1:1935/{stream_path}?user={user}&pass={password}"
    else:
        rtmp_target = f"rtmp://127.0.0.1:1935/{stream_path}"

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

    p = subprocess.Popen(
        cmd,
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    running_processes[drone_id] = p
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
    return True


# --- Endpoints ---

@app.get("/", response_class=FileResponse)
def index():
    """Serve the static HTML page directly."""
    return FileResponse("lite_dashboard.html")

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "lite-cctv-backend"}

@app.get("/cameras")
def get_cameras():
    return cameras_db

@app.post("/auth")
async def authenticate_publish(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    action = data.get("action", "")
    if action != "publish":
        return Response(status_code=200)

    path = (data.get("path") or "").strip("/")
    matched_camera, path_role = find_camera_by_stream_path(path)

    if matched_camera:
        # Authentication happens before MediaMTX declares the path ready.  A
        # raw Larix source is connecting until the analyzed output is ready.
        if path_role == "source":
            matched_camera["status"] = "connecting"
        print(f"[LITE SERVER] Authentication success for camera stream: {path}")
        return Response(status_code=200)

    print(f"[LITE SERVER] Authentication bypass (Success) for unconfigured camera stream: {path}")
    return Response(status_code=200)

@app.post("/cameras/state")
def update_camera_state(req: StateRequest):
    path = req.path.strip("/")
    matched_camera, path_role = find_camera_by_stream_path(path)

    if not matched_camera:
        return {"status": "ignored", "path": path}

    if path_role == "source" and req.status == "online":
        matched_camera["source_online"] = True
        matched_camera["status"] = "connecting"
        matched_camera["error_type"] = None
        try:
            start_live_analysis(matched_camera)
        except Exception as exc:
            matched_camera["analytics_status"] = "error"
            matched_camera["error_type"] = "analysis failed to start"
            print(f"[LITE SERVER] Failed to auto-start analysis for {matched_camera['id']}: {exc}")
    elif path_role == "source":
        matched_camera["source_online"] = False
        matched_camera["output_online"] = False
        matched_camera["status"] = "offline"
        matched_camera["error_type"] = "stream not found"
        matched_camera["analytics_status"] = "idle"
        stop_stream(matched_camera["id"])
        reset_camera_analytics(matched_camera)
    elif req.status == "online":
        matched_camera["output_online"] = True
        matched_camera["status"] = "online"
        matched_camera["error_type"] = None
    else:
        matched_camera["output_online"] = False
        if matched_camera.get("source_online"):
            matched_camera["status"] = "connecting"
            matched_camera["analytics_status"] = (
                "starting" if global_counting_mode else "disabled"
            )
            if not is_stream_running(matched_camera["id"]):
                try:
                    start_live_analysis(matched_camera)
                except Exception as exc:
                    matched_camera["analytics_status"] = "error"
                    matched_camera["error_type"] = "analysis failed to restart"
                    print(f"[LITE SERVER] Failed to restart analysis for {matched_camera['id']}: {exc}")
        else:
            matched_camera["status"] = "offline"
            matched_camera["error_type"] = "stream not found"
            matched_camera["analytics_status"] = "idle"

    return {"status": "updated", "camera_id": matched_camera["id"], "state": {
        "status": matched_camera["status"], "error_type": matched_camera["error_type"]
    }}

@app.post("/cameras/{drone_id}/start")
def start_camera(drone_id: str, req: StartRequest):
    drone_id = drone_id.lower().strip()
    matched_camera = None
    for cam in cameras_db:
        if cam["id"].lower().strip() == drone_id:
            matched_camera = cam
            break

    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    source_url = req.source_url
    if not source_url:
        source_url = get_default_source_for_camera(matched_camera)

    try:
        pid = start_stream(matched_camera, source_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start stream process: {e}")

    matched_camera["status"] = "connecting"
    matched_camera["error_type"] = "stream not found"
    matched_camera["analytics_status"] = (
        "starting" if global_counting_mode else "disabled"
    )
    return {
        "status": "connecting",
        "drone_id": drone_id,
        "pid": pid,
        "command": f"python infer.py {source_url}"
    }

@app.post("/cameras/{drone_id}/stop")
def stop_camera(drone_id: str):
    drone_id = drone_id.lower().strip()
    matched_camera = None
    for cam in cameras_db:
        if cam["id"].lower().strip() == drone_id:
            matched_camera = cam
            break

    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    stop_stream(drone_id)
    matched_camera["status"] = "offline"
    matched_camera["error_type"] = "stream not found"
    matched_camera["output_online"] = False
    matched_camera["analytics_status"] = "idle"
    reset_camera_analytics(matched_camera)
    return {"status": "stopped", "drone_id": drone_id}

@app.post("/cameras/update_stats")
def update_stats(data: StatsUpdate):
    drone_id = data.drone_id.lower().strip()
    
    # Normalize incoming drone/cctv ID (e.g., drone1 -> drone-1, cctv1 -> cctv-1)
    if "drone" in drone_id and "-" not in drone_id:
        drone_id = drone_id.replace("drone", "drone-")
    elif "cctv" in drone_id and "-" not in drone_id:
        drone_id = drone_id.replace("cctv", "cctv-")

    matched_camera = None
    for cam in cameras_db:
        if cam["id"].lower().strip() == drone_id:
            matched_camera = cam
            break

    if not matched_camera:
        return {"status": "ignored", "drone_id": drone_id}

    matched_camera["status"] = "online"
    matched_camera["error_type"] = None

    # Viewing Mode is video-only. Ignore stale/in-flight analytics, and when
    # Counting Mode is re-enabled wait for a fresh result from the worker.
    if not global_counting_mode or not data.analytics_active:
        matched_camera["analytics_status"] = (
            "disabled" if not global_counting_mode else "starting"
        )
        reset_camera_analytics(matched_camera)
        return {"status": "success", "counting_mode": global_counting_mode}

    matched_camera["analytics_status"] = "active"
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

    return {"status": "success", "counting_mode": global_counting_mode}

@app.post("/set_mode")
def set_mode(req: ModeRequest):
    global global_counting_mode
    mode = req.mode.lower().strip()
    if mode == "counting":
        global_counting_mode = True
        for camera in cameras_db:
            if camera.get("status") in ("online", "connecting"):
                camera["analytics_status"] = "starting"
                reset_camera_analytics(camera)
    elif mode == "viewing" or mode == "normal":
        global_counting_mode = False
        for camera in cameras_db:
            if camera.get("status") in ("online", "connecting"):
                camera["analytics_status"] = "disabled"
            reset_camera_analytics(camera)
    else:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'counting' or 'viewing'.")
    print(f"[LITE SERVER] Global mode set to: {'counting' if global_counting_mode else 'viewing'}")
    return {"status": "success", "counting_mode": global_counting_mode}

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
    # Start on dynamic backend port
    uvicorn.run("lite_server:app", host="127.0.0.1", port=config.BACKEND_PORT, reload=True)
