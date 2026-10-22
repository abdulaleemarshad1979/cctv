"""Server behaviour for the reconnect grace period, raw playback and diagnostics."""

import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

with patch("subprocess.Popen"):
    import lite_server


ADMIN = ("admin", "review_admin_password_2026")
VIEWER = ("viewer", "review_viewer_password_2026")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("VIEWER_USERNAME", VIEWER[0])
    monkeypatch.setenv("VIEWER_PASSWORD", VIEWER[1])
    monkeypatch.setenv("SECRET_KEY", "review_only_secret_key_0123456789abcdef")
    monkeypatch.setattr(lite_server, "stream_events",
                        lite_server.StreamEventLog(str(tmp_path / "events.jsonl")))
    monkeypatch.setattr(lite_server, "get_worker_dependency_error", lambda: None)
    monkeypatch.setattr(lite_server, "start_stream", lambda camera, url: 1234)
    lite_server.global_counting_mode = True
    lite_server.load_cameras()
    yield
    lite_server.load_cameras()


def _camera():
    return lite_server.cameras_db[0]


def _state(path, status):
    return lite_server.update_camera_state(lite_server.StateRequest(path=path, status=status))


def _client(credentials):
    client = TestClient(lite_server.app)
    response = client.post("/login", json={"username": credentials[0], "password": credentials[1]})
    assert response.status_code == 200, response.text
    return client


def test_dropped_source_enters_grace_then_expires(monkeypatch):
    monkeypatch.setattr(lite_server, "STREAM_GRACE_SECONDS", 20.0)
    _state("live/drone1", "online")
    assert _camera()["status"] == "online"

    _state("live/drone1", "offline")
    cam = _camera()
    assert cam["status"] == "reconnecting"
    assert cam["source_online"] is False
    assert "reconnecting" in cam["connection_message"].lower()

    lite_server.expire_stream_grace()
    assert cam["status"] == "reconnecting"          # still inside 20 s

    cam["reconnecting_since"] -= 21
    lite_server.expire_stream_grace()
    assert cam["status"] == "connecting"
    assert "reconnecting_since" not in cam
    assert lite_server.stream_events.recent("drone-1")[-1]["event"] == "grace_expired"


def test_source_returning_inside_grace_is_logged_and_back_online(monkeypatch):
    monkeypatch.setattr(lite_server, "STREAM_GRACE_SECONDS", 20.0)
    _state("live/drone1", "online")
    _state("live/drone1", "offline")
    _state("live/drone1", "online")
    cam = _camera()
    assert cam["status"] == "online" and cam["source_online"] is True
    assert "reconnecting_since" not in cam
    assert lite_server.stream_events.recent("drone-1")[-1]["event"] == "recovered_within_grace"


def test_analyzer_output_dropping_does_not_cancel_grace(monkeypatch):
    monkeypatch.setattr(lite_server, "STREAM_GRACE_SECONDS", 20.0)
    _state("live/drone1", "online")
    _state("analyzed/drone1", "online")
    _state("live/drone1", "offline")
    _state("analyzed/drone1", "offline")
    assert _camera()["status"] == "reconnecting"


def test_grace_disabled_restores_previous_behaviour(monkeypatch):
    monkeypatch.setattr(lite_server, "STREAM_GRACE_SECONDS", 0.0)
    _state("live/drone1", "online")
    _state("live/drone1", "offline")
    assert _camera()["status"] == "connecting"


def test_never_online_camera_does_not_enter_grace(monkeypatch):
    monkeypatch.setattr(lite_server, "STREAM_GRACE_SECONDS", 20.0)
    _state("live/drone2", "offline")
    assert lite_server.cameras_db[1]["status"] != "reconnecting"


def test_playback_stays_on_raw_feed_when_analyzer_runs():
    _state("live/drone1", "online")
    _state("analyzed/drone1", "online")
    assert _camera()["playback_stream_path"] == "live/drone1"


