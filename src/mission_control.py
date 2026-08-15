"""Mission dispatch and survey planning for the EGDMS admin console.

This module deliberately does not talk to a drone flight controller.  DJI Air
3/Air 3S missions created here are operational requests that a field pilot
acknowledges and executes with DJI Fly.  Keeping this boundary explicit lets us
add useful fleet coordination without changing the existing MediaMTX/video
pipeline or implying unsupported direct aircraft control.
"""

from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


EARTH_LAT_METERS = 110_540.0
EARTH_LON_METERS = 111_320.0

ACTIVE_MISSION_STATES = {"planned", "dispatched", "accepted", "in_progress"}
TERMINAL_MISSION_STATES = {"completed", "cancelled", "declined"}
MISSION_TYPES = {
    "survey",
    "inspection",
    "crowd_monitoring",
    "incident_response",
    "hold_request",
    "return_home_request",
}
MISSION_PRIORITIES = {"low", "normal", "high", "critical"}
FLEET_AVAILABILITY = {"available", "assigned", "charging", "maintenance", "offline"}
MISSION_TRANSITIONS = {
    "planned": {"dispatched", "cancelled"},
    "dispatched": {"accepted", "declined", "cancelled"},
    "accepted": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "declined": set(),
    "completed": set(),
    "cancelled": set(),
}


