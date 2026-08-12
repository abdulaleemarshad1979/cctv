import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from lite_server import app, ADMIN_USERNAME, SESSION_COOKIE_NAME

client = TestClient(app)

def test_unauthenticated_redirect_dashboard():
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert "/login" in response.headers["location"]

def test_unauthenticated_api_rejection():
    response = client.get("/cameras")
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthenticated. Please log in."

def test_login_invalid_credentials():
    response = client.post("/login", json={"username": "wrong", "password": "bad"})
    assert response.status_code == 401

def test_login_success_and_logout():
    # Login
    test_password = os.getenv("ADMIN_PASSWORD", "Egdronepolice@1143")
    response = client.post("/login", json={"username": ADMIN_USERNAME, "password": test_password})
    assert response.status_code == 200
    assert SESSION_COOKIE_NAME in client.cookies

    # Access protected route
    res_dash = client.get("/cameras")
    assert res_dash.status_code == 200

    # Logout
    res_logout = client.get("/logout", follow_redirects=False)
    assert res_logout.status_code == 307
    assert "/login" in res_logout.headers["location"]