def test_cameras_payload_carries_player_settings():
    viewer = _client(VIEWER)
    cams = viewer.get("/cameras").json()
    assert cams[0]["stream_grace_seconds"] == lite_server.STREAM_GRACE_SECONDS
    assert cams[0]["player_latency_target_seconds"] == lite_server.PLAYER_LATENCY_TARGET_SECONDS
    assert "abr_available" in cams[0]


def test_diagnostics_and_continuity_are_admin_only():
    assert TestClient(lite_server.app).get("/api/stream-diagnostics").status_code == 401
    viewer = _client(VIEWER)
    assert viewer.get("/api/stream-diagnostics").status_code == 403
    assert viewer.get("/api/recordings/continuity").status_code == 403
    assert viewer.get("/admin/diagnostics", follow_redirects=False).status_code == 403
    admin = _client(ADMIN)
    body = admin.get("/api/stream-diagnostics?hours=1").json()
    assert body["settings"]["stream_grace_seconds"] == lite_server.STREAM_GRACE_SECONDS
    assert {"server", "disk", "abr", "cameras", "timeline"} <= set(body)
    assert admin.get("/api/recordings/continuity?hours=1").status_code == 200
    assert admin.get("/admin/diagnostics").status_code == 200


def test_browser_events_require_login_validate_and_rate_limit():
    assert TestClient(lite_server.app).post("/api/stream-events", json={"events": []}).status_code == 401
    viewer = _client(VIEWER)
    ok = viewer.post("/api/stream-events", json={"events": [
        {"camera_id": "drone-1", "event": "stall", "detail": "no progress", "transport": "hls"},
        {"camera_id": "drone-1", "event": "rm -rf", "detail": "unknown type"},
        {"camera_id": "nope-9", "event": "stall"},
        {"camera_id": "drone-1", "event": "latency_sample", "latency_seconds": 4.8, "level_height": 480},
    ]})
    assert ok.json() == {"accepted": 1}
    logged = lite_server.stream_events.recent("drone-1")[-1]
    assert (logged["category"], logged["event"], logged["viewer"]) == ("browser", "stall", "viewer")
    lite_server._browser_event_times.clear()
    accepted = sum(viewer.post("/api/stream-events", json={"events": [
        {"camera_id": "drone-1", "event": "stall"}] * 20}).json()["accepted"] for _ in range(2))
    assert accepted == 20


def test_abr_requires_login_and_falls_back_when_source_offline():
    assert TestClient(lite_server.app).get("/abr/drone-1/master.m3u8").status_code == 401
    viewer = _client(VIEWER)
    response = viewer.get("/abr/drone-1/master.m3u8")
    assert response.status_code == 503
    assert response.json()["fallback"] == "/hls/live/drone1/index.m3u8"
    assert viewer.get("/abr/drone-1/480p/..%2f..%2fetc%2fpasswd").status_code == 404


def test_master_playlist_passes_the_full_screen_priority_flag_and_runs_off_the_event_loop(monkeypatch, tmp_path):
    playlist = tmp_path / "master.m3u8"
    playlist.write_text("#EXTM3U\n")
    calls = []

    class FakeAbr:
        enabled = True

        def ensure(self, camera_id, source_path, height, bitrate, priority=False):
            import asyncio
            try:
                asyncio.get_running_loop()      # only succeeds on the event-loop thread
                on_loop = True
            except RuntimeError:
                on_loop = False
            calls.append((camera_id, priority, on_loop))

        def is_ready(self, camera_id):
            return True

        def touch(self, camera_id):
            pass

        def resolve_file(self, camera_id, parts):
            return str(playlist)

    monkeypatch.setattr(lite_server, "abr_manager", FakeAbr())
    _state("live/drone1", "online")
    viewer = _client(VIEWER)

    assert viewer.get("/abr/drone-1/master.m3u8").status_code == 200
    assert viewer.get("/abr/drone-1/master.m3u8?priority=1").status_code == 200
    assert [c[1] for c in calls] == [False, True]
    assert not any(c[2] for c in calls), "ensure() must not block the event-loop thread"
