"""Live publishers keep raw video when CPU counting slots are full."""

import lite_server


class RunningProcess:
    def poll(self):
        return None


def camera(number):
    return {
        "id": f"drone-{number}",
        "enabled": True,
        "source_online": True,
        "output_online": False,
        "source_stream_path": f"live/drone{number}",
        "stream_path": f"analyzed/drone{number}",
        "analytics_status": "waiting_for_source",
    }


def test_live_video_stays_available_and_queued_analysis_starts_when_slot_opens(monkeypatch):
    first, second = camera(1), camera(2)
    processes = {first["id"]: RunningProcess()}
    starts = []

    def fake_start_stream(cam, source_url):
        starts.append((cam["id"], source_url))
        processes[cam["id"]] = RunningProcess()
        return 123

    monkeypatch.setattr(lite_server, "cameras_db", [first, second])
    monkeypatch.setattr(lite_server, "running_processes", processes)
    monkeypatch.setattr(lite_server, "global_counting_mode", True)
    monkeypatch.setattr(lite_server, "get_worker_dependency_error", lambda: None)
    monkeypatch.setattr(lite_server, "start_stream", fake_start_stream)
    monkeypatch.setenv("MAX_CONCURRENT_AI_FEEDS", "1")

    lite_server.start_live_analysis(second)
    assert starts == []
    assert second["status"] == "online"
    assert second["analytics_status"] == "capacity"
    assert second["playback_stream_path"] == "live/drone2"

    processes.pop(first["id"])
    lite_server.refresh_camera_health()
    assert starts == [(second["id"], "rtsp://%s:%s/live/drone2" % (
        lite_server.MEDIAMTX_HOST, lite_server.MEDIAMTX_RTSP_PORT
    ))]
    assert second["analytics_status"] == "starting"
