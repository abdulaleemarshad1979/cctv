"""
Behavioral tests for live-video playback controller:
- Paused video with advancing currentTime (live-edge seek bug)
- Failed readiness probe followed by retry on subsequent polls
- Superseded switch probe cancellation via AbortController and sequence tokens
- FastAPI /webrtc WHEP proxying, methods, and Location header rewriting
"""

import json
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lite_server


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_watchdog_detects_stall_when_video_paused_with_advancing_current_time():
    """Verify that watchdog logic catches paused video even if currentTime advances.

    hls.js can advance currentTime through live-edge seeking while the video remains paused.
    The watchdog must check if video.paused is true BEFORE checking currentTime progress,
    and must attempt safePlay recovery instead of treating the tick as healthy progress.
    """
    admin_html = _read("admin_dashboard.html")
    lite_html = _read("lite_dashboard.html")

    for html in [admin_html, lite_html]:
        # Locate watchdog block
        assert "playerWatchdogTimers[cam.id] = setInterval(" in html
        watchdog_code = html.split("playerWatchdogTimers[cam.id] = setInterval(")[1].split("}, 2000);")[0]

        # Ensure video.paused is handled and attempts safePlay
        assert "if (video.paused) {" in watchdog_code
        assert "safePlay(video);" in watchdog_code

        # Ensure currentTime check requires video not to be paused or seeking
        assert "!video.seeking" in watchdog_code
        assert "video.readyState >= 2" in watchdog_code
        assert "video.currentTime > egdmsLastTime" in watchdog_code

        # Verify pause handler checks for user pause
        assert "video._userPaused" in watchdog_code


def test_poll_cameras_reconciles_active_path_against_desired_path():
    """Verify that pollCameras retries unfinished switches.

    If a readiness probe failed on a previous poll, metadata remains unchanged on subsequent
    polls. The dashboard must reconcile activePath !== desiredPath on each poll.
    """
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        html = _read(name)
        poll_code = html.split("async function pollCameras()")[1].split("finally {")[0]

        assert "const desiredPath = cameraPlaybackPath(newCam);" in poll_code
        assert "const activePlayer = hlsPlayers[newCam.id];" in poll_code
        assert "!playerUsesPath(activePlayer, desiredPath)" in poll_code
        assert "triggerSeamlessStreamSwitch(newCam, desiredPath);" in poll_code


def test_superseded_probe_cancellation_and_stale_token_rejection():
    """Verify that obsolete probes are aborted and stale probe responses are discarded."""
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        html = _read(name)
        switch_fn = html.split("function triggerSeamlessStreamSwitch(")[1].split("function teardownVideoFeed(")[0]

        # Cancellation of previous in-flight probe
        assert "probesInFlight[cam.id].controller.abort()" in switch_fn
        # Token increment and check
        assert "probeTokens[cam.id]" in switch_fn
        assert "if (probeTokens[cam.id] !== token) return;" in switch_fn


def test_fastapi_webrtc_proxy_methods_and_location_rewrite():
    """Verify that FastAPI /webrtc route handles all WHEP methods and rewrites Location headers."""
    client = TestClient(lite_server.app)

    # Test OPTIONS method for WHEP capability negotiation
    options_res = client.options("/webrtc/live/drone1/whep")
    # Even if upstream MediaMTX is not running in test env, route must not return 405 Method Not Allowed
    assert options_res.status_code != 405, f"OPTIONS method should be supported, got {options_res.status_code}"

    # Test POST method (used for initial WHEP SDP exchange)
    post_res = client.post(
        "/webrtc/live/drone1/whep",
        content="v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n",
        headers={"Content-Type": "application/sdp"},
    )
    assert post_res.status_code != 405, f"POST method should be supported, got {post_res.status_code}"

    # Test PATCH method (used for ICE trickle candidates)
    patch_res = client.patch(
        "/webrtc/live/drone1/whep",
        content="a=candidate:...",
        headers={"Content-Type": "application/trickle-ice-sdpfrag"},
    )
    assert patch_res.status_code != 405, f"PATCH method should be supported, got {patch_res.status_code}"

    # Test DELETE method (used for terminating WHEP session)
    del_res = client.delete("/webrtc/live/drone1/whep")
    assert del_res.status_code != 405, f"DELETE method should be supported, got {del_res.status_code}"

    # Test GET and HEAD methods
    get_res = client.get("/webrtc/live/drone1/")
    assert get_res.status_code != 405, f"GET method should be supported, got {get_res.status_code}"

    head_res = client.head("/webrtc/live/drone1/")
    assert head_res.status_code != 405, f"HEAD method should be supported, got {head_res.status_code}"


def test_fastapi_hls_proxy_supports_get_and_head():
    """Verify that FastAPI /hls route supports GET and HEAD."""
    client = TestClient(lite_server.app)

    get_res = client.get("/hls/live/drone1/index.m3u8", follow_redirects=False)
    assert get_res.status_code == 307
    assert "/live/drone1/index.m3u8" in get_res.headers["location"]

    head_res = client.head("/hls/live/drone1/index.m3u8", follow_redirects=False)
    assert head_res.status_code == 307
    assert "/live/drone1/index.m3u8" in head_res.headers["location"]


