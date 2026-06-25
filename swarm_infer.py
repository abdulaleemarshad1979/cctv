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
print(f"[SWARM-INFER] Device: {device}  |  Drones: {SWARM_DRONE_COUNT}")

def _load_model():
    if not os.path.exists(config.WEIGHTS_PATH):
        raise FileNotFoundError(f"Model not found: {config.WEIGHTS_PATH}")
    ckpt = torch.load(config.WEIGHTS_PATH, map_location=device)
    if isinstance(ckpt, dict):
        for k in ("state_dict", "model_state_dict", "model", "ema"):
            if k in ckpt and isinstance(ckpt[k], dict):
                ckpt = ckpt[k]; break
    sd = {k.replace("module.", "").replace("model.", ""): v
          for k, v in ckpt.items() if isinstance(v, torch.Tensor)}
    m = vgg19()
    try:   m.load_state_dict(sd, strict=True)
    except RuntimeError: m.load_state_dict(sd, strict=False)
    m.to(device).eval()
    print("[SWARM-INFER] Model loaded.")
    return m

model = _load_model()

# ── Start swarm ───────────────────────────────────────────────────────
swarm = SwarmManager(sources=DRONE_SOURCES[:SWARM_DRONE_COUNT], model=model)
swarm.start()

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        state = swarm.get_unified_state()
        body = json.dumps(state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass  # silence access logs

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", 8080), StatusHandler).serve_forever(),
    daemon=True,
    name="HTTPStatusAPI"
).start()
print("[SWARM-INFER] HTTP status API server running on http://0.0.0.0:8080")

# ── Display window ────────────────────────────────────────────────────
MOSAIC_W = config.DISPLAY_WIDTH
MOSAIC_H = config.DISPLAY_HEIGHT

cv2.namedWindow("Pushkaralu 2027 — Swarm Command", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Pushkaralu 2027 — Swarm Command", MOSAIC_W, MOSAIC_H)

focus_drone = -1   # -1 = mosaic; 0-3 = single drone

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
                    disp = heatmap_generator.apply_heatmap(disp, ds.dmap_np, alpha=config.HEATMAP_ALPHA)
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
        bar_h   = 32
        cv2.rectangle(disp, (0, 0), (w2, bar_h), (0, 0, 0), -1)

        x = 5
        for i, ds in enumerate(swarm.states):
            with ds.lock:
                name  = ds.name
                zone  = ds.comp_zone
                color = ds.comp_color
                online = ds.online

            status = zone if online else "OFFLINE"
            s_color = color if online else (80, 80, 80)
            txt = f"D{i+1}:{name[:6]}[{status}]  "
            cv2.putText(disp, txt, (x, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, s_color, 1, cv2.LINE_AA)
            (tw, _), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            x += tw + 5

        # Dispatch GPS alerts
        all_alerts = []
        for ds in swarm.states:
            with ds.lock:
                all_alerts.extend(ds.gps_alerts)
        if all_alerts:
            geo_alert.dispatch(all_alerts)

    cv2.imshow("Pushkaralu 2027 — Swarm Command", disp)
    key = cv2.waitKey(16) & 0xFF   # ~60 fps display refresh

    if   key == ord('q'):         swarm.stop(); break
    elif key == ord('f'):         focus_drone = -1
    elif key == ord('1'):         focus_drone = 0
    elif key == ord('2') and SWARM_DRONE_COUNT >= 2: focus_drone = 1
    elif key == ord('3') and SWARM_DRONE_COUNT >= 3: focus_drone = 2
    elif key == ord('4') and SWARM_DRONE_COUNT >= 4: focus_drone = 3

cv2.destroyAllWindows()
print("[SWARM-INFER] Done.")
