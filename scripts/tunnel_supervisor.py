"""
scripts/tunnel_supervisor.py — Supervises the Pinggy SSH reverse tunnel to keep it alive
"""
import os
import sys
import time
import socket
import subprocess
import threading
import json

# Tunnel command
CMD = ["ssh", "-4", "-p", "443", "-R", "0:127.0.0.1:1935", "tcp@free.pinggy.io", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "outputs", "perf")
LOG_PATH = os.path.join(LOG_DIR, "tunnel_status.jsonl")

os.makedirs(LOG_DIR, exist_ok=True)

class TunnelSupervisor:
    def __init__(self):
        self.reconnects = 0
        self.uptime_start = time.time()
        self.running = True
        self.tunnel_proc = None
        self.mediamtx_reachable = False

    def check_mediamtx(self) -> bool:
        """Check if MediaMTX RTSP port is locally listening."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", 8554))
                return True
        except Exception:
            return False

    def log_status(self):
        """Append status log to jsonl."""
        status = {
            "timestamp": time.time(),
            "uptime_s": time.time() - self.uptime_start,
            "reconnect_count": self.reconnects,
            "tunnel_active": self.tunnel_proc is not None and self.tunnel_proc.poll() is None,
            "mediamtx_online": self.mediamtx_reachable
        }
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(status) + "\n")
        except Exception:
            pass

    def supervisor_loop(self):
        backoff = 1.0
        max_backoff = 30.0
        
        print("[SUPERVISOR] Starting tunnel supervisor process...")
        while self.running:
            # Check MediaMTX
            self.mediamtx_reachable = self.check_mediamtx()
            if not self.mediamtx_reachable:
                print("[SUPERVISOR] Warning: MediaMTX is offline locally (RTSP port 8554 unreachable).")

            print(f"[SUPERVISOR] Launching Pinggy SSH Tunnel (Attempt {self.reconnects + 1})...")
            try:
                # We pipe output to see connection details/URL
                self.tunnel_proc = subprocess.Popen(
                    CMD,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                
                # Start a thread to read and print tunnel stdout/stderr
                def read_output(proc):
                    for line in iter(proc.stdout.readline, ''):
                        if not self.running:
                            break
                        # Strip and display pinggy output (contains the public URL)
                        cleaned = line.strip()
                        if cleaned:
                            print(f"[TUNNEL] {cleaned}")
                
                threading.Thread(target=read_output, args=(self.tunnel_proc,), daemon=True).start()

                # Reset backoff on successful launch
                backoff = 1.0
                
                # Monitor process execution
                while self.running:
                    ret = self.tunnel_proc.poll()
                    if ret is not None:
                        print(f"[SUPERVISOR] Tunnel process exited with code {ret}")
                        break
                    time.sleep(2.0)
                    self.log_status()
                    
            except Exception as e:
                print(f"[SUPERVISOR] Tunnel launch failed: {e}")

            if not self.running:
                break
                
            self.reconnects += 1
            print(f"[SUPERVISOR] Reconnecting tunnel in {backoff:.1f}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    def stop(self):
        self.running = False
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
                self.tunnel_proc.wait(timeout=2)
            except Exception:
                try:
                    self.tunnel_proc.kill()
                except Exception:
                    pass
        print("[SUPERVISOR] Supervisor stopped.")

if __name__ == "__main__":
    sv = TunnelSupervisor()
    try:
        sv.supervisor_loop()
    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Terminating...")
        sv.stop()
