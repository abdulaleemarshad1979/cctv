"""Recording continuity, integrity and gap accounting for MediaMTX archives.

MediaMTX writes one fMP4 file per publisher session (split at
``recordSegmentDuration``).  A drone that drops its uplink therefore leaves
one file per connection.  This module measures what was actually recorded:

* every segment's real duration and whether it contains video (ffprobe,
  cached by size+mtime so each finished file is probed once);
* gaps between consecutive segments, grouped into flights;
* the event that most likely caused each gap (camera / network / ingest),
  taken from the stream event log;
* a recorder-stall check: a source that is online while its newest file
  stopped growing.

Nothing here writes to, repairs, or deletes a recording, and no footage is
ever synthesised: gaps are reported, never filled.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from src import stream_events as ev


FILENAME_RE = re.compile(r"^(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)-(\d{1,9})\.mp4$")
CAMERA_DIR_RE = re.compile(r"^drone(\d+)$")


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_segment_start(filename: str) -> Optional[float]:
    """MediaMTX names files ``%Y-%m-%d_%H-%M-%S-%f`` in UTC."""
    match = FILENAME_RE.match(filename)
    if not match:
        return None
    base = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=timezone.utc)
    fraction = float("0." + match.group(2))
    return base.timestamp() + fraction


def ffprobe_segment(path: str, ffprobe: str = "ffprobe", timeout: float = 30.0) -> dict:
    result = {"duration": None, "has_video": False, "codec": None, "width": None,
              "height": None, "fps": None, "probe_error": None}
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
             "-of", "json", path],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["probe_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    try:
        result["duration"] = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            result["has_video"] = True
            result["codec"] = stream.get("codec_name")
            result["width"] = stream.get("width")
            result["height"] = stream.get("height")
            rate = str(stream.get("avg_frame_rate") or "")
            try:
                num, den = rate.split("/")
                result["fps"] = round(float(num) / float(den), 2) if float(den) else None
            except ValueError:
                pass
            break
    if proc.returncode != 0 or proc.stderr.strip():
        result["probe_error"] = (proc.stderr.strip() or f"ffprobe exit {proc.returncode}")[:200]
    return result


class RecordingContinuity:
    def __init__(
        self,
        recordings_dir: str,
        cache_file: str,
        *,
        events: Optional[ev.StreamEventLog] = None,
        scan_days: float = 14.0,
        flight_break_seconds: float = 600.0,
        gap_tolerance_seconds: float = 1.5,
        active_window_seconds: float = 10.0,
        recorder_stall_seconds: float = 20.0,
        disk_warn_free_gb: float = 50.0,
        disk_critical_free_gb: float = 10.0,
        probe: Callable[[str], dict] = ffprobe_segment,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.recordings_dir = recordings_dir
        self.cache_file = cache_file
        self._events = events
        self.scan_seconds = max(1.0, float(scan_days)) * 86400.0
        self.flight_break_seconds = float(flight_break_seconds)
        self.gap_tolerance_seconds = float(gap_tolerance_seconds)
        self.active_window_seconds = float(active_window_seconds)
        self.recorder_stall_seconds = float(recorder_stall_seconds)
        self.disk_warn_bytes = float(disk_warn_free_gb) * 1024 ** 3
        self.disk_critical_bytes = float(disk_critical_free_gb) * 1024 ** 3
        self._probe = probe
        self._clock = clock
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._segments: Dict[str, dict] = {}
        self._initialized = False
        self._recorder_state: Dict[str, str] = {}
        self._online_since: Dict[str, float] = {}
        self._disk_level: Optional[str] = None
        self._disk: dict = {}
        self.last_scan_at: Optional[float] = None
        self.last_scan_seconds: Optional[float] = None
        self._load_cache()

    # ---- cache -----------------------------------------------------------------
    def _load_cache(self) -> None:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._segments = {k: v for k, v in data.get("segments", {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._segments = {}

    def _save_cache(self) -> None:
        parent = os.path.dirname(self.cache_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.cache_file + ".tmp"
        with self._lock:
            final = {k: v for k, v in self._segments.items() if v.get("final")}
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "segments": final}, handle, separators=(",", ":"))
            os.replace(tmp, self.cache_file)
        except OSError:
            pass

    # ---- scanning ----------------------------------------------------------------
    def _emit(self, *args, **kwargs) -> None:
        if self._events is not None:
            self._events.record(*args, **kwargs)

    def scan(self) -> dict:
        """Probe new or changed segment files.  Safe to call repeatedly."""
        if not self._scan_lock.acquire(blocking=False):
            return {"skipped": True}
        started = self._clock()
        probed = 0
        try:
            live_root = os.path.join(self.recordings_dir, "live")
            cutoff = started - self.scan_seconds
            seen = set()
            try:
                camera_dirs = sorted(os.listdir(live_root))
            except OSError:
                camera_dirs = []
            for dirname in camera_dirs:
                match = CAMERA_DIR_RE.match(dirname)
                if not match:
                    continue
                camera_id = f"drone-{int(match.group(1))}"
                directory = os.path.join(live_root, dirname)
                try:
                    names = os.listdir(directory)
                except OSError:
                    continue
                for name in names:
                    start = parse_segment_start(name)
                    if start is None or start < cutoff:
                        continue
                    rel = f"live/{dirname}/{name}"
                    path = os.path.join(directory, name)
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    seen.add(rel)
                    key = f"{stat.st_size}:{stat.st_mtime_ns}"
                    growing = started - stat.st_mtime < self.active_window_seconds
                    with self._lock:
                        cached = self._segments.get(rel)
                    if cached and cached.get("key") == key and cached.get("final"):
                        continue
                    if growing:
                        entry = dict(cached or {})
                        entry.update({
                            "camera_id": camera_id, "file": rel, "start": start, "key": key,
                            "size": stat.st_size, "mtime": stat.st_mtime, "final": False,
                            "duration": max(0.0, stat.st_mtime - start),
                        })
                        entry.setdefault("has_video", None)
                        with self._lock:
                            self._segments[rel] = entry
                        continue
                    info = self._probe(path)
                    probed += 1
                    entry = {
                        "camera_id": camera_id, "file": rel, "start": start, "key": key,
                        "size": stat.st_size, "mtime": stat.st_mtime, "final": True, **info,
                    }
                    with self._lock:
                        was_tracked = rel in self._segments
                        self._segments[rel] = entry
                    if self._initialized or was_tracked:
                        self._report_finalized(entry)
            with self._lock:
                for rel in [rel for rel in self._segments if rel not in seen]:
                    # Out of the scan window (or moved by an operator): forget it.
                    self._segments.pop(rel, None)
            if probed:
                self._save_cache()
            self._initialized = True
            return {"probed": probed, "segments": len(seen)}
        finally:
            self.last_scan_at = self._clock()
            self.last_scan_seconds = round(self.last_scan_at - started, 2)
            self._scan_lock.release()

    def _report_finalized(self, entry: dict) -> None:
        duration = entry.get("duration")
        detail = f"{entry['file']} closed after {duration:.1f}s" if duration is not None else f"{entry['file']} closed"
        self._emit(entry["camera_id"], ev.RECORDER, "segment_finalized", detail,
                   file=entry["file"], duration_seconds=round(duration, 2) if duration else duration,
                   has_video=entry.get("has_video"), size_bytes=entry.get("size"))
        if entry.get("probe_error") or not entry.get("has_video"):
            problem = entry.get("probe_error") or "segment contains no video track"
            self._emit(entry["camera_id"], ev.RECORDER, "segment_integrity_warning",
                       f"{entry['file']}: {problem}", file=entry["file"])

    # ---- recorder / disk health -----------------------------------------------
    def check_recorder(self, camera_id: str, source_online: bool) -> str:
        """Return 'recording', 'stalled' or 'idle' and log transitions."""
        now = self._clock()
        if not source_online:
            self._online_since.pop(camera_id, None)
            state = "idle"
        else:
            since = self._online_since.setdefault(camera_id, now)
            newest = self._newest_mtime(camera_id)
            if newest is not None and now - newest < self.recorder_stall_seconds:
                state = "recording"
            elif now - since < self.recorder_stall_seconds:
                state = "recording"  # stream just started; first file may not exist yet
            else:
                state = "stalled"
        previous = self._recorder_state.get(camera_id)
        if previous != state:
            if state == "stalled":
                self._emit(camera_id, ev.RECORDER, "recording_stalled",
                           f"Source is online but no recording file was written for {self.recorder_stall_seconds:.0f}s")
            elif state == "recording" and previous == "stalled":
                self._emit(camera_id, ev.RECORDER, "recording_resumed", "Recording file is growing again")
            self._recorder_state[camera_id] = state
        return state

    def _newest_mtime(self, camera_id: str) -> Optional[float]:
        number = camera_id.split("-")[-1]
        directory = os.path.join(self.recordings_dir, "live", f"drone{number}")
        newest = None
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.endswith(".mp4"):
                        mtime = entry.stat().st_mtime
                        newest = mtime if newest is None or mtime > newest else newest
        except OSError:
            return None
        return newest

    def check_disk(self) -> dict:
        try:
            usage = shutil.disk_usage(self.recordings_dir)
        except OSError as exc:
            self._disk = {"error": str(exc)}
            return self._disk
        level = "ok"
        if usage.free < self.disk_critical_bytes:
            level = "critical"
        elif usage.free < self.disk_warn_bytes:
            level = "low"
        if level != self._disk_level:
            if self._disk_level is not None or level != "ok":
                self._emit(ev.SYSTEM_ID, ev.DISK, f"disk_{level}",
                           f"Recording disk free space {usage.free / 1024 ** 3:.1f} GB ({level})")
            self._disk_level = level
        self._disk = {
            "level": level,
            "free_gb": round(usage.free / 1024 ** 3, 1),
            "total_gb": round(usage.total / 1024 ** 3, 1),
            "used_percent": round(100.0 * usage.used / usage.total, 1) if usage.total else None,
        }
        return self._disk

    # ---- reporting ------------------------------------------------------------
    def _cause(self, camera_id: str, gap_from: float, gap_to: float) -> Optional[dict]:
        if self._events is None:
            return None
        window = self._events.recent(camera_id, limit=1000, since=gap_from - 15.0)
        for event in window:
            if event["ts"] > gap_to + 5.0:
                break
            if event["category"] in (ev.CAMERA, ev.NETWORK, ev.INGEST, ev.RECORDER, ev.DISK) \
                    and event["event"] not in ("publisher_connected", "segment_finalized", "uplink_recovered"):
                return {"category": event["category"], "event": event["event"], "time": event["time"]}
        return None

    def report(self, camera_id: Optional[str] = None, hours: float = 24.0) -> dict:
        now = self._clock()
        since = now - max(0.1, float(hours)) * 3600.0
        with self._lock:
            segments = [dict(s) for s in self._segments.values()
                        if s.get("start", 0) + (s.get("duration") or 0) >= since
                        and (camera_id is None or s.get("camera_id") == camera_id)]
        by_camera: Dict[str, List[dict]] = {}
        for segment in segments:
            by_camera.setdefault(segment["camera_id"], []).append(segment)

        cameras = []
        for cam, items in sorted(by_camera.items(), key=lambda kv: int(kv[0].split("-")[-1])):
            items.sort(key=lambda s: s["start"])
            flights: List[dict] = []
            gaps: List[dict] = []
            current = None
            for segment in items:
                duration = segment.get("duration") or 0.0
                end = segment["start"] + duration
                if current is None or segment["start"] - current["end"] > self.flight_break_seconds:
                    current = {"start": segment["start"], "end": end, "recorded": 0.0, "missing": 0.0, "segments": 0}
                    flights.append(current)
                else:
                    gap = segment["start"] - current["end"]
                    if gap > self.gap_tolerance_seconds:
                        current["missing"] += gap
                        gaps.append({
                            "from": _iso(current["end"]), "to": _iso(segment["start"]),
                            "seconds": round(gap, 1), "cause": self._cause(cam, current["end"], segment["start"]),
                        })
                current["recorded"] += duration
                current["segments"] += 1
                current["end"] = max(current["end"], end)
            recorded = sum(s.get("duration") or 0.0 for s in items)
            last = items[-1]
            cameras.append({
                "camera_id": cam,
                "segments": len(items),
                "recorded_seconds": round(recorded, 1),
                "missing_seconds_within_flights": round(sum(f["missing"] for f in flights), 1),
                "gaps": gaps,
                "flights": [{
                    "start": _iso(f["start"]), "end": _iso(f["end"]), "segments": f["segments"],
                    "recorded_seconds": round(f["recorded"], 1), "missing_seconds": round(f["missing"], 1),
                    "coverage_percent": round(100.0 * f["recorded"] / max(1e-6, f["end"] - f["start"]), 1),
                } for f in flights],
                "short_segments_under_30s": sum(1 for s in items if (s.get("duration") or 0) < 30.0),
                "segments_without_video": sum(1 for s in items if s.get("final") and not s.get("has_video")),
                "probe_errors": sum(1 for s in items if s.get("probe_error")),
                "last_segment": {
                    "file": last["file"], "start": _iso(last["start"]),
                    "duration_seconds": round(last.get("duration") or 0.0, 1),
                    "growing": not last.get("final"),
                    "resolution": f"{last.get('width')}x{last.get('height')}" if last.get("width") else None,
                    "fps": last.get("fps"),
                },
                "recorder_state": self._recorder_state.get(cam, "idle"),
            })
        return {
            "generated_at": _iso(now),
            "window_hours": hours,
            "scan": {"last_scan_at": _iso(self.last_scan_at), "last_scan_seconds": self.last_scan_seconds,
                     "initialized": self._initialized},
            "disk": self._disk,
            "cameras": cameras,
        }
