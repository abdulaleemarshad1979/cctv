"""
gen_rtmp.py — Pushkaralu Drone Monitor | RTMP Stream Generator & Simulator
========================================================================
This script simulates a DJI drone pushing an RTMP stream.
It will:
  1. Download and extract MediaMTX (RTSP/RTMP server) if not present.
  2. Run MediaMTX locally in the background.
  3. Locate FFmpeg on your system.
  4. Stream one of your video files in a loop to:
     rtmp://localhost:1935/live
     
MediaMTX automatically exposes this RTMP stream as an RTSP endpoint:
     rtsp://localhost:8554/live

Which is exactly what the dji_air3 preset expects!
"""

import os
import sys
import zipfile
import urllib.request
import subprocess
import time
import shutil

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEOS_DIR = os.path.join(BASE_DIR, "Videos")
MEDIAMTX_EXE = os.path.join(BASE_DIR, "mediamtx.exe")
MEDIAMTX_ZIP = os.path.join(BASE_DIR, "mediamtx.zip")

# ── Colours ───────────────────────────────────────────────────────────
RED   = "\033[91m"
GRN   = "\033[92m"
YLW   = "\033[93m"
BLU   = "\033[94m"
MAG   = "\033[95m"
CYN   = "\033[96m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def banner():
    print(f"""
{CYN}{BOLD}+--------------------------------------------------------------+
|       RTMP STREAM GENERATOR & DJI SIMULATOR                  |
|       Bridges Local Videos → MediaMTX → RTSP                 |
+--------------------------------------------------------------+{RESET}
""")

def find_ffmpeg():
    """Attempt to find FFmpeg in PATH or typical locations."""
    # 1. Check system path
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    
    # 2. Check the specific winget path found on the user's PC
    winget_path = r"C:\\Users\\abdul\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\ffmpeg-8.1.1-full_build\\bin\\ffmpeg.exe"
    if os.path.exists(winget_path):
        return winget_path
        
    # 3. Check current folder
    local_path = os.path.join(BASE_DIR, "ffmpeg.exe")
    if os.path.exists(local_path):
        return local_path
        
    return None

def check_or_download_mediamtx():
    """Ensure MediaMTX is downloaded and extracted."""
    if os.path.exists(MEDIAMTX_EXE):
        print(f"{GRN}[INFO] MediaMTX binary found at: {MEDIAMTX_EXE}{RESET}")
        return True
        
    url = "https://github.com/bluenviron/mediamtx/releases/download/v1.9.0/mediamtx_v1.9.0_windows_amd64.zip"
    print(f"{YLW}[INFO] MediaMTX not found. Downloading v1.9.0 from GitHub...{RESET}")
    print(f"{DIM}Source: {url}{RESET}\n")
    
    def progress(count, block_size, total_size):
        percent = int(count * block_size * 100 / total_size)
        sys.stdout.write(f"\rDownloading: [{('█' * (percent // 5)):<20}] {percent}%")
        sys.stdout.flush()
        
    try:
        urllib.request.urlretrieve(url, MEDIAMTX_ZIP, progress)
        print(f"\n\n{GRN}[INFO] Download complete. Extracting...{RESET}")
        
        with zipfile.ZipFile(MEDIAMTX_ZIP, 'r') as zip_ref:
            # We only need mediamtx.exe
            zip_ref.extract("mediamtx.exe", path=BASE_DIR)
            
        print(f"{GRN}[INFO] Extracted mediamtx.exe successfully.{RESET}")
        
        # Clean up zip
        if os.path.exists(MEDIAMTX_ZIP):
            os.remove(MEDIAMTX_ZIP)
            
        return True
    except Exception as e:
        print(f"\n{RED}[ERROR] Failed to download or extract MediaMTX: {e}{RESET}")
        return False

def list_videos():
    """List all mp4 files in the Videos folder."""
    if not os.path.exists(VIDEOS_DIR):
        print(f"{RED}[ERROR] Videos folder not found at: {VIDEOS_DIR}{RESET}")
        return []
    
    videos = [f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith(('.mp4', '.mkv', '.avi', '.mov'))]
    return sorted(videos)

