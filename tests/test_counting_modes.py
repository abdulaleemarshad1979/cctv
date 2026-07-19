from unittest.mock import patch


with patch("subprocess.Popen"):
    import lite_server


def _camera():
    return lite_server.cameras_db[0]


def _stats(count=42, analytics_active=True):
    return lite_server.StatsUpdate(
        drone_id="drone-1",
        density_score=count,
        comp_zone="WATCH",
        pressure=17.5,
        analytics_active=analytics_active,
    )


def test_viewing_mode_clears_and_rejects_analytics():
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))
    lite_server.update_stats(_stats())
    assert _camera()["people_count"] == 42

    lite_server.set_mode(lite_server.ModeRequest(mode="viewing"))
    assert _camera()["people_count"] == 0
    assert _camera()["zone_scores"] is None

    result = lite_server.update_stats(_stats(count=99))
    assert result["counting_mode"] is False
    assert _camera()["people_count"] == 0


def test_counting_mode_requires_a_fresh_active_result():
    lite_server.set_mode(lite_server.ModeRequest(mode="viewing"))
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))

    lite_server.update_stats(_stats(count=99, analytics_active=False))
    assert _camera()["people_count"] == 0

    lite_server.update_stats(_stats(count=37.6, analytics_active=True))
    assert _camera()["people_count"] == 38


def test_drone1_uses_only_the_real_stream_without_demo_footage():
    camera = _camera()
    assert camera["fallback_video"] is None
    source = lite_server.get_default_source_for_camera(camera)
    assert source == "rtsp://127.0.0.1:8554/live/drone1"
    assert camera["source_stream_path"] == "live/drone1"
    assert camera["stream_path"] == "analyzed/drone1"


def test_raw_drone1_publish_starts_analyzer_on_separate_output_path():
    camera = _camera()
    lite_server.running_processes.pop(camera["id"], None)
    camera["source_online"] = False
    camera["output_online"] = False

    with patch.object(lite_server, "start_stream", return_value=1234) as start:
        result = lite_server.update_camera_state(
            lite_server.StateRequest(path="live/drone1", status="online")
        )

    start.assert_called_once_with(camera, "rtsp://127.0.0.1:8554/live/drone1")
    assert result["state"]["status"] == "connecting"
    assert camera["source_online"] is True
    assert camera["analytics_status"] == "starting"

    lite_server.update_camera_state(
        lite_server.StateRequest(path="analyzed/drone1", status="online")
    )
    assert camera["status"] == "online"
    assert camera["output_online"] is True

    lite_server.update_stats(_stats(count=21, analytics_active=True))
    assert camera["analytics_status"] == "active"
    assert camera["people_count"] == 21
