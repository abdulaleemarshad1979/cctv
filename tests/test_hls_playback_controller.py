from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_mediamtx_configs_have_exact_hls_low_latency_settings():
    for rel_path in ["mediamtx.yml", "deployment/mediamtx.yml"]:
        content = _read(rel_path)
        assert "hls: yes" in content, f"hls: yes missing in {rel_path}"
        assert "hlsVariant: lowLatency" in content, f"hlsVariant: lowLatency missing in {rel_path}"
        assert "hlsSegmentCount: 3" in content, f"hlsSegmentCount: 3 missing in {rel_path}"
        assert "hlsSegmentDuration: 1s" in content, f"hlsSegmentDuration: 1s missing in {rel_path}"
        assert "hlsPartDuration: 200ms" in content, f"hlsPartDuration: 200ms missing in {rel_path}"


def test_lite_server_generates_correct_mediamtx_config():
    server_code = _read("lite_server.py")
    assert "hlsVariant: lowLatency" in server_code
    assert "hlsSegmentCount: 3" in server_code
    assert "hlsSegmentDuration: 1s" in server_code
    assert "hlsPartDuration: 200ms" in server_code


def test_dashboards_have_low_latency_hls_configuration():
    admin = _read("admin_dashboard.html")
    lite = _read("lite_dashboard.html")

    required_hls_settings = [
        "lowLatencyMode: true",
        "liveSyncDurationCount: 2",
        "liveMaxLatencyDurationCount: 3",
        "maxLiveSyncPlaybackRate: 1.5",
        "backBufferLength: 0",
        "maxBufferLength: 2",
        "maxMaxBufferLength: 4",
        "startPosition: -1",
        "enableWorker: true",
        "liveDurationInfinity: true",
    ]

    for setting in required_hls_settings:
        assert setting in admin, f"Setting '{setting}' missing in admin_dashboard.html"
        assert setting in lite, f"Setting '{setting}' missing in lite_dashboard.html"


def test_dashboards_have_immediate_autoplay_without_stalling():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "video.autoplay = true;" in content, f"video.autoplay must be true in {name}"
        assert "video.muted = true;" in content, f"video.muted must be true in {name}"
        assert "Hls.Events.MANIFEST_PARSED" in content, f"MANIFEST_PARSED listener missing in {name}"
        assert "video.play().catch(" in content, f"video.play() on manifest parsed missing in {name}"
        assert "hls._destroyed || hlsPlayers[cam.id] !== hls" in content, (
            f"Stale callback protection guard missing in {name}"
        )

        # Confirm artificial jitter buffer and manual pause-on-stall are completely eliminated
        assert "getForwardBuffer" not in content, f"getForwardBuffer should not exist in {name}"
        assert "handleBufferStall" not in content, f"handleBufferStall should not exist in {name}"
        assert "BUFFER_STALLED_ERROR" not in content, f"BUFFER_STALLED_ERROR manual pause should not exist in {name}"


def test_hls_cdn_script_is_version_pinned_not_latest():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "hls.js@latest" not in content, (
            f"{name} must not load hls.js@latest — an upstream CDN release can silently "
            "break playback with no code change on our side; pin an exact version instead"
        )
        assert "cdn.jsdelivr.net/npm/hls.js@1." in content, f"Pinned hls.js version missing in {name}"


def test_dashboards_have_live_stall_reconnect_watchdog():
    # A genuinely live drone feed should never sit frozen: hls.js only self-heals
    # on *fatal* errors, so a silent stall (buffer starvation, decoder hiccup, or
    # the server finalizing the playlist on an encoder reconnect) needs its own
    # recovery path that reconnects cleanly without ever pausing playback on purpose.
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "playerWatchdogTimers" in content, f"Stall watchdog registry missing in {name}"
        assert "egdmsReconnect" in content, f"Stall watchdog reconnect handler missing in {name}"
        assert 'addEventListener("ended", egdmsReconnect)' in content, (
            f"'ended' event must trigger a reconnect (a live feed never legitimately ends) in {name}"
        )
        # The watchdog must clean itself up on teardown, or repeated reconnects would leak intervals
        assert "clearInterval(playerWatchdogTimers[id])" in content, (
            f"Watchdog interval must be cleared in teardownVideoFeed in {name}"
        )

