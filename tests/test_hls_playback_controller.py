from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_mediamtx_configs_have_exact_hls_low_latency_settings():
    for rel_path in ["mediamtx.yml", "deployment/mediamtx.yml"]:
        content = _read(rel_path)
        assert "hls: yes" in content, f"hls: yes missing in {rel_path}"
        assert "hlsVariant: lowLatency" in content, f"hlsVariant: lowLatency missing in {rel_path}"
        assert "hlsSegmentCount: 6" in content, f"hlsSegmentCount: 6 missing in {rel_path}"
        assert "hlsSegmentDuration: 1s" in content, f"hlsSegmentDuration: 1s missing in {rel_path}"
        assert "hlsPartDuration: 200ms" in content, f"hlsPartDuration: 200ms missing in {rel_path}"
        assert "webrtcLocalUDPAddress: :8189" in content, f"webrtcLocalUDPAddress: :8189 missing in {rel_path}"


def test_lite_server_generates_correct_mediamtx_config():
    server_code = _read("lite_server.py")
    assert "hlsVariant: lowLatency" in server_code
    assert "hlsSegmentCount: 6" in server_code
    assert "hlsSegmentDuration: 1s" in server_code
    assert "hlsPartDuration: 200ms" in server_code
    assert "webrtcLocalUDPAddress: :8189" in server_code


def test_dashboards_have_low_latency_hls_configuration():
    admin = _read("admin_dashboard.html")
    lite = _read("lite_dashboard.html")

    required_hls_settings = [
        "lowLatencyMode: true",
        "liveSyncDuration: 1.5",
        "liveMaxLatencyDuration: 3.5",
        "liveSyncDurationCount: 3",
        "liveMaxLatencyDurationCount: 5",
        "maxLiveSyncPlaybackRate: 1.1",
        "backBufferLength: 4",
        "maxBufferLength: 5",
        "maxMaxBufferLength: 10",
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
        assert "safePlay(video)" in content, f"safePlay on manifest parsed missing in {name}"
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
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "playerWatchdogTimers" in content, f"Stall watchdog registry missing in {name}"
        assert "egdmsReconnect" in content, f"Stall watchdog reconnect handler missing in {name}"
        assert 'addEventListener("ended", egdmsReconnect)' in content, (
            f"'ended' event must trigger a reconnect (a live feed never legitimately ends) in {name}"
        )
        assert "clearInterval(playerWatchdogTimers[id])" in content, (
            f"Watchdog interval must be cleared in teardownVideoFeed in {name}"
        )


def test_dashboards_have_get_readiness_probe_with_abort_controller():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "probeStreamReadiness" in content, f"probeStreamReadiness function missing in {name}"
        assert 'method: "GET"' in content, f"GET-based probe missing in {name}"
        assert 'method: "HEAD"' not in content, f"Outdated HEAD-based probe must not be used in {name}"
        assert "AbortController" in content, f"AbortController missing in {name}"
        assert "#EXTM3U" in content, f"Playable media validation missing in {name}"


def test_dashboards_have_path_reconciliation_and_superseded_cancellation():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "triggerSeamlessStreamSwitch" in content, f"triggerSeamlessStreamSwitch missing in {name}"
        assert "probeTokens" in content, f"probeTokens sequence tracker missing in {name}"
        assert "probesInFlight[cam.id].controller.abort()" in content, f"Obsolete probe abort missing in {name}"
        assert "activePath !== desiredPath" in content, f"Active vs desired path reconciliation missing in {name}"


def test_dashboards_have_safeplay_handling_and_user_pause_tracking():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "NotAllowedError" in content, f"NotAllowedError autoplay policy handling missing in {name}"
        assert "showPlayOverlay" in content, f"Play overlay for interaction missing in {name}"
        assert "video._userPaused" in content, f"User pause distinction missing in {name}"
        assert "requestVideoFrameCallback" in content, f"Frame callback progress tracking missing in {name}"


def test_dashboards_resume_playback_on_full_view_transitions():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "openFullViewModal" in content
        assert "closeFullViewModal" in content
        assert "safePlay(video)" in content


def test_dashboards_have_webrtc_with_clean_hls_fallback():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "setupWebRtcPlayback" in content, f"setupWebRtcPlayback missing in {name}"
        assert "setupHlsPlayback" in content, f"setupHlsPlayback missing in {name}"
        assert "webrtcUnavailable" in content, f"webrtcUnavailable fallback cache missing in {name}"
        assert "RTCPeerConnection" in content, f"RTCPeerConnection missing in {name}"
        assert "/webrtc/${playbackPath}/whep" in content, f"WHEP endpoint call missing in {name}"


def test_nginx_and_fastapi_webrtc_proxy_routing():
    nginx_conf = _read("deployment/nginx.conf")
    assert "proxy_redirect ~^/(.*)$ /webrtc/$1;" in nginx_conf
    assert "proxy_pass http://mediamtx:8889/;" in nginx_conf

    server_code = _read("lite_server.py")
    assert 'methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]' in server_code
    assert 'resp_headers["location"] = "/webrtc/"' in server_code
