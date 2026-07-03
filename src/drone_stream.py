"""
drone_stream.py  —  Universal Drone / RTSP Stream Handler
==========================================================
Works with ANY drone or camera that outputs RTSP video:

  DJI        — Mini / Mavic / Air / Phantom / Avata / Enterprise
  Parrot     — Anafi / Anafi USA / Bebop
  Autel      — EVO Lite / EVO II / EVO Max
  Skydio     — Skydio 2 / X10
  Yuneec     — Typhoon H / H520
  Freefly    — Alta X
  Custom FPV — Betaflight + onboard RPi + MediaMTX
  IP Camera  — Hikvision / Dahua / Reolink / Axis / Amcrest
  Android    — IP Webcam app (free)
  iPhone     — EpocCam / Camo
  OBS Studio — Virtual camera RTSP output
  MediaMTX   — Any relay / re-stream server

Usage
-----
  # Paste your RTSP URL and go:
  CCTV_SOURCE=rtsp://YOUR_IP/live python infer.py

  # Or use a drone shortcut name:
  DRONE=dji_mini3 python infer.py

  # Or test any stream instantly:
  python drone_stream.py rtsp://YOUR_IP/live
  python drone_stream.py dji_mini3
  python drone_stream.py --list
"""

import cv2
import os
import sys
import time
import threading
import collections
import socket
import re
import numpy as np

# Optional Rust capture core integration
USE_RUST_CAPTURE = os.environ.get("USE_RUST_CAPTURE", "0").lower() in ("1", "true", "yes", "on")
HAS_RUST_CAPTURE = False
if USE_RUST_CAPTURE:
    try:
        import rust_core
        if hasattr(rust_core, "RustDroneCapture"):
            HAS_RUST_CAPTURE = True
            print("[STREAM] Rust Capture module ('rust_core') imported successfully.")
        else:
            print("[STREAM] WARNING: 'rust_core' is present as a folder/namespace but compiled binary is missing. Falling back to pure Python DroneStreamHandler.")
    except ImportError:
        print("[STREAM] WARNING: USE_RUST_CAPTURE=1 was requested, but compiled 'rust_core' module is missing. Falling back to pure Python DroneStreamHandler.")