def main():
    clear()
    banner()
    
    # 1. Find FFmpeg
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print(f"{RED}[ERROR] FFmpeg could not be found!{RESET}")
        print("Please install FFmpeg or make sure it is in your system PATH.")
        sys.exit(1)
    print(f"{GRN}[INFO] FFmpeg path resolved: {ffmpeg_path}{RESET}")
    
    # 2. Check/Download MediaMTX
    if not check_or_download_mediamtx():
        print(f"{RED}[ERROR] Could not start without MediaMTX.{RESET}")
        sys.exit(1)
        
    # 3. List available videos
    videos = list_videos()
    if not videos:
        print(f"{RED}[ERROR] No video files found in {VIDEOS_DIR}. Please add some MP4s.{RESET}")
        sys.exit(1)
        
    print(f"\n{BOLD}Select a video to stream as the drone feed:{RESET}")
    for idx, video in enumerate(videos, 1):
        size_mb = os.path.getsize(os.path.join(VIDEOS_DIR, video)) / (1024 * 1024)
        print(f"  {CYN}[{idx}]{RESET} {video:<25} ({size_mb:.1f} MB)")
        
    print()
    try:
        choice = input(f"{BOLD}Choose (default is 1): {RESET}").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting.")
        sys.exit(0)
        
    if not choice:
        selected_video = videos[0]
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(videos):
                selected_video = videos[idx]
            else:
                print(f"{RED}Invalid index. Defaulting to 1.{RESET}")
                selected_video = videos[0]
        except ValueError:
            print(f"{RED}Invalid input. Defaulting to 1.{RESET}")
            selected_video = videos[0]
            
    video_path = os.path.join(VIDEOS_DIR, selected_video)
    print(f"\n{GRN}Selected: {selected_video}{RESET}")
    
    # 4. Run MediaMTX in background
    print(f"\n{YLW}[INFO] Starting MediaMTX server...{RESET}")
    mediamtx_proc = None
    try:
        # Run mediamtx using subprocess in its own window/process
        # On Windows we can run it in a new console window to keep outputs separate if desired, 
        # or run silently. Let's start it in a separate console window so the user can see logs!
        mediamtx_proc = subprocess.Popen(
            [MEDIAMTX_EXE],
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
            cwd=BASE_DIR
        )
        print(f"{GRN}[SUCCESS] MediaMTX is running!{RESET}")
    except Exception as e:
        print(f"{RED}[ERROR] Failed to start MediaMTX: {e}{RESET}")
        sys.exit(1)
        
    # Give MediaMTX a second to bind to ports
    time.sleep(1.5)
    
    # 5. Start FFmpeg RTMP Push Stream in a loop
    print(f"\n{GRN}{BOLD}[STREAM] Starting loop stream of {selected_video}...{RESET}")
    print(f"  Pushing RTMP →  {CYN}rtmp://localhost:1935/live{RESET}")
    print(f"  MediaMTX RTSP → {MAG}rtsp://localhost:8554/live{RESET}")
    print(f"\n{DIM}Close this window / press Ctrl+C here to stop streaming.{RESET}\n")
    
    # ffmpeg stream command:
    # -re (read at native framerate)
    # -stream_loop -1 (loop infinitely)
    # -i <video> (input video)
    # -c:v h264/copy -c:a copy -f flv rtmp://localhost:1935/live
    # Using -c copy is fast and consumes zero GPU/CPU.
    cmd = [
        ffmpeg_path,
        "-re",
        "-stream_loop", "-1",
        "-i", video_path,
        "-c", "copy",
        "-f", "flv",
        "rtmp://localhost:1935/live"
    ]
    
    ffmpeg_proc = None
    try:
        ffmpeg_proc = subprocess.run(cmd, cwd=BASE_DIR)
    except KeyboardInterrupt:
        print(f"\n{YLW}[STOPPING] Stopping RTMP stream...{RESET}")
    finally:
        # Cleanup
        if ffmpeg_proc and hasattr(ffmpeg_proc, "terminate"):
            try:
                ffmpeg_proc.terminate()
            except Exception:
                pass
        if mediamtx_proc:
            print(f"{YLW}[CLEANUP] Stopping MediaMTX server...{RESET}")
            try:
                mediamtx_proc.terminate()
                mediamtx_proc.wait(timeout=2)
            except Exception:
                # Force kill if needed
                try:
                    mediamtx_proc.kill()
                except Exception:
                    pass
        print(f"{GRN}[INFO] Done. Safe to close.{RESET}")

if __name__ == "__main__":
    # Enable ANSI escape sequences on Windows Command Prompt
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
            
    main()
