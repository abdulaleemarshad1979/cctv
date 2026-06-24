"""
manage_rtsp.py  —  RTSP Preset Manager
=======================================
Use this IN FRONT OF the drone team to:
  1. Paste their RTSP URL
  2. Live-test it (opens video window)
  3. Give it a short name
  4. Save it permanently to drone_stream.py + source_picker.py

Run:
    python manage_rtsp.py          ← interactive menu
    python manage_rtsp.py test     ← jump straight to test
    python manage_rtsp.py list     ← show all saved presets
    python manage_rtsp.py remove   ← remove a preset
"""

import os
import sys
import re
import cv2
import time
import threading
import subprocess
from pathlib import Path

BASE_DIR         = Path(__file__).parent
DRONE_STREAM_PY  = BASE_DIR / "src" / "drone_stream.py"
SOURCE_PICKER_PY = BASE_DIR / "src" / "source_picker.py"

# ── ANSI colours ─────────────────────────────────────────────────────
R = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GRN   = "\033[92m"
YLW   = "\033[93m"
RED   = "\033[91m"
CYN   = "\033[96m"
MAG   = "\033[95m"
WHT   = "\033[97m"

def c(col, txt): return f"{col}{txt}{R}"
def ok(msg):     print(f"  {c(GRN,'✓')} {msg}")
def err(msg):    print(f"  {c(RED,'✗')} {msg}")
def info(msg):   print(f"  {c(CYN,'→')} {msg}")
def warn(msg):   print(f"  {c(YLW,'!')} {msg}")

def clear(): os.system("cls" if os.name == "nt" else "clear")

def _enable_ansi():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def divider(ch="─", w=62, col=DIM):
    print(c(col, ch * w))

def header(title):
    clear()
    divider("═", 62, CYN)
    print(c(CYN + BOLD, f"  RTSP MANAGER  ·  {title}"))
    divider("═", 62, CYN)
    print()

