"""
swarm_manager.py  —  Pushkaralu 2027 Unified Drone Swarm Manager
=================================================================
Manages 4 parallel drone feeds, runs inference on each, fuses results
into a unified command view, and broadcasts to the HTML dashboard.

Usage
-----
  # Single-entry point (replaces infer.py when SWARM_MODE=True):
  python swarm_manager.py

  # Or import in your existing infer.py:
  from swarm_manager import SwarmManager
  mgr = SwarmManager()
  mgr.start()
"""

import os
import sys
import cv2
import time
import queue
import threading
import numpy as np

# ── Import your existing modules ─────────────────────────────────────
import config
from . import risk_engine
from . import zone_monitor
from . import optical_flow
from . import heatmap_generator
from . import density_filter
from . import geo_alert

from .drone_stream     import DroneStreamHandler, resolve_source
from .history_buffer   import HistoryBuffer
from .hotspot_tracker  import HotspotTracker
from .stampede_predictor import StampedePredictor
from .opposing_flow_detector import OpposingFlowDetector

# ── Swarm config (add these keys to config.py) ───────────────────────
SWARM_DRONE_COUNT = getattr(config, 'SWARM_DRONE_COUNT', 4)

DRONE_SOURCES = getattr(config, 'DRONE_SOURCES', [
    'rtsp://localhost:8554/drone1',
    'rtsp://localhost:8554/drone2',
    'rtsp://localhost:8554/drone3',
    'rtsp://localhost:8554/drone4',
])

DRONE_NAMES = getattr(config, 'DRONE_NAMES', [
    'North Ghat', 'Main Ghat', 'South Ghat', 'Mobile'
])

DRONE_ALTITUDES_M = getattr(config, 'DRONE_ALTITUDES_M', [30.0, 25.0, 30.0, 20.0])

# GPS bounding boxes: [lat_min, lon_min, lat_max, lon_max]
# UPDATE THESE to actual Rajahmundry ghat GPS coordinates!
DRONE_GPS_BOUNDS = getattr(config, 'DRONE_GPS_BOUNDS', [
    [16.9820, 81.7355, 16.9850, 81.7380],   # Drone 1 North
    [16.9800, 81.7375, 16.9825, 81.7400],   # Drone 2 Main
    [16.9775, 81.7390, 16.9805, 81.7415],   # Drone 3 South
    [0.0, 0.0, 0.0, 0.0],                   # Drone 4 Dynamic (update at runtime)
])

# Safe headcount per 3x3 cell per drone (tune from field measurements)
ZONE_CAPACITY = getattr(config, 'ZONE_CAPACITY', [
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 1
    [[400, 600, 400], [450, 700, 450], [400, 600, 400]],   # Drone 2
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 3
    [[300, 400, 300], [350, 500, 350], [300, 400, 300]],   # Drone 4
])

# Baseline pixels/meter at ground level (calibrate with known object)
BASELINE_PX_PER_M  = getattr(config, 'BASELINE_PX_PER_M', 50.0)
ENABLE_ALT_CORRECT = getattr(config, 'ENABLE_ALTITUDE_CORRECTION', True)

LOW_BATTERY_PCT    = 25    # Alert threshold
HEALTH_REPORT_INTERVAL_S = 10.0


# ══════════════════════════════════════════════════════════════════════
#  ALTITUDE CORRECTION
# ══════════════════════════════════════════════════════════════════════

def altitude_scale_correction(density_raw: float, altitude_m: float,
                               hfov_deg: float = 84.0,
                               frame_width_px: int = config.INFER_WIDTH) -> float:
    """
    Correct DM-Count density estimate for drone altitude.
    DM-Count was trained on ~ground-level data; at 30m the crowd
    appears smaller in pixels, causing systematic underestimation.

    Returns corrected density (higher than raw when altitude is large).
    """
    if not ENABLE_ALT_CORRECT or altitude_m < 5.0:
        return density_raw
    import math
    ground_width_m = 2.0 * altitude_m * math.tan(math.radians(hfov_deg / 2.0))
    px_per_m = frame_width_px / max(ground_width_m, 0.1)
    scale_factor = (px_per_m / BASELINE_PX_PER_M) ** 2
    return density_raw / max(scale_factor, 0.05)


