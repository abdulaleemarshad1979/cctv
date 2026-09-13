from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_mediamtx_configs_have_exact_hls_low_latency_settings():
    for rel_path in ["mediamtx.yml", "deployment/mediamtx.yml"]:
        content = _read(rel_path)
        assert "hls: yes" in content, f"hls: yes missing in {rel_path}"
        assert "hlsVariant: lowLatency" in content, f"hlsVariant: lowLatency missing in {rel_path}"
        assert "hlsSegmentCount: 7" in content, f"hlsSegmentCount: 7 missing in {rel_path}"
        assert "hlsSegmentDuration: 1s" in content, f"hlsSegmentDuration: 1s missing in {rel_path}"
        assert "hlsPartDuration: 200ms" in content, f"hlsPartDuration: 200ms missing in {rel_path}"


def test_lite_server_generates_correct_mediamtx_config():
    server_code = _read("lite_server.py")
    assert "hlsVariant: lowLatency" in server_code
    assert "hlsSegmentCount: 7" in server_code
    assert "hlsSegmentDuration: 1s" in server_code
    assert "hlsPartDuration: 200ms" in server_code


def test_dashboards_have_identical_time_based_hls_configuration():
    admin = _read("admin_dashboard.html")
    lite = _read("lite_dashboard.html")

    required_hls_settings = [
        "lowLatencyMode: true",
        "liveSyncDuration: 3",
        "liveMaxLatencyDuration: 6",
        "liveSyncOnStallIncrease: 0",
        "maxLiveSyncPlaybackRate: 1.10",
        "maxBufferLength: 6",
        "maxMaxBufferLength: 10",
        "backBufferLength: 0",
        "startPosition: -1",
        "enableWorker: true",
    ]

    for setting in required_hls_settings:
        assert setting in admin, f"Setting '{setting}' missing in admin_dashboard.html"
        assert setting in lite, f"Setting '{setting}' missing in lite_dashboard.html"

    # Verify count-based settings are completely removed
    forbidden_settings = [
        "liveSyncDurationCount",
        "liveMaxLatencyDurationCount",
    ]
    for setting in forbidden_settings:
        assert setting not in admin, f"Forbidden setting '{setting}' found in admin_dashboard.html"
        assert setting not in lite, f"Forbidden setting '{setting}' found in lite_dashboard.html"


def test_dashboards_have_startup_buffering_and_stall_recovery():
    for name in ["admin_dashboard.html", "lite_dashboard.html"]:
        content = _read(name)
        assert "video.autoplay = false;" in content, f"video.autoplay must be false initially in {name}"
        assert "video.muted = true;" in content, f"video.muted must be true in {name}"
        assert "getForwardBuffer(video, hls)" in content, f"getForwardBuffer missing in {name}"
        assert "getCurrentLatency(video, hls)" in content, f"getCurrentLatency missing in {name}"
        assert "Hls.Events.FRAG_BUFFERED" in content, f"FRAG_BUFFERED listener missing in {name}"
        assert "Hls.Events.BUFFER_APPENDED" in content, f"BUFFER_APPENDED listener missing in {name}"
        assert "10000" in content, f"Safe 10-second startup wait timer missing in {name}"
        assert "BUFFER_STALLED_ERROR" in content, f"BUFFER_STALLED_ERROR handling missing in {name}"
        assert "visibilitychange" in content, f"visibilitychange listener missing in {name}"
        assert "hls._destroyed || hlsPlayers[cam.id] !== hls" in content, (
            f"Stale callback protection guard missing in {name}"
        )


def test_js_jitter_buffer_state_machine_unit_test():
    # Run a node unit test verifying buffer calculation, startup threshold, stall recovery, and cooldown
    result = subprocess.run(
        ["node", "tests/test_hls_simulation.js"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Node test failed: {result.stderr}\n{result.stdout}"
