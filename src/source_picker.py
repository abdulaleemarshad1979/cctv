"""
source_picker.py  —  Terminal Source Selector for CCTV Monitor
==============================================================
Prompts the user to pick a video source from saved sources, presets, or URLs
before importing config and initializing infer.py.
"""

import os
import sys
from pathlib import Path

# ── check if environment is already configured ────────────────────────
# If CCTV_SOURCE or DRONE env vars are already set (e.g. via launch.py),
# skip the picker entirely.
if "CCTV_SOURCE" in os.environ or "DRONE" in os.environ:
    # Environment already set up, nothing to do
    pass
else:
    # ── paths ─────────────────────────────────────────────────────────
    BASE_DIR     = Path(__file__).parent
    SOURCES_FILE = BASE_DIR / "sources.txt"

    DRONE_PRESETS = {
        "dji_phantom4":     "rtsp://192.168.0.1/live",
        "dji_mavic2":       "rtsp://192.168.0.1/live",
        "dji_mavic3":       "rtsp://192.168.42.1/live",
        "dji_mini2":        "rtsp://192.168.42.1/live",
        "dji_mini3":        "rtsp://192.168.0.1:8554/live",
        "dji_mini4pro":     "rtsp://192.168.0.1:8554/live",
        "dji_air2s":        "rtsp://192.168.42.1/live",
        "dji_air3":         "rtsp://192.168.42.1/live",
        "dji_avata":        "rtsp://10.0.0.22/live",
        "parrot_anafi":     "rtsp://192.168.42.1/live",
        "autel_evo2":       "rtsp://192.168.0.80/live/ch01",
        "skydio2":          "rtsp://192.168.110.1/mpeg_ts.264",
        "mediamtx_local":   "rtsp://localhost:8554/drone",
        "android_ipwebcam": "rtsp://192.168.1.X:8080/h264_ulaw.sdp",
    }

    DEFAULT_SOURCES_CONTENT = """\
# ====================================================================
# Pushkaralu Drone Monitor — Saved Sources
# ====================================================================
# Add one source per line. Lines starting with # are comments.
# Format options:
#
#   rtsp://192.168.42.1/live                 ← paste any RTSP URL here
#   rtsp://admin:pass@192.168.1.64:554/stream ← with credentials
#   dji_mini3                                ← use a drone preset name
#   dji_mavic3
#   /path/to/video.mp4                       ← local video file
#   0                                        ← USB webcam
#
# ====================================================================
# Add your sources below this line:

rtsp://192.168.42.1/live
"""

    def load_sources() -> list[str]:
        if not SOURCES_FILE.exists():
            SOURCES_FILE.write_text(DEFAULT_SOURCES_CONTENT, encoding="utf-8")
        lines = SOURCES_FILE.read_text(encoding="utf-8").splitlines()
        sources = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                sources.append(stripped)
        return sources

    def save_source(source: str):
        existing = load_sources()
        if source in existing:
            return
        with open(SOURCES_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{source}\n")

    def resolve_to_url(source: str) -> str:
        s = source.strip().lower()
        if s in DRONE_PRESETS:
            return DRONE_PRESETS[s]
        return source.strip()

    def select_source():
        print("\n" + "=" * 60)
        print("  PUSHKARALU DRONE MONITOR  —  SOURCE SELECTOR")
        print("=" * 60)

        sources = load_sources()
        if sources:
            print("  Saved sources:")
            for i, src in enumerate(sources, 1):
                desc = src
                if src.lower() in DRONE_PRESETS:
                    desc = f"{src} [Preset]"
                print(f"    [{i}] {desc}")
            print()
            print("  Enter index number, paste an RTSP URL/drone preset,")
            print("  or press Enter to use default video:")
        else:
            print("  Paste your RTSP URL, drone preset name, or press Enter for default:")

        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            sys.exit(0)

        if not raw:
            print("  Using default configuration video source.")
            return

        # check if index
        if raw.isdigit() and sources:
            idx = int(raw) - 1
            if 0 <= idx < len(sources):
                chosen = sources[idx]
            else:
                print("  Invalid selection. Exiting.")
                sys.exit(1)
        else:
            chosen = raw

        # Set env variables for configuration
        url = resolve_to_url(chosen)
        os.environ["CCTV_SOURCE"] = url
        if chosen.lower() in DRONE_PRESETS:
            os.environ["DRONE"] = chosen.lower()
        
        # Save if it was new
        save_source(chosen)
        print(f"  Selected: {chosen}\n")

    # Run selection
    select_source()
