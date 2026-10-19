"""Live source telemetry derived from MediaMTX's path list.

The application already polls ``/v3/paths/list`` every couple of seconds.
Each item carries a cumulative ``inboundBytes`` counter, which is enough to
measure the source bitrate, the time the last media bytes arrived, and
uplink stalls.  That in turn lets a disconnect be attributed:

* bytes stopped flowing and MediaMTX later dropped the publisher
  -> the uplink network stalled until the server read-timeout fired;
* bytes were still flowing when the path disappeared
  -> the camera / drone app closed its own connection.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src import stream_events as ev


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class StreamTelemetry:
    def __init__(
        self,
        events: ev.StreamEventLog,
        resolve_camera_id: Callable[[str], Optional[str]],
        *,
        stall_after_seconds: float = 4.0,
        server_read_timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._events = events
        self._resolve = resolve_camera_id
        self.stall_after_seconds = max(1.0, float(stall_after_seconds))
        self.server_read_timeout_seconds = max(1.0, float(server_read_timeout_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._paths: Dict[str, Dict[str, Any]] = {}
        self._api_ok: Optional[bool] = None
        self._api_failed_since: Optional[float] = None

    @staticmethod
    def _bytes(item: dict) -> int:
        for key in ("inboundBytes", "bytesReceived"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return 0

    @staticmethod
    def _video(item: dict) -> dict:
        for track in item.get("tracks2") or []:
            if not isinstance(track, dict):
                continue
            props = track.get("codecProps") or {}
            if "width" in props or str(track.get("codec", "")).upper() in ("H264", "H265", "AV1", "VP9"):
                return {
                    "codec": track.get("codec"),
                    "width": props.get("width"),
                    "height": props.get("height"),
                }
        return {}

    def observe(self, payload: dict, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        self.api_ok(now)
        ready: Dict[str, dict] = {}
        for item in payload.get("items", []) or []:
            name = str(item.get("name", "")).strip("/")
            if not name.startswith("live/"):
                continue
            is_ready = bool(item.get("ready", item.get("available", False)))
            if is_ready and bool(item.get("online", is_ready)):
                ready[name] = item

        with self._lock:
            for name, item in ready.items():
                camera_id = self._resolve(name)
                if not camera_id:
                    continue
                received = self._bytes(item)
                state = self._paths.get(name)
                if state is None or not state["online"]:
                    previous = state or {}
                    gap = (now - previous["last_packet_at"]) if previous.get("last_packet_at") else None
                    state = {
                        "camera_id": camera_id,
                        "online": True,
                        "session_started_at": now,
                        "last_sample_at": now,
                        "last_bytes": received,
                        "last_packet_at": now,
                        "stall_since": None,
                        "bitrate_kbps": None,
                        "reconnects": previous.get("reconnects", 0) + (1 if previous else 0),
                        "sessions": previous.get("sessions", 0) + 1,
                    }
                    self._paths[name] = state
                    self._events.record(
                        camera_id, ev.CAMERA, "publisher_connected",
                        "Source stream is publishing to the server",
                        ts=now, path=name, gap_seconds=round(gap, 1) if gap is not None else None,
                    )
                else:
                    elapsed = max(1e-3, now - state["last_sample_at"])
                    delta = received - state["last_bytes"]
                    if delta > 0:
                        kbps = delta * 8.0 / elapsed / 1000.0
                        previous_rate = state["bitrate_kbps"]
                        state["bitrate_kbps"] = kbps if previous_rate is None else 0.6 * previous_rate + 0.4 * kbps
                        if state["stall_since"] is not None:
                            stalled_for = now - state["stall_since"]
                            self._events.record(
                                camera_id, ev.NETWORK, "uplink_recovered",
                                f"Media resumed after {stalled_for:.1f}s without data",
                                ts=now, path=name, stall_seconds=round(stalled_for, 1),
                            )
                            state["stall_since"] = None
                        state["last_packet_at"] = now
                    elif delta < 0:
                        self._events.record(
                            camera_id, ev.CAMERA, "publisher_replaced",
                            "A new publisher took over the path without a gap",
                            ts=now, path=name,
                        )
                        state["last_packet_at"] = now
                    elif state["stall_since"] is None and now - state["last_packet_at"] >= self.stall_after_seconds:
                        state["stall_since"] = state["last_packet_at"]
                        state["bitrate_kbps"] = 0.0
                        self._events.record(
                            camera_id, ev.NETWORK, "uplink_stalled",
                            f"No media received for {now - state['last_packet_at']:.1f}s",
                            ts=now, path=name,
                        )
                    state["last_bytes"] = received
                    state["last_sample_at"] = now
                state.update(self._video(item))
                state["readers"] = len(item.get("readers") or [])

            for name, state in self._paths.items():
                if not state["online"] or name in ready:
                    continue
                state["online"] = False
                silent_for = now - state["last_packet_at"]
                if state["stall_since"] is not None and silent_for >= 0.6 * self.server_read_timeout_seconds:
                    self._events.record(
                        state["camera_id"], ev.NETWORK, "publisher_timed_out",
                        f"Uplink delivered no media for {silent_for:.0f}s; the server dropped the stream",
                        ts=now, path=name, silent_seconds=round(silent_for, 1),
                    )
                else:
                    self._events.record(
                        state["camera_id"], ev.CAMERA, "publisher_disconnected",
                        "The camera/drone app closed its stream connection",
                        ts=now, path=name, silent_seconds=round(silent_for, 1),
                    )
                state["stall_since"] = None
                state["bitrate_kbps"] = None

    def api_ok(self, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        if self._api_ok is False and self._api_failed_since is not None:
            self._events.record(
                ev.SYSTEM_ID, ev.INGEST, "server_recovered",
                f"MediaMTX API reachable again after {now - self._api_failed_since:.0f}s",
                ts=now,
            )
        self._api_ok = True
        self._api_failed_since = None

    def api_failed(self, error: str, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        if self._api_ok is not False:
            self._events.record(
                ev.SYSTEM_ID, ev.INGEST, "server_unreachable",
                f"MediaMTX API request failed: {error}", ts=now,
            )
            self._api_failed_since = now
        self._api_ok = False

    def snapshot(self, camera_id: str, now: Optional[float] = None) -> dict:
        now = self._clock() if now is None else now
        with self._lock:
            state = next((s for s in self._paths.values() if s["camera_id"] == camera_id), None)
            if state is None:
                return {"observed": False}
            last_packet = state.get("last_packet_at")
            return {
                "observed": True,
                "online": state["online"],
                "bitrate_kbps": round(state["bitrate_kbps"], 1) if state.get("bitrate_kbps") is not None else None,
                "codec": state.get("codec"),
                "width": state.get("width"),
                "height": state.get("height"),
                "last_packet_at": _iso(last_packet),
                "seconds_since_last_packet": round(now - last_packet, 1) if last_packet else None,
                "stalled": state.get("stall_since") is not None,
                "stall_seconds": round(now - state["stall_since"], 1) if state.get("stall_since") else 0.0,
                "session_started_at": _iso(state.get("session_started_at")),
                "reconnects": state.get("reconnects", 0),
                "sessions": state.get("sessions", 0),
                "server_readers": state.get("readers", 0),
            }

    def source_height(self, camera_id: str) -> Optional[int]:
        with self._lock:
            for state in self._paths.values():
                if state["camera_id"] == camera_id and state["online"]:
                    height = state.get("height")
                    return int(height) if height else None
        return None

    def server_status(self) -> dict:
        return {
            "api_reachable": self._api_ok,
            "unreachable_since": _iso(self._api_failed_since),
        }