def check_connection(url: str, timeout: float = 1.0) -> bool:
    """
    Perform a quick TCP socket connection check to see if the host/port is reachable.
    Returns True if reachable, False otherwise.
    For local files, youtube URLs, and webcam indices, returns True.
    """
    if isinstance(url, int):
        return True
    if not isinstance(url, str):
        return False
    
    url_lower = url.lower().strip()
    if url_lower.startswith(("srt://", "srts://")):
        return True
    if not url_lower.startswith(("rtsp://", "rtsps://", "http://", "https://", "rtmp://", "rtmps://")):
        # Probably a local file path
        return True
        
    try:
        # Extract host and port using regex
        match = re.search(r'^(?:rtsp|rtsps|http|https|rtmp|rtmps)://(?:[^@\n]+@)?([^/:\n]+)(?::([0-9]+))?', url_lower)
        if not match:
            return True
        host = match.group(1)
        port_str = match.group(2)
        
        if port_str:
            port = int(port_str)
        else:
            if url_lower.startswith("rtsp://"):
                port = 554
            elif url_lower.startswith("rtsps://"):
                port = 322
            elif url_lower.startswith("http://"):
                port = 80
            elif url_lower.startswith("https://"):
                port = 443
            elif url_lower.startswith("rtmp://"):
                port = 1935
            else:
                port = 554
                
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
            return True
    except Exception as e:
        print(f"[STREAM] Connection pre-check failed for {url}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  TRANSPORT SETTINGS
# ══════════════════════════════════════════════════════════════════════
RTSP_TRANSPORT = os.environ.get("RTSP_TRANSPORT", "tcp").lower()
MAX_RECONNECT  = 10
BASE_BACKOFF_S = 0.5
MAX_BACKOFF_S  = 8.0


from .presets import DRONE_DB


def list_drones():
    """Print all supported drone shortcuts in a readable table."""
    brands = {}
    for key, (url, note) in DRONE_DB.items():
        brand = key.split("_")[0].upper()
        brands.setdefault(brand, []).append((key, url, note))

    print("\n" + "=" * 76)
    print("  SUPPORTED DRONE / CAMERA PRESETS")
    print("=" * 76)
    for brand, items in brands.items():
        print(f"\n  [{brand}]")
        for key, url, note in items:
            print(f"    {key:<24}  {url}")
            print(f"    {'':24}  {note}")
    print("\n" + "=" * 76 + "\n")


def resolve_source(default_source: str = None) -> str:
    """
    Resolve the video source. Priority:
      1. DRONE env var  (preset name)   e.g. DRONE=dji_mini3
      2. CCTV_SOURCE env var (full URL) e.g. CCTV_SOURCE=rtsp://192.168.42.1/live
      3. default_source argument
    """
    drone_name = os.environ.get("DRONE", "").strip().lower()
    if drone_name:
        if drone_name in DRONE_DB:
            url, note = DRONE_DB[drone_name]
            print(f"[STREAM] Preset  : {drone_name}")
            print(f"[STREAM] URL     : {url}")
            print(f"[STREAM] Note    : {note}")
            return url
        else:
            print(f"[STREAM] ERROR: Unknown DRONE preset '{drone_name}'")
            list_drones()
            raise SystemExit(1)

    cctv = os.environ.get("CCTV_SOURCE", "").strip()
    if cctv:
        return cctv

    if default_source:
        return default_source

    # Fallback — should not happen when called from infer.py
    return ""


# ══════════════════════════════════════════════════════════════════════
#  FFMPEG LOW-LATENCY OPTIONS
# ══════════════════════════════════════════════════════════════════════

def _ffmpeg_opts(transport: str = "tcp") -> str:
    return (
        f"rtsp_transport;{transport}|"
        "fflags;nobuffer|"
        "flags;low_delay|"
        "stimeout;3000000|"
        "analyzeduration;100000|"
        "probesize;500000"
    )


# ══════════════════════════════════════════════════════════════════════
#  STREAM HANDLER CLASS
# ══════════════════════════════════════════════════════════════════════

class DroneStreamHandler:
    """
    Universal stream handler. Pass any RTSP URL, HTTP stream, local
    video file, or integer webcam index.

    Examples:
        DroneStreamHandler("rtsp://192.168.42.1/live")
        DroneStreamHandler("rtsp://admin:pass@192.168.1.64:554/stream")
        DroneStreamHandler("/path/to/video.mp4")
        DroneStreamHandler(0)   # USB webcam
    """

    def __init__(
        self,
        source,
        transport: str = RTSP_TRANSPORT,
        target_width: int = 0,
        target_height: int = 0,
    ):
        self.source        = source
        self.transport     = transport.lower()
        self.target_width  = target_width
        self.target_height = target_height

        # If it is a YouTube URL, resolve the underlying direct stream
        if isinstance(source, str) and ("youtube.com" in source.lower() or "youtu.be" in source.lower()):
            print(f"[STREAM] Resolving YouTube stream URL for: {source}")
            resolved = self._resolve_youtube_url(source)
            if resolved:
                print(f"[STREAM] Resolved YouTube direct URL.")
                self.source = resolved
            else:
                print(f"[STREAM] Warning: Could not resolve YouTube URL, trying raw source.")

        self.is_live       = self._detect_live(self.source)
        self._lock         = threading.Lock()

        self._frame_times = collections.deque(maxlen=120)
        self._drop_count  = 0
        self._total_reads = 0
        self._connect_ts  = None

        self.is_rust = (USE_RUST_CAPTURE and HAS_RUST_CAPTURE and self.is_live)
        if self.is_rust:
            self.rust_cap = rust_core.RustDroneCapture(str(self.source))
            self.rust_cap.start()
            self.cap = None
            self._connect_ts = time.monotonic()
            print(f"[STREAM] Connected via Rust Core! : {self.source}")
        else:
            self.rust_cap = None
            self.cap = self._open()

        # Threaded low-latency background frame grabber
        self.latest_frame = None
        self.latest_ret   = False
        self.running      = True
        self.frame_ready  = threading.Event()
        self.bg_thread    = None

        if self.is_live and not self.is_rust and self.cap and self.cap.isOpened():
            self.bg_thread = threading.Thread(target=self._bg_update_loop, daemon=True)
            self.bg_thread.start()

    # ── classmethod shortcut ─────────────────────────────────────────

    @classmethod
    def from_name(cls, name: str, **kw):
        name = name.lower().strip()
        if name not in DRONE_DB:
            list_drones()
            raise ValueError(f"Unknown preset: '{name}'")
        url, note = DRONE_DB[name]
        print(f"[STREAM] Preset '{name}'  →  {url}")
        print(f"[STREAM] Note   : {note}")
        return cls(url, **kw)

    # ── helpers ──────────────────────────────────────────────────────

    def _detect_live(self, src) -> bool:
        if isinstance(src, int):
            return True
        if not isinstance(src, str):
            return False
        return src.lower().strip().startswith(
            ("rtsp://", "rtsps://", "http://", "https://", "rtmp://", "rtmps://", "srt://", "srts://")
        )

    def _resolve_youtube_url(self, url: str) -> str:
        try:
            import yt_dlp
            ydl_opts = {
                "format": "best",
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("url")
        except Exception as e:
            print(f"[STREAM] YouTube extract error: {e}")
            return None

    def _safe_url(self, url: str) -> str:
        if "@" in url:
            proto, rest = url.split("://", 1)
            return proto + "://*:*@" + rest.split("@", 1)[1]
        return url

    # ── open ─────────────────────────────────────────────────────────

    def _open(self) -> cv2.VideoCapture:
        if self.is_live:
            return self._open_rtsp()
        cap = cv2.VideoCapture(self.source)
        if cap.isOpened():
            if self.target_width and self.target_height:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.target_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
            print(f"[STREAM] Opened: {self.source}")
        else:
            print(f"[STREAM] ERROR: Cannot open: {self.source}")
        return cap

    def _open_rtsp(self) -> cv2.VideoCapture:
        src_str  = str(self.source)
        is_srt = src_str.lower().startswith(("srt://", "srts://"))
        if is_srt:
            opts = (
                "fflags;nobuffer|"
                "flags;low_delay|"
                "analyzeduration;100000|"
                "probesize;500000"
            )
        else:
            opts = _ffmpeg_opts(self.transport)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts + "|hwaccel;auto"

        safe_url = self._safe_url(src_str)

        print(f"[STREAM] Connecting  : {safe_url}")
        if is_srt:
            print(f"[STREAM] Protocol    : SRT (Low Latency UDP)")
        else:
            print(f"[STREAM] Transport   : {self.transport.upper()}")

        # Pre-check socket connection before initiating blocking cv2.VideoCapture
        if not check_connection(src_str, timeout=1.5):
            print(f"[STREAM] PRE-CHECK FAILED: {safe_url} is offline/unreachable.")
            # Return an unopened VideoCapture object
            return cv2.VideoCapture()

        cap = cv2.VideoCapture(src_str, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)

        if self.target_width and self.target_height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.target_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)

        if cap.isOpened():
            self._connect_ts = time.monotonic()
            w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            print(f"[STREAM] Connected!  : {w}x{h} @ {fps:.1f} fps")
        else:
            print(f"[STREAM] FAILED      : {safe_url}")
            print(f"[STREAM] Check: drone on? Wi-Fi connected? App streaming?")

        return cap

    # ── read ─────────────────────────────────────────────────────────

    def read_frame(self):
        """Returns (True, frame_bgr) or (False, None)."""
        self._total_reads += 1

        if self.is_rust:
            # Rust capture integration
            data = self.rust_cap.read_frame()
            if data is not None:
                # Place mock image for testing
                frame = np.zeros((config.DISPLAY_HEIGHT // 2, config.DISPLAY_WIDTH // 2, 3), dtype=np.uint8)
                cv2.putText(frame, "RUST CAPTURE ACTIVE (MOCK)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 1)
                self._frame_times.append(time.monotonic())
                return True, frame
            else:
                time.sleep(0.01)
                return False, None

        if self.is_live:
            # Wait for the background thread to fetch a new frame
            ok = self.frame_ready.wait(timeout=2.0)
            with self._lock:
                if not self.running:
                    return False, None
                if not self.latest_ret:
                    return False, None
                # Clear event so subsequent reads block
                self.frame_ready.clear()
                return True, self.latest_frame
        else:
            with self._lock:
                ok, frame = self.cap.read()
                if not ok:
                    # Loop video files infinitely
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = self.cap.read()
                    if not ok:
                        return False, None
            self._frame_times.append(time.monotonic())
            return True, frame

    def _bg_update_loop(self):
        while self.running:
            with self._lock:
                cap_opened = self.cap.isOpened()
            if not cap_opened:
                time.sleep(0.01)
                continue

            ret = self.cap.grab()
            if ret:
                ok, frame = self.cap.retrieve()
                if ok and frame is not None:
                    with self._lock:
                        self.latest_frame = frame
                        self.latest_ret   = True
                        self._frame_times.append(time.monotonic())
                        self.frame_ready.set()
                else:
                    self._handle_bg_reconnect()
            else:
                self._handle_bg_reconnect()
            time.sleep(0.001)

    def _handle_bg_reconnect(self):
        with self._lock:
            if not self.running:
                return
            self.latest_ret = False
            self.latest_frame = None

        backoff = BASE_BACKOFF_S
        for attempt in range(1, MAX_RECONNECT + 1):
            self._drop_count += 1
            print(f"[STREAM] Reconnect {attempt}/{MAX_RECONNECT} (wait {backoff:.1f}s) ...")
            with self._lock:
                self.cap.release()
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
            
            new_cap = self._open_rtsp()
            if new_cap.isOpened():
                ok = new_cap.grab()
                if ok:
                    ok2, frame = new_cap.retrieve()
                    if ok2 and frame is not None:
                        with self._lock:
                            self.cap = new_cap
                            self.latest_frame = frame
                            self.latest_ret   = True
                            self._frame_times.append(time.monotonic())
                            self.frame_ready.set()
                        print(f"[STREAM] Reconnected after {attempt} attempt(s).")
                        return
                new_cap.release()
                
        print(f"[STREAM] FATAL: could not reconnect after {MAX_RECONNECT} tries.")
        with self._lock:
            self.running = False
            self.latest_ret = False
            self.frame_ready.set()

    # ── info ─────────────────────────────────────────────────────────

    def get_fps(self) -> float:
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            if span > 0:
                return (len(self._frame_times) - 1) / span
        if self.is_rust:
            return 30.0
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if (fps and fps == fps and fps > 0) else 25.0

    def get_resolution(self):
        if self.is_rust:
            return (640, 360)
        return (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def is_opened(self) -> bool:
        if self.is_rust:
            return self.rust_cap.is_opened()
        return self.cap.isOpened() if self.cap else False

    def health_report(self) -> dict:
        w, h    = self.get_resolution()
        uptime  = time.monotonic() - self._connect_ts if self._connect_ts else 0.0
        return {
            "resolution":  f"{w}x{h}",
            "live_fps":    round(self.get_fps(), 1),
            "drop_rate_%": round(self._drop_count / max(self._total_reads, 1) * 100, 2),
            "reconnects":  self._drop_count,
            "uptime_s":    round(uptime, 1),
        }

    def print_health(self):
        r = self.health_report()
        print(
            f"[STREAM HEALTH] {r['resolution']}  {r['live_fps']} fps  "
            f"drop={r['drop_rate_%']:.1f}%  reconnects={r['reconnects']}  "
            f"uptime={r['uptime_s']}s"
        )

    def set_transport(self, t: str):
        self.transport = t.lower()
        print(f"[STREAM] Transport set to {self.transport.upper()} (next reconnect)")

    def release(self):
        with self._lock:
            self.running = False
            self.latest_ret = False
            self.frame_ready.set()
            if self.is_rust and self.rust_cap:
                self.rust_cap.stop()
            elif self.cap and self.cap.isOpened():
                self.cap.release()
        print("[STREAM] Released.")


# ══════════════════════════════════════════════════════════════════════
#  STANDALONE TEST — run directly:  python drone_stream.py <URL or name>
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("--list", "-l", "list"):
        list_drones()
        print("Quick start:")
        print("  python drone_stream.py rtsp://192.168.42.1/live   # test URL")
        print("  python drone_stream.py dji_mini3                   # preset name")
        print("  python drone_stream.py --list                      # all presets")
        sys.exit(0)

    arg = sys.argv[1].strip()

    if arg in DRONE_DB:
        url, note = DRONE_DB[arg]
        print(f"\n[TEST] Preset  : {arg}")
        print(f"[TEST] URL     : {url}")
        print(f"[TEST] Note    : {note}\n")
    elif arg.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        url = arg
        print(f"\n[TEST] Testing URL: {url}\n")
    else:
        print(f"[TEST] Not recognised: '{arg}'")
        print("       Use an RTSP URL or a preset name (--list to see all)")
        sys.exit(1)

    transport = os.environ.get("RTSP_TRANSPORT", "tcp")
    print(f"[TEST] Transport: {transport.upper()}")
    print("[TEST] Press Q to quit\n")

    sh = DroneStreamHandler(url, transport=transport)
    if not sh.is_opened():
        print("[TEST] FAILED — could not open stream.")
        print("[TEST] Checklist:")
        print("  1. Is the drone powered on and hovering / motors off?")
        print("  2. Is laptop Wi-Fi connected to the drone's hotspot?")
        print("  3. Is the DJI / Parrot / Autel app open and live view showing?")
        print("  4. Try: ffplay -rtsp_transport tcp <URL>  to isolate the issue")
        sys.exit(1)

    last_health = time.monotonic()
    frame_no    = 0

    while True:
        ok, frame = sh.read_frame()
        if not ok:
            print("[TEST] Stream ended.")
            break

        frame_no += 1
        now = time.monotonic()

        if now - last_health >= 5.0:
            sh.print_health()
            last_health = now

        disp = cv2.resize(frame, (960, 540))
        w2, h2 = sh.get_resolution()
        fps_live = sh.get_fps()

        cv2.putText(disp,
                    f"Frame {frame_no}  |  {fps_live:.1f} fps  |  {w2}x{h2}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        cv2.putText(disp,
                    f"{url}",
                    (10, 528), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1)

        cv2.imshow("Drone Stream Test — Q to quit", disp)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            break

    sh.release()
    cv2.destroyAllWindows()
    sh.print_health()
