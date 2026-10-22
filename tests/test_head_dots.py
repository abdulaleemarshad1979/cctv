import base64
import struct
from unittest.mock import patch

import numpy as np
import pytest

from src.counting_accuracy import (
    MAX_PUBLISHED_HEADS,
    density_head_points,
    pack_head_points,
    unpack_head_points,
)

with patch("subprocess.Popen"):
    import lite_server


# ── payload ────────────────────────────────────────────────────────────────

def test_pack_round_trips_within_one_part_in_65535():
    points = np.array([[0, 0], [1279, 719], [640, 360], [12.5, 700.25]], dtype=np.float32)
    data, count = pack_head_points(points, (720, 1280))
    assert count == 4 and len(data) == 16            # 4 bytes per head
    restored = unpack_head_points(data) * [1280, 720]
    assert np.abs(restored - points).max() < 0.02


def test_pack_clips_outside_points_and_thins_huge_crowds_evenly():
    data, count = pack_head_points([[-50, 9999]], (100, 100))
    assert unpack_head_points(data).tolist() == [[0.0, 1.0]]

    many = np.column_stack((np.arange(20000), np.arange(20000))).astype(np.float32)
    data, count = pack_head_points(many, (20000, 20000))
    assert count == MAX_PUBLISHED_HEADS
    xs = unpack_head_points(data)[:, 0]
    assert xs[0] == 0.0 and xs[-1] == pytest.approx(1.0, abs=1e-4)
    assert np.all(np.diff(xs) > 0)                   # even thinning keeps order


def test_empty_payload_and_bad_length():
    assert pack_head_points(np.empty((0, 2)), (10, 10)) == (b"", 0)
    assert unpack_head_points(b"").shape == (0, 2)
    with pytest.raises(ValueError):
        unpack_head_points(b"abc")


def test_peak_refinement_places_dot_inside_the_cell_toward_the_mass():
    # A head whose mass is split 3:1 between two neighbouring cells: the peak
    # cell's dot must sit toward the heavier side, not at the cell centre.
    dmap = np.zeros((10, 10), dtype=np.float32)
    dmap[5, 5] = 0.75
    dmap[5, 6] = 0.25
    (x, y), = density_head_points(dmap, 1, (100, 100))
    assert 55.0 < x < 60.0                            # cell 5 spans 50..60, centre is 55
    assert y == pytest.approx(55.0, abs=0.01)


# ── server ─────────────────────────────────────────────────────────────────

def _stats(seq, heads, ts, count=None, **extra):
    raw = np.asarray(heads, dtype="<u2").tobytes()
    return lite_server.StatsUpdate(
        drone_id="drone-1", density_score=count if count is not None else len(raw) // 4,
        comp_zone="SAFE", analytics_active=True, analytics_seq=seq,
        heads_b64=base64.b64encode(raw).decode(), heads_n=len(raw) // 4, heads_ts=ts, **extra,
    )


@pytest.fixture
def camera():
    lite_server.global_counting_mode = True
    lite_server.load_cameras()
    cam = lite_server.cameras_db[0]
    cam["enabled"] = True
    lite_server._head_store.clear()
    yield cam
    lite_server._head_store.clear()
    lite_server.load_cameras()


def _records(body):
    out, offset = [], 0
    while offset < len(body):
        seq, count, ts = struct.unpack_from("<IId", body, offset)
        offset += 16
        out.append((seq, count, ts, np.frombuffer(body, "<u2", count * 2, offset).reshape(-1, 2)))
        offset += count * 4
    return out


def test_heads_endpoint_serves_recent_results_oldest_first_with_capture_times(camera):
    base = lite_server.time.time() - 5.0
    lite_server.update_stats(_stats(1, [[100, 200]], base))
    lite_server.update_stats(_stats(2, [[300, 400], [500, 600]], base + 1.8))

    response = lite_server.get_camera_heads("drone-1")
    assert response.media_type == "application/octet-stream"
    records = _records(response.body)
    assert [(r[0], r[1]) for r in records] == [(1, 1), (2, 2)]
    assert records[0][2] == pytest.approx(base) and records[1][2] == pytest.approx(base + 1.8)
    assert records[1][3].tolist() == [[300, 400], [500, 600]]
    assert camera["heads_seq"] == 2 and camera["heads_count"] == 2


