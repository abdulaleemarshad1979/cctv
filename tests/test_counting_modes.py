from unittest.mock import patch


with patch("subprocess.Popen"):
    import lite_server


def _camera():
    return lite_server.cameras_db[0]


def _stats(count=42, analytics_active=True, analytics_seq=None):
    return lite_server.StatsUpdate(
        drone_id="drone-1",
        density_score=count,
        comp_zone="WATCH",
        pressure=17.5,
        analytics_active=analytics_active,
        analytics_seq=analytics_seq,
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


def test_normal_connect_uses_real_input_and_separate_analyzed_output():
    camera = _camera()
    source = lite_server.get_default_source_for_camera(camera)
    assert source == "rtsp://127.0.0.1:8554/live/drone1"
    assert camera["source_stream_path"] == "live/drone1"
    assert camera["stream_path"] == "analyzed/drone1"

    second_camera = lite_server.cameras_db[1]
    assert second_camera["source_stream_path"] == "live/drone2"
    assert second_camera["stream_path"] == "analyzed/drone2"
    assert lite_server.get_default_source_for_camera(second_camera).endswith(
        "/live/drone2"
    )

    cctv = next(cam for cam in lite_server.cameras_db if cam["id"] == "cctv-1")
    assert cctv["source_stream_path"] == "live/cctv1"
    assert cctv["stream_path"] == "analyzed/cctv1"


def test_drone_publish_connection_has_no_authentication_credentials():
    camera = _camera()
    assert "publish_user" not in camera
    assert "publish_pass" not in camera
    assert lite_server.get_analyzed_publish_target(camera) == (
        "rtmp://127.0.0.1:1935/analyzed/drone1"
    )
    assert lite_server.find_camera_by_id("drone1") is camera

    with open("mediamtx.yml", encoding="utf-8") as config_file:
        media_config = config_file.read()
    assert "externalAuthenticationURL" not in media_config


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
    assert result["state"]["status"] == "online"
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


def test_duplicate_inference_does_not_refresh_or_replace_live_count():
    camera = _camera()
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))
    lite_server.update_stats(_stats(count=20, analytics_seq=7))
    first_timestamp = camera["stats_updated_at"]

    result = lite_server.update_stats(_stats(count=999, analytics_seq=7))

    assert result["status"] == "duplicate"
    assert camera["people_count"] == 20
    assert camera["stats_updated_at"] == first_timestamp


def test_stale_inference_is_not_presented_as_live_data():
    camera = _camera()
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))
    lite_server.update_stats(_stats(count=31, analytics_seq=8))
    camera["stats_updated_at"] -= lite_server.ANALYTICS_STALE_AFTER_SECONDS + 1

    lite_server.refresh_camera_health()

    assert camera["analytics_status"] == "stale"
    assert camera["people_count"] == 0
    assert camera["zone_scores"] is None
