"""
Flights: one archive entry per drone flight instead of one per MediaMTX file.

MediaMTX starts a new file whenever a publisher reconnects or its recorder
resets after timestamp drift (slow 4G uplinks), so one flight can be dozens of
short segments.  A flight is every segment of one drone, with the same video
format, separated by gaps of at most RECORDING_FLIGHT_GAP_SECONDS.

For playback the flight's segments are copied (no re-encode, identical
quality) into one regular MP4 with its index at the front, so browsers know
the duration and can seek instantly.  Copies live in a size-capped cache under
outputs/; original segments are never modified, and the cache is safe to
delete at any time.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from src.recording_continuity import parse_segment_start


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


FLIGHT_GAP_SECONDS = _env_float("RECORDING_FLIGHT_GAP_SECONDS", 120)
CACHE_MAX_BYTES = int(_env_float("RECORDING_PLAYBACK_CACHE_GB", 10) * 1024 ** 3)
PROGRESS_RE = re.compile(r"^out_time_us=(\d+)$")


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def group_flights(items: List[Dict[str, Any]], gap_seconds: float = FLIGHT_GAP_SECONDS) -> List[Dict[str, Any]]:
    """Group public recording items (one per file) into flights, newest first."""
    segments = []
    for item in items:
        start = parse_segment_start(item.get("filename") or "")
        if start is None:
            continue
        duration = max(0.0, float(item.get("duration_seconds") or 0))
        segments.append((item, start, start + duration))
    segments.sort(key=lambda s: (s[0]["drone_id"], s[1]))

    flights: List[Dict[str, Any]] = []
    current = None
    for item, start, end in segments:
        fmt = (item.get("codec"), item.get("width"), item.get("height"))
        if (
            current is None
            or current["drone_id"] != item["drone_id"]
            or current["_format"] != fmt
            or start - current["end_epoch"] > gap_seconds
        ):
            current = {
                "drone_id": item["drone_id"],
                "location": item.get("location") or item["drone_id"],
                "codec": item.get("codec"),
                "width": item.get("width") or 0,
                "height": item.get("height") or 0,
                "resolution": item.get("resolution"),
                "start_epoch": start,
                "end_epoch": end,
                "duration_seconds": 0.0,
                "gap_seconds": 0.0,
                "size_bytes": 0,
                "segments": [],
                "_format": fmt,
            }
            flights.append(current)
        else:
            current["gap_seconds"] += max(0.0, start - current["end_epoch"])
        current["end_epoch"] = max(current["end_epoch"], end)
        current["duration_seconds"] += end - start
        current["size_bytes"] += int(item.get("size_bytes") or 0)
        current["segments"].append({
            "relative_path": item["relative_path"],
            "start_epoch": round(start, 3),
            "duration_seconds": round(end - start, 3),
            "size_bytes": int(item.get("size_bytes") or 0),
            "url": item.get("video_url"),
        })

    for flight in flights:
        flight.pop("_format")
        first = flight["segments"][0]["relative_path"]
        flight["id"] = _short_hash(f"{flight['drone_id']}|{first}")
        flight["segment_count"] = len(flight["segments"])
        flight["start_epoch"] = round(flight["start_epoch"], 3)
        flight["end_epoch"] = round(flight["end_epoch"], 3)
        flight["duration_seconds"] = round(flight["duration_seconds"], 3)
        flight["gap_seconds"] = round(flight["gap_seconds"], 1)
        # Thumbnail from the longest segment (short ones are often a black
        # start-up frame).
        flight["thumbnail_path"] = max(flight["segments"], key=lambda s: s["duration_seconds"])["relative_path"]
    flights.sort(key=lambda f: f["start_epoch"], reverse=True)
    return flights


class FlightPlayback:
    """Build and cache single-file MP4 copies of flights and segment thumbnails."""

    def __init__(self, recordings_dir: str, cache_dir: str, max_cache_bytes: int = CACHE_MAX_BYTES,
                 ffmpeg: str = "ffmpeg"):
        self.recordings_dir = os.path.abspath(recordings_dir)
        self.cache_dir = cache_dir
        self.thumb_dir = os.path.join(cache_dir, "thumbs")
        self.max_cache_bytes = max_cache_bytes
        self.ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flight-remux")
        self._thumb_slots = threading.BoundedSemaphore(2)

    def _source(self, relative_path: str) -> str:
        path = os.path.abspath(os.path.join(self.recordings_dir, relative_path))
        if os.path.commonpath([path, self.recordings_dir]) != self.recordings_dir:
            raise ValueError("path outside recordings")
        return path

    def _cache_key(self, flight: Dict[str, Any]) -> str:
        parts = []
        for seg in flight["segments"]:
            try:
                stat = os.stat(self._source(seg["relative_path"]))
                parts.append(f"{seg['relative_path']}:{stat.st_size}:{stat.st_mtime_ns}")
            except (OSError, ValueError):
                parts.append(f"{seg['relative_path']}:missing")
        return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()

    def video_path(self, flight: Dict[str, Any]) -> str:
        return os.path.join(self.cache_dir, f"{self._cache_key(flight)}.mp4")

    def status(self, flight: Dict[str, Any], start: bool = True) -> Dict[str, Any]:
        """Return {state: ready|preparing|error, progress}; start a build if needed."""
        target = self.video_path(flight)
        if os.path.exists(target):
            return {"state": "ready", "progress": 1.0}
        key = os.path.basename(target)
        with self._lock:
            job = self._jobs.get(key)
            if job and job["state"] == "error" and time.time() - job["finished_at"] > 60:
                job = None
            if job is None and start:
                job = {"state": "preparing", "progress": 0.0, "error": None, "finished_at": 0.0}
                self._jobs[key] = job
                self._pool.submit(self._build, flight, target, job)
        if job is None:
            return {"state": "missing", "progress": 0.0}
        return {"state": job["state"], "progress": round(job["progress"], 3), "error": job["error"]}

    def _build(self, flight: Dict[str, Any], target: str, job: Dict[str, Any]) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        total = max(1.0, float(flight.get("duration_seconds") or 0))
        list_fd, list_path = tempfile.mkstemp(suffix=".txt", dir=self.cache_dir)
        partial = target + ".part"
        try:
            with os.fdopen(list_fd, "w", encoding="utf-8") as listing:
                for seg in flight["segments"]:
                    escaped = self._source(seg["relative_path"]).replace("'", "'\\''")
                    listing.write(f"file '{escaped}'\n")
            cmd = [
                self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-movflags", "+faststart", "-f", "mp4",
                "-progress", "pipe:1", "-nostats", partial,
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in proc.stdout:
                match = PROGRESS_RE.match(line.strip())
                if match:
                    # Copying is ~95% of the work; faststart rewrites the rest.
                    job["progress"] = min(0.95, int(match.group(1)) / 1e6 / total * 0.95)
            stderr = proc.stderr.read()
            if proc.wait() != 0 or not os.path.exists(partial):
                raise RuntimeError((stderr or "ffmpeg failed").strip()[-300:])
            os.replace(partial, target)
            job["progress"] = 1.0
            job["state"] = "ready"
            self._evict(keep=target)
        except Exception as exc:
            job["state"] = "error"
            job["error"] = str(exc)[:300]
            print(f"[RECORDINGS] flight {flight.get('id')} could not be prepared: {exc}")
        finally:
            job["finished_at"] = time.time()
            for leftover in (list_path, partial):
                try:
                    os.remove(leftover)
                except OSError:
                    pass

    def touch(self, path: str) -> None:
        try:
            os.utime(path)
        except OSError:
            pass

    def _evict(self, keep: str) -> None:
        """Delete least recently used cached copies above the size cap (never originals)."""
        try:
            entries = []
            for name in os.listdir(self.cache_dir):
                path = os.path.join(self.cache_dir, name)
                if name.endswith(".mp4") and os.path.isfile(path):
                    stat = os.stat(path)
                    entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            return
        total = sum(size for _mtime, size, _path in entries)
        for _mtime, size, path in sorted(entries):
            if total <= self.max_cache_bytes:
                break
            if path == keep:
                continue
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass

    def thumbnail(self, relative_path: str, duration_seconds: float) -> Optional[str]:
        """Path to a small JPEG frame of a segment, generated once on demand."""
        source = self._source(relative_path)
        try:
            stat = os.stat(source)
        except OSError:
            return None
        name = _short_hash(f"{relative_path}:{stat.st_size}:{stat.st_mtime_ns}") + ".jpg"
        target = os.path.join(self.thumb_dir, name)
        if os.path.exists(target):
            return target
        os.makedirs(self.thumb_dir, exist_ok=True)
        at = max(0.0, min(2.0, float(duration_seconds or 0) / 2))
        partial = target + ".part.jpg"
        with self._thumb_slots:
            if os.path.exists(target):
                return target
            try:
                subprocess.run(
                    [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", f"{at:.2f}", "-i", source, "-frames:v", "1",
                     "-vf", "scale=480:-2", "-q:v", "6", partial],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True,
                )
                os.replace(partial, target)
            except (OSError, subprocess.SubprocessError):
                try:
                    os.remove(partial)
                except OSError:
                    pass
                return None
        return target
