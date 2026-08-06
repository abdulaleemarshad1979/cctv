"""Keep application camera state synchronized with MediaMTX.

This module deliberately has no dependency on the dashboard.  MediaMTX is the
source of truth for whether a publisher is online; the UI only renders the
state exposed by the backend.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any


LOGGER = logging.getLogger(__name__)


class MediaMTXStateMonitor:
    """Reconcile MediaMTX paths with the application's camera records.

    Webhooks remain the fastest notification mechanism.  This monitor is an
    independent fallback that repairs state after app restarts, missed hooks,
    or container start-order races.
    """

    def __init__(
        self,
        *,
        api_url: str,
        list_cameras: Callable[[], Iterable[dict[str, Any]]],
        resolve_camera: Callable[[str], tuple[dict[str, Any] | None, str | None]],
        apply_state: Callable[[str, str], Any],
        interval_seconds: float = 2.0,
        request_timeout_seconds: float = 2.0,
        offline_confirmations: int = 2,
        fetch_json: Callable[[str, float], dict[str, Any]] | None = None,
    ) -> None:
        self.api_url = api_url
        self._list_cameras = list_cameras
        self._resolve_camera = resolve_camera
        self._apply_state = apply_state
        self.interval_seconds = max(0.25, float(interval_seconds))
        self.request_timeout_seconds = max(0.25, float(request_timeout_seconds))
        self.offline_confirmations = max(1, int(offline_confirmations))
        self._fetch_json = fetch_json or self._default_fetch_json

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()
        self._misses: dict[str, int] = {}
        self._last_success_monotonic: float | None = None
        self._last_error: str | None = None
        self._last_ready_paths: set[str] = set()

    @staticmethod
    def _default_fetch_json(url: str, timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"MediaMTX API returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _ready_paths(payload: dict[str, Any]) -> set[str]:
        ready_paths: set[str] = set()
        for item in payload.get("items", []) or []:
            name = str(item.get("name", "")).strip("/")
            if not name:
                continue
            # New MediaMTX releases expose both ready and online.  Older
            # releases may expose only ready, so online falls back to ready.
            ready = bool(item.get("ready", item.get("available", False)))
            online = bool(item.get("online", ready))
            if ready and online:
                ready_paths.add(name)
        return ready_paths

    def sync_once(self) -> set[str] | None:
        """Perform one reconciliation pass.

        API failures never mark streams offline.  This avoids dropping healthy
        video from the dashboard during a temporary MediaMTX API timeout.
        """

        if not self._sync_lock.acquire(blocking=False):
            return None

        try:
            try:
                payload = self._fetch_json(
                    self.api_url,
                    self.request_timeout_seconds,
                )
                ready_paths = self._ready_paths(payload)
            except Exception as exc:  # network/runtime boundary
                self._last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.warning("MediaMTX state synchronization failed: %s", exc)
                return None

            ready_camera_ids: set[str] = set()
            for path in ready_paths:
                camera, role = self._resolve_camera(path)
                if camera is not None and role == "source":
                    ready_camera_ids.add(str(camera.get("id", "")))

            for camera in list(self._list_cameras()):
                camera_id = str(camera.get("id", ""))
                source_path = str(camera.get("source_stream_path", "")).strip("/")
                if not camera_id or not source_path:
                    continue

                is_ready = camera_id in ready_camera_ids
                is_marked_online = bool(camera.get("source_online", False))

                if is_ready:
                    self._misses.pop(camera_id, None)
                    if not is_marked_online:
                        self._apply_state(source_path, "online")
                    continue

                if not is_marked_online:
                    self._misses.pop(camera_id, None)
                    continue

                misses = self._misses.get(camera_id, 0) + 1
                self._misses[camera_id] = misses
                if misses >= self.offline_confirmations:
                    self._apply_state(source_path, "offline")
                    self._misses.pop(camera_id, None)

            self._last_ready_paths = ready_paths
            self._last_success_monotonic = time.monotonic()
            self._last_error = None
            return ready_paths
        finally:
            self._sync_lock.release()

    def _run(self) -> None:
        # Reconcile immediately so an app/UI restart does not wait for the
        # first interval before rediscovering active publishers.
        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception as exc:  # keep the safety loop alive
                self._last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("Unexpected MediaMTX reconciliation error")
            self._stop_event.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mediamtx-state-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def snapshot(self) -> dict[str, Any]:
        age_seconds = None
        if self._last_success_monotonic is not None:
            age_seconds = round(
                max(0.0, time.monotonic() - self._last_success_monotonic),
                2,
            )
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_success_age_seconds": age_seconds,
            "last_error": self._last_error,
            "ready_paths": sorted(self._last_ready_paths),
        }