def prompt(label, default=""):
    suffix = f" [{c(DIM, default)}]" if default else ""
    try:
        val = input(f"  {c(BOLD, label)}{suffix}: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


# ═══════════════════════════════════════════════════════════════════
#  READ existing presets from drone_stream.py
# ═══════════════════════════════════════════════════════════════════

def read_drone_db() -> dict:
    """Parse DRONE_DB from drone_stream.py — handles two-line entries."""
    raw = DRONE_STREAM_PY.read_text(encoding="utf-8")
    marker = raw.find("DRONE_DB = {")
    if marker < 0:
        return {}
    # Find the opening brace
    brace_start = marker + raw[marker:].index("{")
    # Count braces to find the closing }
    brace, end = 0, brace_start
    for i, ch in enumerate(raw[brace_start:]):
        if ch == "{": brace += 1
        elif ch == "}": brace -= 1
        if brace == 0:
            end = brace_start + i
            break
    block = raw[brace_start: end + 1]
    # Flatten two-line entries: join url-line with note-line
    block = re.sub(r',\s*\n\s*"', ', "', block)
    pattern = re.compile(r'"([\w]+)"\s*:\s*\("([^"]+)",\s*"([^"]+)"\s*\)')
    db = {}
    for name, url, note in pattern.findall(block):
        db[name] = (url, note)
    return db


# ═══════════════════════════════════════════════════════════════════
#  WRITE — inject into drone_stream.py
# ═══════════════════════════════════════════════════════════════════

def write_to_drone_stream(name: str, url: str, note: str) -> bool:
    """Append a new entry to DRONE_DB in drone_stream.py."""
    text = DRONE_STREAM_PY.read_text(encoding="utf-8")

    # Find the closing brace of DRONE_DB
    # We insert just before the last } that ends the dict
    # Strategy: find "# ─── Relay / Restream" block end and insert after it
    # Fallback: find the closing } of DRONE_DB and insert before it

    insert_marker = '    "obs_studio":'
    mavion_section_marker = "# ─── Mavion Aerospace"

    # If Mavion section already exists, insert after last mavion entry
    if mavion_section_marker in text:
        # Find position of last mavion_ entry and insert after it
        lines = text.splitlines()
        last_mavion_line = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('"mavion_') or \
               line.strip().startswith("# ─── Mavion"):
                last_mavion_line = i

        if last_mavion_line >= 0:
            # find the closing paren of that entry
            for i in range(last_mavion_line, min(last_mavion_line + 4, len(lines))):
                if lines[i].rstrip().endswith("),"):
                    last_mavion_line = i
                    break
            new_entry = (
                f'    "{name}":{" " * max(1, 20 - len(name))}'
                f'("{url}",\n'
                f'                         "{note}"),\n'
            )
            lines.insert(last_mavion_line + 1, new_entry.rstrip("\n"))
            DRONE_STREAM_PY.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

    # No Mavion section yet — create it before the closing } of DRONE_DB
    # Find "obs_studio" entry (last in DRONE_DB) and insert after its closing ),
    lines = text.splitlines()
    insert_after = -1
    for i, line in enumerate(lines):
        if '"obs_studio"' in line or '"mediamtx_server"' in line:
            # scan forward to find closing ),
            for j in range(i, min(i + 4, len(lines))):
                if lines[j].rstrip().endswith("),"):
                    insert_after = j
                    break

    if insert_after < 0:
        # Fallback: find the } that closes DRONE_DB
        in_db = False
        for i, line in enumerate(lines):
            if "DRONE_DB" in line and "=" in line:
                in_db = True
            if in_db and line.strip() == "}":
                insert_after = i - 1
                break

    if insert_after < 0:
        return False

    new_lines = [
        "",
        "    # ─── Mavion Aerospace (India) ──────────────────────────────",
        (f'    "{name}":{" " * max(1, 20 - len(name))}'
         f'("{url}",'),
        f'                         "{note}"),',
    ]
    for offset, nl in enumerate(new_lines):
        lines.insert(insert_after + 1 + offset, nl)

    DRONE_STREAM_PY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def remove_from_drone_stream(name: str) -> bool:
    """Remove a named entry from DRONE_DB in drone_stream.py."""
    text  = DRONE_STREAM_PY.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = []
    skip_next = False
    removed = False
    for line in lines:
        if skip_next:
            # continuation line of multi-line entry — ends with ),
            if line.rstrip().endswith("),"):
                skip_next = False
            removed = True
            continue
        # Match the start of this entry
        if re.match(rf'\s*"{re.escape(name)}"\s*:', line):
            if not line.rstrip().endswith("),"):
                skip_next = True   # multi-line entry
            removed = True
            continue
        new_lines.append(line)
    if removed:
        DRONE_STREAM_PY.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed


# ═══════════════════════════════════════════════════════════════════
#  WRITE — inject into source_picker.py
# ═══════════════════════════════════════════════════════════════════

def write_to_source_picker(name: str, url: str) -> bool:
    """Add entry to PRESETS dict in source_picker.py."""
    text  = SOURCE_PICKER_PY.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the closing } of PRESETS (in source_picker.py this dict is DRONE_PRESETS)
    in_presets = False
    close_idx  = -1
    for i, line in enumerate(lines):
        if re.match(r"^    DRONE_PRESETS\s*=\s*\{", line):
            in_presets = True
        if in_presets and line.strip() == "}":
            close_idx = i
            break

    if close_idx < 0:
        return False

    # Check if name already exists
    if f'"{name}"' in text:
        return True   # already there

    pad      = max(1, 24 - len(name))
    new_line = f'        "{name}":{" " * pad}"{url}",'
    lines.insert(close_idx, new_line)
    SOURCE_PICKER_PY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def remove_from_source_picker(name: str) -> bool:
    text  = SOURCE_PICKER_PY.read_text(encoding="utf-8")
    lines = text.splitlines()
    new_lines = [l for l in lines if f'"{name}"' not in l or "DRONE_PRESETS" in l]
    removed = len(new_lines) < len(lines)
    if removed:
        SOURCE_PICKER_PY.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed


