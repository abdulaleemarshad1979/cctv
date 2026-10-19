"""
Flights: one archive entry per drone flight instead of one per MediaMTX file.

MediaMTX starts a new file whenever a publisher reconnects or its recorder
resets after timestamp drift (slow 4G uplinks), so one flight can be dozens of
short segments.  A flight is every segment of one drone, with the same video
format, separated by gaps of at most RECORDING_FLIGHT_GAP_SECONDS.

A flight plays as one video without copying anything: its files are
fragmented MP4, so an HLS VOD playlist can point at their fragments by byte
range (EXT-X-BYTERANGE), with a discontinuity between files.  Players get the
full duration at once, start instantly and seek anywhere; the only stored
extra is a small per-file fragment index.  Downloads of multi-file flights
are joined on the fly (stream copy) and never stored.  Originals are never
modified.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import tempfile
import threading
from collections import OrderedDict
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.recording_continuity import parse_segment_start


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


FLIGHT_GAP_SECONDS = _env_float("RECORDING_FLIGHT_GAP_SECONDS", 120)
# Playlist segments are cut at keyframes once at least this long.
VOD_SEGMENT_SECONDS = 4.0
NON_SYNC_SAMPLE = 0x00010000


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




SUMMARY_FIELDS = ("id", "drone_id", "location", "codec", "width", "height", "resolution", "start_epoch",
                  "end_epoch", "duration_seconds", "gap_seconds", "size_bytes", "segment_count",
                  "thumbnail_path")


def flight_summary(flight: Dict[str, Any]) -> Dict[str, Any]:
    """A flight without its per-file list (for paged listings)."""
    return {key: flight.get(key) for key in SUMMARY_FIELDS}


# --- Fragmented MP4 layout -------------------------------------------------

def _boxes(data: bytes, start: int = 0, end: Optional[int] = None) -> Iterator[Tuple[bytes, int, int]]:
    """Yield (type, payload_start, box_end) for boxes inside data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, kind: bytes) -> Optional[Tuple[int, int]]:
    for found, payload, box_end in _boxes(data, start, end):
        if found == kind:
            return payload, box_end
    return None


def _parse_moov(moov: bytes) -> Dict[int, Dict[str, Any]]:
    """Track id -> {timescale, video, default_duration, default_flags}."""
    tracks: Dict[int, Dict[str, Any]] = {}
    for kind, payload, end in _boxes(moov):
        if kind != b"trak":
            continue
        tkhd = _child(moov, payload, end, b"tkhd")
        mdia = _child(moov, payload, end, b"mdia")
        if not tkhd or not mdia:
            continue
        version = moov[tkhd[0]]
        track_id = struct.unpack_from(">I", moov, tkhd[0] + (20 if version == 1 else 12))[0]
        mdhd = _child(moov, mdia[0], mdia[1], b"mdhd")
        hdlr = _child(moov, mdia[0], mdia[1], b"hdlr")
        if not mdhd:
            continue
        version = moov[mdhd[0]]
        timescale = struct.unpack_from(">I", moov, mdhd[0] + (20 if version == 1 else 12))[0]
        handler = moov[hdlr[0] + 8:hdlr[0] + 12] if hdlr else b""
        tracks[track_id] = {"timescale": timescale or 1, "video": handler == b"vide",
                            "default_duration": 0, "default_flags": 0}
    mvex = _child(moov, 0, len(moov), b"mvex")
    if mvex:
        for kind, payload, _end in _boxes(moov, mvex[0], mvex[1]):
            if kind == b"trex":
                track_id, _desc, duration, _size, flags = struct.unpack_from(">IIIII", moov, payload + 4)
                if track_id in tracks:
                    tracks[track_id]["default_duration"] = duration
                    tracks[track_id]["default_flags"] = flags
    return tracks


