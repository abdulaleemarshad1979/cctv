"""Per-camera stream event timeline with bounded, rotating storage.

Every event names the layer it came from so an operator can tell whether a
failure started at the camera, the uplink network, the MediaMTX ingest
server, the recorder, the disk, or a viewer's browser.

Storage is append-only JSON Lines.  The active file rotates by size and
rotated files older than the retention window are removed, so diagnostics
survive long enough to investigate intermittent faults without growing
without bound.  Only these diagnostic log files are ever deleted.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


CAMERA = "camera"        # publisher (drone app / camera) opened or closed the stream
NETWORK = "network"      # uplink stalled or packets stopped arriving
INGEST = "ingest"        # MediaMTX streaming server
RECORDER = "recorder"    # recording files / segment writer
DISK = "disk"            # free space and write failures
BROWSER = "browser"      # a viewer's player
ANALYZER = "analyzer"    # AI counting worker
CATEGORIES = (CAMERA, NETWORK, INGEST, RECORDER, DISK, BROWSER, ANALYZER)

SYSTEM_ID = "*"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class StreamEventLog:
    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        backup_count: int = 10,
        retention_days: float = 30.0,
        memory_per_camera: int = 300,
    ) -> None:
        self.path = path
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self.retention_seconds = max(1.0, float(retention_days)) * 86400.0
        self._memory_per_camera = max(10, int(memory_per_camera))
        self._lock = threading.Lock()
        self._recent: Dict[str, Deque[dict]] = {}
        self._write_errors = 0
        self._last_write_error: Optional[str] = None
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._restore_recent()

    def _remember(self, event: dict) -> None:
        bucket = self._recent.get(event["camera_id"])
        if bucket is None:
            bucket = self._recent[event["camera_id"]] = deque(maxlen=self._memory_per_camera)
        bucket.append(event)

    def _restore_recent(self) -> None:
        """Reload the tail of the active file so restarts keep the timeline."""
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 2 * 1024 * 1024))
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return
        for line in lines[1:] if len(lines) > 1 else lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and "camera_id" in event and "ts" in event:
                self._remember(event)

    def _rotate_locked(self) -> None:
        for index in range(self.backup_count - 1, 0, -1):
            src = f"{self.path}.{index}"
            if os.path.exists(src):
                os.replace(src, f"{self.path}.{index + 1}")
        if os.path.exists(self.path):
            os.replace(self.path, f"{self.path}.1")
        overflow = f"{self.path}.{self.backup_count + 1}"
        if os.path.exists(overflow):
            os.remove(overflow)
        cutoff = time.time() - self.retention_seconds
        for index in range(1, self.backup_count + 1):
            rotated = f"{self.path}.{index}"
            try:
                if os.path.getmtime(rotated) < cutoff:
                    os.remove(rotated)
            except OSError:
                pass

    def record(
        self,
        camera_id: str,
        category: str,
        event: str,
        detail: str = "",
        *,
        ts: Optional[float] = None,
        **fields: Any,
    ) -> dict:
        if category not in CATEGORIES:
            raise ValueError(f"unknown event category: {category}")
        now = time.time() if ts is None else float(ts)
        entry = {
            "ts": round(now, 3),
            "time": _iso(now),
            "camera_id": str(camera_id or SYSTEM_ID),
            "category": category,
            "event": str(event),
            "detail": str(detail)[:500],
        }
        for key, value in fields.items():
            if value is not None:
                entry[key] = value
        line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            self._remember(entry)
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) + len(line) > self.max_bytes:
                    self._rotate_locked()
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError as exc:
                self._write_errors += 1
                self._last_write_error = f"{type(exc).__name__}: {exc}"
        return entry

    def recent(
        self,
        camera_id: Optional[str] = None,
        *,
        limit: int = 200,
        since: Optional[float] = None,
        categories: Optional[List[str]] = None,
    ) -> List[dict]:
        with self._lock:
            if camera_id is None:
                events = [event for bucket in self._recent.values() for event in bucket]
            else:
                events = list(self._recent.get(str(camera_id), ()))
                if str(camera_id) != SYSTEM_ID:
                    events += list(self._recent.get(SYSTEM_ID, ()))
        if since is not None:
            events = [event for event in events if event["ts"] >= since]
        if categories:
            events = [event for event in events if event["category"] in categories]
        events.sort(key=lambda event: event["ts"])
        return events[-max(1, int(limit)):]

    def snapshot(self) -> dict:
        return {
            "path": self.path,
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "retention_days": round(self.retention_seconds / 86400.0, 2),
            "write_errors": self._write_errors,
            "last_write_error": self._last_write_error,
        }