# ═══════════════════════════════════════════════════════════════════
#  LIVE TEST  — opens cv2 window, shows frames + stats
# ═══════════════════════════════════════════════════════════════════

def live_test(url: str, timeout_s: int = 15) -> bool:
    """
    Try to open the RTSP stream and show a preview window.
    Returns True if at least one frame was received.
    """
    print()
    info(f"Connecting to: {c(CYN, url)}")
    info(f"Timeout: {timeout_s}s  |  Press  {c(BOLD,'Q')}  inside window to close")
    print()

    # Set low-latency ffmpeg options
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
        "rtsp_transport;tcp|"
        "fflags;nobuffer|"
        "flags;low_delay|"
        "stimeout;5000000|"
        "analyzeduration;100000|"
        "probesize;500000"
    )

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

    if not cap.isOpened():
        err("Could not open stream — check URL and Wi-Fi connection.")
        cap.release()
        return False

    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    ok(f"Connected!  {w}×{h} @ {fps:.0f} fps")
    print()

    start      = time.monotonic()
    frame_no   = 0
    got_frame  = False
    win_name   = "RTSP Test — Q to close"

    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, min(w, 960), min(h, 540))

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout_s and not got_frame:
            err("Timed out — no frames received.")
            break

        ret, frame = cap.read()
        if not ret:
            if got_frame:
                warn("Stream dropped.")
            else:
                err("No frame received.")
            break

        frame_no  += 1
        got_frame  = True
        elapsed    = time.monotonic() - start
        live_fps   = frame_no / max(elapsed, 0.001)

        # Overlay
        disp = cv2.resize(frame, (min(w, 960), min(h, 540)))
        cv2.rectangle(disp, (0, 0), (disp.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(disp,
            f"RTSP TEST  |  Frame {frame_no}  |  {live_fps:.1f} fps  |  {w}x{h}",
            (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 220, 100), 1)
        cv2.putText(disp, url,
            (8, disp.shape[0] - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.34, (160, 160, 160), 1)

        cv2.imshow(win_name, disp)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):
            break

    cap.release()
    cv2.destroyAllWindows()

    if got_frame:
        ok(f"Stream verified  ✓  ({frame_no} frames received)")
    return got_frame


# ═══════════════════════════════════════════════════════════════════
#  MENU ACTIONS
# ═══════════════════════════════════════════════════════════════════

def action_add():
    """Full flow: paste URL → test → name → save."""
    header("Add New RTSP Preset")

    print(c(BOLD, "  Step 1 — Paste the RTSP URL"))
    divider()
    url = prompt("RTSP URL")
    if not url:
        warn("No URL entered. Returning to menu.")
        return

    if not url.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        warn("URL doesn't look like an RTSP address. Continuing anyway.")

    print()
    print(c(BOLD, "  Step 2 — Live test"))
    divider()

    test_choice = prompt("Test the stream now? (y/n)", "y").lower()
    verified = False
    if test_choice == "y":
        verified = live_test(url)
        print()
        if not verified:
            save_anyway = prompt(
                f"Stream did not connect. Save anyway? (y/n)", "n").lower()
            if save_anyway != "y":
                warn("Not saved. Check the URL and try again.")
                input(f"\n  {c(DIM, 'Press Enter to return...')}")
                return
    else:
        warn("Skipping test — URL will be saved as-is.")

    print()
    print(c(BOLD, "  Step 3 — Give it a short name"))
    divider()
    print(f"  {c(DIM,'Examples: mavion_yodha  mavion_jwala  site_camera_1')}")
    print(f"  {c(DIM,'Use lowercase letters, numbers, and underscores only.')}")
    print()

    while True:
        name = prompt("Preset name").lower().strip()
        if not name:
            warn("Name cannot be empty.")
            continue
        if not re.match(r"^[a-z0-9_]+$", name):
            err("Use only lowercase letters, numbers, underscores.")
            continue

        # Check for duplicate
        existing = read_drone_db()
        if name in existing:
            ex_url, _ = existing[name]
            warn(f'Name "{name}" already exists → {ex_url}')
            overwrite = prompt("Overwrite it? (y/n)", "n").lower()
            if overwrite != "y":
                continue
            # Remove old entry first
            remove_from_drone_stream(name)
            remove_from_source_picker(name)
        break

    note = prompt(
        "Short note (shown in list, optional)",
        f"Mavion Aerospace → {url}"
    )
    if not note:
        note = f"Added via manage_rtsp.py"

    print()
    print(c(BOLD, "  Step 4 — Saving"))
    divider()

    d_ok = write_to_drone_stream(name, url, note)
    p_ok = write_to_source_picker(name, url)

    if d_ok:
        ok(f"drone_stream.py  → added  \"{name}\"")
    else:
        err("drone_stream.py  → FAILED to write")

    if p_ok:
        ok(f"source_picker.py → added  \"{name}\"")
    else:
        err("source_picker.py → FAILED to write")

    if d_ok or p_ok:
        print()
        print(f"  {c(GRN + BOLD,'Saved!')}  You can now use it as:")
        print(f"    {c(CYN, f'python launch.py {name}')}")
        print(f"    {c(CYN, f'python drone_stream.py {name}')}")
        print(f"    {c(DIM, f'Or type  {name}  in the source picker menu')}")

    print()
    input(f"  {c(DIM,'Press Enter to return to menu...')}")


def action_test_only():
    """Just test a URL — no saving."""
    header("Test an RTSP URL")
    print(f"  {c(DIM,'Paste the full RTSP URL below.')}")
    print(f"  {c(DIM,'A video window will open. Press Q to close it.')}")
    print()
    url = prompt("RTSP URL")
    if not url:
        return
    live_test(url)
    print()
    input(f"  {c(DIM,'Press Enter to return to menu...')}")


def action_list():
    """List all saved presets from drone_stream.py."""
    header("All Saved Presets")
    divider()
    db = read_drone_db()

    if not db:
        warn("No presets found in drone_stream.py")
        input(f"\n  {c(DIM,'Press Enter to return...')}")
        return

    brands = {}
    for name, (url, note) in db.items():
        brand = name.split("_")[0].upper()
        brands.setdefault(brand, []).append((name, url, note))

    for brand, entries in brands.items():
        print(f"  {c(CYN + BOLD, f'[{brand}]')}")
        for name, url, note in entries:
            highlight = c(YLW, name) if "mavion" in name else c(WHT, name)
            print(f"    {highlight:<38}  {c(GRN, url)}")
            print(f"    {' ' * 4}  {c(DIM, note)}")
        print()

    print(f"  Total: {len(db)} presets")
    print()
    input(f"  {c(DIM,'Press Enter to return...')}")


def action_remove():
    """Remove a preset by name."""
    header("Remove a Preset")

    db = read_drone_db()
    names = list(db.keys())

    print(f"  {c(BOLD,'Existing presets:')}  (type the name to remove)\n")
    for i, name in enumerate(names, 1):
        url, _ = db[name]
        col = YLW if "mavion" in name else DIM
        print(f"  {c(col, name):<32}  {c(DIM, url)}")

    print()
    name = prompt("Preset name to remove (or Enter to cancel)").lower().strip()
    if not name:
        return

    if name not in db:
        err(f'"{name}" not found.')
        input(f"\n  {c(DIM,'Press Enter to return...')}")
        return

    url, note = db[name]
    print()
    warn(f'About to remove: {c(CYN, name)} → {url}')
    confirm = prompt("Confirm removal? (y/n)", "n").lower()
    if confirm != "y":
        info("Cancelled.")
        input(f"\n  {c(DIM,'Press Enter to return...')}")
        return

    d_ok = remove_from_drone_stream(name)
    p_ok = remove_from_source_picker(name)

    print()
    if d_ok: ok(f"Removed from drone_stream.py")
    else:    err(f"Not found in drone_stream.py")
    if p_ok: ok(f"Removed from source_picker.py")
    else:    warn(f"Not in source_picker.py (that's ok)")

    print()
    input(f"  {c(DIM,'Press Enter to return...')}")


def action_quick_launch():
    """Pick a saved preset and launch infer.py with it."""
    header("Quick Launch")
    db = read_drone_db()
    names = list(db.keys())

    print(f"  {c(BOLD,'Pick a preset to launch infer.py:')}\n")
    for i, name in enumerate(names, 1):
        url, _ = db[name]
        col = GRN if "mavion" in name else WHT
        print(f"  {c(CYN, f'[{i}]')}  {c(col, name):<30}  {c(DIM, url)}")

    print()
    raw = prompt("Number or preset name").strip()

    chosen_name = None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(names):
            chosen_name = names[idx]
        else:
            err("Number out of range.")
            input(f"\n  {c(DIM,'Press Enter...')}")
            return
    elif raw.lower() in db:
        chosen_name = raw.lower()
    else:
        err(f'"{raw}" not found.')
        input(f"\n  {c(DIM,'Press Enter...')}")
        return

    url, _ = db[chosen_name]
    info(f"Launching with preset:  {c(YLW, chosen_name)}")
    info(f"URL: {c(CYN, url)}")
    print()

    env = os.environ.copy()
    env["CCTV_SOURCE"] = url
    infer = BASE_DIR / "infer.py"
    if not infer.exists():
        err(f"infer.py not found at {infer}")
        return

    try:
        subprocess.run([sys.executable, str(infer)], env=env, cwd=str(BASE_DIR))
    except KeyboardInterrupt:
        pass

    print()
    input(f"  {c(DIM,'Monitor closed. Press Enter to return to menu...')}")


# ═══════════════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════════════

MENU_ITEMS = [
    ("A", "Add new RTSP preset",          action_add),
    ("T", "Test a URL (no saving)",        action_test_only),
    ("L", "List all presets",              action_list),
    ("R", "Remove a preset",               action_remove),
    ("G", "Launch monitor with a preset",  action_quick_launch),
    ("Q", "Quit",                          None),
]

def main_menu():
    _enable_ansi()
    while True:
        header("Main Menu")

        db = read_drone_db()
        mavion_count = sum(1 for k in db if "mavion" in k)
        total = len(db)

        print(f"  {c(DIM, f'Presets loaded: {total}  |  Mavion presets: {mavion_count}')}")
        print()

        for key, label, _ in MENU_ITEMS:
            col = RED if key == "Q" else CYN
            highlight = YLW if key == "A" else WHT
            print(f"  {c(col, f'[{key}]')}  {c(highlight, label)}")

        print()
        divider()
        try:
            choice = input(f"  {c(BOLD,'Choose: ')}").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice == "Q":
            break

        for key, label, fn in MENU_ITEMS:
            if choice == key and fn:
                fn()
                break
        else:
            warn("Invalid choice.")
            time.sleep(0.6)

    clear()
    print(c(CYN, "  RTSP Manager closed.\n"))


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""

    if arg == "test":
        _enable_ansi()
        header("Quick Test")
        url = prompt("RTSP URL to test")
        if url:
            live_test(url)
    elif arg == "list":
        _enable_ansi()
        action_list()
    elif arg == "add":
        _enable_ansi()
        action_add()
    elif arg == "remove":
        _enable_ansi()
        action_remove()
    else:
        main_menu()