def _parse_moof(moof: bytes, tracks: Dict[int, Dict[str, Any]], track_id: int) -> Optional[Tuple[float, bool]]:
    """(duration seconds, starts with keyframe) of one track in a fragment."""
    track = tracks[track_id]
    for kind, payload, end in _boxes(moof):
        if kind != b"traf":
            continue
        tfhd = _child(moof, payload, end, b"tfhd")
        if not tfhd:
            continue
        flags = struct.unpack_from(">I", moof, tfhd[0])[0] & 0xFFFFFF
        if struct.unpack_from(">I", moof, tfhd[0] + 4)[0] != track_id:
            continue
        pos = tfhd[0] + 8
        if flags & 0x01:
            pos += 8
        if flags & 0x02:
            pos += 4
        default_duration = track["default_duration"]
        default_flags = track["default_flags"]
        if flags & 0x08:
            default_duration = struct.unpack_from(">I", moof, pos)[0]
            pos += 4
        if flags & 0x10:
            pos += 4
        if flags & 0x20:
            default_flags = struct.unpack_from(">I", moof, pos)[0]
        total = 0
        first_flags = None
        for kind2, run, _run_end in _boxes(moof, payload, end):
            if kind2 != b"trun":
                continue
            run_flags = struct.unpack_from(">I", moof, run)[0] & 0xFFFFFF
            count = struct.unpack_from(">I", moof, run + 4)[0]
            pos = run + 8
            if run_flags & 0x001:
                pos += 4
            run_first = None
            if run_flags & 0x004:
                run_first = struct.unpack_from(">I", moof, pos)[0]
                pos += 4
            fields = [bit for bit in (0x100, 0x200, 0x400, 0x800) if run_flags & bit]
            for index in range(count):
                values = dict(zip(fields, struct.unpack_from(">" + "I" * len(fields), moof, pos)))
                pos += 4 * len(fields)
                total += values.get(0x100, default_duration)
                if first_flags is None:
                    first_flags = run_first if (index == 0 and run_first is not None) else values.get(0x400, default_flags)
        sync = first_flags is not None and not (first_flags & NON_SYNC_SAMPLE)
        return total / track["timescale"], sync
    return None


def read_fmp4_layout(path: str, segment_seconds: float = VOD_SEGMENT_SECONDS) -> Optional[Dict[str, Any]]:
    """Byte layout of a fragmented MP4 for HLS byte-range playback.

    Returns {"init": [offset, length], "segments": [[offset, length, seconds], ...]}
    with segments starting at keyframes, or None if the file is not fragmented
    MP4.  Only box headers and the small moov/moof boxes are read.
    """
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        pos = 0
        tracks: Dict[int, Dict[str, Any]] = {}
        track_id = None
        init_end = None
        segments: List[List[float]] = []
        current: Optional[List[float]] = None
        pending: Optional[Tuple[int, float, bool]] = None  # moof start, duration, sync
        while pos + 8 <= size:
            handle.seek(pos)
            head = handle.read(16)
            box_size, kind = struct.unpack_from(">I4s", head)
            header = 8
            if box_size == 1:
                box_size, header = struct.unpack_from(">Q", head, 8)[0], 16
            elif box_size == 0:
                box_size = size - pos
            if box_size < header or pos + box_size > size:
                break  # truncated (file still being written)
            if kind == b"moov":
                handle.seek(pos + header)
                tracks = _parse_moov(handle.read(box_size - header))
                video = [tid for tid, t in tracks.items() if t["video"]]
                track_id = (video or sorted(tracks) or [None])[0]
                init_end = pos + box_size
            elif kind == b"moof" and track_id is not None:
                handle.seek(pos + header)
                parsed = _parse_moof(handle.read(box_size - header), tracks, track_id)
                pending = (pos, parsed[0], parsed[1]) if parsed else None
            elif kind == b"mdat" and pending is not None:
                moof_start, duration, sync = pending
                pending = None
                end = pos + box_size
                if current is None or (sync and current[2] >= segment_seconds):
                    current = [moof_start, end - moof_start, duration]
                    segments.append(current)
                else:
                    current[1] = end - current[0]
                    current[2] += duration
            pos += box_size
    if init_end is None or not segments:
        return None
    for segment in segments:
        segment[2] = round(segment[2], 3)
    return {"init": [0, init_end], "segments": segments}


