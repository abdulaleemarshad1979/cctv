"""
tools/gen_rtmp_swarm.py — Launches 4 concurrent RTMP streams using ffmpeg to simulate active drone feeds
"""
import os
import sys
import subprocess
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_DIR = os.path.join(BASE_DIR, "Videos")
VIDEO_FILE = os.path.join(VIDEO_DIR, "VID-20260722-WA0011.mp4")

# Targets RTMP ingestion endpoints on local MediaMTX
TARGETS = [
    "rtmp://127.0.0.1:1935/live/drone1",
    "rtmp://127.0.0.1:1935/live/drone2",
    "rtmp://127.0.0.1:1935/live/drone3",
    "rtmp://127.0.0.1:1935/live/drone4",
]

def main():
    print("[SIMULATOR] Starting Swarm Stream Simulator...")
    
    if not os.path.exists(VIDEO_FILE):
        print(f"[SIMULATOR] Error: Base video file not found at {VIDEO_FILE}")
        sys.exit(1)
        
    print(f"[SIMULATOR] Using source video: {VIDEO_FILE}")
    
    processes = []
    try:
        for i, target in enumerate(TARGETS):
            print(f"[SIMULATOR] Launching Drone {i+1} simulator stream to: {target}")
            # Loop the video file infinitely, copy codec (extremely low CPU usage)
            cmd = [
                "ffmpeg",
                "-re",
                "-stream_loop", "-1",
                "-i", VIDEO_FILE,
                "-c", "copy",
                "-f", "flv",
                target
            ]
            
            # Hide output to keep terminal clean, but allow errors
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            processes.append(p)
            
        print("[SIMULATOR] All 4 simulator streams running. Press Ctrl+C to terminate.")
        while True:
            time.sleep(1.0)
            
    except KeyboardInterrupt:
        print("\n[SIMULATOR] Terminating all streaming processes...")
    finally:
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=1.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("[SIMULATOR] All simulators stopped.")

if __name__ == "__main__":
    main()
