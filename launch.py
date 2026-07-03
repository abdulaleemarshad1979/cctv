"""
launch.py  —  Pushkaralu Drone Crowd Monitor  |  MASTER LAUNCHER
=================================================================
USAGE:
    python launch.py

    That's it. Paste your RTSP URL or drone name when asked,
    and everything else starts automatically.

    You can also pass the source directly:
        python launch.py rtsp://192.168.42.1/live
        python launch.py dji_mini3
        python launch.py                        ← interactive menu

SAVED SOURCES:
    Your last-used sources are saved in  sources.txt
    Edit that file any time, or just use the menu.
"""

import os
import sys
import subprocess
import json
import time
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
SOURCES_FILE = BASE_DIR / "sources.txt"
INFER_SCRIPT = BASE_DIR / "infer.py"

# ── colours (ANSI) ────────────────────────────────────────────────────
RED    = "\033[91m"
GRN    = "\033[92m"
YLW    = "\033[93m"
BLU    = "\033[94m"
MAG    = "\033[95m"
CYN    = "\033[96m"
WHT    = "\033[97m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── known drone presets (imported from src.presets) ───────────────────
from src.presets import DRONE_DB
DRONE_PRESETS = {k: v[0] for k, v in DRONE_DB.items()}


# ── default sources list content ──────────────────────────────────────
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

# More examples (uncomment to use):
# rtsp://192.168.0.1:8554/live
# dji_mini4pro
# /home/user/Videos/test_crowd.mp4

