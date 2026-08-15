import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import lite_server
from src.mission_control import MissionControlStore, generate_survey_route


ADMIN_PASSWORD = "mission_admin_password_2026"
VIEWER_PASSWORD = "mission_viewer_password_2026"
SECRET_KEY = "mission_test_secret_key_0123456789abcdef"


RECTANGLE = [
    {"lat": 17.0000, "lng": 81.8000},
    {"lat": 17.0000, "lng": 81.8030},
    {"lat": 17.0020, "lng": 81.8030},
    {"lat": 17.0020, "lng": 81.8000},
]


@pytest.fixture
def mission_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("VIEWER_USERNAME", "viewer")
    monkeypatch.setenv("VIEWER_PASSWORD", VIEWER_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)

    store = MissionControlStore(
        str(tmp_path / "missions.json"),
        str(tmp_path / "mission_audit.jsonl"),
        fleet_size=250,
    )
    store.reset()
    monkeypatch.setattr(lite_server, "mission_control_store", store)
    lite_server.load_cameras()

    client = TestClient(lite_server.app)
    login = client.post(
        "/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    yield client, store
    lite_server.load_cameras()


def test_polygon_survey_generator_produces_clipped_lawnmower_route():
    route = generate_survey_route(
        RECTANGLE,
        spacing_m=30,
        angle_deg=15,
        altitude_m=65,
        speed_mps=6,
    )

    assert route["planner"] == "polygon_lawnmower_v1"
    assert route["direct_flight_control"] is False
    assert route["manual_execution_required"] is True
    assert route["area_m2"] > 50_000
    assert route["route_distance_m"] > 100
    assert len(route["waypoints"]) >= 4
    assert route["geojson"]["type"] == "FeatureCollection"


def test_mission_control_is_admin_only(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("VIEWER_USERNAME", "viewer")
    monkeypatch.setenv("VIEWER_PASSWORD", VIEWER_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    viewer = TestClient(lite_server.app)
    assert viewer.post(
        "/login",
        json={"username": "viewer", "password": VIEWER_PASSWORD},
    ).status_code == 200
    assert viewer.get("/api/mission-control/overview").status_code == 403


def test_overview_keeps_250_fleet_separate_from_60_feed_slots(mission_client):
    client, _ = mission_client
    response = client.get("/api/mission-control/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["fleet_total"] == 250
    assert data["summary"]["feed_slots"] == 60
    assert data["direct_flight_control"] is False
    assert len(data["fleet"]) == 250


def test_admin_can_preview_dispatch_acknowledge_and_complete_survey(mission_client):
    client, _ = mission_client
    drone_1 = lite_server.find_camera_by_id("drone-1")
    drone_1["status"] = "online"
    drone_1["source_online"] = True

    fleet_update = client.post(
        "/api/mission-control/fleet/drone-1",
        json={
            "battery_percent": 88,
            "availability": "available",
            "pilot_name": "Pilot Alpha",
            "last_known_lat": 17.001,
            "last_known_lng": 81.801,
        },
    )
    assert fleet_update.status_code == 200

    preview = client.post(
        "/api/mission-control/surveys/preview",
        json={
            "boundary": RECTANGLE,
            "spacing_m": 35,
            "angle_deg": 0,
            "altitude_m": 60,
            "speed_mps": 5,
        },
    )
    assert preview.status_code == 200
    survey = preview.json()

    create = client.post(
        "/api/mission-control/missions",
        json={
            "title": "Kotilingala Ghat morning survey",
            "mission_type": "survey",
            "priority": "high",
            "drone_id": "auto",
            "location_name": "Kotilingala Ghat",
            "instructions": "Pilot to review the route and start it in DJI Fly.",
            "minimum_battery_percent": 40,
            "dispatch_now": True,
            "survey": survey,
        },
    )
    assert create.status_code == 200
    mission = create.json()
    assert mission["assigned_drone_id"] == "drone-1"
    assert mission["assignment_mode"] == "automatic"
    assert mission["status"] == "dispatched"
    assert mission["manual_execution_required"] is True

    mission_id = mission["id"]
    for status in ("accepted", "in_progress", "completed"):
        transition = client.post(
            f"/api/mission-control/missions/{mission_id}/transition",
            json={"status": status, "note": f"Moved to {status}"},
        )
        assert transition.status_code == 200
        assert transition.json()["status"] == status

    route_download = client.get(
        f"/api/mission-control/missions/{mission_id}/route.geojson"
    )
    assert route_download.status_code == 200
    assert route_download.json()["type"] == "FeatureCollection"

    overview = client.get("/api/mission-control/overview").json()
    drone = next(item for item in overview["fleet"] if item["id"] == "drone-1")
    assert drone["availability"] == "available"
    assert overview["summary"]["active_missions"] == 0


def test_admin_can_issue_explicit_pilot_rth_request(mission_client):
    client, _ = mission_client
    response = client.post(
        "/api/mission-control/fleet/drone-7/request",
        json={
            "action": "RETURN_HOME",
            "location_name": "VIP Ghat",
            "instructions": "Pilot: confirm airspace is clear, then initiate RTH in DJI Fly.",
        },
    )
    assert response.status_code == 200
    mission = response.json()
    assert mission["mission_type"] == "return_home_request"
    assert mission["assigned_drone_id"] == "drone-7"
    assert mission["direct_flight_control"] is False
    assert mission["delivery_mode"] == "pilot_acknowledgement"

    audit = client.get("/api/mission-control/audit").json()["events"]
    assert any(event["action"] == "mission.created" for event in audit)


def test_action_request_does_not_release_drone_with_another_active_mission(mission_client):
    client, _ = mission_client
    primary = client.post(
        "/api/mission-control/missions",
        json={
            "title": "Primary inspection",
            "mission_type": "inspection",
            "drone_id": "drone-9",
            "dispatch_now": True,
        },
    )
    assert primary.status_code == 200

    duplicate = client.post(
        "/api/mission-control/missions",
        json={
            "title": "Conflicting inspection",
            "mission_type": "inspection",
            "drone_id": "drone-9",
            "dispatch_now": True,
        },
    )
    assert duplicate.status_code == 400

    action = client.post(
        "/api/mission-control/fleet/drone-9/request",
        json={"action": "HOLD", "instructions": "Pilot: hold only if it is safe."},
    )
    assert action.status_code == 200
    action_mission = action.json()

    for status in ("accepted", "in_progress", "completed"):
        transitioned = client.post(
            f"/api/mission-control/missions/{action_mission['id']}/transition",
            json={"status": status},
        )
        assert transitioned.status_code == 200

    overview = client.get("/api/mission-control/overview").json()
    drone = next(item for item in overview["fleet"] if item["id"] == "drone-9")
    assert drone["availability"] == "assigned"
    assert drone["active_mission"]["id"] == primary.json()["id"]
