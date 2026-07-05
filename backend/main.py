import json
import os
import sys
import subprocess
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Pushkaralu Drone CCTV Backend")

# Enable CORS for frontend clients (Vercel, localhost, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for prototype flexibility
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for camera states and running subprocesses
# Structure: {camera_id: {"status": str, "error_type": Optional[str]}}
camera_states = {}
running_processes = {}

# Load camera configs
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "cameras.json")

def load_cameras():
    if not os.path.exists(CONFIG_PATH):
        cameras = []
    else:
        with open(CONFIG_PATH, "r") as f:
            try:
                cameras = json.load(f)
            except Exception:
                cameras = []
    
    # Auto-ensure drone1 to drone40 exist in the camera database
    existing_ids = {c["id"] for c in cameras}
    for i in range(1, 41):
        drone_id = f"drone{i}"
        if drone_id not in existing_ids:
            cameras.append({
                "id": drone_id,
                "name": f"Drone {i}",
                "location": "Pushkaralu Swarm",
                "stream_path": f"live/drone{i}",
                "publish_user": "operator",
                "publish_pass": "pushkar2026",
                "stream_url": f"http://localhost:8088/live/drone{i}/index.m3u8"
            })
    return cameras

# Initialize states
cameras_db = load_cameras()
for cam in cameras_db:
    camera_states[cam["id"]] = {
        "status": "offline",
        "error_type": "stream not found"
    }

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

@app.get("/health")
def health_check():
    """Simple health check endpoint to keep Render service warm."""
    return {"status": "healthy", "service": "cctv-backend"}

@app.get("/cameras")
def get_cameras():
    """Returns the list of configured cameras with their real-time status."""
    cameras = load_cameras()
    result = []
    for cam in cameras:
        cam_id = cam["id"]
        state = camera_states.get(cam_id, {"status": "offline", "error_type": "stream not found"})
        result.append({
            "id": cam_id,
            "name": cam["name"],
            "location": cam["location"],
            "stream_path": cam.get("stream_path", f"live/{cam_id}"),
            "stream_url": cam["stream_url"],
            "status": state["status"],
            "error_type": state["error_type"]
        })
    return result

@app.post("/auth")
def authenticate_publish(req: AuthRequest):
    """
    MediaMTX External Authentication Endpoint.
    Only authenticates 'publish' actions; allows 'read' actions (HLS viewing) freely.
    """
    # If it is not a publish request, let it pass (we don't restrict HLS viewers for now)
    if req.action != "publish":
        return Response(status_code=200)

    cameras = load_cameras()
    matched_camera = None

    # Search for a camera matching the requested stream path
    # Normalize paths by removing leading/trailing slashes
    req_path = req.path.strip("/")
    for cam in cameras:
        cam_path = cam["stream_path"].strip("/")
        if cam_path == req_path:
            matched_camera = cam
            break

    if not matched_camera:
        print(f"Auth Reject: Path '{req.path}' not found in cameras.json")
        raise HTTPException(status_code=404, detail="Camera path not configured")

    # Verify credentials (allow localhost publishers automatically)
    is_local = req.ip in ("127.0.0.1", "::1", "localhost")
    if is_local or (req.user == matched_camera["publish_user"] and req.password == matched_camera["publish_pass"]):
        print(f"Auth Success (Local={is_local}): Publisher authenticated for path '{req.path}'")
        # Pre-emptively set status to online (will be confirmed by runOnPublish)
        camera_states[matched_camera["id"]] = {
            "status": "online",
            "error_type": None
        }
        return Response(status_code=200)
    else:
        print(f"Auth Fail: Invalid credentials for path '{req.path}' (User: {req.user!r})")
        # Record authentication failure state
        camera_states[matched_camera["id"]] = {
            "status": "error",
            "error_type": "authentication failed"
        }
        raise HTTPException(status_code=401, detail="Authentication failed")

@app.post("/cameras/state")
def update_camera_state(req: StateRequest):
    """
    Webhook called by MediaMTX scripts (runOnPublish/runOnUnpublish).
    Updates state when streams start or stop publishing.
    """
    cameras = load_cameras()
    matched_camera = None
    req_path = req.path.strip("/")
    
    for cam in cameras:
        cam_path = cam["stream_path"].strip("/")
        if cam_path == req_path:
            matched_camera = cam
            break

    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera path not found")

    cam_id = matched_camera["id"]
    if req.status == "online":
        camera_states[cam_id] = {
            "status": "online",
            "error_type": None
        }
    else:
        # Default offline error type is 'stream not found'
        camera_states[cam_id] = {
            "status": "offline",
            "error_type": "stream not found"
        }
        
    print(f"State Hook: Path '{req.path}' updated to {req.status}")
    return {"status": "updated", "camera_id": cam_id, "state": camera_states[cam_id]}

class StartStreamRequest(BaseModel):
    source_url: str

@app.post("/cameras/{drone_id}/start")
def start_camera_stream(drone_id: str, req: StartStreamRequest):
    drone_id = drone_id.lower().strip()
    
    # Terminate existing process for this drone if any
    if drone_id in running_processes:
        try:
            running_processes[drone_id].terminate()
            running_processes[drone_id].wait(timeout=2)
        except Exception:
            try:
                running_processes[drone_id].kill()
            except Exception:
                pass
    
    cameras = load_cameras()
    matched_camera = None
    for cam in cameras:
        if cam["id"].lower().strip() == drone_id:
            matched_camera = cam
            break
            
    if not matched_camera:
        raise HTTPException(status_code=404, detail="Camera config not found")
        
    user = matched_camera.get("publish_user", "")
    password = matched_camera.get("publish_pass", "")
    stream_path = matched_camera.get("stream_path", f"live/{drone_id}")
    
    if user and password:
        rtmp_target = f"rtmp://{user}:{password}@127.0.0.1:1935/{stream_path}"
    else:
        rtmp_target = f"rtmp://127.0.0.1:1935/{stream_path}"
        
    # Start: python infer.py <source_url>
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, "infer.py", req.source_url]
    
    # Use headless mode to avoid GUI window popping up on the server
    # Pass RTMP target so infer.py can stream its output back to MediaMTX
    env = os.environ.copy()
    env["HEADLESS"] = "1"
    env["RTMP_TARGET"] = rtmp_target
    
    try:
        p = subprocess.Popen(
            cmd,
            cwd=base_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        running_processes[drone_id] = p
        # Let state remain offline until MediaMTX starts receiving the publish stream
        camera_states[drone_id] = {
            "status": "offline",
            "error_type": "stream not found"
        }
        print(f"[BACKEND] Started infer.py process for {drone_id} (PID: {p.pid}) on source: {req.source_url}, streaming to: {rtmp_target}")
        return {
            "status": "started",
            "drone_id": drone_id,
            "pid": p.pid,
            "command": f"python infer.py {req.source_url}"
        }
    except Exception as e:
        print(f"[BACKEND] Failed to start process for {drone_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start stream process: {e}")

@app.post("/cameras/{drone_id}/stop")
def stop_camera_stream(drone_id: str):
    drone_id = drone_id.lower().strip()
    if drone_id in running_processes:
        p = running_processes[drone_id]
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        del running_processes[drone_id]
        print(f"[BACKEND] Stopped infer.py process for {drone_id}")
        
    camera_states[drone_id] = {
        "status": "offline",
        "error_type": "stream not found"
    }
    return {"status": "stopped", "drone_id": drone_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