"""


# ═════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print(f"""
{CYN}{BOLD}+--------------------------------------------------------------+
|       PUSHKARALU DRONE CROWD MONITOR  —  LAUNCHER            |
|       Powered by DM-Count  |  AI Crowd Risk Engine           |
+--------------------------------------------------------------+{RESET}
""")


def load_sources() -> list[str]:
    """Read sources.txt and return non-empty, non-comment lines."""
    if not SOURCES_FILE.exists():
        SOURCES_FILE.write_text(DEFAULT_SOURCES_CONTENT, encoding="utf-8")
        print(f"{YLW}[INFO] Created sources.txt — edit it to add your RTSP URLs.{RESET}\n")
    lines = SOURCES_FILE.read_text(encoding="utf-8").splitlines()
    sources = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(stripped)
    return sources


def save_source(source: str):
    """Append a new source to sources.txt if not already present."""
    existing = load_sources()
    if source in existing:
        return
    with open(SOURCES_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{source}\n")
    print(f"{GRN}[SAVED] '{source}' added to sources.txt{RESET}")


def resolve_to_url(source: str) -> str:
    """If source is a preset name, return its URL; otherwise return as-is."""
    s = source.strip().lower()
    if s in DRONE_PRESETS:
        return DRONE_PRESETS[s]
    return source.strip()


def validate_source(source: str) -> tuple[bool, str]:
    """Basic validation. Returns (ok, reason)."""
    s = source.strip()
    if not s:
        return False, "Empty input."
    if s.lower() in DRONE_PRESETS:
        return True, f"Drone preset  ->  {DRONE_PRESETS[s.lower()]}"
    if s.startswith(("rtsp://", "rtsps://", "rtmp://", "rtmps://", "http://", "https://")):
        return True, "RTSP / RTMP / HTTP stream"
    if s.isdigit():
        return True, f"USB Webcam #{s}"
    if os.path.isfile(s):
        return True, f"Local video file"
    if os.path.isfile(os.path.join(BASE_DIR, "Videos", os.path.basename(s))):
        return True, "Found in Videos/ folder"
    # Could still be valid (network path, future file, etc.)
    return True, f"Custom source (not validated)"


def describe_source(source: str) -> str:
    s = source.strip()
    if s.lower() in DRONE_PRESETS:
        return f"{MAG}[PRESET]{RESET}  {s}  ->  {DIM}{DRONE_PRESETS[s.lower()]}{RESET}"
    if s.startswith("rtsp://") or s.startswith("rtsps://"):
        return f"{CYN}[RTSP  ]{RESET}  {s}"
    if s.startswith("rtmp://") or s.startswith("rtmps://"):
        return f"{GRN}[RTMP  ]{RESET}  {s}"
    if s.startswith(("http://", "https://")):
        return f"{BLU}[HTTP  ]{RESET}  {s}"
    if s.isdigit():
        return f"{GRN}[WEBCAM]{RESET}  USB Camera #{s}"
    return f"{YLW}[FILE  ]{RESET}  {s}"


# ═════════════════════════════════════════════════════════════════════
#  LAUNCH
# ═════════════════════════════════════════════════════════════════════

def launch_infer(source: str):
    """Set env vars and launch infer.py as a subprocess."""
    source = source.strip()
    url    = resolve_to_url(source)

    # Build environment
    env = os.environ.copy()
    env["CCTV_SOURCE"] = url

    # If the source is a preset name, also set DRONE so infer.py
    # can log it correctly via drone_stream.resolve_source()
    if source.lower() in DRONE_PRESETS:
        env["DRONE"] = source.lower()

    print(f"\n{GRN}{BOLD}[LAUNCH] Starting monitor...{RESET}")
    print(f"  Source : {describe_source(source)}")
    print(f"  Script : {INFER_SCRIPT}")
    print(f"\n{DIM}Press Q inside the video window to stop.{RESET}\n")
    time.sleep(0.8)

    cmd = [sys.executable, str(INFER_SCRIPT)]
    try:
        result = subprocess.run(cmd, env=env, cwd=str(BASE_DIR))
        if result.returncode != 0:
            print(f"\n{YLW}[INFO] infer.py exited with code {result.returncode}.{RESET}")
    except KeyboardInterrupt:
        print(f"\n{YLW}[STOPPED] Launcher interrupted.{RESET}")
    except FileNotFoundError:
        print(f"{RED}[ERROR] infer.py not found at: {INFER_SCRIPT}{RESET}")
        print(f"        Make sure launch.py is in the same folder as infer.py.")


def launch_swarm(sources: list[str], dry_run: bool = False):
    """Resolve, validate, and launch swarm_infer.py as a subprocess."""
    import config
    default_sources = config.DRONE_SOURCES
    resolved_sources = []
    
    for i in range(4):
        if i < len(sources):
            resolved_sources.append(resolve_to_url(sources[i]))
        else:
            resolved_sources.append(default_sources[i])

    print(f"\n{GRN}{BOLD}[LAUNCH-SWARM] Starting 4-drone Swarm Command Center...{RESET}")
    for i, src in enumerate(resolved_sources):
        print(f"  Drone {i+1} Source : {describe_source(src)}")

    if dry_run:
        print(f"\n{YLW}[DRY-RUN] Validating connections to all 4 drone streams...{RESET}")
        from src.drone_stream import check_connection
        all_ok = True
        for i, src in enumerate(resolved_sources):
            url = resolve_to_url(src)
            ok = check_connection(url, timeout=1.5)
            status = f"{GRN}REACHABLE{RESET}" if ok else f"{RED}UNREACHABLE / OFFLINE{RESET}"
            print(f"  Drone {i+1}: {describe_source(src)} -> {status}")
            if not ok:
                all_ok = False
        if not all_ok:
            print(f"\n{RED}[WARN] One or more sources are unreachable. Swarm may start with offline tiles.{RESET}")
        else:
            print(f"\n{GRN}[SUCCESS] All 4 sources are reachable!{RESET}")
        return

    # Build environment overrides
    env = os.environ.copy()
    for i, src in enumerate(resolved_sources):
        env[f"CCTV_SOURCE_{i+1}"] = resolve_to_url(src)

    print(f"  Script : swarm_infer.py")
    print(f"\n{DIM}Press Q inside the video window to stop.{RESET}\n")
    time.sleep(0.8)

    cmd = [sys.executable, "swarm_infer.py"]
    try:
        result = subprocess.run(cmd, env=env, cwd=str(BASE_DIR))
        if result.returncode != 0:
            print(f"\n{YLW}[INFO] swarm_infer.py exited with code {result.returncode}.{RESET}")
    except KeyboardInterrupt:
        print(f"\n{YLW}[STOPPED] Launcher interrupted.{RESET}")
    except FileNotFoundError:
        print(f"{RED}[ERROR] swarm_infer.py not found.{RESET}")


# ═════════════════════════════════════════════════════════════════════
#  MENU
# ═════════════════════════════════════════════════════════════════════

def show_saved_sources_menu(sources: list[str]) -> str | None:
    """Display saved sources and let user pick one. Returns chosen source or None."""
    if not sources:
        return None

    print(f"{BOLD}  Saved sources:{RESET}")
    for i, src in enumerate(sources, 1):
        print(f"    {CYN}[{i}]{RESET}  {describe_source(src)}")
    print()
    return None   # caller decides


def main_menu():
    clear()
    banner()

    # ── check infer.py exists ────────────────────────────────────────
    if not INFER_SCRIPT.exists():
        print(f"{RED}[ERROR] infer.py not found in: {BASE_DIR}{RESET}")
        print(f"        Run launch.py from the same folder as infer.py.")
        sys.exit(1)

    sources = load_sources()

    # ── show saved sources ────────────────────────────────────────────
    if sources:
        show_saved_sources_menu(sources)
        print(f"  Enter a {BOLD}number{RESET} to use a saved source,")
        print(f"  or {BOLD}paste any RTSP URL / drone name{RESET} to use it directly.")
        print(f"  Type {CYN}swarm{RESET} to start the 4-drone Swarm Command Center,")
        print(f"  Type {CYN}edit{RESET} to open sources.txt,")
        print(f"  or   {CYN}list{RESET} to see all drone preset names.\n")
    else:
        print(f"  {YLW}No saved sources yet.{RESET}")
        print(f"  {BOLD}Paste your RTSP URL or drone name below.{RESET}")
        print(f"  (It will be saved automatically.)\n")
        print(f"  Type {CYN}swarm{RESET} to start the 4-drone Swarm Command Center,")
        print(f"  Type {CYN}list{RESET} to see all drone preset names.\n")

    # ── prompt ────────────────────────────────────────────────────────
    try:
        raw = input(f"{BOLD}  > {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        print(f"\n{YLW}[EXIT] Cancelled.{RESET}")
        sys.exit(0)

    if not raw:
        print(f"{YLW}[INFO] Nothing entered. Exiting.{RESET}")
        sys.exit(0)

    # ── commands ──────────────────────────────────────────────────────
    if raw.lower() in ("swarm", "s"):
        launch_swarm([])
        print(f"\n{CYN}[DONE] Swarm closed. Returning to launcher...{RESET}")
        time.sleep(1.5)
        main_menu()
        return

    if raw.lower() in ("list", "--list", "-l"):
        print()
        for name, url in DRONE_PRESETS.items():
            print(f"  {MAG}{name:<20}{RESET}  {DIM}{url}{RESET}")
        print()
        input("  Press Enter to return...")
        main_menu()
        return

    if raw.lower() in ("edit", "e"):
        _open_sources_file()
        main_menu()
        return

    # ── numeric pick ──────────────────────────────────────────────────
    if raw.isdigit() and sources:
        idx = int(raw) - 1
        if 0 <= idx < len(sources):
            chosen = sources[idx]
            print(f"\n  {GRN}Using:{RESET} {describe_source(chosen)}\n")
        else:
            print(f"{RED}[ERROR] Number out of range. Pick 1–{len(sources)}.{RESET}")
            time.sleep(1.5)
            main_menu()
            return
    else:
        # Treat as a raw source (URL, preset name, file path)
        chosen = raw

    # ── validate ──────────────────────────────────────────────────────
    ok, reason = validate_source(chosen)
    if not ok:
        print(f"{RED}[ERROR] {reason}{RESET}")
        time.sleep(1.5)
        main_menu()
        return

    print(f"  {DIM}({reason}){RESET}")

    # ── save if new ───────────────────────────────────────────────────
    save_source(chosen)

    # ── launch ────────────────────────────────────────────────────────
    launch_infer(chosen)

    # ── after monitor closes, return to menu ──────────────────────────
    print(f"\n{CYN}[DONE] Monitor closed. Returning to launcher...{RESET}")
    time.sleep(1.5)
    main_menu()


def _open_sources_file():
    """Open sources.txt in the system default editor."""
    import platform
    print(f"\n{GRN}[INFO] Opening sources.txt...{RESET}")
    if platform.system() == "Windows":
        os.startfile(str(SOURCES_FILE))
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(SOURCES_FILE)])
    else:
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(SOURCES_FILE)])
    time.sleep(0.5)


# ═════════════════════════════════════════════════════════════════════
#  DIRECT-ARG MODE  (python launch.py rtsp://... or python launch.py dji_mini3)
# ═════════════════════════════════════════════════════════════════════

def run_direct(source: str):
    """Skip the menu and launch directly with the given source."""
    banner()
    ok, reason = validate_source(source)
    print(f"  Source : {describe_source(source)}")
    print(f"  {DIM}({reason}){RESET}")
    save_source(source)
    launch_infer(source)


# ═════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Enable ANSI escape sequences on Windows Command Prompt
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    args = sys.argv[1:]
    swarm_mode = False
    dry_run = False
    
    # Process flags
    filtered_args = []
    for arg in args:
        if arg.lower() == "--swarm":
            swarm_mode = True
        elif arg.lower() == "--dry-run":
            dry_run = True
        else:
            filtered_args.append(arg)
            
    if swarm_mode:
        launch_swarm(filtered_args, dry_run=dry_run)
    elif dry_run:
        # Dry-run check for a single source
        if not filtered_args:
            print(f"{RED}[ERROR] No source specified for dry-run.{RESET}")
            sys.exit(1)
        source = filtered_args[0]
        banner()
        print(f"{YLW}[DRY-RUN] Checking connection for single source...{RESET}")
        print(f"  Source : {describe_source(source)}")
        from src.drone_stream import check_connection
        url = resolve_to_url(source)
        ok = check_connection(url, timeout=1.5)
        status = f"{GRN}REACHABLE{RESET}" if ok else f"{RED}UNREACHABLE / OFFLINE{RESET}"
        print(f"  Status : {status}")
    elif filtered_args:
        run_direct(" ".join(filtered_args))
    else:
        main_menu()