# ══════════════════════════════════════════════════════════════════════
#  GPS UTILITIES
# ══════════════════════════════════════════════════════════════════════

def zone_cell_to_gps(drone_id: int, row: int, col: int) -> tuple[float, float]:
    """Map a 3x3 grid cell to its GPS centroid for this drone."""
    lat_min, lon_min, lat_max, lon_max = DRONE_GPS_BOUNDS[drone_id]
    if lat_min == lat_max == 0.0:
        return (0.0, 0.0)  # Dynamic drone — GPS unknown
    lat = lat_max - (row + 0.5) / 3.0 * (lat_max - lat_min)
    lon = lon_min + (col + 0.5) / 3.0 * (lon_max - lon_min)
    return round(lat, 6), round(lon, 6)


def build_gps_alert(drone_id: int, row: int, col: int,
                    risk_zone: str, density: float,
                    occupancy_pct: float = 0.0) -> dict:
    """Generate a GPS-tagged alert dict for this zone cell."""
    lat, lon = zone_cell_to_gps(drone_id, row, col)
    ghat_name  = DRONE_NAMES[drone_id]
    cell_label = f"{'ABC'[row]}{col + 1}"
    gps_str    = f"{lat}N {lon}E" if lat != 0.0 else "GPS pending"
    occ_str    = f" ({int(occupancy_pct)}% capacity)" if occupancy_pct > 0 else ""
    return {
        "drone_id":     drone_id,
        "ghat":         ghat_name,
        "cell":         cell_label,
        "zone":         risk_zone,
        "gps_lat":      lat,
        "gps_lon":      lon,
        "density":      round(density),
        "occupancy_pct": round(occupancy_pct, 1),
        "message": (
            f"[{risk_zone}] {ghat_name} Zone {cell_label}{occ_str} — "
            f"GPS: {gps_str} — estimated {int(density)} people"
        ),
    }


# ══════════════════════════════════════════════════════════════════════
#  PER-DRONE STATE
# ══════════════════════════════════════════════════════════════════════

class DroneState:
    """All live state for one drone feed."""

    def __init__(self, drone_id: int):
        self.drone_id    = drone_id
        self.name        = DRONE_NAMES[drone_id]
        self.altitude_m  = DRONE_ALTITUDES_M[drone_id]
        self.lock        = threading.RLock()
        self.alert_first_shown = {}  # tracks when each alert was first shown

        # Frame
        self.frame_bgr   = None
        self.cap_ts      = 0.0
        self.frame_q     = queue.Queue(maxsize=1)
        self.is_live     = False

        # Inference outputs
        self.dmap_np        = None
        self.density_score  = 0.0
        self.peak_density   = 0.0
        self.hotspot_ratio  = 0.0
        self.risk_score     = 0.0
        self.zone_val       = "SAFE"
        self.zone_color     = (0, 255, 0)
        self.zone_scores    = np.zeros((3, 3))
        self.occupancy_pcts = np.zeros((3, 3))  # NEW: % of capacity

        # Motion
        self.speed_grid     = np.zeros((3, 3))
        self.motion_speed   = 0.0
        self.turbulence     = 0.0

        # Composite risk
        self.comp_risk      = 0.0
        self.comp_zone      = "SAFE"
        self.comp_color     = (0, 255, 0)
        self.pressure_smooth = 0.0

        # GPS alerts (list of dicts)
        self.gps_alerts     = []

        # Health
        self.online          = False
        self.uptime_s        = 0.0
        self.drop_rate_pct   = 0.0
        self.reconnect_count = 0
        self.last_frame_age_s = 0.0
        self.infer_time      = 0.0
        self.live_fps        = 0.0
        self.heatmap_state   = {}

        # Analysis objects
        self.history     = HistoryBuffer(max_seconds=30.0, fps_estimate=5.0)
        self.hs_tracker  = HotspotTracker(self.history)
        self.opp_det     = OpposingFlowDetector()
        self.stamp_pred  = StampedePredictor(self.history)
        self.motion_anal = optical_flow.CrowdMotionAnalyzer()
        self.risk_track  = risk_engine.RiskEngineTracker()
        self.zone_mon    = zone_monitor.ZoneMonitor()

        # Hotspot / stampede results
        self.hs_result   = {"trend_matrix": None, "alert_text": "", "expanding": False}
        self.sp_result   = {"smooth_prob": 0.0, "label": "SAFE",
                            "label_color": (0, 255, 0), "alert_text": "", "terms": {}}
        self.opp_result  = {"danger_grid": None, "max_score": 0.0,
                            "any_dangerous": False, "alert_text": ""}

    def get_alerts(self) -> list[str]:
        """Return list of all active alert strings for this drone."""
        return [a for a in [
            self.hs_result.get("alert_text", ""),
            self.opp_result.get("alert_text", ""),
            self.sp_result.get("alert_text", ""),
        ] if a]

    def summary_dict(self) -> dict:
        """Serializable snapshot for WebSocket push."""
        with self.lock:
            return {
                "drone_id":      self.drone_id,
                "name":          self.name,
                "online":        self.online,
                "zone":          self.comp_zone,
                "pressure":      round(self.pressure_smooth, 1),
                "stampede_prob": round(self.sp_result.get("smooth_prob", 0.0) * 100, 1),
                "stamp_label":   self.sp_result.get("label", "SAFE"),
                "uptime_s":      round(self.uptime_s, 0),
                "drop_rate":     round(self.drop_rate_pct, 1),
                "gps_alerts":    self.gps_alerts,
                "alerts":        self.get_alerts(),
            }


