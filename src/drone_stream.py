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


# ══════════════════════════════════════════════════════════════════════
#  TRANSPORT SETTINGS
# ══════════════════════════════════════════════════════════════════════
RTSP_TRANSPORT = os.environ.get("RTSP_TRANSPORT", "tcp").lower()
MAX_RECONNECT  = 10
BASE_BACKOFF_S = 0.5
MAX_BACKOFF_S  = 8.0


# ══════════════════════════════════════════════════════════════════════
#  DRONE DATABASE — every brand + model that supports RTSP
#  Format:  "shortcut_name": ("rtsp://url", "setup note")
# ══════════════════════════════════════════════════════════════════════

DRONE_DB = {

    # ─── DJI ─────────────────────────────────────────────────────────
    "dji_phantom4":     ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-PHANTOM-XXXX"),
    "dji_mavic2":       ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-MAVIC-XXXX"),
    "dji_mavic3":       ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MAVIC3-XXXX"),
    "dji_mini2":        ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI2-XXXX"),
    "dji_mini3":        ("rtsp://192.168.0.1:8554/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI3-XXXX"),
    "dji_mini4pro":     ("rtsp://192.168.0.1:8554/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI4PRO-XXXX"),
    "dji_air2s":        ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-AIR2S-XXXX"),
    "dji_air3":         ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-AIR3-XXXX"),
    "dji_spark":        ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-SPARK-XXXX"),
    "dji_avata":        ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: FPV Goggles 2"),
    "dji_avata2":       ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: Goggles 3"),
    "dji_fpv":          ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: FPV Goggles"),
    "dji_m300":         ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_m350":         ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_m30":          ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_go4":          ("rtsp://192.168.0.1/live",
                         "Any DJI GO 4 drone (Phantom/Mavic 2/Spark)"),
    "dji_fly":          ("rtsp://192.168.42.1/live",
                         "Any DJI Fly drone (Mini 2/3/Air 2S/Mavic 3)"),

    # ─── Parrot ──────────────────────────────────────────────────────
    "parrot_anafi":     ("rtsp://192.168.42.1/live",
                         "FreeFlight 6 -> Wi-Fi: ANAFI-XXXXXX"),
    "parrot_anafi_usa": ("rtsp://192.168.42.1/live",
                         "FreeFlight 6 -> Wi-Fi: ANAFI-USA-XXXX"),
    "parrot_bebop2":    ("rtsp://192.168.42.1/arstream",
                         "FreeFlight Pro -> Wi-Fi: Bebop-XXXXXXXX"),
    "parrot_disco":     ("rtsp://192.168.42.1/live",
                         "FreeFlight Pro -> Wi-Fi: disco-XXXXXXXX"),

    # ─── Autel ───────────────────────────────────────────────────────
    "autel_evo2":       ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-XXXXXX"),
    "autel_evo_lite":   ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-LITE-XXXX"),
    "autel_evo_nano":   ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-NANO-XXXX"),
    "autel_evo_max":    ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-MAX-XXXX"),

    # ─── Skydio ──────────────────────────────────────────────────────
    "skydio2":          ("rtsp://192.168.110.1/mpeg_ts.264",
                         "Skydio SDK -> Wi-Fi: Skydio-XXXXXX"),
    "skydio_x10":       ("rtsp://192.168.110.1/mpeg_ts.264",
                         "Skydio SDK -> Wi-Fi: SkydioX10-XXXX"),

    # ─── Yuneec ──────────────────────────────────────────────────────
    "yuneec_h520":      ("rtsp://192.168.0.1/live",
                         "DataPilot -> Wi-Fi: YUNEEC-XXXX"),
    "yuneec_typhoonh":  ("rtsp://192.168.0.1:8080/live",
                         "Controller Wi-Fi -> YUNEEC-XXXX"),

    # ─── Freefly ─────────────────────────────────────────────────────
    "freefly_altax":    ("rtsp://192.168.0.1/live",
                         "Freefly app -> Wi-Fi: AltaX-XXXX"),

    # ─── Custom FPV (RPi / OrangePi onboard) ─────────────────────────
    "fpv_rpi":          ("rtsp://192.168.1.100:8554/fpv",
                         "Raspberry Pi onboard running MediaMTX -> confirm IP"),
    "fpv_orange_pi":    ("rtsp://192.168.1.101:8554/fpv",
                         "Orange Pi onboard running MediaMTX -> confirm IP"),

    # ─── IP Cameras (ground-mounted surveillance) ─────────────────────
    "hikvision":        ("rtsp://admin:admin@192.168.1.64:554/h264/ch1/main/av_stream",
                         "Change admin:admin to your credentials"),
    "dahua":            ("rtsp://admin:admin@192.168.1.65:554/cam/realmonitor?channel=1&subtype=0",
                         "Change admin:admin to your credentials"),
    "reolink":          ("rtsp://admin:@192.168.1.66:554/h264Preview_01_main",
                         "Change admin: to your password"),
    "amcrest":          ("rtsp://admin:admin@192.168.1.67:554/cam/realmonitor?channel=1",
                         "Change credentials to yours"),
    "axis":             ("rtsp://192.168.1.68/axis-media/media.amp",
                         "Axis cam — no credentials needed by default"),

    # ─── Phone as camera ─────────────────────────────────────────────
    "android_ipwebcam": ("rtsp://192.168.1.X:8080/h264_ulaw.sdp",
                         "Install 'IP Webcam' (free) -> Start Server -> replace X with shown IP"),
    "iphone_epoccam":   ("rtsp://192.168.1.X:8554/live",
                         "Install 'EpocCam' + PC driver -> replace X with shown IP"),
    "iphone_camo":      ("rtsp://192.168.1.X:8080/live",
                         "Install 'Camo' -> enable RTSP -> replace X with shown IP"),

    # ─── Relay / Restream ────────────────────────────────────────────
    "mediamtx_local":   ("rtsp://localhost:8554/drone",
                         "MediaMTX on same machine -> drone app pushes to it"),
    "mediamtx_server":  ("rtsp://SERVER_IP:8554/drone",
                         "MediaMTX on another machine -> replace SERVER_IP"),
    "obs_studio":       ("rtsp://localhost:8554/obs",
                         "OBS -> RTSP Server plugin -> stream key = obs"),
}


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
        "stimeout;5000000|"
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
        self.is_live       = self._detect_live(source)
        self._lock         = threading.Lock()

        self._frame_times = collections.deque(maxlen=120)
        self._drop_count  = 0
        self._total_reads = 0
        self._connect_ts  = None

        self.cap = self._open()

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
            ("rtsp://", "rtsps://", "http://", "https://")
        )

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
        opts = _ffmpeg_opts(self.transport)
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts + "|hwaccel;auto"

        src_str  = str(self.source)
        safe_url = self._safe_url(src_str)

        print(f"[STREAM] Connecting  : {safe_url}")
        print(f"[STREAM] Transport   : {self.transport.upper()}")

        cap = cv2.VideoCapture(src_str, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

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

        with self._lock:
            if self.is_live:
                ret = self.cap.grab()
                if not ret:
                    return self._reconnect()
                ok, frame = self.cap.retrieve()
                if not ok or frame is None:
                    return self._reconnect()
            else:
                ok, frame = self.cap.read()
                if not ok:
                    return False, None

        self._frame_times.append(time.monotonic())
        return True, frame

    def _reconnect(self):
        if not self.is_live:
            return False, None
        backoff = BASE_BACKOFF_S
        for attempt in range(1, MAX_RECONNECT + 1):
            self._drop_count += 1
            print(f"[STREAM] Reconnect {attempt}/{MAX_RECONNECT} (wait {backoff:.1f}s) ...")
            self.cap.release()
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
            self.cap = self._open_rtsp()
            if self.cap.isOpened():
                ok = self.cap.grab()
                if ok:
                    ok2, frame = self.cap.retrieve()
                    if ok2 and frame is not None:
                        print(f"[STREAM] Reconnected after {attempt} attempt(s).")
                        return True, frame
        print(f"[STREAM] FATAL: could not reconnect after {MAX_RECONNECT} tries.")
        return False, None

    # ── info ─────────────────────────────────────────────────────────

    def get_fps(self) -> float:
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            if span > 0:
                return (len(self._frame_times) - 1) / span
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if (fps and fps == fps and fps > 0) else 25.0

    def get_resolution(self):
        return (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def is_opened(self) -> bool:
        return self.cap.isOpened()

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
            if self.cap.isOpened():
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
