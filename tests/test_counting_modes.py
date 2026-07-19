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