class MissionControlError(ValueError):
    """Raised when a mission-control request cannot be safely applied."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _point_pair(point: Any) -> Tuple[float, float]:
    if isinstance(point, dict):
        lat = point.get("lat")
        lng = point.get("lng", point.get("lon"))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        lat, lng = point[0], point[1]
    else:
        raise MissionControlError("Every boundary point must contain latitude and longitude.")
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError) as exc:
        raise MissionControlError("Boundary coordinates must be numeric.") from exc
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lng_f <= 180.0):
        raise MissionControlError("Boundary coordinates are outside the valid latitude/longitude range.")
    return lat_f, lng_f


def _project_points(points: Sequence[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], float, float, float]:
    lat0 = sum(point[0] for point in points) / len(points)
    lng0 = sum(point[1] for point in points) / len(points)
    lon_scale = EARTH_LON_METERS * max(0.01, math.cos(math.radians(lat0)))
    projected = [
        ((lng - lng0) * lon_scale, (lat - lat0) * EARTH_LAT_METERS)
        for lat, lng in points
    ]
    return projected, lat0, lng0, lon_scale


def _unproject_point(x: float, y: float, lat0: float, lng0: float, lon_scale: float) -> Tuple[float, float]:
    return lat0 + (y / EARTH_LAT_METERS), lng0 + (x / lon_scale)


def _rotate(point: Tuple[float, float], radians: float) -> Tuple[float, float]:
    x, y = point
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return x * cosine - y * sine, x * sine + y * cosine


def _polygon_area(points: Sequence[Tuple[float, float]]) -> float:
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
    ) / 2.0


def _route_distance(points: Sequence[Tuple[float, float]]) -> float:
    return sum(
        math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1])
        for index in range(1, len(points))
    )


def generate_survey_route(
    boundary: Sequence[Any],
    spacing_m: float = 25.0,
    angle_deg: float = 0.0,
    altitude_m: float = 60.0,
    speed_mps: float = 5.0,
) -> Dict[str, Any]:
    """Generate a lawnmower survey route clipped to a polygon.

    The calculation uses a local equirectangular projection, which is accurate
    enough for the event-sized survey areas this dashboard is designed for.
    Returned routes are planning artefacts for DJI Fly operators, not uploaded
    or transmitted flight commands.
    """

    if len(boundary) < 3:
        raise MissionControlError("A survey boundary needs at least three map points.")
    if len(boundary) > 100:
        raise MissionControlError("A survey boundary is limited to 100 map points.")

    points = [_point_pair(point) for point in boundary]
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise MissionControlError("A survey boundary needs at least three unique map points.")

    spacing = _clamp(float(spacing_m), 5.0, 500.0)
    altitude = _clamp(float(altitude_m), 5.0, 120.0)
    speed = _clamp(float(speed_mps), 1.0, 15.0)
    angle = float(angle_deg) % 360.0

    projected, lat0, lng0, lon_scale = _project_points(points)
    area_m2 = _polygon_area(projected)
    if area_m2 < 25.0:
        raise MissionControlError("The survey boundary is too small; draw an area of at least 25 m².")

    # Rotate the polygon so scan lines can be calculated horizontally.  A
    # positive UI angle remains intuitive after rotating the result back.
    scan_rotation = math.radians(-angle)
    rotated = [_rotate(point, scan_rotation) for point in projected]
    min_y = min(point[1] for point in rotated)
    max_y = max(point[1] for point in rotated)
    height = max_y - min_y

    if height <= spacing:
        scan_levels = [(min_y + max_y) / 2.0]
    else:
        first_y = min_y + spacing / 2.0
        scan_levels = []
        current_y = first_y
        while current_y <= max_y - spacing / 2.0 + 1e-6:
            scan_levels.append(current_y)
            current_y += spacing

    route_rotated: List[Tuple[float, float]] = []
    sweep_index = 0
    for scan_y in scan_levels:
        intersections: List[float] = []
        for index, (x1, y1) in enumerate(rotated):
            x2, y2 = rotated[(index + 1) % len(rotated)]
            # Half-open edge handling avoids double-counting polygon vertices.
            crosses = (y1 <= scan_y < y2) or (y2 <= scan_y < y1)
            if not crosses or math.isclose(y1, y2):
                continue
            fraction = (scan_y - y1) / (y2 - y1)
            intersections.append(x1 + fraction * (x2 - x1))
        intersections.sort()

        segments = []
        for index in range(0, len(intersections) - 1, 2):
            start_x, end_x = intersections[index], intersections[index + 1]
            if end_x - start_x >= 1.0:
                segments.append(((start_x, scan_y), (end_x, scan_y)))
        if not segments:
            continue

        if sweep_index % 2:
            segments = [(end, start) for start, end in reversed(segments)]
        for start, end in segments:
            route_rotated.extend((start, end))
        sweep_index += 1

    if len(route_rotated) < 2:
        raise MissionControlError("The selected spacing does not produce a usable route inside this boundary.")
    if len(route_rotated) > 1000:
        raise MissionControlError("The route exceeds 1,000 waypoints; increase line spacing or reduce the area.")

    route_projected = [_rotate(point, -scan_rotation) for point in route_rotated]
    route_latlng = [
        _unproject_point(x, y, lat0, lng0, lon_scale)
        for x, y in route_projected
    ]
    waypoints = [
        {
            "sequence": index + 1,
            "lat": round(lat, 7),
            "lng": round(lng, 7),
            "altitude_m": round(altitude, 1),
            "speed_mps": round(speed, 1),
            "action": "survey_pass",
        }
        for index, (lat, lng) in enumerate(route_latlng)
    ]
    distance_m = _route_distance(route_projected)
    duration_seconds = distance_m / speed
    polygon_coordinates = [[lng, lat] for lat, lng in points] + [[points[0][1], points[0][0]]]
    line_coordinates = [[point["lng"], point["lat"]] for point in waypoints]

    return {
        "route_id": f"SURVEY-{uuid.uuid4().hex[:8].upper()}",
        "planner": "polygon_lawnmower_v1",
        "direct_flight_control": False,
        "manual_execution_required": True,
        "boundary": [{"lat": lat, "lng": lng} for lat, lng in points],
        "waypoints": waypoints,
        "spacing_m": round(spacing, 1),
        "angle_deg": round(angle, 1),
        "altitude_m": round(altitude, 1),
        "speed_mps": round(speed, 1),
        "area_m2": round(area_m2, 1),
        "route_distance_m": round(distance_m, 1),
        "estimated_duration_seconds": round(duration_seconds),
        "center": {"lat": round(lat0, 7), "lng": round(lng0, 7)},
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"kind": "survey_boundary"},
                    "geometry": {"type": "Polygon", "coordinates": [polygon_coordinates]},
                },
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "planned_route",
                        "altitude_m": round(altitude, 1),
                        "speed_mps": round(speed, 1),
                    },
                    "geometry": {"type": "LineString", "coordinates": line_coordinates},
                },
            ],
        },
    }


class MissionControlStore:
    """Small durable JSON store for fleet coordination and audit history."""

    def __init__(self, data_path: str, audit_path: str, fleet_size: int = 250):
        self.data_path = data_path
        self.audit_path = audit_path
        self.fleet_size = max(1, min(1000, int(fleet_size)))
        self._lock = threading.RLock()

    def _default_fleet_item(self, index: int) -> Dict[str, Any]:
        return {
            "id": f"drone-{index}",
            "availability": "available",
            "battery_percent": 100,
            "pilot_name": "",
            "last_known_lat": None,
            "last_known_lng": None,
            "notes": "",
            "updated_at": None,
        }

    def _default_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "fleet": {
                f"drone-{index}": self._default_fleet_item(index)
                for index in range(1, self.fleet_size + 1)
            },
            "missions": [],
        }

    def _normalise_state(self, state: Any) -> Dict[str, Any]:
        if not isinstance(state, dict):
            state = self._default_state()
        fleet = state.get("fleet")
        missions = state.get("missions")
        if not isinstance(fleet, dict):
            fleet = {}
        if not isinstance(missions, list):
            missions = []
        for index in range(1, self.fleet_size + 1):
            drone_id = f"drone-{index}"
            record = fleet.get(drone_id)
            if not isinstance(record, dict):
                fleet[drone_id] = self._default_fleet_item(index)
            else:
                default = self._default_fleet_item(index)
                default.update(record)
                default["id"] = drone_id
                fleet[drone_id] = default
        state.update({"version": 1, "fleet": fleet, "missions": missions})
        return state

    def _load_unlocked(self) -> Dict[str, Any]:
        try:
            with open(self.data_path, "r", encoding="utf-8") as data_file:
                return self._normalise_state(json.load(data_file))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self._default_state()

    def _save_unlocked(self, state: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.data_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary_path = f"{self.data_path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as data_file:
            json.dump(state, data_file, indent=2, sort_keys=True)
        os.replace(temporary_path, self.data_path)

    def _audit_unlocked(self, action: str, details: Dict[str, Any]) -> Dict[str, Any]:
        event = {
            "event_id": f"AUD-{uuid.uuid4().hex[:10].upper()}",
            "timestamp": _utc_now(),
            "action": action,
            "details": details,
        }
        directory = os.path.dirname(self.audit_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def reset(self) -> None:
        """Reset state. Intended for tests and deliberate administrative repair."""
        with self._lock:
            self._save_unlocked(self._default_state())
            try:
                os.remove(self.audit_path)
            except FileNotFoundError:
                pass

    def update_fleet(self, drone_id: str, updates: Dict[str, Any], actor: str = "admin") -> Dict[str, Any]:
        with self._lock:
            state = self._load_unlocked()
            record = state["fleet"].get(drone_id)
            if record is None:
                raise MissionControlError(f"Unknown fleet aircraft: {drone_id}")

            if "availability" in updates and updates["availability"] is not None:
                availability = str(updates["availability"]).strip().lower()
                if availability not in FLEET_AVAILABILITY:
                    raise MissionControlError("Unsupported fleet availability value.")
                record["availability"] = availability
            if "battery_percent" in updates and updates["battery_percent"] is not None:
                try:
                    battery = int(updates["battery_percent"])
                except (TypeError, ValueError) as exc:
                    raise MissionControlError("Battery percentage must be a whole number.") from exc
                if not 0 <= battery <= 100:
                    raise MissionControlError("Battery percentage must be between 0 and 100.")
                record["battery_percent"] = battery
            for key in ("pilot_name", "notes"):
                if key in updates and updates[key] is not None:
                    record[key] = str(updates[key]).strip()[:240]
            for key in ("last_known_lat", "last_known_lng"):
                if key in updates:
                    value = updates[key]
                    record[key] = None if value in (None, "") else float(value)
            if record["last_known_lat"] is not None and not -90 <= record["last_known_lat"] <= 90:
                raise MissionControlError("Last-known latitude is invalid.")
            if record["last_known_lng"] is not None and not -180 <= record["last_known_lng"] <= 180:
                raise MissionControlError("Last-known longitude is invalid.")

            record["updated_at"] = _utc_now()
            self._save_unlocked(state)
            self._audit_unlocked("fleet.updated", {"actor": actor, "drone_id": drone_id, "changes": updates})
            return dict(record)

    @staticmethod
    def _camera_is_online(camera: Optional[Dict[str, Any]]) -> bool:
        if not camera:
            return False
        return bool(
            camera.get("source_online")
            or camera.get("output_online")
            or camera.get("status") == "online"
        )

    @staticmethod
    def _distance_to_target(record: Dict[str, Any], target: Optional[Dict[str, Any]]) -> Optional[float]:
        if not target:
            return None
        lat = record.get("last_known_lat")
        lng = record.get("last_known_lng")
        if lat is None or lng is None:
            return None
        try:
            target_lat = float(target["lat"])
            target_lng = float(target["lng"])
        except (KeyError, TypeError, ValueError):
            return None
        mean_lat = math.radians((lat + target_lat) / 2.0)
        dx = (lng - target_lng) * EARTH_LON_METERS * math.cos(mean_lat)
        dy = (lat - target_lat) * EARTH_LAT_METERS
        return math.hypot(dx, dy)

    def _select_drone_unlocked(
        self,
        state: Dict[str, Any],
        cameras: Sequence[Dict[str, Any]],
        target: Optional[Dict[str, Any]],
        minimum_battery_percent: int,
    ) -> str:
        camera_by_id = {camera.get("id"): camera for camera in cameras}
        busy = {
            mission.get("assigned_drone_id")
            for mission in state["missions"]
            if mission.get("status") in ACTIVE_MISSION_STATES
        }
        candidates = []
        for drone_id, record in state["fleet"].items():
            if drone_id in busy:
                continue
            if record.get("availability") != "available":
                continue
            battery = int(record.get("battery_percent", 0) or 0)
            if battery < minimum_battery_percent:
                continue
            camera = camera_by_id.get(drone_id)
            distance = self._distance_to_target(record, target)
            score = battery * 2.0
            if self._camera_is_online(camera):
                score += 1000.0
            if distance is not None:
                score += max(0.0, 300.0 - distance / 10.0)
            elif target:
                score -= 100.0
            try:
                numeric_id = int(drone_id.split("-")[-1])
            except ValueError:
                numeric_id = self.fleet_size + 1
            candidates.append((score, -numeric_id, drone_id))
        if not candidates:
            raise MissionControlError("No available aircraft meets the assignment and battery requirements.")
        candidates.sort(reverse=True)
        return candidates[0][2]

    def create_mission(
        self,
        mission_request: Dict[str, Any],
        cameras: Sequence[Dict[str, Any]],
        actor: str = "admin",
    ) -> Dict[str, Any]:
        with self._lock:
            state = self._load_unlocked()
            title = str(mission_request.get("title") or "").strip()
            if not title:
                raise MissionControlError("Mission title is required.")
            mission_type = str(mission_request.get("mission_type") or "inspection").strip().lower()
            if mission_type not in MISSION_TYPES:
                raise MissionControlError("Unsupported mission type.")
            priority = str(mission_request.get("priority") or "normal").strip().lower()
            if priority not in MISSION_PRIORITIES:
                raise MissionControlError("Unsupported mission priority.")
            minimum_battery = int(mission_request.get("minimum_battery_percent") or 30)
            minimum_battery = max(0, min(100, minimum_battery))
            target = mission_request.get("target")

            requested_drone = str(mission_request.get("drone_id") or "auto").strip().lower()
            if requested_drone in ("", "auto"):
                assigned_drone = self._select_drone_unlocked(
                    state, cameras, target, minimum_battery
                )
                assignment_mode = "automatic"
            else:
                if requested_drone not in state["fleet"]:
                    raise MissionControlError(f"Unknown fleet aircraft: {requested_drone}")
                assigned_drone = requested_drone
                assignment_mode = "manual"
                if state["fleet"][assigned_drone].get("availability") not in ("available", "assigned"):
                    raise MissionControlError(f"{assigned_drone} is not currently available for dispatch.")
                has_active_mission = any(
                    existing.get("assigned_drone_id") == assigned_drone
                    and existing.get("status") in ACTIVE_MISSION_STATES
                    for existing in state["missions"]
                )
                if has_active_mission and mission_type not in {"hold_request", "return_home_request"}:
                    raise MissionControlError(
                        f"{assigned_drone} already has an active mission; use automatic assignment or finish it first."
                    )

            dispatch_now = bool(mission_request.get("dispatch_now", True))
            mission_id = f"MSN-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:5].upper()}"
            now = _utc_now()
            mission = {
                "id": mission_id,
                "title": title[:160],
                "mission_type": mission_type,
                "priority": priority,
                "status": "dispatched" if dispatch_now else "planned",
                "assigned_drone_id": assigned_drone,
                "assignment_mode": assignment_mode,
                "minimum_battery_percent": minimum_battery,
                "location_name": str(mission_request.get("location_name") or "").strip()[:160],
                "instructions": str(mission_request.get("instructions") or "").strip()[:1000],
                "target": target,
                "survey": mission_request.get("survey"),
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
                "history": [{"status": "dispatched" if dispatch_now else "planned", "at": now, "actor": actor}],
                "direct_flight_control": False,
                "manual_execution_required": True,
                "delivery_mode": "pilot_acknowledgement",
            }
            state["missions"].insert(0, mission)
            state["missions"] = state["missions"][:2000]
            state["fleet"][assigned_drone]["availability"] = "assigned"
            state["fleet"][assigned_drone]["updated_at"] = now
            self._save_unlocked(state)
            self._audit_unlocked(
                "mission.created",
                {
                    "actor": actor,
                    "mission_id": mission_id,
                    "mission_type": mission_type,
                    "assigned_drone_id": assigned_drone,
                    "assignment_mode": assignment_mode,
                    "status": mission["status"],
                },
            )
            return dict(mission)

    def transition_mission(self, mission_id: str, new_status: str, actor: str = "admin", note: str = "") -> Dict[str, Any]:
        with self._lock:
            state = self._load_unlocked()
            mission = next((item for item in state["missions"] if item.get("id") == mission_id), None)
            if mission is None:
                raise MissionControlError("Mission not found.")
            current_status = mission.get("status")
            requested_status = str(new_status).strip().lower()
            if requested_status not in MISSION_TRANSITIONS.get(current_status, set()):
                raise MissionControlError(f"Mission cannot move from {current_status} to {requested_status}.")

            now = _utc_now()
            mission["status"] = requested_status
            mission["updated_at"] = now
            mission.setdefault("history", []).append(
                {"status": requested_status, "at": now, "actor": actor, "note": str(note).strip()[:500]}
            )
            drone_id = mission.get("assigned_drone_id")
            if drone_id in state["fleet"] and requested_status in TERMINAL_MISSION_STATES:
                has_other_active_mission = any(
                    item is not mission
                    and item.get("assigned_drone_id") == drone_id
                    and item.get("status") in ACTIVE_MISSION_STATES
                    for item in state["missions"]
                )
                if (
                    not has_other_active_mission
                    and state["fleet"][drone_id].get("availability") == "assigned"
                ):
                    state["fleet"][drone_id]["availability"] = "available"
                    state["fleet"][drone_id]["updated_at"] = now
            self._save_unlocked(state)
            self._audit_unlocked(
                "mission.transitioned",
                {
                    "actor": actor,
                    "mission_id": mission_id,
                    "from": current_status,
                    "to": requested_status,
                    "note": str(note).strip()[:500],
                },
            )
            return dict(mission)

    def overview(self, cameras: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            state = self._load_unlocked()
            camera_by_id = {camera.get("id"): camera for camera in cameras}
            active_by_drone = {
                mission.get("assigned_drone_id"): mission
                for mission in reversed(state["missions"])
                if mission.get("status") in ACTIVE_MISSION_STATES
            }
            fleet = []
            for drone_id, record in state["fleet"].items():
                camera = camera_by_id.get(drone_id)
                active_mission = active_by_drone.get(drone_id)
                item = dict(record)
                item.update(
                    {
                        "camera_name": camera.get("name") if camera else drone_id.upper().replace("-", " "),
                        "location": camera.get("location") if camera else "Unassigned",
                        "feed_configured": camera is not None,
                        "feed_online": self._camera_is_online(camera),
                        "feed_status": camera.get("status") if camera else "not_configured",
                        "risk_level": (camera or {}).get("risk_level", (camera or {}).get("comp_zone", "UNKNOWN")),
                        "active_mission": (
                            {
                                "id": active_mission.get("id"),
                                "title": active_mission.get("title"),
                                "status": active_mission.get("status"),
                                "priority": active_mission.get("priority"),
                            }
                            if active_mission
                            else None
                        ),
                    }
                )
                fleet.append(item)
            fleet.sort(key=lambda item: int(item["id"].split("-")[-1]))
            missions = state["missions"][:500]
            return {
                "generated_at": _utc_now(),
                "direct_flight_control": False,
                "manual_execution_required": True,
                "summary": {
                    "fleet_total": len(fleet),
                    "feed_slots": len([item for item in fleet if item["feed_configured"]]),
                    "feeds_online": len([item for item in fleet if item["feed_online"]]),
                    "available": len([item for item in fleet if item.get("availability") == "available"]),
                    "assigned": len([item for item in fleet if item.get("availability") == "assigned"]),
                    "charging": len([item for item in fleet if item.get("availability") == "charging"]),
                    "maintenance": len([item for item in fleet if item.get("availability") == "maintenance"]),
                    "active_missions": len([item for item in missions if item.get("status") in ACTIVE_MISSION_STATES]),
                    "awaiting_acknowledgement": len([item for item in missions if item.get("status") == "dispatched"]),
                },
                "fleet": fleet,
                "missions": missions,
            }

    def audit_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._lock:
            try:
                with open(self.audit_path, "r", encoding="utf-8") as audit_file:
                    lines = audit_file.readlines()
            except OSError:
                return []
        events = []
        for line in reversed(lines[-limit:]):
            try:
                events.append(json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return events
