"""
In-memory subprocess manager for per-drone `infer.py` processes.

This mirrors the behaviour of the original FastAPI backend (backend/main.py):
starting/stopping the inference+RTMP-publish process for a given drone_id.

NOTE: like the original prototype, this keeps process handles in a
module-level dict, so it only works correctly with a single Django process
(e.g. `manage.py runserver`, or gunicorn with --workers 1). For a real
multi-worker deployment this should move to a proper task queue / process
supervisor.
"""
import os
import sys
import subprocess

from django.conf import settings

running_processes = {}


def build_rtmp_target(camera):
    if camera.publish_user and camera.publish_pass:
        return f"rtmp://{camera.publish_user}:{camera.publish_pass}@127.0.0.1:1935/{camera.stream_path}"
    return f"rtmp://127.0.0.1:1935/{camera.stream_path}"


def start_mediamtx_if_needed():
    import socket
    import time
    def is_port_in_use(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
            
    if is_port_in_use(1935) or is_port_in_use(8554) or is_port_in_use(8088):
        return
        
    mediamtx_path = os.path.join(settings.DRONE_PROJECT_ROOT, "mediamtx.exe")
    if os.path.exists(mediamtx_path):
        try:
            subprocess.Popen(
                [mediamtx_path],
                cwd=str(settings.DRONE_PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            time.sleep(1.0)
        except Exception:
            pass


def start_stream(camera, source_url):
    drone_id = camera.id
    stop_stream(drone_id)  # terminate any existing process for this drone first

    start_mediamtx_if_needed()

    rtmp_target = build_rtmp_target(camera)
    infer_script = str(settings.INFER_SCRIPT_PATH)
    cmd = [sys.executable, infer_script, source_url]

    env = os.environ.copy()
    env["HEADLESS"] = "1"
    env["RTMP_TARGET"] = rtmp_target
    env["DRONE_ID"] = str(drone_id)
    env["DJANGO_UPDATE_URL"] = "http://127.0.0.1:8000/cameras/update_stats"

    p = subprocess.Popen(
        cmd,
        cwd=str(settings.DRONE_PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    running_processes[drone_id] = p
    return {
        "pid": p.pid,
        "command": f"python {os.path.basename(infer_script)} {source_url}",
        "rtmp_target": rtmp_target,
    }


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
