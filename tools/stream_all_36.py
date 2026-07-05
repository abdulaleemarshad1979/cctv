import json
import os
import subprocess
import time
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMERAS_JSON = os.path.join(BASE_DIR, "backend", "cameras.json")
VIDEO_FILE = os.path.join(BASE_DIR, "Videos", "K.mp4")

def main():
    print("[STREAMER] Starting 36-stream simulator...")
    if not os.path.exists(VIDEO_FILE):
        print(f"[STREAMER] Error: Video file not found at {VIDEO_FILE}")
        sys.exit(1)

    if not os.path.exists(CAMERAS_JSON):
        print(f"[STREAMER] Error: cameras.json not found at {CAMERAS_JSON}")
        sys.exit(1)

    with open(CAMERAS_JSON, "r") as f:
        cameras = json.load(f)

    processes = []
    try:
        for idx, cam in enumerate(cameras):
            stream_path = cam["stream_path"]
            # Extract the raw name after live/
            raw_path = stream_path.replace("live/", "")
            target = f"rtmp://127.0.0.1:1935/live/{raw_path}?user=operator&pass=pushkar2026"
            
            print(f"[STREAMER] [{idx+1}/36] Launching stream for {cam['name']} to: {target}")
            
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
            
            # Hide output to keep terminal clean
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            processes.append(p)
            time.sleep(0.1) # stagger launch slightly

        print("\n[STREAMER] All 36 simulator streams running. Press Ctrl+C to terminate.")
        while True:
            time.sleep(1.0)
            
    except KeyboardInterrupt:
        print("\n[STREAMER] Terminating all streaming processes...")
    finally:
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=0.5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        print("[STREAMER] All simulator streams stopped.")

if __name__ == "__main__":
    main()