def build_vod_playlist(parts: List[Tuple[str, Dict[str, Any]]]) -> str:
    """HLS VOD playlist over (url, layout) pairs, one discontinuity per file."""
    longest = max(seg[2] for _url, layout in parts for seg in layout["segments"])
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{max(1, math.ceil(longest))}",
             "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for index, (url, layout) in enumerate(parts):
        if index:
            lines.append("#EXT-X-DISCONTINUITY")
        init_offset, init_length = layout["init"]
        lines.append(f'#EXT-X-MAP:URI="{url}",BYTERANGE="{init_length}@{init_offset}"')
        for offset, length, seconds in layout["segments"]:
            lines += [f"#EXTINF:{seconds:.3f},", f"#EXT-X-BYTERANGE:{int(length)}@{int(offset)}", url]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class FlightPlayback:
    """Per-file fragment indexes, flight playlists, streamed downloads, thumbnails."""

    def __init__(self, recordings_dir: str, cache_dir: str, ffmpeg: str = "ffmpeg", memory_items: int = 512):
        self.recordings_dir = os.path.abspath(recordings_dir)
        self.cache_dir = cache_dir
        self.layout_dir = os.path.join(cache_dir, "layouts")
        self.thumb_dir = os.path.join(cache_dir, "thumbs")
        self.ffmpeg = ffmpeg
        self.memory_items = memory_items
        self._layouts: "OrderedDict[str, Optional[Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()
        self._thumb_slots = threading.BoundedSemaphore(2)

    def _source(self, relative_path: str) -> str:
        path = os.path.abspath(os.path.join(self.recordings_dir, relative_path))
        if os.path.commonpath([path, self.recordings_dir]) != self.recordings_dir:
            raise ValueError("path outside recordings")
        return path

    def layout(self, relative_path: str) -> Optional[Dict[str, Any]]:
        """Fragment index of one file: memory LRU, then disk, then parse once."""
        source = self._source(relative_path)
        try:
            stat = os.stat(source)
        except OSError:
            return None
        key = _short_hash(f"{relative_path}:{stat.st_size}:{stat.st_mtime_ns}")
        with self._lock:
            if key in self._layouts:
                self._layouts.move_to_end(key)
                return self._layouts[key]
        cached = os.path.join(self.layout_dir, key + ".json")
        layout = None
        try:
            with open(cached, "r", encoding="utf-8") as handle:
                layout = json.load(handle)
        except (OSError, ValueError):
            try:
                layout = read_fmp4_layout(source)
            except (OSError, struct.error, KeyError, IndexError) as exc:
                print(f"[RECORDINGS] cannot index {relative_path}: {exc}")
                layout = None
            if layout is not None:
                os.makedirs(self.layout_dir, exist_ok=True)
                fd, temp = tempfile.mkstemp(dir=self.layout_dir, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(layout, handle, separators=(",", ":"))
                os.replace(temp, cached)
        with self._lock:
            self._layouts[key] = layout
            while len(self._layouts) > self.memory_items:
                self._layouts.popitem(last=False)
        return layout

    def playlist(self, flight: Dict[str, Any]) -> Optional[str]:
        parts = []
        for seg in flight["segments"]:
            layout = self.layout(seg["relative_path"])
            if layout:
                parts.append((seg["url"], layout))
        return build_vod_playlist(parts) if parts else None

    def download_command(self, flight: Dict[str, Any], list_path: str) -> List[str]:
        """FFmpeg command joining a flight (stream copy) into fragmented MP4 on stdout."""
        with open(list_path, "w", encoding="utf-8") as listing:
            for seg in flight["segments"]:
                escaped = self._source(seg["relative_path"]).replace("'", "'\\''")
                listing.write(f"file '{escaped}'\n")
        return [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]

    def stream_download(self, flight: Dict[str, Any], chunk_size: int = 256 * 1024) -> Iterator[bytes]:
        os.makedirs(self.cache_dir, exist_ok=True)
        fd, list_path = tempfile.mkstemp(suffix=".txt", dir=self.cache_dir)
        os.close(fd)
        proc = None
        try:
            proc = subprocess.Popen(self.download_command(flight, list_path),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc and proc.poll() is None:
                proc.kill()
            if proc:
                proc.wait()
            try:
                os.remove(list_path)
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