def test_dashboards_default_to_hls_and_have_latency_catchup():
    """HLS (adaptive when available) is the default; ?transport=webrtc opts in.

    Latency is bounded by the shared hls.js config (live-sync target ~5 s,
    hard ceiling target + 7 s) plus the watchdog/visibility catch-up jumps.
    """
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert 'let useWebRtc = urlParams.get("transport") === "webrtc";' in content, (
            f"HLS must be the default transport (WebRTC only with transport=webrtc) in {name}"
        )
        assert "targetLatency: cam.player_latency_target_seconds || 5" in content
        # The watchdog holds the target delay by adjusting speed; it only
        # jumps when far behind, so short hiccups never skip video.
        assert "EGDMSPlayer.keepLiveDelay(hlsPlayers[cam.id], video);" in content, (
            f"Watchdog live-delay control missing in {name}"
        )
        assert "player.liveSyncPosition - video.currentTime > 3.0" in content, (
            f"Visibilitychange live-sync catchup missing in {name}"
        )


def test_webrtc_additional_hosts_includes_localhost_and_loopback():
    """Verify that MediaMTX configs include 127.0.0.1 and localhost for low-latency local WebRTC."""
    for path in ["mediamtx.yml", "deployment/mediamtx.yml"]:
        content = _read(path)
        assert "- 127.0.0.1" in content, f"127.0.0.1 missing in {path}"
        assert "- localhost" in content, f"localhost missing in {path}"

    server_code = _read("lite_server.py")
    assert "- 127.0.0.1" in server_code
    assert "- localhost" in server_code


def test_gen_rtmp_scripts_enforce_short_gop_for_low_latency():
    """Verify that RTMP stream generator enforces 1s keyframes rather than copying long GOP."""
    content = _read("tools/gen_rtmp.py")
    assert "-g" in content and "30" in content, "gen_rtmp.py must enforce short keyframe interval"
    assert "ultrafast" in content and "zerolatency" in content



def test_live_players_only_run_for_tiles_on_screen():
    player = _read("static/monitor/js/egdms-player.js")
    assert "P.LiveTileGate = class" in player
    assert "new IntersectionObserver" in player
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "new EGDMSPlayer.LiveTileGate(" in content, name
        assert "liveTileGate.observe(card);" in content, name
        assert "liveTileGate.unobserve(card);" in content, name
        assert "if (!liveTileWanted(cam.id)) return;" in content, name


def test_recordings_archive_does_not_create_a_video_per_card():
    admin = _read("admin_dashboard.html")
    assert 'view: "flights"' in admin
    assert 'loading="lazy"' in admin
    # Pages come from the server; a flight plays as one HLS video.
    assert "renderRecordingsPager(data.page, data.total_pages)" in admin
    assert "/playlist.m3u8" in admin
    assert '<video src="${rec.video_url}' not in admin
    # A blurred backdrop over moving video made the player controls flicker.
    assert "backdrop-filter: blur(8px)" not in admin


def test_switching_admin_tabs_does_not_restart_live_video():
    admin = _read("admin_dashboard.html")
    assert "const HUB_LEAVE_GRACE_MS = 60000;" in admin
    assert "tilesShownWhenHubLeft.has(camId)" in admin
    # A hidden Active Hub may pause video; the watchdog must not reconnect it.
    assert 'currentDashboardSection !== "active-hub" && activeFullViewCamId !== cam.id' in admin


def test_live_delay_control_only_uses_buffered_video():
    player = _read("static/monitor/js/egdms-player.js")
    assert "P.keepLiveDelay = function (hls, video)" in player
    assert "behind > 3 && ahead > 3" in player
    assert "if (ahead < 1) {" in player


def test_manual_quality_falls_back_to_auto_and_is_not_kept_forever():
    player = _read("static/monitor/js/egdms-player.js")
    assert "window.sessionStorage.getItem(prefKey(camId))" in player
    assert "window.localStorage.getItem(prefKey" not in player
    assert 'apply("auto", true);' in player
    assert "Slow network — switched to Auto quality" in player


def test_player_script_url_changes_whenever_the_script_changes():
    """The site is behind a CDN that caches /static for hours.  A page that
    needs a newer egdms-player.js than the cached one stopped before drawing
    any tile, so the script URL carries its content hash."""
    import hashlib
    version = hashlib.sha1((ROOT / "static/monitor/js/egdms-player.js").read_bytes()).hexdigest()[:10]
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert f'<script src="/static/monitor/js/egdms-player.js?v={version}"></script>' in content, (
            f"{name}: update ?v= to {version} (sha1 of egdms-player.js, first 10 hex digits)"
        )
        # Pages still work if an older cached script turns up anyway.
        assert "EGDMSPlayer.LiveTileGate\n      ? new EGDMSPlayer.LiveTileGate(" in content
        assert "if (EGDMSPlayer.keepLiveDelay) EGDMSPlayer.keepLiveDelay(" in content


def test_tile_gate_fails_open_without_visibility_reports():
    player = _read("static/monitor/js/egdms-player.js")
    assert "this.failOpen = true;" in player
    assert "return !this.observer || this.failOpen || this.visible.has(id);" in player
