import os
import sys
import json
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from lite_server import (
    app,
    SESSION_COOKIE_NAME,
    CAMERA_CUSTOMIZATIONS_FILE,
    load_cameras,
    cameras_db,
    save_camera_customizations
)

TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "review_admin_password_2026"
TEST_VIEWER_USERNAME = "viewer"
TEST_VIEWER_PASSWORD = "review_viewer_password_2026"
TEST_SECRET_KEY = "review_only_secret_key_0123456789abcdef"

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup_customizations(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", TEST_ADMIN_USERNAME)
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("VIEWER_USERNAME", TEST_VIEWER_USERNAME)
    monkeypatch.setenv("VIEWER_PASSWORD", TEST_VIEWER_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET_KEY)
    # Setup: clean customizations
    save_camera_customizations({})
    load_cameras()
    yield
    # Teardown: clean customizations
    save_camera_customizations({})
    load_cameras()

def test_drone_capacity_scale_to_60():
    """Verify that the system defaults to managing 60 drone slots."""
    client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    
    response = client.get("/cameras")
    assert response.status_code == 200
    cams = response.json()
    assert len(cams) >= 60
    
    drone_ids = [c["id"] for c in cams if c["id"].startswith("drone-")]
    assert len(drone_ids) == 60
    assert "drone-1" in drone_ids
    assert "drone-20" in drone_ids
    assert "drone-60" in drone_ids

def test_admin_dashboard_auth_protection():
    """Ensure unauthenticated access to /admin redirects to login."""
    unauth_client = TestClient(app)
    res = unauth_client.get("/admin", follow_redirects=False)
    assert res.status_code == 307
    assert "/login" in res.headers["location"]

def test_admin_dashboard_authenticated_access():
    """Ensure authenticated admin can access /admin."""
    auth_client = TestClient(app)
    auth_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})
    
    res = auth_client.get("/admin")
    assert res.status_code == 200
    assert "Admin Drone Feed Manager" in res.text

def test_rename_drone_endpoint():
    """Test renaming a single drone (e.g. drone-20 -> Rcpm Drone)."""
    auth_client = TestClient(app)
    auth_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})

    # Rename drone-20
    res = auth_client.post("/api/cameras/drone-20/rename", json={
        "name": "Rcpm Drone",
        "location": "Pushkaralu VIP Ghat"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["name"] == "Rcpm Drone"
    assert data["location"] == "Pushkaralu VIP Ghat"

    # Verify /cameras reflects the change
    res_cams = auth_client.get("/cameras")
    cams = res_cams.json()
    drone20 = next(c for c in cams if c["id"] == "drone-20")
    assert drone20["name"] == "Rcpm Drone"
    assert drone20["location"] == "Pushkaralu VIP Ghat"
    # Ensure source stream path remains unchanged to not break live pipeline
    assert drone20["source_stream_path"] == "live/drone20"

def test_persistence_of_drone_customizations():
    """Verify that restarting/reloading cameras preserves saved customizations."""
    auth_client = TestClient(app)
    auth_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})

    auth_client.post("/api/cameras/drone-20/rename", json={
        "name": "Rcpm Main Drone",
        "location": "Godavari Bridge"
    })

    # Simulate server reload
    load_cameras()

    res_cams = auth_client.get("/cameras")
    cams = res_cams.json()
    drone20 = next(c for c in cams if c["id"] == "drone-20")
    assert drone20["name"] == "Rcpm Main Drone"
    assert drone20["location"] == "Godavari Bridge"

def test_bulk_update_and_reset():
    """Test bulk update of drone metadata and factory reset."""
    auth_client = TestClient(app)
    auth_client.post("/login", json={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD})

    # Bulk update drone-1 and drone-2
    res = auth_client.post("/api/cameras/bulk-update", json=[
        {"id": "drone-1", "name": "Entrance North Drone", "location": "North Ghat"},
        {"id": "drone-2", "name": "Entrance South Drone", "location": "South Ghat"}
    ])
    assert res.status_code == 200
    assert res.json()["updated_count"] == 2

    # Verify
    res_cams = auth_client.get("/cameras")
    cams = {c["id"]: c for c in res_cams.json()}
    assert cams["drone-1"]["name"] == "Entrance North Drone"
    assert cams["drone-2"]["name"] == "Entrance South Drone"

    # Reset names
    res_reset = auth_client.post("/api/cameras/reset-names")
    assert res_reset.status_code == 200

    # Verify reset
    res_cams_after = auth_client.get("/cameras")
    cams_after = {c["id"]: c for c in res_cams_after.json()}
    assert cams_after["drone-1"]["name"] == "DRONE 1"
    assert cams_after["drone-2"]["name"] == "DRONE 2"
