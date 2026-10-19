import os
import shutil
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import lite_server
from lite_server import app, load_cameras, save_camera_customizations
from src.recording_flights import FlightPlayback

ADMIN = ("admin", "review_admin_password_2026")
VIEWER = ("viewer", "review_viewer_password_2026")
SEGMENTS = ["2026-10-19_11-00-00-000000", "2026-10-19_11-00-03-000000"]

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("VIEWER_USERNAME", VIEWER[0])
    monkeypatch.setenv("VIEWER_PASSWORD", VIEWER[1])
    monkeypatch.setenv("SECRET_KEY", "review_only_secret_key_0123456789abcdef")
    save_camera_customizations({})
    load_cameras()

    rec = tmp_path / "rec"
    folder = rec / "live" / "drone1"
    folder.mkdir(parents=True)
    for name in SEGMENTS:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=25:duration=2", "-c:v", "libx264",
             "-preset", "ultrafast", "-g", "25",
             "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(folder / f"{name}.mp4")],
            check=True,
        )
        old = time.time() - 60  # not "being written"
        os.utime(folder / f"{name}.mp4", (old, old))

    manager = lite_server.video_recordings_manager
    monkeypatch.setattr(manager, "recordings_dir", str(rec))
    monkeypatch.setattr(manager, "index_file", str(tmp_path / "index.json"))
    monkeypatch.setattr(manager, "index", {})
    monkeypatch.setattr(lite_server, "flight_playback", FlightPlayback(str(rec), str(tmp_path / "cache")))
    monkeypatch.setitem(lite_server._flight_lookup, "at", 0.0)
    yield rec
    save_camera_customizations({})
    load_cameras()


def _client(user):
    client = TestClient(app)
    client.post("/login", json={"username": user[0], "password": user[1]})
    return client


def test_viewers_cannot_reach_flight_endpoints(archive):
    viewer = _client(VIEWER)
    assert viewer.get("/api/recordings?view=flights").status_code == 403
    assert viewer.get("/api/recordings/flights/x/prepare").status_code == 403
    assert viewer.get("/api/recordings/flights/x/video.mp4").status_code == 403
    assert viewer.get("/api/recordings/thumbnail?path=live/drone1/a.mp4").status_code == 403


def test_flight_listing_preparation_and_range_playback(archive):
    admin = _client(ADMIN)
    data = admin.get("/api/recordings?view=flights").json()
    assert "recordings" not in data
    assert data["summary"]["total_flights"] == 1
    flight = data["flights"][0]
    assert flight["segment_count"] == 2
    assert flight["drone_id"] == "drone-1"

    video_url = f"/api/recordings/flights/{flight['id']}/video.mp4"
    assert admin.get(video_url).status_code == 409  # not prepared yet

    deadline = time.time() + 30
    status = admin.get(f"/api/recordings/flights/{flight['id']}/prepare").json()
    while status["state"] == "preparing" and time.time() < deadline:
        time.sleep(0.1)
        status = admin.get(f"/api/recordings/flights/{flight['id']}/prepare").json()
    assert status["state"] == "ready", status
    assert status["video_url"] == video_url

    ranged = admin.get(video_url, headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert len(ranged.content) == 100
    assert ranged.headers["content-type"] == "video/mp4"
    assert ranged.headers["content-disposition"].startswith("inline")

    download = admin.get(video_url + "?download=1")
    assert download.headers["content-disposition"].startswith("attachment")
    assert "drone-1_2026-10-19_11-00-00Z.mp4" in download.headers["content-disposition"]

    assert admin.get("/api/recordings/flights/unknown/prepare").status_code == 404


def test_thumbnails_only_for_indexed_recordings(archive):
    admin = _client(ADMIN)
    flight = admin.get("/api/recordings?view=flights").json()["flights"][0]
    thumb = admin.get(f"/api/recordings/thumbnail?path={flight['thumbnail_path']}")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert admin.get("/api/recordings/thumbnail?path=../../etc/passwd").status_code == 404
