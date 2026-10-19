"""On-demand adaptive-bitrate HLS for live viewers.

One FFmpeg process per *watched* camera reads the raw MediaMTX feed and
writes a multi-variant HLS ladder:

* the original stream, copied without re-encoding (top quality);
* a 720p encode when the source is taller than 720 lines (optional);
* a 480p encode.

Encoded variants force a keyframe exactly where the source has one, so every
variant is cut into segments at the same instants and hls.js can switch
between them seamlessly.  Audio is not carried (dashboard players are always
muted); recordings are made by MediaMTX from the original stream and are
never affected by viewer quality.

Processes start on the first playlist request, stop after a period without
requests, are capped in number, are never duplicated per camera, and restart
with bounded exponential backoff only while viewers are still asking.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional

from src import stream_events as ev


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class AbrUnavailable(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


def build_ladder(source_height: Optional[int], encode_720: bool = True) -> List[dict]:
    """Return the variant list for a source, top quality first."""
    height = int(source_height or 720)
    ladder = [{"name": "src", "copy": True, "height": height}]
    if height > 720 and encode_720:
        ladder.append({"name": "720p", "copy": False, "height": 720, "bitrate_k": 2000})
    if height > 480:
        ladder.append({"name": "480p", "copy": False, "height": 480, "bitrate_k": 900})
    return ladder


def build_ffmpeg_command(ffmpeg: str, source_url: str, out_dir: str, ladder: List[dict],
                         segment_seconds: float = 1.0, list_size: int = 8) -> List[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
           "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", source_url]
    for _ in ladder:
        cmd += ["-map", "0:v:0"]
    stream_map = []
    for index, variant in enumerate(ladder):
        spec = f"v:{index}"
        if variant["copy"]:
            cmd += [f"-c:{spec}", "copy"]
        else:
            rate = variant["bitrate_k"]
            cmd += [
                f"-c:{spec}", "libx264",
                # Stream copy starts at the first source keyframe; make the
                # encoders start there too so segment numbers and timestamps
                # are identical across variants.
                f"-filter:{spec}",
                f"select='if(isnan(prev_selected_n),key,1)',scale=-2:{variant['height']},format=yuv420p",
                f"-preset:{spec}", "veryfast",
                f"-tune:{spec}", "zerolatency",
                f"-profile:{spec}", "main",
                f"-b:{spec}", f"{rate}k",
                f"-maxrate:{spec}", f"{int(rate * 1.1)}k",
                f"-bufsize:{spec}", f"{rate * 2}k",
                # Keyframes only where the source has them: aligned segments.
                f"-force_key_frames:{spec}", "source",
                f"-g:{spec}", "600",
                f"-sc_threshold:{spec}", "0",
            ]
        stream_map.append(f"v:{index},name:{variant['name']}")
    cmd += [
        "-an",
        "-f", "hls",
        "-hls_time", f"{segment_seconds:g}",
        "-hls_list_size", str(list_size),
        "-hls_delete_threshold", "4",
        "-hls_flags", "delete_segments+independent_segments+program_date_time+omit_endlist+temp_file",
        "-hls_segment_type", "mpegts",
        "-master_pl_name", "master.m3u8",
        "-hls_segment_filename", os.path.join(out_dir, "%v", "seg_%06d.ts"),
        "-var_stream_map", " ".join(stream_map),
        os.path.join(out_dir, "%v", "index.m3u8"),
    ]
    return cmd


class AbrManager:
    def __init__(
        self,
        rtsp_base: str,
        work_dir: str,
        *,
        max_sessions: int = 4,
        idle_seconds: float = 45.0,
        encode_720: bool = True,
        ffmpeg: str = "ffmpeg",
        events: Optional[ev.StreamEventLog] = None,
        is_source_online: Callable[[str], bool] = lambda _camera_id: True,
        clock: Callable[[], float] = time.time,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.rtsp_base = rtsp_base.rstrip("/")
        self.work_dir = work_dir
        self.max_sessions = max(0, int(max_sessions))
        self.idle_seconds = max(5.0, float(idle_seconds))
        self.encode_720 = bool(encode_720)
        self.ffmpeg = ffmpeg
        self._events = events
        self._is_source_online = is_source_online
        self._clock = clock
        self._popen = popen
        self._lock = threading.Lock()
        self._sessions: Dict[str, dict] = {}

    @property
    def enabled(self) -> bool:
        return self.max_sessions > 0 and shutil.which(self.ffmpeg) is not None

    def _session_dir(self, camera_id: str) -> str:
        return os.path.join(self.work_dir, camera_id)

    def _running(self, session: Optional[dict]) -> bool:
        return bool(session) and session.get("process") is not None and session["process"].poll() is None

    def _start_locked(self, camera_id: str, source_path: str, source_height: Optional[int]) -> dict:
        directory = self._session_dir(camera_id)
        shutil.rmtree(directory, ignore_errors=True)
        ladder = build_ladder(source_height, self.encode_720)
        for variant in ladder:
            os.makedirs(os.path.join(directory, variant["name"]), exist_ok=True)
        cmd = build_ffmpeg_command(self.ffmpeg, f"{self.rtsp_base}/{source_path.strip('/')}", directory, ladder)
        log = open(os.path.join(directory, "ffmpeg.log"), "wb")
        try:
            process = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        finally:
            log.close()
        previous = self._sessions.get(camera_id, {})
        now = self._clock()
        session = {
            "camera_id": camera_id,
            "source_path": source_path,
            "source_height": source_height,
            "ladder": ladder,
            "process": process,
            "started_at": now,
            "last_request_at": now,
            "restarts": previous.get("restarts", []),
            "next_restart_at": None,
        }
        self._sessions[camera_id] = session
        if self._events is not None:
            self._events.record(camera_id, ev.INGEST, "abr_started",
                                "Adaptive-quality transcoder started: " + ", ".join(v["name"] for v in ladder),
                                variants=[v["name"] for v in ladder])
        return session

    def ensure(self, camera_id: str, source_path: str, source_height: Optional[int]) -> dict:
        if not self.enabled:
            raise AbrUnavailable("disabled", "Adaptive quality is disabled on this server")
        if source_height is not None and int(source_height) <= 480:
            raise AbrUnavailable("source_small", "Source is already 480p or smaller")
        with self._lock:
            session = self._sessions.get(camera_id)
            if self._running(session):
                session["last_request_at"] = self._clock()
                return session
            if session and session.get("next_restart_at") and self._clock() < session["next_restart_at"]:
                raise AbrUnavailable("restarting", "Transcoder is restarting")
            active = sum(1 for s in self._sessions.values() if self._running(s))
            if active >= self.max_sessions:
                raise AbrUnavailable("capacity", f"All {self.max_sessions} adaptive-quality slots are in use")
            return self._start_locked(camera_id, source_path, source_height)

    def touch(self, camera_id: str) -> None:
        with self._lock:
            session = self._sessions.get(camera_id)
            if session:
                session["last_request_at"] = self._clock()

    def is_ready(self, camera_id: str) -> bool:
        session = self._sessions.get(camera_id)
        if not self._running(session):
            return False
        directory = self._session_dir(camera_id)
        if not os.path.exists(os.path.join(directory, "master.m3u8")):
            return False
        for variant in session["ladder"]:
            try:
                with open(os.path.join(directory, variant["name"], "index.m3u8"), "r", encoding="utf-8") as handle:
                    if "#EXTINF" not in handle.read():
                        return False
            except OSError:
                return False
        return True

    def failure_reason(self, camera_id: str) -> Optional[str]:
        session = self._sessions.get(camera_id)
        if session is None or self._running(session):
            return None
        try:
            with open(os.path.join(self._session_dir(camera_id), "ffmpeg.log"), "r", errors="replace") as handle:
                tail = handle.read()[-300:].strip()
        except OSError:
            tail = ""
        return tail or f"ffmpeg exited with code {session['process'].returncode}"

    def resolve_file(self, camera_id: str, parts: List[str]) -> Optional[str]:
        if not SAFE_NAME.match(camera_id) or not parts or len(parts) > 2:
            return None
        if any(not SAFE_NAME.match(part) or part.startswith(".") for part in parts):
            return None
        if not parts[-1].endswith((".m3u8", ".ts")):
            return None
        path = os.path.join(self._session_dir(camera_id), *parts)
        return path if os.path.isfile(path) else None

    def reap(self) -> None:
        """Stop idle transcoders; restart crashed ones that viewers still want."""
        now = self._clock()
        with self._lock:
            for camera_id, session in list(self._sessions.items()):
                idle = now - session["last_request_at"] > self.idle_seconds
                if self._running(session):
                    if idle:
                        self._stop_locked(camera_id, "no viewers")
                    continue
                if session.get("process") is None:
                    continue
                if idle or not self._is_source_online(camera_id):
                    self._stop_locked(camera_id, "source offline" if not idle else "no viewers")
                    continue
                if session.get("next_restart_at") is None:
                    recent = [t for t in session["restarts"] if now - t < 300.0]
                    delay = min(30.0, 2.0 ** len(recent))
                    session["restarts"] = recent
                    session["next_restart_at"] = now + delay
                    if self._events is not None:
                        self._events.record(camera_id, ev.INGEST, "abr_exited",
                                            f"Transcoder exited ({self.failure_reason(camera_id)}); retry in {delay:.0f}s")
                elif now >= session["next_restart_at"]:
                    if len(session["restarts"]) >= 6:
                        self._stop_locked(camera_id, "too many restarts in 5 minutes")
                        continue
                    session["restarts"].append(now)
                    self._start_locked(camera_id, session["source_path"], session["source_height"])

    def _stop_locked(self, camera_id: str, reason: str) -> None:
        session = self._sessions.pop(camera_id, None)
        if not session:
            return
        process = session.get("process")
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(self._session_dir(camera_id), ignore_errors=True)
        if self._events is not None:
            self._events.record(camera_id, ev.INGEST, "abr_stopped", f"Adaptive-quality transcoder stopped: {reason}")

    def stop_all(self) -> None:
        with self._lock:
            for camera_id in list(self._sessions):
                self._stop_locked(camera_id, "server shutdown")

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            sessions = {
                camera_id: {
                    "running": self._running(s),
                    "variants": [v["name"] for v in s["ladder"]],
                    "uptime_seconds": round(now - s["started_at"], 1),
                    "idle_seconds": round(now - s["last_request_at"], 1),
                    "restarts_last_5min": len([t for t in s["restarts"] if now - t < 300.0]),
                }
                for camera_id, s in self._sessions.items()
            }
        return {"enabled": self.enabled, "max_sessions": self.max_sessions,
                "idle_seconds": self.idle_seconds, "sessions": sessions}
