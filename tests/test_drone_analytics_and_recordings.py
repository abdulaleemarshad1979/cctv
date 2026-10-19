import os
import sys
from pathlib import Path
import re
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lite_server import (
    app,
    drone_flight_analytics_store,
    video_recordings_manager,
    load_cameras,
    save_camera_customizations
)
import lite_server

TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "review_admin_password_2026"
TEST_VIEWER_USERNAME = "viewer"
TEST_VIEWER_PASSWORD = "review_viewer_password_2026"
TEST_SECRET_KEY = "review_only_secret_key_0123456789abcdef"


@pytest.fixture(autouse=True)
def setup_auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", TEST_ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("VIEWER_USERNAME", TEST_VIEWER_USERNAME)
    monkeypatch.setenv("VIEWER_PASSWORD", TEST_VIEWER_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET_KEY)
    save_camera_customizations({})
    load_cameras()
    yield
    save_camera_customizations({})
    load_cameras()


def test_login_page_password_toggle_show():
    """Verify login page includes 'Show' password toggle button and functionality."""
    html_path = Path("login.html")
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    assert "password-toggle-btn" in content
    assert "togglePasswordVisibility" in content
    assert "Show" in content
    assert 'id="password"' in content
    assert 'id="passwordToggleText"' in content


def test_rtmp_links_hidden_from_both_dashboards():
    """Verify neither lite_dashboard.html nor admin_dashboard.html expose RTMP links."""
    for dash in ["lite_dashboard.html", "admin_dashboard.html"]:
        html_path = Path(dash)
        assert html_path.exists()
        content = html_path.read_text(encoding="utf-8")
        assert "rtmp://" not in content
        # Ensure no active tile publish url element
        assert '<code class="tile__publish-url"' not in content


def test_navbar_scrolls_up_naturally():
    """Verify sticky-nav-wrapper uses position: relative so navbar scrolls off when scrolling."""
    for dash in ["lite_dashboard.html", "admin_dashboard.html"]:
        html_path = Path(dash)
        content = html_path.read_text(encoding="utf-8")
        match = re.search(r"\.sticky-nav-wrapper\s*\{([^}]+)\}", content)
        assert match is not None
        block = match.group(1)
        assert "position: relative" in block
        assert "position: sticky" not in block


def test_dashboard_header_unification():
    """Verify both dashboards have the unified AP Police Department and East Godavari Drone Monitoring System header with dignitary portraits."""
    for dash in ["lite_dashboard.html", "admin_dashboard.html"]:
        html_path = Path(dash)
        content = html_path.read_text(encoding="utf-8")
        assert "Andhra Pradesh Police Department" in content
        assert "East Godavari Drone Monitoring System" in content
        assert "ap_police_logo.png" in content
        assert "cm.png" in content
        assert "dgp.png" in content
        assert "sp.png" in content
        assert "Sri N. Chandrababu Naidu" in content
        assert "Sri Harish Kumar Gupta, IPS" in content
        assert "Sri D. Narasimha Kishore, IPS" in content


def test_drone_analytics_and_recordings_admin_only_in_templates():
    """Verify drone analytics and recorded videos sections are in admin dashboard and NOT in viewer dashboard."""
    admin_content = Path("admin_dashboard.html").read_text(encoding="utf-8")
    viewer_content = Path("lite_dashboard.html").read_text(encoding="utf-8")

    # In Admin Dashboard
    assert "section-drone-analytics" in admin_content
    assert "section-recorded-videos" in admin_content
    assert "Centralised Drone Monitoring Portal" in admin_content
    assert "Drone Wise Analytics" in admin_content
    assert "Surveillance Video Recordings" in admin_content
    assert "recording-player-modal" in admin_content

    # NOT in Viewer Dashboard
    assert "section-drone-analytics" not in viewer_content
    assert "section-recorded-videos" not in viewer_content


def test_api_drone_analytics_permissions():
    """Verify /api/drone-analytics enforces admin authentication."""
    anon_client = TestClient(app)
    res = anon_client.get("/api/drone-analytics")
    assert res.status_code in (401, 403)

    # Viewer request
    viewer_client = TestClient(app)
    viewer_client.post("/login", json={"username": TEST_VIEWER_USERNAME, "password": TEST_VIEWER_PASSWORD})
    res_viewer = viewer_client.get("/api/drone-analytics")
    assert res_viewer.status_code == 403

    # Admin request
    admin_client = TestClient(app)
    admin_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    res_admin = admin_client.get("/api/drone-analytics")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "summary" in data
    assert "chart" in data
    assert "drones" in data
    assert len(data["drones"]) >= 40
    assert "today_total_hours" in data["summary"]
    assert "week_total_hours" in data["summary"]


def test_api_recordings_permissions_and_response():
    """Verify /api/recordings enforces admin authentication and returns video list."""
    # Viewer request
    viewer_client = TestClient(app)
    viewer_client.post("/login", json={"username": TEST_VIEWER_USERNAME, "password": TEST_VIEWER_PASSWORD})
    res_viewer = viewer_client.get("/api/recordings")
    assert res_viewer.status_code == 403

    # Admin request
    admin_client = TestClient(app)
    admin_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    res_admin = admin_client.get("/api/recordings")
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "recordings" in data
    assert isinstance(data["recordings"], list)
    assert len(data["recordings"]) >= 1

    rec = data["recordings"][0]
    assert "video_url" in rec
    assert "download_url" in rec
    assert "title" in rec
    assert "duration_formatted" in rec
