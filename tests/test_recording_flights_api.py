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
# A separate flight 10 minutes later, and one for another drone.
OTHER = [("drone1", "2026-10-19_11-10-00-000000"), ("drone3", "2026-10-19_11-05-00-000000")]

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
    for drone, name in [("drone1", n) for n in SEGMENTS] + OTHER:
        folder = rec / "live" / drone
        folder.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setitem(lite_server._flight_cache, "at", 0.0)
    yield rec
    save_camera_customizations({})
    load_cameras()


def _client(user):
    client = TestClient(app)
    client.post("/login", json={"username": user[0], "password": user[1]})
    return client


def test_viewers_cannot_reach_flight_endpoints(archive):
    viewer = _client(VIEWER)
    for url in ["/api/recordings?view=flights", "/api/recordings/flights/x",
                "/api/recordings/flights/x/playlist.m3u8", "/api/recordings/flights/x/download",
                "/api/recordings/thumbnail?path=live/drone1/a.mp4"]:
        assert viewer.get(url).status_code == 403, url


def test_flights_are_paged_and_filtered_on_the_server(archive):
    admin = _client(ADMIN)
    data = admin.get("/api/recordings?view=flights&page_size=2").json()
    assert data["total"] == 3 and data["total_pages"] == 2 and data["page"] == 1
    assert [f["start_epoch"] for f in data["flights"]] == sorted(
        [f["start_epoch"] for f in data["flights"]], reverse=True)
    assert all("segments" not in f for f in data["flights"])
    assert [d["id"] for d in data["drones"]] == ["drone-1", "drone-3"]
    page2 = admin.get("/api/recordings?view=flights&page_size=2&page=2").json()
    assert len(page2["flights"]) == 1
    assert admin.get("/api/recordings?view=flights&page=99&page_size=2").json()["page"] == 2

    only3 = admin.get("/api/recordings?view=flights&drone=drone-3").json()
    assert [f["drone_id"] for f in only3["flights"]] == ["drone-3"]
    # Dates match in the viewer's time zone: 11:00 UTC is 16:30 in India (tz=-330).
    ist = admin.get("/api/recordings?view=flights&q=2026-10-19 16:30&tz=-330").json()
    assert ist["total"] == 1 and ist["flights"][0]["segment_count"] == 2
    assert admin.get("/api/recordings?view=flights&q=2026-10-19 16:30").json()["total"] == 0


def test_flight_plays_as_one_video_and_downloads_joined(archive):
    admin = _client(ADMIN)
    flight = next(f for f in admin.get("/api/recordings?view=flights").json()["flights"] if f["segment_count"] == 2)
    details = admin.get(f"/api/recordings/flights/{flight['id']}").json()
    assert [s["url"] for s in details["segments"]] == [
        f"/recordings/live/drone1/{name}.mp4" for name in SEGMENTS]

    playlist = admin.get(f"/api/recordings/flights/{flight['id']}/playlist.m3u8")
    assert playlist.status_code == 200
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    text = playlist.text
    assert text.count("#EXT-X-MAP:") == 2 and text.endswith("#EXT-X-ENDLIST\n")

    # Byte ranges point at fragments of the original file.
    line = next(l for l in text.splitlines() if l.startswith("#EXT-X-BYTERANGE:"))
    length, offset = map(int, line.split(":")[1].split("@"))
    with open(archive / details["segments"][0]["relative_path"], "rb") as f:
        f.seek(offset)
        assert f.read(8)[4:] == b"moof"

    download = admin.get(f"/api/recordings/flights/{flight['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"] == 'attachment; filename="drone-1_2026-10-19_11-00-00Z.mp4"'
    assert len(download.content) > 10000
    single = next(f for f in admin.get("/api/recordings?view=flights").json()["flights"] if f["segment_count"] == 1)
    assert admin.get(f"/api/recordings/flights/{single['id']}/download").status_code == 200
    assert admin.get("/api/recordings/flights/unknown/playlist.m3u8").status_code == 404


def test_thumbnails_only_for_indexed_recordings(archive):
    admin = _client(ADMIN)
    flight = admin.get("/api/recordings?view=flights").json()["flights"][0]
    thumb = admin.get(f"/api/recordings/thumbnail?path={flight['thumbnail_path']}")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert admin.get("/api/recordings/thumbnail?path=../../etc/passwd").status_code == 404


def test_pages_and_json_are_compressed_but_video_is_not(archive):
    admin = _client(ADMIN)
    page = admin.get("/admin", headers={"Accept-Encoding": "gzip"})
    assert page.status_code == 200 and page.headers.get("content-encoding") == "gzip"
    listing = admin.get("/api/recordings?view=flights", headers={"Accept-Encoding": "gzip"})
    assert listing.headers.get("content-encoding") == "gzip"
    flight = listing.json()["flights"][0]
    download = admin.get(f"/api/recordings/flights/{flight['id']}/download", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in download.headers


def test_pages_and_scripts_are_revalidated_after_deploys(archive):
    admin = _client(ADMIN)
    assert admin.get("/admin").headers["cache-control"] == "no-cache"
    script = admin.get("/static/monitor/js/egdms-player.js")
    assert script.headers["cache-control"] == "no-cache" and script.headers.get("etag")
    again = admin.get("/static/monitor/js/egdms-player.js", headers={"If-None-Match": script.headers["etag"]})
    assert again.status_code == 304
