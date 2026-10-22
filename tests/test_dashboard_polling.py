"""What every dashboard polls each second, and what the dashboards show."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

with patch("subprocess.Popen"):
    import lite_server

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["admin_dashboard.html", "lite_dashboard.html"]


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def _stats(seq, count=10):
    return lite_server.StatsUpdate(
        drone_id="drone-1", density_score=count, comp_zone="SAFE",
        analytics_active=True, analytics_seq=seq,
    )


@pytest.fixture
def camera():
    lite_server.global_counting_mode = True
    lite_server.load_cameras()
    cam = lite_server.cameras_db[0]
    cam["enabled"] = True
    yield cam
    lite_server.global_counting_mode = True
    lite_server.load_cameras()


# ── server ─────────────────────────────────────────────────────────────────

def test_cameras_poll_carries_the_mode_so_every_dashboard_follows_a_switch(camera):
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))
    assert lite_server.get_cameras().headers["X-Counting-Mode"] == "1"
    lite_server.update_stats(_stats(1, count=12))
    assert camera["people_count"] == 12

    lite_server.set_mode(lite_server.ModeRequest(mode="viewing"))
    assert lite_server.get_cameras().headers["X-Counting-Mode"] == "0"
    assert camera["people_count"] == 0                     # Viewing mode carries no counts
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))


def test_cameras_payload_carries_no_forecast_for_cameras_without_a_live_count(camera):
    lite_server.update_stats(_stats(1))
    body = json.loads(lite_server.get_cameras().body)
    live = next(c for c in body if c["id"] == "drone-1")
    idle = next(c for c in body if c["id"] == "drone-2")
    assert live["forecast"]["status"] in ("collecting", "ready")
    assert idle["forecast"] is None


def test_there_are_no_head_dots_anywhere(camera):
    # An analyzer from before the removal may still send head positions: the
    # update is accepted and the positions are dropped, never stored or served.
    old = lite_server.StatsUpdate(
        drone_id="drone-1", density_score=3, comp_zone="SAFE", analytics_active=True,
        analytics_seq=1, heads_b64="AAAAAA==", heads_n=1, heads_ts=1.0,
    )
    assert not hasattr(old, "heads_b64")
    lite_server.update_stats(old)
    live = next(c for c in json.loads(lite_server.get_cameras().body) if c["id"] == "drone-1")
    assert not any(key.startswith("heads") for key in live)
    assert not any("/heads" in getattr(route, "path", "") for route in lite_server.app.routes)

    assert not (ROOT / "static/monitor/js/egdms-dots.js").exists()
    for page in PAGES:
        html = _text(page)
        for needle in ("egdms-dots", "EGDMSDots", "headDots", "btn-head-dots"):
            assert needle not in html, (page, needle)
    infer = _text("infer.py")
    for needle in ("heads_b64", "HeadDotLayer", "locate_heads", "pack_head_points"):
        assert needle not in infer, needle


# ── dashboards ─────────────────────────────────────────────────────────────

def test_dashboards_explain_waiting_states_and_skip_unchanged_tiles():
    for page in PAGES:
        html = _text(page)
        # every analyzer state has its own wording; the reason is visible on the tile
        for state in ("queued", "restarting", "error", "stale"):
            assert f'case "{state}"' in html, (page, state)
        assert "Counting queued" in html and 'id="note-${cam.id}"' in html
        # unchanged tiles are not re-rendered on every one-second poll
        assert "oldCam.analytics_seq !== newCam.analytics_seq" in html


def test_dashboards_follow_a_mode_switch_made_on_another_screen():
    for page in PAGES:
        html = _text(page)
        assert "function applyGlobalMode(mode)" in html, page
        # followed from the regular poll, but a poll sent before this screen's
        # own switch cannot undo it
        assert 'res.headers.get("X-Counting-Mode")' in html, page
        assert "pollStartedAt > modeChangedAt" in html, page


def test_site_is_named_drone_monitoring_portal():
    for page in PAGES + ["login.html", "stream_diagnostics.html"]:
        assert "Drone Monitoring Portal" in _text(page), page
        assert "East Godavari Drone Monitoring System" not in _text(page), page
    assert lite_server.app.title == "Drone Monitoring Portal"
