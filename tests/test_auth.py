import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lite_server import (
    app,
    SESSION_COOKIE_NAME,
    save_camera_customizations,
    load_cameras
)

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_teardown():
    save_camera_customizations({})
    load_cameras()
    yield
    save_camera_customizations({})
    load_cameras()

def test_unauthenticated_redirect_dashboard():
    unauth = TestClient(app)
    response = unauth.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert "/login" in response.headers["location"]
    assert "role=viewer" in response.headers["location"]

def test_unauthenticated_redirect_admin():
    unauth = TestClient(app)
    response = unauth.get("/admin", follow_redirects=False)
    assert response.status_code == 307
    assert "/login" in response.headers["location"]
    assert "role=admin" in response.headers["location"]

def test_unauthenticated_api_rejection():
    unauth = TestClient(app)
    response = unauth.get("/cameras")
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthenticated. Please log in."

def test_login_invalid_credentials():
    unauth = TestClient(app)
    response = unauth.post("/login", json={"username": "wrong_user", "password": "wrong_password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."

def test_viewer_login_success():
    viewer_client = TestClient(app)
    viewer_user = os.getenv("VIEWER_USERNAME", "viewer")
    viewer_pass = os.getenv("VIEWER_PASSWORD", "Egdronepolice@1143")
    
    response = viewer_client.post("/login", json={
        "username": viewer_user,
        "password": viewer_pass
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["role"] == "viewer"
    assert data["redirect"] == "/"
    assert SESSION_COOKIE_NAME in viewer_client.cookies

def test_viewer_can_access_viewing_dashboard_and_reports():
    viewer_client = TestClient(app)
    viewer_user = os.getenv("VIEWER_USERNAME", "viewer")
    viewer_pass = os.getenv("VIEWER_PASSWORD", "Egdronepolice@1143")
    
    viewer_client.post("/login", json={"username": viewer_user, "password": viewer_pass})
    
    # Can access viewing dashboard
    res_dash = viewer_client.get("/")
    assert res_dash.status_code == 200
    assert "East Godavari Drone Monitoring System" in res_dash.text
    # Viewer dashboard must not have Fleet Manager link or button
    assert "Admin Console" not in res_dash.text
    
    # Can view cameras
    res_cams = viewer_client.get("/cameras")
    assert res_cams.status_code == 200
    assert isinstance(res_cams.json(), list)

    # Can check auth status
    res_auth = viewer_client.get("/api/auth/check")
    assert res_auth.status_code == 200
    assert res_auth.json()["authenticated"] is True
    assert res_auth.json()["role"] == "viewer"
    assert res_auth.json()["is_admin"] is False

def test_viewer_blocked_from_admin_route_403():
    viewer_client = TestClient(app)
    viewer_user = os.getenv("VIEWER_USERNAME", "viewer")
    viewer_pass = os.getenv("VIEWER_PASSWORD", "Egdronepolice@1143")
    viewer_client.post("/login", json={"username": viewer_user, "password": viewer_pass})
    
    # Viewer accessing /admin receives 403
    res_admin = viewer_client.get("/admin", follow_redirects=False)
    assert res_admin.status_code == 403

def test_viewer_blocked_from_mutation_apis_403():
    viewer_client = TestClient(app)
    viewer_user = os.getenv("VIEWER_USERNAME", "viewer")
    viewer_pass = os.getenv("VIEWER_PASSWORD", "Egdronepolice@1143")
    viewer_client.post("/login", json={"username": viewer_user, "password": viewer_pass})
    
    # Rename camera blocked
    res_rename = viewer_client.post("/api/cameras/drone-1/rename", json={"name": "Hacked Drone"})
    assert res_rename.status_code == 403

    # Bulk update blocked
    res_bulk = viewer_client.post("/api/cameras/bulk-update", json=[{"id": "drone-1", "name": "Hacked"}])
    assert res_bulk.status_code == 403

    # Reset names blocked
    res_reset = viewer_client.post("/api/cameras/reset-names")
    assert res_reset.status_code == 403

    # Mode toggle blocked
    res_mode = viewer_client.post("/set_mode", json={"mode": "viewing"})
    assert res_mode.status_code == 403

    # Start feed blocked
    res_start = viewer_client.post("/cameras/drone-1/start", json={})
    assert res_start.status_code == 403

    # Stop feed blocked
    res_stop = viewer_client.post("/cameras/drone-1/stop")
    assert res_stop.status_code == 403

    # Clear notifications blocked
    res_clear = viewer_client.post("/api/notifications/clear")
    assert res_clear.status_code == 403

def test_admin_login_success():
    admin_client = TestClient(app)
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "Egdronepolice@1143")
    
    response = admin_client.post("/login", json={
        "username": admin_user,
        "password": admin_pass
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["role"] == "admin"
    assert data["redirect"] == "/admin"
    assert SESSION_COOKIE_NAME in admin_client.cookies

def test_admin_can_access_both_dashboards():
    admin_client = TestClient(app)
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "Egdronepolice@1143")
    admin_client.post("/login", json={"username": admin_user, "password": admin_pass})
    
    # Admin can access /admin
    res_admin = admin_client.get("/admin")
    assert res_admin.status_code == 200
    assert "Admin Drone Feed Manager" in res_admin.text
    
    # Admin can also access viewing dashboard /
    res_viewer = admin_client.get("/")
    assert res_viewer.status_code == 200

    # Auth check reports admin
    res_auth = admin_client.get("/api/auth/check")
    assert res_auth.status_code == 200
    assert res_auth.json()["authenticated"] is True
    assert res_auth.json()["role"] == "admin"
    assert res_auth.json()["is_admin"] is True

def test_admin_can_call_mutation_apis():
    admin_client = TestClient(app)
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "Egdronepolice@1143")
    admin_client.post("/login", json={"username": admin_user, "password": admin_pass})
    
    # Rename camera succeeds
    res_rename = admin_client.post("/api/cameras/drone-1/rename", json={"name": "Command Drone 1", "location": "Main Ghat"})
    assert res_rename.status_code == 200
    assert res_rename.json()["name"] == "Command Drone 1"

    # Reset names succeeds
    res_reset = admin_client.post("/api/cameras/reset-names")
    assert res_reset.status_code == 200

    # Mode switch succeeds
    res_mode = admin_client.post("/set_mode", json={"mode": "viewing"})
    assert res_mode.status_code == 200

def test_open_redirect_protection():
    admin_client = TestClient(app)
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD", "Egdronepolice@1143")
    
    # Try malicious external redirects
    for evil in ["https://attacker.com", "//evil.com", "/\\evil.com", "javascript:alert(1)"]:
        res = admin_client.post("/login", json={
            "username": admin_user,
            "password": admin_pass,
            "redirect": evil
        })
        assert res.status_code == 200
        dest = res.json()["redirect"]
        assert not dest.startswith("//")
        assert not dest.startswith("/\\")
        assert "://" not in dest
