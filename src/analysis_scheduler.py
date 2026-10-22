"""Decides which cameras get an analyzer process, and restarts failed ones.

Every counting worker loads its own copy of the model and keeps its CPU
threads busy, so a server can run only a few at once (``MAX_CONCURRENT_AI_FEEDS``).
Starting one per online camera exhausted RAM and CPU, the kernel or the
workers' own crashes then ended them, and the dashboard showed "Counting
unavailable" with nothing ever starting them again.

This module is pure policy (no processes, no clocks of its own) so it can be
unit-tested:

* at most ``max_active`` analyzers run; other wanted cameras wait in a FIFO
  queue and start as slots free up;
* a worker that exits is restarted with exponential backoff, so a transient
  crash heals itself without a crash loop;
* a worker that reports its model files are missing is *blocked* (restarting
  cannot help) and re-checked every ``model_retry`` seconds, so counting starts
  by itself once an operator installs the weights;
* a worker that produced a result is considered healthy and its failure
  history is cleared.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

# Keep in step with infer.py.
EXIT_MODEL_UNAVAILABLE = 3

QUEUED = "queued"
RUNNING = "running"
BACKOFF = "backoff"
BLOCKED = "blocked"


class Entry:
    __slots__ = ("camera_id", "source_url", "requested_at", "failures",
                 "next_attempt_at", "blocked_reason", "state", "last_reason")

    def __init__(self, camera_id: str, source_url: str, now: float) -> None:
        self.camera_id = camera_id
        self.source_url = source_url
        self.requested_at = now
        self.failures: List[float] = []
        self.next_attempt_at = 0.0
        self.blocked_reason: Optional[str] = None
        self.state = QUEUED
        self.last_reason: Optional[str] = None


class AnalysisScheduler:
    def __init__(
        self,
        max_active: int,
        *,
        clock: Callable[[], float] = time.time,
        backoff_base: float = 2.0,
        backoff_cap: float = 60.0,
        failure_window: float = 300.0,
        max_failures: int = 6,
        blocked_retry: float = 300.0,
        model_retry: float = 30.0,
    ) -> None:
        self.max_active = max(1, int(max_active))
        self._clock = clock
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.failure_window = float(failure_window)
        self.max_failures = int(max_failures)
        self.blocked_retry = float(blocked_retry)
        self.model_retry = float(model_retry)
        self._wanted: "OrderedDict[str, Entry]" = OrderedDict()

    # -- demand ------------------------------------------------------------

    def want(self, camera_id: str, source_url: str) -> Entry:
        """Register (or refresh) the wish to analyze a camera."""
        entry = self._wanted.get(camera_id)
        if entry is None:
            entry = Entry(camera_id, source_url, self._clock())
            self._wanted[camera_id] = entry
        else:
            entry.source_url = source_url
        return entry

    def drop(self, camera_id: str) -> None:
        """The camera no longer needs an analyzer (offline, stopped, viewing)."""
        self._wanted.pop(camera_id, None)

    def entry(self, camera_id: str) -> Optional[Entry]:
        return self._wanted.get(camera_id)

    # -- events from the process layer --------------------------------------

    def started(self, camera_id: str) -> None:
        entry = self._wanted.get(camera_id)
        if entry is not None:
            entry.state = RUNNING
            entry.blocked_reason = None

    def healthy(self, camera_id: str) -> None:
        """The worker delivered a result: forget earlier failures."""
        entry = self._wanted.get(camera_id)
        if entry is not None and entry.failures:
            entry.failures.clear()

    def exited(self, camera_id: str, exit_code: Optional[int], reason: str = "") -> Entry:
        """Record that a worker ended and schedule what happens next."""
        entry = self._wanted.get(camera_id)
        if entry is None:
            entry = self.want(camera_id, "")
        now = self._clock()
        entry.last_reason = reason or f"exit code {exit_code}"
        if exit_code == EXIT_MODEL_UNAVAILABLE:
            entry.state = BLOCKED
            entry.blocked_reason = reason or "Counting model files are missing"
            entry.next_attempt_at = now + self.model_retry
            return entry
        entry.failures = [t for t in entry.failures if now - t < self.failure_window]
        entry.failures.append(now)
        if len(entry.failures) >= self.max_failures:
            entry.state = BLOCKED
            entry.blocked_reason = (
                f"Counting stopped {len(entry.failures)} times in "
                f"{int(self.failure_window // 60)} minutes ({entry.last_reason})"
            )
            entry.next_attempt_at = now + self.blocked_retry
            entry.failures.clear()
            return entry
        delay = min(self.backoff_cap, self.backoff_base ** len(entry.failures))
        entry.state = BACKOFF
        entry.next_attempt_at = now + delay
        return entry

    # -- decisions -----------------------------------------------------------

    def due(self, active_ids) -> List[Entry]:
        """Entries to start now, oldest request first, within free capacity."""
        active = set(active_ids)
        free = self.max_active - len(active)
        if free <= 0:
            return []
        now = self._clock()
        ready: List[Entry] = []
        for entry in sorted(self._wanted.values(), key=lambda e: e.requested_at):
            if entry.camera_id in active:
                continue
            if entry.state in (BACKOFF, BLOCKED) and now < entry.next_attempt_at:
                continue
            ready.append(entry)
            if len(ready) >= free:
                break
        return ready

    def queue_position(self, camera_id: str, active_ids) -> Optional[int]:
        """1-based place in the start queue, or None if not waiting."""
        active = set(active_ids)
        waiting = [e for e in sorted(self._wanted.values(), key=lambda e: e.requested_at)
                   if e.camera_id not in active and e.state in (QUEUED, RUNNING)]
        for index, entry in enumerate(waiting, 1):
            if entry.camera_id == camera_id:
                return index
        return None

    def snapshot(self, active_ids=()) -> Dict[str, dict]:
        active = set(active_ids)
        now = self._clock()
        out = {}
        for entry in self._wanted.values():
            out[entry.camera_id] = {
                "state": RUNNING if entry.camera_id in active else entry.state,
                "failures": len(entry.failures),
                "retry_in_seconds": (
                    round(max(0.0, entry.next_attempt_at - now), 1)
                    if entry.state in (BACKOFF, BLOCKED) else None
                ),
                "reason": entry.blocked_reason or entry.last_reason,
            }
        return out
