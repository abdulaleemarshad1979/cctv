"""
src/perf_metrics.py — Performance & Telemetry metrics for Pushkaralu Drone crowd monitor
"""
import os
import csv
import time
from threading import Lock

class PerfMetricsCollector:
    def __init__(self, output_dir=None):
        if output_dir is None:
            # Locate relative to project root
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.output_dir = os.path.join(base_dir, "outputs", "perf")
        else:
            self.output_dir = output_dir

        os.makedirs(self.output_dir, exist_ok=True)
        self.csv_path = os.path.join(self.output_dir, "metrics.csv")
        self.lock = Lock()
        
        # Initialize CSV headers if the file does not exist
        if not os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "timestamp", "drone_id", "drone_name",
                        "capture_to_display_latency_s", "inference_wall_time_s",
                        "reconnect_count", "drop_rate_pct", "fps"
                    ])
            except Exception as e:
                print(f"[PERF-METRICS] Warning: Could not initialize CSV: {e}")
                
        # In-memory history for printing rolling averages
        self.history = {} # drone_id -> list of records

    def log_frame(self, drone_id, drone_name, cap_to_disp_s, infer_time_s, reconnect_count, drop_rate_pct, fps):
        now = time.time()
        record = {
            "timestamp": now,
            "drone_id": drone_id,
            "drone_name": drone_name,
            "capture_to_display_latency_s": cap_to_disp_s,
            "inference_wall_time_s": infer_time_s,
            "reconnect_count": reconnect_count,
            "drop_rate_pct": drop_rate_pct,
            "fps": fps
        }
        with self.lock:
            # Write to CSV
            try:
                with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        now, drone_id, drone_name,
                        f"{cap_to_disp_s:.6f}" if cap_to_disp_s is not None else "",
                        f"{infer_time_s:.6f}" if infer_time_s is not None else "",
                        reconnect_count,
                        f"{drop_rate_pct:.2f}" if drop_rate_pct is not None else "",
                        f"{fps:.2f}" if fps is not None else ""
                    ])
            except Exception as e:
                pass
                
            if drone_id not in self.history:
                self.history[drone_id] = []
            self.history[drone_id].append(record)
            
            # Keep history bounded (e.g. 500 entries per drone)
            if len(self.history[drone_id]) > 500:
                self.history[drone_id].pop(0)

    def print_summary(self):
        with self.lock:
            if not self.history:
                print("[PERF-METRICS] No metrics recorded in this period.")
                return
            
            print("\n" + "="*80)
            print(" SYSTEM PERFORMANCE TELEMETRY SUMMARY (Last 10s)")
            print("="*80)
            now = time.time()
            for drone_id, records in sorted(self.history.items()):
                recent = [r for r in records if now - r["timestamp"] <= 10.0]
                if not recent:
                    recent = records[-5:] if records else []
                if not recent:
                    continue
                
                name = recent[0]["drone_name"]
                latencies = [r["capture_to_display_latency_s"] for r in recent if r["capture_to_display_latency_s"] is not None]
                inf_times = [r["inference_wall_time_s"] for r in recent if r["inference_wall_time_s"] is not None]
                rec_counts = [r["reconnect_count"] for r in recent]
                drop_rates = [r["drop_rate_pct"] for r in recent if r["drop_rate_pct"] is not None]
                fps_vals = [r["fps"] for r in recent if r["fps"] is not None]
                
                avg_lat = sum(latencies)/len(latencies) if latencies else 0.0
                avg_inf = sum(inf_times)/len(inf_times) if inf_times else 0.0
                max_rec = max(rec_counts) if rec_counts else 0
                avg_drop = sum(drop_rates)/len(drop_rates) if drop_rates else 0.0
                avg_fps = sum(fps_vals)/len(fps_vals) if fps_vals else 0.0
                
                print(f" Drone {drone_id+1} ({name}):")
                print(f"   Avg Capture-to-Display Latency: {avg_lat*1000:.1f} ms")
                print(f"   Avg Inference Wall-time:        {avg_inf*1000:.1f} ms")
                print(f"   Avg Stream Processing Rate:     {avg_fps:.1f} FPS")
                print(f"   Avg Frame Drop Rate:            {avg_drop:.2f}%")
                print(f"   Max Reconnects:                 {max_rec}")
            print("="*80 + "\n")

# Global singleton
global_collector = PerfMetricsCollector()

def log_drone_metrics(drone_id, drone_name, cap_to_disp_s, infer_time_s, reconnect_count, drop_rate_pct, fps):
    global_collector.log_frame(drone_id, drone_name, cap_to_disp_s, infer_time_s, reconnect_count, drop_rate_pct, fps)

def print_metrics_summary():
    global_collector.print_summary()