# ══════════════════════════════════════════════════════════════════════
#  SWARM MANAGER
# ══════════════════════════════════════════════════════════════════════

class SwarmManager:
    """
    Manages N drone feeds in parallel.
    Each drone runs its own producer + inference threads.
    A fusion thread merges results into a unified state.
    """

    def __init__(self, sources=None, model=None):
        self.sources    = sources or DRONE_SOURCES[:SWARM_DRONE_COUNT]
        self.n          = len(self.sources)
        self.model      = model          # Pass your loaded vgg19 model
        self.states     = [DroneState(i) for i in range(self.n)]
        self._stop      = threading.Event()
        self._threads   = []
        self._model_lock = threading.Semaphore(1)  # one inference at a time
        self.batch_queue = queue.Queue()

        # Unified state (merged across all drones)
        self._unified_lock = threading.Lock()
        self._unified = {
            "worst_zone":      "SAFE",
            "worst_pressure":  0.0,
            "worst_drone_id":  -1,
            "critical_alerts": [],
            "drone_summaries": [],
        }

    def start(self):
        self.thread_specs = {}

        if getattr(config, "SWARM_BATCH_INFERENCE", True):
            d_t = threading.Thread(target=self._inference_dispatcher,
                                   daemon=True,
                                   name="Swarm-Inference-Dispatcher")
            self._threads.append(d_t)
            d_t.start()
            self.thread_specs["Swarm-Inference-Dispatcher"] = {
                "thread": d_t,
                "target": self._inference_dispatcher,
                "args": ()
            }
            print("[SWARM] Started batch inference dispatcher thread.")

        for i, src in enumerate(self.sources):
            # Clear history on intentional startup
            self.states[i].history.clear()

            p_t = threading.Thread(target=self._producer,
                                   args=(i, src), daemon=True,
                                   name=f"Swarm-D{i+1}-Producer")
            i_t = threading.Thread(target=self._inference_worker,
                                   args=(i,), daemon=True,
                                   name=f"Swarm-D{i+1}-Inference")
            h_t = threading.Thread(target=self._health_monitor,
                                   args=(i,), daemon=True,
                                   name=f"Swarm-D{i+1}-Health")
            
            p_t.start()
            i_t.start()
            h_t.start()

            for t in (p_t, i_t, h_t):
                self._threads.append(t)

            self.thread_specs[f"Swarm-D{i+1}-Producer"] = {
                "thread": p_t,
                "target": self._producer,
                "args": (i, src)
            }
            self.thread_specs[f"Swarm-D{i+1}-Inference"] = {
                "thread": i_t,
                "target": self._inference_worker,
                "args": (i,)
            }
            self.thread_specs[f"Swarm-D{i+1}-Health"] = {
                "thread": h_t,
                "target": self._health_monitor,
                "args": (i,)
            }

        # Start watchdog
        w_t = threading.Thread(target=self._watchdog_loop,
                               daemon=True,
                               name="Swarm-Watchdog")
        self._threads.append(w_t)
        w_t.start()
        print(f"[SWARM] Started {self.n} drone pipelines under Watchdog supervision.")

    def stop(self):
        self._stop.set()
        print("[SWARM] Stopping all drone pipelines...")

    # ── Producer ───────────────────────────────────────────────────

    def _producer(self, idx: int, src: str):
        ds = self.states[idx]

        # Resolve preset names locally, ignoring global DRONE/CCTV_SOURCE env overrides in swarm mode
        from .presets import DRONE_DB
        resolved_src = src
        if isinstance(src, str) and src in DRONE_DB:
            resolved_src, _ = DRONE_DB[src]

        start_t   = time.monotonic()

        while not self._stop.is_set():
            handler = DroneStreamHandler(resolved_src)
            if not handler.is_opened():
                ds.online = False
                # Print once in a while or silently retry
                print(f"[SWARM] Drone {idx+1} ({ds.name}): Offline. Retrying connection to {resolved_src} in 3.0s...")
                # Avoid tight loop on quick failures
                time.sleep(3.0)
                continue

            print(f"[SWARM] Drone {idx+1} ({ds.name}): Connected to {resolved_src}")
            ds.online = True
            frame_no  = 0
            src_fps   = handler.get_fps()
            nft       = time.monotonic()
            with ds.lock:
                ds.is_live = handler.is_live

            while not self._stop.is_set():
                ok, frame = handler.read_frame()
                if not ok:
                    print(f"[SWARM] Drone {idx+1} stream ended/disconnected. Reconnecting...")
                    ds.online = False
                    break

                frame_no += 1
                ts = time.monotonic()

                with ds.lock:
                    ds.frame_bgr = frame
                    ds.cap_ts    = ts
                    ds.uptime_s  = ts - start_t
                    health       = handler.health_report()
                    ds.drop_rate_pct   = health["drop_rate_%"]
                    ds.reconnect_count = health["reconnects"]
                    ds.live_fps        = health["live_fps"]

                # Push to inference queue (drop old frame if full)
                if frame_no % getattr(config, 'INITIAL_INFERENCE_STRIDE', 12) == 0:
                    q = ds.frame_q
                    if q.full():
                        try: q.get_nowait()
                        except queue.Empty: pass
                    q.put_nowait((frame.copy(), ts))

                if not handler.is_live:
                    nft += 1.0 / src_fps
                    s = nft - time.monotonic()
                    if s > 0:
                        time.sleep(s)

            handler.release()
            ds.online = False
            if not self._stop.is_set():
                time.sleep(3.0)

    # ── Inference Worker ───────────────────────────────────────────

    def _inference_worker(self, idx: int):
        """Per-drone inference loop. Reuses main model if available."""
        import torch
        from PIL import Image
        from torchvision import transforms

        ds = self.states[idx]
        
        # Check if multi-GPU is available and model is not shared
        if torch.cuda.is_available():
            num_gpus = torch.cuda.device_count()
            if num_gpus > 1 and self.model is None:
                device = torch.device(f"cuda:{idx % num_gpus}")
            else:
                device = torch.device("cuda")
        else:
            device = torch.device("cpu")

        # Load model (if not shared — for multi-GPU setups)
        if self.model is not None:
            mdl = self.model
        elif torch.cuda.is_available() and torch.cuda.device_count() > 1:
            print(f"[SWARM] Drone {idx+1} loading own model copy on device {device}...")
            from dm_count.models import vgg19
            ckpt = torch.load(config.WEIGHTS_PATH, map_location=device)
            if isinstance(ckpt, dict):
                for k in ("state_dict", "model_state_dict", "model", "ema"):
                    if k in ckpt and isinstance(ckpt[k], dict):
                        ckpt = ckpt[k]; break
            sd = {k.replace("module.", "").replace("model.", ""): v
                  for k, v in ckpt.items() if isinstance(v, torch.Tensor)}
            mdl = vgg19()
            try:   mdl.load_state_dict(sd, strict=True)
            except RuntimeError: mdl.load_state_dict(sd, strict=False)
            mdl.to(device).eval()
            print(f"[SWARM] Drone {idx+1} model ready on {device}.")
        else:
            print(f"[SWARM] Drone {idx+1} waiting for shared model to load...")
            while self.model is None and not self._stop.is_set():
                time.sleep(0.1)
            mdl = self.model

        tfm = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std =[0.229, 0.224, 0.225]),
        ])

        while not self._stop.is_set():
            try:
                frame, cap_ts = ds.frame_q.get(timeout=0.5)
            except queue.Empty:
                continue

            # Preprocess
            t0 = time.perf_counter()
            if getattr(config, "CLEAN_INPUT_OVERLAYS", True):
                frame_for_infer = density_filter.suppress_broadcast_overlays(frame)
            else:
                frame_for_infer = frame
            small = cv2.resize(frame_for_infer, (config.INFER_WIDTH, config.INFER_HEIGHT),
                               interpolation=cv2.INTER_AREA)
            rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            tensor = tfm(Image.fromarray(rgb)).unsqueeze(0).to(device)

            # Infer
            dmap_np = None
            if getattr(config, "SWARM_BATCH_INFERENCE", True):
                done_event = threading.Event()
                result_container = {}
                self.batch_queue.put((idx, tensor, done_event, result_container))
                if done_event.wait(timeout=15.0) and "dmap_np" in result_container:
                    dmap_np = result_container["dmap_np"]
                    infer_time = time.perf_counter() - t0
                else:
                    print(f"[SWARM] Drone {idx+1} batch request timeout/failure, falling back to sequential.")

            if dmap_np is None:
                # Fallback to sequential
                if self.model is not None:
                    with self._model_lock:
                        with torch.inference_mode():
                            out = mdl(tensor)
                else:
                    with torch.inference_mode():
                        out = mdl(tensor)
                dmap_t  = out[0] if isinstance(out, (tuple, list)) else out
                dmap_np = dmap_t.squeeze().detach().float().cpu().numpy()
                infer_time = time.perf_counter() - t0
            
            dmap_np = density_filter.clean_density_map(
                dmap_np,
                source_frame_bgr=frame,
                speckle_ratio=getattr(config, "DENSITY_SPECKLE_RATIO", 0.015),
            )

            # ── Altitude correction ──────────────────────────────
            raw_sum = float(np.clip(dmap_np, 0, None).sum())
            corr_factor = 1.0
            with ds.lock:
                is_live_val = getattr(ds, 'is_live', False)
            if ENABLE_ALT_CORRECT and ds.altitude_m > 5.0 and is_live_val:
                import math
                hfov = getattr(config, 'DRONE_SENSOR_HFOV', 84.0)
                gw   = 2.0 * ds.altitude_m * math.tan(math.radians(hfov / 2.0))
                ppm  = config.INFER_WIDTH / max(gw, 0.1)
                corr_factor = (BASELINE_PX_PER_M / max(ppm, 0.01)) ** 2
                dmap_np_corr = dmap_np * corr_factor
            else:
                dmap_np_corr = dmap_np

            # ── Metrics ─────────────────────────────────────────
            ds_val, pd, hr, rs = risk_engine.compute_pressure_metrics(dmap_np_corr)
            zv, zc = risk_engine.get_risk_zone(rs,
                         config.SAFE_THRESHOLD,
                         config.WATCH_THRESHOLD,
                         config.HIGH_THRESHOLD)
            scores, _ = ds.zone_mon.analyze_zones(dmap_np_corr)

            # ── Occupancy % per cell ─────────────────────────────
            cap_grid = np.array(ZONE_CAPACITY[min(idx, len(ZONE_CAPACITY)-1)],
                                dtype=float)
            occ_pcts = np.clip(scores / np.maximum(cap_grid, 1), 0, 5) * 100.0

            # ── Motion ──────────────────────────────────────────
            disp = cv2.resize(frame, (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT),
                              interpolation=cv2.INTER_AREA)
            speed_grid, motion_speed, turbulence = ds.motion_anal.analyze_motion(disp)
            crowd_present = ds_val >= risk_engine.MIN_CROWD_DENSITY

            # Zero out motion in cells with no detected people (water / noise filter)
            if scores is not None:
                for r in range(3):
                    for c in range(3):
                        if scores[r, c] < 5.0:
                            speed_grid[r, c] = 0.0
            if not crowd_present:
                speed_grid[:] = 0.0
                motion_speed = 0.0
                turbulence = 0.0

            # ── Opposing flow ─────────────────────────────────
            last_flow = getattr(ds.motion_anal, 'last_flow', None)
            opp_res = (ds.opp_det.analyze(last_flow, zone_scores=scores)
                       if last_flow is not None and crowd_present
                       else {"danger_grid": None, "max_score": 0.0,
                             "any_dangerous": False, "alert_text": ""})

            # ── Composite risk ────────────────────────────────
            comp_risk = ds.risk_track.compute_composite_risk(
                ds_val, pd, hr, motion_speed, turbulence
            )
            comp_zone, comp_color = risk_engine.get_risk_zone(
                comp_risk,
                config.SAFE_THRESHOLD, config.WATCH_THRESHOLD, config.HIGH_THRESHOLD)

            if crowd_present:
                pressure_s = 0.85 * ds.pressure_smooth + 0.15 * (comp_risk * 100.0)
            else:
                pressure_s = 0.0
                ds.history.clear()

            # ── History & trackers ────────────────────────────
            hs_res = {"trend_matrix": None, "alert_text": "", "expanding": False}
            sp_res = {"smooth_prob": 0.0, "label": "SAFE",
                      "label_color": (0,255,0), "alert_text": "", "terms": {}}

            if scores is not None and crowd_present:
                ds.history.push(
                    timestamp=time.monotonic(),
                    density_score=ds_val, peak_density=pd,
                    hotspot_ratio=hr, motion_speed=motion_speed,
                    turbulence=turbulence, composite_risk=comp_risk,
                    zone_scores=scores, zone_motions=speed_grid,
                )
                hs_res = ds.hs_tracker.update(scores)
                sp_res = ds.stamp_pred.predict(
                    density_score=ds_val,
                    motion_speed=motion_speed,
                    turbulence=turbulence,
                    opposing_score=opp_res.get("max_score", 0.0),
                )

            # ── GPS alerts for HIGH/CRITICAL cells ───────────
            gps_alerts = []
            for r in range(3):
                for c in range(3):
                    cell_risk, _ = risk_engine.get_risk_zone(
                        scores[r, c] / max(cap_grid[r, c], 1),
                        0.25, 0.50, 0.75)
                    if cell_risk in ("HIGH", "CRITICAL"):
                        ga = build_gps_alert(
                            idx, r, c,
                            cell_risk,
                            float(scores[r, c]),
                            float(occ_pcts[r, c])
                        )
                        gps_alerts.append(ga)

            if gps_alerts:
                geo_alert.dispatch(gps_alerts)

            # ── Write back ────────────────────────────────────
            with ds.lock:
                ds.dmap_np        = dmap_np_corr.copy()
                ds.density_score  = ds_val
                ds.peak_density   = pd
                ds.hotspot_ratio  = hr
                ds.risk_score     = rs
                ds.zone_val       = zv
                ds.zone_color     = zc
                ds.zone_scores    = scores
                ds.occupancy_pcts = occ_pcts
                ds.speed_grid     = speed_grid
                ds.motion_speed   = motion_speed
                ds.turbulence     = turbulence
                ds.comp_risk      = comp_risk
                ds.comp_zone      = comp_zone
                ds.comp_color     = comp_color
                ds.pressure_smooth = pressure_s
                ds.hs_result      = hs_res
                ds.sp_result      = sp_res
                ds.opp_result     = opp_res
                ds.gps_alerts     = gps_alerts
                ds.infer_time     = infer_time

            from src.perf_metrics import log_drone_metrics
            log_drone_metrics(
                idx, ds.name, time.monotonic() - cap_ts, infer_time,
                ds.reconnect_count, ds.drop_rate_pct, ds.live_fps
            )

            self._update_unified()

    # ── Unified State Fusion ────────────────────────────────────────

    def _update_unified(self):
        """Merge all drone states into a single command-level summary."""
        zone_rank = {"SAFE": 0, "WATCH": 1, "HIGH": 2, "CRITICAL": 3}
        worst_rank = 0
        worst_id   = 0
        worst_pres = 0.0
        all_alerts = []
        summaries  = []

        for ds in self.states:
            with ds.lock:
                rank = zone_rank.get(ds.comp_zone, 0)
                if rank > worst_rank or (rank == worst_rank and ds.pressure_smooth > worst_pres):
                    worst_rank = rank
                    worst_id   = ds.drone_id
                    worst_pres = ds.pressure_smooth
                all_alerts.extend([g["message"] for g in ds.gps_alerts])
                summaries.append(ds.summary_dict())

        zone_names = {0: "SAFE", 1: "WATCH", 2: "HIGH", 3: "CRITICAL"}
        with self._unified_lock:
            self._unified = {
                "worst_zone":      zone_names[worst_rank],
                "worst_pressure":  round(worst_pres, 1),
                "worst_drone_id":  worst_id,
                "critical_alerts": all_alerts,
                "drone_summaries": summaries,
                "timestamp":       time.time(),
            }

    def get_unified_state(self) -> dict:
        with self._unified_lock:
            return dict(self._unified)

    # ── Health Monitor ──────────────────────────────────────────────

    def _health_monitor(self, idx: int):
        ds = self.states[idx]
        while not self._stop.is_set():
            time.sleep(HEALTH_REPORT_INTERVAL_S)
            with ds.lock:
                print(
                    f"[SWARM-D{idx+1}] {ds.name} | "
                    f"online={ds.online} | "
                    f"uptime={ds.uptime_s:.0f}s | "
                    f"drop={ds.drop_rate_pct:.1f}% | "
                    f"zone={ds.comp_zone} | "
                    f"press={ds.pressure_smooth:.0f} | "
                    f"stamp={ds.sp_result.get('label','?')}"
                )
            if idx == 0:
                from .perf_metrics import print_metrics_summary
                print_metrics_summary()

    # ── Per-Drone Display Frame ─────────────────────────────────────

    def get_display_frame(self, idx: int) -> np.ndarray | None:
        """
        Return an annotated display frame for one drone.
        Suitable for tiling into a 2x2 mosaic.
        """
        from . import overlay
        ds = self.states[idx]
        with ds.lock:
            if ds.frame_bgr is None:
                # Offline placeholder
                placeholder = np.zeros((config.DISPLAY_HEIGHT // 2,
                                        config.DISPLAY_WIDTH // 2, 3), dtype=np.uint8)
                cv2.putText(placeholder, f"D{idx+1}: {ds.name}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
                cv2.putText(placeholder, "OFFLINE",
                            (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
                return placeholder

            frame  = ds.frame_bgr.copy()
            dmap   = ds.dmap_np
            comp_zone  = ds.comp_zone
            comp_color = ds.comp_color
            pressure   = ds.pressure_smooth
            zone_scores   = ds.zone_scores
            speed_grid    = ds.speed_grid
            hs_result     = ds.hs_result
            opp_result    = ds.opp_result
            online     = ds.online
            uptime_s   = ds.uptime_s

        disp = cv2.resize(frame, (config.DISPLAY_WIDTH // 2, config.DISPLAY_HEIGHT // 2),
                          interpolation=cv2.INTER_AREA)

        if dmap is not None:
            disp = heatmap_generator.apply_heatmap(disp, dmap, alpha=config.HEATMAP_ALPHA, state=ds.heatmap_state)

        # Sleek mini header overlay
        h2, w2 = disp.shape[:2]
        header_h = 32
        
        # Transparent overlay
        overlay_hdr = disp[0:header_h, 0:w2].copy()
        cv2.rectangle(overlay_hdr, (0, 0), (w2, header_h), (12, 12, 15), -1)
        disp[0:header_h, 0:w2] = cv2.addWeighted(disp[0:header_h, 0:w2], 0.15, overlay_hdr, 0.85, 0)
        
        # Colored top outline bar indicating risk zone color
        cv2.rectangle(disp, (0, 0), (w2, 3), comp_color, -1)
        
        # Sleek text
        text_y = 21
        # Left side: drone name & state
        txt_left = f"D{idx+1}: {ds.name} | {comp_zone}"
        cv2.putText(disp, txt_left, (10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (240, 240, 245), 1, cv2.LINE_AA)
        
        # Right side: telemetry (headcount and uptime)
        if online:
            txt_right = f"{int(ds.density_score)} pax | {uptime_s:.0f}s"
            (trw, _), _ = cv2.getTextSize(txt_right, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            cv2.putText(disp, txt_right, (w2 - trw - 10, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 185), 1, cv2.LINE_AA)

        return disp

    def get_mosaic(self) -> np.ndarray:
        """
        Return a 2x2 mosaic of all 4 drone feeds.
        Perfect for the command center single-screen view.
        """
        frames = [self.get_display_frame(i) for i in range(min(self.n, 4))]

        # Pad to 4 if fewer drones
        while len(frames) < 4:
            h, w = frames[0].shape[:2]
            frames.append(np.zeros((h, w, 3), dtype=np.uint8))

        top    = np.hstack(frames[:2])
        bottom = np.hstack(frames[2:4])
        return np.vstack([top, bottom])

    def _inference_dispatcher(self):
        """Batch dispatcher thread running inference across active queues."""
        print("[SWARM-DISPATCHER] Waiting for shared model to load...")
        while self.model is None and not self._stop.is_set():
            time.sleep(0.1)
        if self._stop.is_set():
            return
        
        mdl = self.model
        print("[SWARM-DISPATCHER] Model ready. Starting dispatcher loop.")
        
        import torch
        
        while not self._stop.is_set():
            try:
                # Wait for the first request
                req = self.batch_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            requests = [req]
            start_time = time.perf_counter()
            # 15ms aggregation window
            timeout = 0.015
            
            while time.perf_counter() - start_time < timeout and len(requests) < self.n:
                try:
                    next_req = self.batch_queue.get_nowait()
                    requests.append(next_req)
                except queue.Empty:
                    time.sleep(0.001)

            tensors = [r[1] for r in requests]
            if len(tensors) == 1:
                batched_tensor = tensors[0]
            else:
                batched_tensor = torch.cat(tensors, dim=0)

            try:
                with torch.inference_mode():
                    out = mdl(batched_tensor)
                dmap_batch = out[0] if isinstance(out, (tuple, list)) else out
                
                # Split and scatter back
                for i, r in enumerate(requests):
                    idx, _, done_event, result_container = r
                    # Slice from the batch output
                    single_dmap = dmap_batch[i]
                    result_container["dmap_np"] = single_dmap.squeeze().detach().float().cpu().numpy()
                    done_event.set()
            except Exception as e:
                print(f"[SWARM-DISPATCHER] Error during batched inference: {e}")
                for r in requests:
                    r[2].set()

    def _watchdog_loop(self):
        """Watchdog loop checking and restarting exited daemon threads."""
        print("[SWARM-WATCHDOG] Thread supervisor started.")
        while not self._stop.is_set():
            time.sleep(5.0)
            if self._stop.is_set():
                break

            for name, spec in list(self.thread_specs.items()):
                t = spec.get("thread")
                if t is None or not t.is_alive():
                    if self._stop.is_set():
                        break
                    print(f"[SWARM-WATCHDOG] Warning: Thread '{name}' died unexpectedly! Restarting...")
                    target = spec["target"]
                    args = spec["args"]
                    new_t = threading.Thread(target=target, args=args, daemon=True, name=name)
                    spec["thread"] = new_t
                    new_t.start()
                    self._threads.append(new_t)


if __name__ == "__main__":
    # Test execution or entrypoint
    print("[SWARM] Initializing Swarm Manager...")
    mgr = SwarmManager()
    mgr.start()

    try:
        while True:
            time.sleep(1)
            # Show mosaic for local testing
            mosaic = mgr.get_mosaic()
            cv2.imshow("Pushkaralu 2027 Swarm Command Center", mosaic)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop()
        cv2.destroyAllWindows()
