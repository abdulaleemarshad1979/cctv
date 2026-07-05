import os
import sys
import json
import socket
import time
import subprocess
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="AP Police Drone Monitoring Portal (LITE)")

# Mount static files directly from monitor/static
app.mount("/static", StaticFiles(directory="monitor/static"), name="static")

# In-memory cameras database and processes
cameras_db = []
running_processes = {}
LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}

def load_cameras():
    global cameras_db
    cameras_db = []
    video_files = ["Kumbh.mp4", "mecca.mp4", "stadium.mp4", "concert.mp4", "Crowd.mp4"]
    for i in range(1, 41):
        v_file = video_files[(i - 1) % len(video_files)]
        fallback_path = f"Videos/{v_file}" if os.path.exists(os.path.join(os.getcwd(), "Videos", v_file)) else "Videos/Crowd.mp4"
        cameras_db.append({
            "id": f"drone-{i}",
            "name": f"DRONE {i}",
            "location": "Pushkaralu" if i % 2 == 0 else "Rjy",
            "stream_path": f"live/drone{i}",
            "publish_user": "operator",
            "publish_pass": "pushkar2026",
            "fallback_video": fallback_path,
            "status": "offline",
            "error_type": "stream not found",
            "people_count": 0,
            "comp_zone": "SAFE",
            "pressure": 0.0,
            "stampede_prob": 0.0,
            "motion_speed": 0.0,
            "turbulence": 0.0,
            "hotspot_alert": "",
            "opposing_alert": "",
            "gps_alerts": []
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
    motion_speed: Optional[float] = 0.0
    turbulence: Optional[float] = 0.0
    hotspot_alert: Optional[str] = ""
    opposing_alert: Optional[str] = ""
    gps_alerts: Optional[list] = []

def cleanup_and_start_mediamtx():
    # Kill any active mediamtx or ffmpeg process to clear ports and reset config
    try:
        if os.name == 'nt':
            subprocess.run("taskkill /F /IM mediamtx.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run("taskkill /F /IM ffmpeg.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run("killall -9 mediamtx ffmpeg", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    except Exception:
        pass

    # Start mediamtx with our local config
    mediamtx_path = os.path.join(os.getcwd(), "mediamtx.exe")
    mediamtx_config = os.path.join(os.getcwd(), "mediamtx.yml")
    if os.path.exists(mediamtx_path) and os.path.exists(mediamtx_config):
        try:
            print("[LITE SERVER] Starting MediaMTX with project configuration...")
            subprocess.Popen(
                [mediamtx_path, mediamtx_config],
                cwd=os.getcwd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            time.sleep(1.0)
        except Exception as e:
            print(f"[LITE SERVER] Failed to start MediaMTX: {e}")

cleanup_and_start_mediamtx()

def get_default_source_for_camera(camera):
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
        rtmp_target = f"rtmp://{user}:{password}@127.0.0.1:1935/{stream_path}"
    else:
        rtmp_target = f"rtmp://127.0.0.1:1935/{stream_path}"

    infer_script = os.path.join(os.getcwd(), "infer.py")
    cmd = [sys.executable, infer_script, source_url]

    env = os.environ.copy()
    env["HEADLESS"] = "1"
    env["RTMP_TARGET"] = rtmp_target
    env["DRONE_ID"] = str(drone_id)
    env["DJANGO_UPDATE_URL"] = "http://127.0.0.1:8000/cameras/update_stats"

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
    matched_camera = None
    for cam in cameras_db:
        if cam["stream_path"].strip("/") == path:
            matched_camera = cam
            break

    if not matched_camera:
        # Check if the user is publishing to a custom path matching drone format
        if path.startswith("live/drone"):
            return Response(status_code=200)
        raise HTTPException(status_code=404, detail="Camera path not configured")

    ip = data.get("ip", "")
    user = data.get("user", "")
    password = data.get("password", "")

    is_local = ip in LOCAL_IPS
    if is_local or (user == matched_camera.get("publish_user", "") and password == matched_camera.get("publish_pass", "")):
        matched_camera["status"] = "online"
        matched_camera["error_type"] = None
        return Response(status_code=200)

    matched_camera["status"] = "error"
    matched_camera["error_type"] = "authentication failed"
    raise HTTPException(status_code=401, detail="Authentication failed")

@app.post("/cameras/state")
def update_camera_state(req: StateRequest):
    path = req.path.strip("/")
    matched_camera = None
    for cam in cameras_db:
        if cam["stream_path"].strip("/") == path:
            matched_camera = cam
            break

    if not matched_camera:
        return {"status": "ignored", "path": path}

    if req.status == "online":
        matched_camera["status"] = "online"
        matched_camera["error_type"] = None
    else:
        matched_camera["status"] = "offline"
        matched_camera["error_type"] = "stream not found"

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
    return {"status": "stopped", "drone_id": drone_id}

@app.post("/cameras/update_stats")
def update_stats(data: StatsUpdate):
    drone_id = data.drone_id.lower().strip()
    
    # Normalize incoming drone ID (e.g., drone1 -> drone-1)
    if "drone" in drone_id and "-" not in drone_id:
        drone_id = drone_id.replace("drone", "drone-")

    matched_camera = None
    for cam in cameras_db:
        if cam["id"].lower().strip() == drone_id:
            matched_camera = cam
            break

    if not matched_camera:
        return {"status": "ignored", "drone_id": drone_id}

    matched_camera["people_count"] = int(data.density_score)
    matched_camera["comp_zone"] = data.comp_zone
    matched_camera["status"] = "online"
    matched_camera["error_type"] = None
    matched_camera["pressure"] = data.pressure
    matched_camera["stampede_prob"] = data.stampede_prob
    matched_camera["motion_speed"] = data.motion_speed
    matched_camera["turbulence"] = data.turbulence
    matched_camera["hotspot_alert"] = data.hotspot_alert
    matched_camera["opposing_alert"] = data.opposing_alert
    matched_camera["gps_alerts"] = data.gps_alerts

    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    # Start on port 8000
    uvicorn.run("lite_server:app", host="127.0.0.1", port=8000, reload=True)
