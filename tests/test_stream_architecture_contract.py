from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_backend_starts_independent_mediamtx_state_monitor():
    server = _read("lite_server.py")
    assert "from src.stream_state_monitor import MediaMTXStateMonitor" in server
    assert "_stream_state_monitor = build_stream_state_monitor()" in server
    assert "_stream_state_monitor.start()" in server
    assert "_stream_state_monitor.stop()" in server


def test_mediamtx_webhooks_are_backend_only_and_have_a_fallback_api():
    config = _read("deployment/mediamtx.yml")
    assert "api: yes" in config
    assert "apiAddress: :9997" in config
    assert "runOnAvailable:" in config
    assert "runOnUnavailable:" in config
    assert "http://app:8000/cameras/state" in config


def test_mediamtx_runtime_contains_curl_for_webhooks():
    dockerfile = _read("deployment/Dockerfile.mediamtx")
    compose = _read("docker-compose.yml")
    assert "apk add --no-cache curl" in dockerfile
    assert "dockerfile: deployment/Dockerfile.mediamtx" in compose
    assert "http://127.0.0.1:9997/v3/paths/list" in compose


def test_frontend_is_not_allowed_to_mutate_camera_availability():
    dashboard = _read("lite_dashboard.html")
    assert 'fetch("/cameras"' in dashboard
    assert 'fetch("/cameras/state"' not in dashboard


def test_ui_deployment_never_recreates_mediamtx():
    deploy_script = _read("scripts/deploy_ui_safe.sh")
    assert "docker compose build app" in deploy_script
    assert "docker compose up -d --no-deps app" in deploy_script
    assert "mediamtx_container_after" in deploy_script
    assert "mediamtx_container_before" in deploy_script
    assert "docker compose up -d mediamtx" not in deploy_script