def test_heads_endpoint_since_unchanged_and_analyzer_restart(camera):
    now = lite_server.time.time()
    for seq in (7, 8, 9):
        lite_server.update_stats(_stats(seq, [[seq, seq]], now - 10 + seq))

    assert [r[0] for r in _records(lite_server.get_camera_heads("drone-1", since=8).body)] == [9]
    assert lite_server.get_camera_heads("drone-1", since=9).status_code == 304
    # The analyzer restarted (its seq is back to small numbers): resend everything.
    assert [r[0] for r in _records(lite_server.get_camera_heads("drone-1", since=500).body)] == [7, 8, 9]


def test_heads_are_hidden_when_counting_is_not_live(camera):
    lite_server.update_stats(_stats(1, [[1, 1]], lite_server.time.time()))
    camera["analytics_status"] = "stale"
    assert lite_server.get_camera_heads("drone-1").status_code == 204
    camera["analytics_status"] = "active"
    lite_server.global_counting_mode = False
    assert lite_server.get_camera_heads("drone-1").status_code == 204
    with pytest.raises(lite_server.HTTPException):
        lite_server.get_camera_heads("nope")


def test_cameras_poll_carries_the_mode_so_every_dashboard_follows_a_switch(camera):
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))
    assert lite_server.get_cameras().headers["X-Counting-Mode"] == "1"
    lite_server.update_stats(_stats(1, [[1, 1]], lite_server.time.time()))
    assert camera["heads_count"] == 1

    lite_server.set_mode(lite_server.ModeRequest(mode="viewing"))
    assert lite_server.get_cameras().headers["X-Counting-Mode"] == "0"
    # Viewing mode carries no dots at all: none listed, none served.
    assert camera["heads_count"] == 0
    assert lite_server.get_camera_heads("drone-1").status_code == 204
    lite_server.set_mode(lite_server.ModeRequest(mode="counting"))


def test_viewing_mode_and_reset_drop_the_dots(camera):
    lite_server.update_stats(_stats(1, [[1, 1]], lite_server.time.time()))
    assert camera["heads_count"] == 1
    lite_server.reset_camera_analytics(camera)
    assert "drone-1" not in lite_server._head_store
    assert camera["heads_count"] == 0 and camera["heads_seq"] == -1


def test_malformed_head_payloads_are_ignored_not_stored(camera):
    bad_base64 = _stats(1, [[1, 1]], 8000.0)
    bad_base64.heads_b64 = "not base64!!"
    ragged = _stats(2, [[1, 1]], 8000.0)
    ragged.heads_b64 = base64.b64encode(b"123456").decode()          # 6 bytes
    oversized = _stats(3, [[1, 1]], 8000.0)
    oversized.heads_b64 = base64.b64encode(bytes(lite_server.MAX_HEAD_PAYLOAD_BYTES + 4)).decode()
    for update in (bad_base64, ragged, oversized):
        lite_server.update_stats(update)
    assert "drone-1" not in lite_server._head_store


def test_head_history_is_bounded_in_time_and_count(camera, monkeypatch):
    monkeypatch.setattr(lite_server.time, "time", lambda: 10_000.0)
    for seq in range(1, 60):
        lite_server.update_stats(_stats(seq, [[1, 1]], 10_000.0 - 100 + seq))
    history = lite_server._head_store["drone-1"]
    assert len(history) <= lite_server.HEAD_HISTORY_MAX_RECORDS
    lite_server.update_stats(_stats(60, [[1, 1]], 10_000.0))
    assert all(10_000.0 - r[1] <= lite_server.HEAD_HISTORY_SECONDS + 1 for r in history)


