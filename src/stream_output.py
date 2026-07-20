"""Bounded, non-blocking video output for the browser stream.

The analysis/render loop must never wait for FFmpeg.  A slow encoder or a
temporary network stall otherwise creates an ever-growing delay between the
camera and the browser.  ``LatestFrameEncoder`` stores only the newest frame
and writes it from one dedicated thread at a fixed cadence.

Complexity per submitted frame:
    time:  O(1) in the render thread (one pointer replacement)
    space: O(width * height), bounded to one retained BGR frame

Encoding still necessarily reads O(width * height) bytes in the writer thread.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np


def resolve_ffmpeg_path(project_dir: str | os.PathLike[str]) -> Optional[str]:
    """Return an installed FFmpeg executable without assuming one user path."""
    system_path = shutil.which("ffmpeg")
    if system_path:
        return system_path

    local_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local_path = Path(project_dir) / local_name
    if local_path.is_file():
        return str(local_path)

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            for candidate in winget_root.glob(
                "Gyan.FFmpeg_*/*/bin/ffmpeg.exe"
            ):
                if candidate.is_file():
                    return str(candidate)
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            conventional = Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe"
            if conventional.is_file():
                return str(conventional)
    return None


def build_ffmpeg_command(
    ffmpeg_path: str,
    target_url: str,
    width: int,
    height: int,
    fps: float = 30.0,
) -> list[str]:
    """Build a constant-frame-rate, low-latency RTMP encoder command."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")

    fps_text = f"{fps:g}"
    keyframe_interval = max(1, int(round(fps)))
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", fps_text,
        "-i", "-",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-g", str(keyframe_interval),
        "-keyint_min", str(keyframe_interval),
        "-sc_threshold", "0",
        "-bf", "0",
        "-flush_packets", "1",
        "-f", "flv",
        target_url,
    ]


class LatestFrameEncoder:
    """Write the newest submitted frame at a stable cadence.

    ``submit`` never waits for the encoder. When several frames arrive before
    the next write, older frames are intentionally replaced. This favors live
    video latency over historical completeness, which is the correct policy
    for a monitoring dashboard.
    """

    def __init__(
        self,
        ffmpeg_path: str,
        target_url: str,
        fps: float = 30.0,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.ffmpeg_path = ffmpeg_path
        self.target_url = target_url
        self.fps = float(fps)
        self._process_factory = process_factory

        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_generation = 0
        self._written_generation = 0
        self._shape: Optional[tuple[int, int, int]] = None
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.last_error: Optional[str] = None

        self.frames_submitted = 0
        self.frames_written = 0
        self.frames_replaced = 0

    @property
    def is_running(self) -> bool:
        process_alive = self._process is not None and self._process.poll() is None
        thread_alive = self._thread is not None and self._thread.is_alive()
        return process_alive and thread_alive and not self._stop_event.is_set()

    def start(self, frame_shape: tuple[int, int, int]) -> None:
        """Start FFmpeg for frames shaped ``(height, width, 3)``."""
        if self.is_running:
            return
        if len(frame_shape) != 3 or frame_shape[2] != 3:
            raise ValueError("frame_shape must be (height, width, 3)")

        height, width, _ = frame_shape
        command = build_ffmpeg_command(
            self.ffmpeg_path,
            self.target_url,
            width,
            height,
            self.fps,
        )
        self._shape = tuple(frame_shape)
        self._stop_event.clear()
        self.last_error = None
        self._process = self._process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._thread = threading.Thread(
            target=self._write_loop,
            daemon=True,
            name="LatestFrameEncoder",
        )
        self._thread.start()

    def submit(self, frame: np.ndarray) -> None:
        """Replace the pending frame and return immediately."""
        if not self.is_running:
            raise RuntimeError("encoder is not running")
        if frame.shape != self._shape or frame.dtype != np.uint8:
            raise ValueError(
                f"expected uint8 frame shaped {self._shape}, got "
                f"{frame.dtype} {frame.shape}"
            )

        with self._condition:
            if self._latest_generation > self._written_generation:
                self.frames_replaced += 1
            self._latest_frame = frame
            self._latest_generation += 1
            self.frames_submitted += 1
            self._condition.notify()

    def _write_loop(self) -> None:
        interval = 1.0 / self.fps
        next_write = time.monotonic()

        while not self._stop_event.is_set():
            with self._condition:
                while self._latest_frame is None and not self._stop_event.is_set():
                    self._condition.wait(timeout=0.25)
                if self._stop_event.is_set():
                    break

                # New submissions wake the condition so the retained pointer
                # can change, but they must not accelerate the output clock.
                while not self._stop_event.is_set():
                    wait_seconds = next_write - time.monotonic()
                    if wait_seconds <= 0:
                        break
                    self._condition.wait(timeout=wait_seconds)
                if self._stop_event.is_set():
                    break

                frame = self._latest_frame
                generation = self._latest_generation

            process = self._process
            if frame is None or process is None or process.stdin is None:
                break
            if process.poll() is not None:
                self.last_error = "FFmpeg exited unexpectedly"
                break

            try:
                contiguous = np.ascontiguousarray(frame)
                process.stdin.write(memoryview(contiguous).cast("B"))
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.last_error = str(exc)
                break

            with self._condition:
                self._written_generation = max(self._written_generation, generation)
                self.frames_written += 1

            next_write += interval
            now = time.monotonic()
            if next_write < now - interval:
                # Never burst old frames after a stall; jump back to live time.
                next_write = now + interval

        self._stop_event.set()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the writer and terminate its FFmpeg subprocess."""
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

        process = self._process
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                process.terminate()
                process.wait(timeout=timeout)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
