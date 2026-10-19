"""
Real drone flight analytics.

There is intentionally NO seeded/demo/reference flight data in this module.

A flight session starts only when the real source stream becomes online and
ends only when the real source stream becomes offline.

Completed sessions are persisted to disk. Current live sessions are measured
from the actual MediaMTX online event.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


class DroneFlightAnalyticsStore:
    VERSION = 2

    def __init__(self, data_file: str = "config/drone_flight_analytics.json"):
        self.data_file = data_file
        self._lock = threading.RLock()

        # Current process live sessions.
        # drone_id -> unix start timestamp
        self.flight_start_times: Dict[str, float] = {}

        # Completed REAL sessions only.
        self.sessions: List[Dict[str, Any]] = []

        self._load()

    def _load(self) -> None:
        """
        Only accepts the real v2 session format.

        Any older seeded/reference format is deliberately ignored instead of
        contaminating operational analytics.
        """
        with self._lock:
            self.sessions = []

            if not os.path.exists(self.data_file):
                self.save()
                return

            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)

                if (
                    isinstance(payload, dict)
                    and payload.get("version") == self.VERSION
                    and isinstance(payload.get("sessions"), list)
                ):
                    self.sessions = [
                        s for s in payload["sessions"]
                        if isinstance(s, dict)
                        and s.get("drone_id")
                        and s.get("start")
                        and s.get("end")
                    ]
                else:
                    print(
                        "[DRONE ANALYTICS] Old/demo analytics format detected. "
                        "Discarding it and starting with real data only."
                    )
                    self.sessions = []
                    self.save()

            except Exception as exc:
                print(f"[DRONE ANALYTICS] Could not load analytics file: {exc}")
                self.sessions = []
                self.save()

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)

            payload = {
                "version": self.VERSION,
                "sessions": self.sessions,
            }

            # Write in-place because this file can be a Docker bind mount.
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

    @staticmethod
    def _now() -> datetime:
        return datetime.now().astimezone()

    @staticmethod
    def _parse_iso(value: str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt
        except Exception:
            return None

    @staticmethod
    def _overlap_seconds(
        start: datetime,
        end: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> float:
        left = max(start, window_start)
        right = min(end, window_end)
        return max(0.0, (right - left).total_seconds())

    @staticmethod
    def _display(seconds: float) -> str:
        seconds = max(0.0, float(seconds))

        if seconds == 0:
            return "0"

        minutes = int(seconds // 60)

        if minutes == 0:
            return "<1 min"

        hours = round(seconds / 3600.0, 1)
        return f"{minutes} mins ({hours}h)"

    def on_drone_online(self, drone_id: str) -> None:
        drone_id = str(drone_id).strip().lower()

        if not drone_id:
            return

        with self._lock:
            # Repeated MediaMTX events do NOT create duplicate sessions.
            if drone_id not in self.flight_start_times:
                self.flight_start_times[drone_id] = time.time()
                print(f"[DRONE ANALYTICS] REAL flight started: {drone_id}")

    def on_drone_offline(self, drone_id: str) -> None:
        drone_id = str(drone_id).strip().lower()

        with self._lock:
            started = self.flight_start_times.pop(drone_id, None)

            if started is None:
                return

            ended = time.time()
            duration = max(0.0, ended - started)

            # Ignore sub-second state flaps.
            if duration < 1.0:
                return

            start_dt = datetime.fromtimestamp(started).astimezone()
            end_dt = datetime.fromtimestamp(ended).astimezone()

            self.sessions.append({
                "drone_id": drone_id,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration_seconds": round(duration, 3),
            })

            # Keep a large but bounded history.
            if len(self.sessions) > 100000:
                self.sessions = self.sessions[-100000:]

            self.save()

            print(
                f"[DRONE ANALYTICS] REAL flight ended: "
                f"{drone_id} duration={duration:.1f}s"
            )

    def _session_spans(self, now: datetime) -> List[Dict[str, Any]]:
        spans: List[Dict[str, Any]] = []

        with self._lock:
            for session in self.sessions:
                start = self._parse_iso(str(session.get("start", "")))
                end = self._parse_iso(str(session.get("end", "")))

                if start is None or end is None or end <= start:
                    continue

                spans.append({
                    "drone_id": str(session["drone_id"]).lower(),
                    "start": start,
                    "end": end,
                    "active": False,
                })

            for drone_id, started in self.flight_start_times.items():
                start = datetime.fromtimestamp(started).astimezone()

                spans.append({
                    "drone_id": drone_id,
                    "start": start,
                    "end": now,
                    "active": True,
                })

        return spans

    def get_drone_flight_analytics(
        self,
        cameras: List[Dict[str, Any]],
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:

        now = self._now()
        tz = now.tzinfo
        today = now.date()

        drones = [
            c for c in cameras
            if str(c.get("id", "")).lower().startswith("drone-")
        ]

        # Reconcile analytics state with actual MediaMTX source state.
        for cam in drones:
            drone_id = str(cam["id"]).lower()
            online = bool(cam.get("source_online", False))

            if online:
                self.on_drone_online(drone_id)
            else:
                self.on_drone_offline(drone_id)

        try:
            selected_from = (
                datetime.strptime(from_date, "%Y-%m-%d").date()
                if from_date else today - timedelta(days=7)
            )
        except Exception:
            selected_from = today - timedelta(days=7)

        try:
            selected_to = (
                datetime.strptime(to_date, "%Y-%m-%d").date()
                if to_date else today
            )
        except Exception:
            selected_to = today

        if selected_from > selected_to:
            selected_from, selected_to = selected_to, selected_from

        # Avoid pathological multi-year browser charts.
        if (selected_to - selected_from).days > 90:
            selected_from = selected_to - timedelta(days=90)

        today_start = datetime.combine(
            today,
            datetime.min.time(),
            tzinfo=tz,
        )
        tomorrow_start = today_start + timedelta(days=1)

        week_start = today_start - timedelta(days=6)

        spans = self._session_spans(now)

        rows: List[Dict[str, Any]] = []

        for cam in drones:
            drone_id = str(cam["id"]).lower()
            relevant = [s for s in spans if s["drone_id"] == drone_id]

            today_seconds = sum(
                self._overlap_seconds(
                    s["start"], s["end"], today_start, tomorrow_start
                )
                for s in relevant
            )

            week_seconds = sum(
                self._overlap_seconds(
                    s["start"], s["end"], week_start, tomorrow_start
                )
                for s in relevant
            )

            is_online = bool(cam.get("source_online", False))

            real_name = str(cam.get("name") or cam.get("id") or drone_id)
            real_location = str(cam.get("location") or "").strip()

            rows.append({
                "drone_id": drone_id,
                "location": real_name,
                "name": real_name,
                "status": "ONLINE" if is_online else "OFFLINE",
                "is_online": is_online,

                "today_minutes": int(today_seconds // 60),
                "today_hours": round(today_seconds / 3600.0, 2),
                "today_duration_display": self._display(today_seconds),

                "last_7_days_minutes": int(week_seconds // 60),
                "last_7_days_hours": round(week_seconds / 3600.0, 2),
                "last_7_days_display": self._display(week_seconds),

                # Only real configured metadata is exposed.
                "contact": real_location if real_location else "—",

                # There is currently no real battery telemetry source.
                "battery_health": "N/A",

                "total_missions": sum(
                    1 for s in relevant if not s["active"]
                ) + (1 if any(s["active"] for s in relevant) else 0),
            })

        chart_labels: List[str] = []
        chart_values: List[int] = []

        cursor = selected_from

        while cursor <= selected_to:
            day_start = datetime.combine(
                cursor,
                datetime.min.time(),
                tzinfo=tz,
            )
            day_end = day_start + timedelta(days=1)

            seconds = sum(
                self._overlap_seconds(
                    s["start"], s["end"], day_start, day_end
                )
                for s in spans
            )

            chart_labels.append(cursor.strftime("%d %b"))
            chart_values.append(int(seconds // 60))

            cursor += timedelta(days=1)

        total_today_seconds = sum(
            self._overlap_seconds(
                s["start"], s["end"], today_start, tomorrow_start
            )
            for s in spans
        )

        total_week_seconds = sum(
            self._overlap_seconds(
                s["start"], s["end"], week_start, tomorrow_start
            )
            for s in spans
        )

        return {
            "title": "Centralised Drone Monitoring Portal",
            "subtitle": "Real Drone Analytics",
            "data_source": "Live MediaMTX source-state events",
            "from_date": selected_from.isoformat(),
            "to_date": selected_to.isoformat(),

            "chart": {
                "label": "Actual Flying Minutes (All Drones)",
                "labels": chart_labels,
                "data": chart_values,
                "unit": "Minutes",
            },

            "summary": {
                "total_drones": len(rows),
                "active_drones": sum(1 for r in rows if r["is_online"]),

                "today_total_minutes": int(total_today_seconds // 60),
                "today_total_hours": round(
                    total_today_seconds / 3600.0, 2
                ),

                "week_total_minutes": int(total_week_seconds // 60),
                "week_total_hours": round(
                    total_week_seconds / 3600.0, 2
                ),
            },

            "drones": rows,
        }


drone_flight_analytics_store = DroneFlightAnalyticsStore()
