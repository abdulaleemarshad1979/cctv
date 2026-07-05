import json

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Camera
from . import stream_runner

LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

@require_GET
def dashboard(request):
    cameras = Camera.objects.filter(is_active=True)
    return render(request, "monitor/dashboard.html", {"cameras": cameras})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@require_GET
def health_check(request):
    return JsonResponse({"status": "healthy", "service": "cdmp-django-backend"})


# ---------------------------------------------------------------------------
# GET /cameras  -> list cameras + live status (polled by the dashboard JS)
# ---------------------------------------------------------------------------

@require_GET
def get_cameras(request):
    cameras = Camera.objects.filter(is_active=True)
    return JsonResponse([c.to_dict() for c in cameras], safe=False)


# ---------------------------------------------------------------------------
# POST /auth  -> MediaMTX externalAuthenticationURL
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def authenticate_publish(request):
    data = _json_body(request)
    action = data.get("action", "")

    # Only guard "publish" actions; let read/HLS-viewer requests through.
    if action != "publish":
        return JsonResponse({}, status=200)

    path = (data.get("path") or "").strip("/")
    user = data.get("user", "")
    password = data.get("password", "")
    ip = data.get("ip", "")

    camera = Camera.objects.filter(stream_path=path).first()
    if not camera:
        return JsonResponse({"detail": "Camera path not configured"}, status=404)

    is_local = ip in LOCAL_IPS
    if is_local or (user == camera.publish_user and password == camera.publish_pass):
        camera.status = "online"
        camera.error_type = None
        camera.save(update_fields=["status", "error_type", "updated_at"])
        return JsonResponse({}, status=200)

    camera.status = "error"
    camera.error_type = "authentication failed"
    camera.save(update_fields=["status", "error_type", "updated_at"])
    return JsonResponse({"detail": "Authentication failed"}, status=401)


# ---------------------------------------------------------------------------
# POST /cameras/state  -> MediaMTX runOnPublish / runOnNotReady webhook
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def update_camera_state(request):
    data = _json_body(request)
    path = (data.get("path") or "").strip("/")
    status = data.get("status", "offline")

    camera = Camera.objects.filter(stream_path=path).first()
    if not camera:
        return JsonResponse({"detail": "Camera path not found"}, status=404)

    if status == "online":
        camera.status = "online"
        camera.error_type = None
    else:
        camera.status = "offline"
        camera.error_type = "stream not found"
    camera.save(update_fields=["status", "error_type", "updated_at"])

    return JsonResponse({"status": "updated", "camera_id": camera.id, "state": {
        "status": camera.status, "error_type": camera.error_type,
    }})


def get_default_source_for_camera(camera):
    import os
    from django.conf import settings
    video_dir = os.path.join(settings.DRONE_PROJECT_ROOT, "Videos")
    video_files = []
    if os.path.exists(video_dir):
        video_files = [f for f in os.listdir(video_dir) if f.endswith(".mp4")]
    
    if video_files:
        video_files.sort()
        
        # Try to find a matching video file by location, name, or id
        for vf in video_files:
            name_no_ext = os.path.splitext(vf)[0].lower()
            if (name_no_ext in camera.name.lower() or 
                name_no_ext in camera.id.lower() or 
                name_no_ext in camera.location.lower()):
                return os.path.join("Videos", vf)
                
        # Otherwise select based on modulo of the camera ID
        camera_index = sum(ord(c) for c in camera.id)
        selected_video = video_files[camera_index % len(video_files)]
        return os.path.join("Videos", selected_video)

    # Fallback to RTSP URL only if no local videos are found
    return f"rtsp://127.0.0.1:8554/live/{camera.id}"


# ---------------------------------------------------------------------------
# POST /cameras/<drone_id>/start  -> launch infer.py for this drone
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def start_camera_stream(request, drone_id):
    drone_id = drone_id.lower().strip()
    camera = get_object_or_404(Camera, id=drone_id)

    data = _json_body(request)
    source_url = data.get("source_url")
    if not source_url:
        source_url = get_default_source_for_camera(camera)

    try:
        info = stream_runner.start_stream(camera, source_url)
    except Exception as e:
        return JsonResponse({"detail": f"Failed to start stream process: {e}"}, status=500)

    # Leave status as offline/"stream not found" until MediaMTX confirms
    # the publish via the /cameras/state webhook.
    camera.status = "offline"
    camera.error_type = "stream not found"
    camera.save(update_fields=["status", "error_type", "updated_at"])

    return JsonResponse({"status": "started", "drone_id": drone_id, **info})


# ---------------------------------------------------------------------------
# POST /cameras/<drone_id>/stop
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def stop_camera_stream(request, drone_id):
    drone_id = drone_id.lower().strip()
    camera = get_object_or_404(Camera, id=drone_id)

    stream_runner.stop_stream(drone_id)

    camera.status = "offline"
    camera.error_type = "stream not found"
    camera.save(update_fields=["status", "error_type", "updated_at"])

    return JsonResponse({"status": "stopped", "drone_id": drone_id})


# ---------------------------------------------------------------------------
# POST /cameras/update_stats
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def update_camera_stats(request):
    data = _json_body(request)
    drone_id = data.get("drone_id", "").lower().strip()
    if not drone_id:
        return JsonResponse({"detail": "drone_id is required"}, status=400)

    camera = Camera.objects.filter(id=drone_id).first()
    if not camera:
        return JsonResponse({"detail": "Camera not found"}, status=404)

    camera.people_count = int(float(data.get("density_score", 0)))
    camera.comp_zone = data.get("comp_zone", "SAFE")
    
    if data.get("status") == "online":
        camera.status = "online"
        camera.error_type = None

    camera.save(update_fields=["people_count", "comp_zone", "status", "error_type", "updated_at"])
    return JsonResponse({"status": "success"})