def test_cameras_payload_carries_no_forecast_for_cameras_without_a_live_count(camera):
    import json

    lite_server.update_stats(_stats(1, [[1, 1]], lite_server.time.time()))
    body = json.loads(lite_server.get_cameras().body)
    live = next(c for c in body if c["id"] == "drone-1")
    idle = next(c for c in body if c["id"] == "drone-2")
    assert live["forecast"]["status"] in ("collecting", "ready")
    assert idle["forecast"] is None
    assert live["heads_seq"] == 1 and "heads_sync_offset_ms" in live
    assert "heads_b64" not in live


# ── dashboards ─────────────────────────────────────────────────────────────

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["admin_dashboard.html", "lite_dashboard.html"]


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_dots_script_url_carries_its_content_hash():
    """/static is cached by a CDN for hours: a changed script needs a new URL.
    Fix with ``python tools/refresh_asset_versions.py``."""
    version = hashlib.sha1((ROOT / "static/monitor/js/egdms-dots.js").read_bytes()).hexdigest()[:10]
    for page in PAGES:
        assert f'<script src="/static/monitor/js/egdms-dots.js?v={version}"></script>' in _text(page), page


def test_dashboards_draw_dots_over_the_raw_video_and_explain_waiting_states():
    for page in PAGES:
        html = _text(page)
        assert "new EGDMSDots.Manager(" in html
        assert "headDots.sync(newCam)" in html
        # a player restart that holds the last frame must not delete the dots
        assert 'child.classList.contains("egdms-dots")' in html
        # dots are matched to the frame on screen, not to "now"
        assert "player.playingDate" in html
        # every analyzer state has its own wording; the reason is visible on the tile
        for state in ("queued", "restarting", "error", "stale"):
            assert f'case "{state}"' in html, (page, state)
        assert "Counting queued" in html and 'id="note-${cam.id}"' in html
        # unchanged tiles are not re-rendered on every one-second poll
        assert "oldCam.analytics_seq !== newCam.analytics_seq" in html


def test_dots_belong_to_counting_mode_and_have_no_switch():
    for page in PAGES:
        html = _text(page)
        assert "btn-head-dots" not in html and "bindToggle" not in html and "readEnabled" not in html, page
        assert 'wanted: cam => globalMode === "counting"' in html, page
        # a mode switch shows/drops the dots at once, not on the next poll
        apply = html[html.index("function applyGlobalMode(mode)"):]
        apply = apply[:apply.index("\n    }\n")]
        assert "headDots.sync(cam)" in apply, page
        # a switch made on another screen is followed from the regular poll,
        # but a poll sent before this screen's own switch cannot undo it
        assert 'res.headers.get("X-Counting-Mode")' in html, page
        assert "pollStartedAt > modeChangedAt" in html, page
    js = _text("static/monitor/js/egdms-dots.js")
    assert "egdms-dots-toggle" not in js and "bindToggle" not in js and "setEnabled" not in js
    assert 'localStorage.removeItem("egdms.headDots")' in js      # an old "off" choice cannot hide them
    # hidden dots free their canvas, observer and request; the timer stops when idle
    assert "hide(entry)" in js and "entry.layer.destroy()" in js and "this.stopTicking()" in js
    assert "heads_sync_offset_ms" in js


def test_dots_script_matches_dots_to_frames_and_handles_object_fit():
    js = _text("static/monitor/js/egdms-dots.js")
    for needle in (
        "D.contentRect", "D.pickRecord", "D.decodeRecords",
        'fit === "cover"',                       # the tile video is object-fit: cover
        "/heads${since}",                         # incremental fetch
        "pointer-events:none",                    # never block the video controls
    ):
        assert needle in js, needle


def test_site_is_named_drone_monitoring_portal():
    for page in PAGES + ["login.html", "stream_diagnostics.html"]:
        assert "Drone Monitoring Portal" in _text(page), page
        assert "East Godavari Drone Monitoring System" not in _text(page), page
    assert lite_server.app.title == "Drone Monitoring Portal"
