"""
swarm_infer.py  —  Pushkaralu 2027 Swarm Entry Point
=====================================================
Replaces infer.py when running 4 drones simultaneously.
Displays a 2x2 mosaic of all drone feeds with full analysis overlay.

Run:
    python swarm_infer.py

Keys:
    q = quit
    h = toggle heatmap
    f = toggle fullscreen mosaic / single-drone view
    1/2/3/4 = focus on that drone's feed
    s = toggle stampede panel
"""

import os, sys, cv2, time, threading
import numpy as np
import torch
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

import config
from src import overlay, geo_alert
from src.swarm_manager import SwarmManager, SWARM_DRONE_COUNT, DRONE_SOURCES, DRONE_NAMES

# ── Load model once, share across all drones ─────────────────────────
from dm_count.models import vgg19

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
if device.type == "cpu":
    # Limit CPU threads to prevent UI/capture starvation in swarm mode
    torch.set_num_threads(max(1, min(2, (os.cpu_count() or 4) // 2)))
    print(f"[SWARM-INFER] CPU Thread count set to {torch.get_num_threads()} to prevent core saturation.")
print(f"[SWARM-INFER] Device: {device}  |  Drones: {SWARM_DRONE_COUNT}")

class ONNXModelWrapper:
    def __init__(self, onnx_path):
        import onnxruntime as ort
        providers = ['CPUExecutionProvider']
        if torch.cuda.is_available():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        self.session = ort.InferenceSession(onnx_path, providers=providers)
        print(f"[ONNX] Model loaded on providers: {self.session.get_providers()}")

    def __call__(self, tensor):
        input_data = tensor.cpu().numpy()
        outputs = self.session.run(None, {'input': input_data})
        return torch.from_numpy(outputs[0]), torch.from_numpy(outputs[1])

    def to(self, device):
        return self

    def eval(self):
        return self


def _load_model():
    if getattr(config, "INFERENCE_BACKEND", "torch") == "onnx":
        onnx_path = config.WEIGHTS_PATH.replace(".pth", ".onnx")
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"ONNX model not found at {onnx_path}. Run tools/export_onnx.py first.")
        print(f"[INFO] Loading ONNX model from {onnx_path}...")
        return ONNXModelWrapper(onnx_path)

    if not os.path.exists(config.WEIGHTS_PATH):
        raise FileNotFoundError(f"Model not found: {config.WEIGHTS_PATH}")
    ckpt = torch.load(config.WEIGHTS_PATH, map_location=device)
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "ema"):
            if k in ckpt and isinstance(ckpt[k], dict):
                ckpt = ckpt[k]; break
    sd = {k.replace("module.", "").replace("model.", ""): v
          for k, v in ckpt.items() if isinstance(v, torch.Tensor)}
    m = vgg19(pretrained=False)
    try:   m.load_state_dict(sd, strict=True)
    except RuntimeError: m.load_state_dict(sd, strict=False)
    m.to(device).eval()
    print("[SWARM-INFER] Model loaded.")
    return m

model = None
model_loaded = threading.Event()

def async_load_model():
    global model
    try:
        model = _load_model()
        swarm.model = model
        model_loaded.set()
    except Exception as e:
        print(f"[SWARM-INFER] Failed to load model: {e}")

# ── Start swarm ───────────────────────────────────────────────────────
swarm_sources = DRONE_SOURCES[:SWARM_DRONE_COUNT]
use_videos = os.environ.get("USE_VIDEOS", "").lower() in ("1", "true", "yes") or "--videos" in sys.argv

if use_videos:
    video_files = ["Kumbh.mp4", "mecca.mp4", "stadium.mp4", "concert.mp4", "Crowd.mp4"]
    local_sources = []
    for f in video_files:
        p = os.path.join("Videos", f)
        if os.path.exists(p):
            local_sources.append(p)
    if local_sources:
        swarm_sources = [local_sources[i % len(local_sources)] for i in range(SWARM_DRONE_COUNT)]
        print(f"[SWARM-INFER] Test mode: Using local videos: {swarm_sources}")
    else:
        print("[SWARM-INFER] Warning: No test videos found in Videos/ directory.")

swarm = SwarmManager(sources=swarm_sources, model=None)
swarm.start()

# Load model in parallel
threading.Thread(target=async_load_model, daemon=True, name="SwarmModelLoader").start()

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            report = {
                "timestamp": time.time(),
                "worst_zone": swarm.get_unified_state()["worst_zone"],
                "drones": []
            }
            for i in range(swarm.n):
                ds = swarm.states[i]
                with ds.lock:
                    report["drones"].append({
                        "id": i,
                        "name": ds.name,
                        "online": ds.online,
                        "uptime_s": ds.uptime_s,
                        "drop_rate_pct": ds.drop_rate_pct,
                        "reconnect_count": ds.reconnect_count,
                        "live_fps": ds.live_fps,
                        "infer_time_ms": ds.infer_time * 1000.0,
                        "density_score": ds.density_score,
                        "comp_zone": ds.comp_zone
                    })
            body = json.dumps(report).encode()
        else:
            state = swarm.get_unified_state()
            body = json.dumps(state).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass  # silence access logs

def start_http_server():
    for port in range(8080, 8090):
        try:
            server = HTTPServer(("0.0.0.0", port), StatusHandler)
            print(f"[SWARM-INFER] HTTP status API server running on http://localhost:{port}")
            server.serve_forever()
            return
        except OSError:
            continue
    print("[SWARM-INFER] Warning: Could not start HTTP status API server (ports 8080-8089 in use).")

threading.Thread(
    target=start_http_server,
    daemon=True,
    name="HTTPStatusAPI"
).start()

# ── Display window ────────────────────────────────────────────────────
MOSAIC_W = config.DISPLAY_WIDTH
MOSAIC_H = config.DISPLAY_HEIGHT

cv2.namedWindow("Pushkaralu 2027 | Swarm Command Center", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Pushkaralu 2027 | Swarm Command Center", MOSAIC_W, MOSAIC_H)

focus_drone = -1   # -1 = mosaic; 0-3 = single drone
last_metrics_log_times = [0.0] * SWARM_DRONE_COUNT

print("[SWARM-INFER] Keys: q=quit  1-4=focus drone  f=mosaic  h=heatmap  s=stampede")

while True:
    # Build display
    if focus_drone < 0:
        disp = swarm.get_mosaic()
        disp = cv2.resize(disp, (MOSAIC_W, MOSAIC_H), interpolation=cv2.INTER_AREA)
    else:
        ds = swarm.states[focus_drone]
        with ds.lock:
            if ds.frame_bgr is not None:
                disp = cv2.resize(ds.frame_bgr.copy(),
                                  (MOSAIC_W, MOSAIC_H), interpolation=cv2.INTER_AREA)
                if ds.dmap_np is not None:
                    from src import heatmap_generator
                    disp = heatmap_generator.apply_heatmap(disp, ds.dmap_np, alpha=config.HEATMAP_ALPHA, state=ds.heatmap_state)
                overlay.draw_top_banner(disp, ds.comp_zone, ds.comp_color, ds.pressure_smooth)
                overlay.draw_grid_3x3(disp,
                                      zone_scores=ds.zone_scores,
                                      zone_motions=ds.speed_grid,
                                      trend_matrix=ds.hs_result.get("trend_matrix"),
                                      opposing_danger=ds.opp_result.get("danger_grid"),
                                      panel_visible=True)
                overlay.draw_stampede_panel(disp, ds.sp_result)
                alerts = ds.get_alerts()
                
                # Track alert first shown times
                now = time.monotonic()
                for a in list(ds.alert_first_shown.keys()):
                    if a not in alerts:
                        del ds.alert_first_shown[a]
                for a in alerts:
                    if a not in ds.alert_first_shown:
                        ds.alert_first_shown[a] = now

                overlay.draw_alert_ticker(disp, alerts, ds.alert_first_shown, now)
            else:
                disp = np.zeros((MOSAIC_H, MOSAIC_W, 3), dtype=np.uint8)
                cv2.putText(disp, f"Drone {focus_drone+1}: {DRONE_NAMES[focus_drone]} — NO SIGNAL",
                            (20, MOSAIC_H//2), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,255), 2)

    # Draw swarm status bar across top of mosaic view
    if focus_drone < 0:
        unified = swarm.get_unified_state()
        h2, w2  = disp.shape[:2]
        bar_h   = 44
        
        # Draw semi-transparent header overlay
        overlay_bar = disp[0:bar_h, 0:w2].copy()
        cv2.rectangle(overlay_bar, (0, 0), (w2, bar_h), (18, 18, 22), -1)
        disp[0:bar_h, 0:w2] = cv2.addWeighted(disp[0:bar_h, 0:w2], 0.1, overlay_bar, 0.9, 0)
        
        # Subtle bottom border line
        cv2.line(disp, (0, bar_h-1), (w2, bar_h-1), (45, 45, 52), 1, cv2.LINE_AA)

        x = 15
        for i, ds in enumerate(swarm.states):
            with ds.lock:
                name = ds.name
                zone = ds.comp_zone
                color = ds.comp_color
                online = ds.online
                density = ds.density_score

            # Status indicators
            dot_color = color if online else (90, 90, 90)
            status_txt = zone if online else "OFFLINE"
            
            # Status dot
            cv2.circle(disp, (x + 8, 22), 5, dot_color, -1, cv2.LINE_AA)
            
            if online:
                txt = f"D{i+1}: {name} ({int(density)} pax) | {status_txt}"
            else:
                txt = f"D{i+1}: {name} | {status_txt}"
                
            cv2.putText(disp, txt, (x + 22, 27),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 235) if online else (130, 130, 135), 1, cv2.LINE_AA)
                        
            (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
            
            # Divider line
            divider_x = x + 22 + tw + 15
            if i < len(swarm.states) - 1 and divider_x < w2 - 180:
                cv2.line(disp, (divider_x, 12), (divider_x, 32), (45, 45, 52), 1, cv2.LINE_AA)
            
            x = divider_x + 15
            
        # Unified system status on the far right
        system_status = unified.get("worst_zone", "SAFE")
        system_color = (0, 255, 0) if system_status == "SAFE" else (0, 0, 255) if system_status in ("HIGH", "CRITICAL") else (0, 255, 255)
        sys_txt = f"SYSTEM: {system_status}"
        (stw, _), _ = cv2.getTextSize(sys_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(disp, sys_txt, (w2 - stw - 15, 27),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, system_color, 1, cv2.LINE_AA)

    cv2.imshow("Pushkaralu 2027 | Swarm Command Center", disp)
    key = cv2.waitKey(16) & 0xFF   # ~60 fps display refresh

    if   key == ord('q'):         swarm.stop(); break
    elif key == ord('f'):         focus_drone = -1
    elif key == ord('1'):         focus_drone = 0
    elif key == ord('2') and SWARM_DRONE_COUNT >= 2: focus_drone = 1
    elif key == ord('3') and SWARM_DRONE_COUNT >= 3: focus_drone = 2
    elif key == ord('4') and SWARM_DRONE_COUNT >= 4: focus_drone = 3

cv2.destroyAllWindows()
print("[SWARM-INFER] Done.")
