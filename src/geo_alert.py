"""
geo_alert.py  —  Pushkaralu 2027 GPS Alert Dispatcher
======================================================
Converts zone alerts into GPS-tagged messages and dispatches
them to field teams via:
  - Console / log
  - GeoJSON log file (for GIS overlay in command dashboard)
  - Telegram bot (optional, free)
  - WhatsApp via CallMeBot API (optional, free)

Setup
-----
  1. GeoJSON log: No setup needed. File: outputs/alerts.geojson
  2. Telegram bot:
       a. Message @BotFather on Telegram -> /newbot
       b. Copy the token into TELEGRAM_BOT_TOKEN below
       c. Start a chat with your bot, get your chat ID via:
            https://api.telegram.org/bot<TOKEN>/getUpdates
       d. Set TELEGRAM_CHAT_ID below
  3. CallMeBot WhatsApp:
       a. Add +34 644 60 49 48 to WhatsApp contacts as "CallMeBot"
       b. Send: "I allow callmebot to send me messages"
       c. You receive your API key
       d. Set WHATSAPP_PHONE and WHATSAPP_API_KEY below
"""

import os
import json
import time
import threading
import requests
from datetime import datetime
from pathlib import Path

# ── Config (override via environment variables) ────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

WHATSAPP_PHONE     = os.environ.get("WHATSAPP_PHONE", "")   # e.g. "919876543210"
WHATSAPP_API_KEY   = os.environ.get("WHATSAPP_API_KEY", "")

GEOJSON_LOG_PATH   = Path("outputs/alerts.geojson")
ALERT_LOG_PATH     = Path("outputs/alert_log.jsonl")
ALERT_COOLDOWN_S   = 60.0   # Don't re-alert same zone within 60 seconds

# Minimum zone to trigger external dispatch (HIGH or CRITICAL)
DISPATCH_ZONE_THRESHOLD = {"HIGH", "CRITICAL"}


# ══════════════════════════════════════════════════════════════════════
#  ALERT DISPATCHER
# ══════════════════════════════════════════════════════════════════════

class GeoAlertDispatcher:
    """
    Receives GPS alert dicts from SwarmManager and dispatches them
    to field teams via multiple channels.

    Thread-safe: call dispatch_alerts() from any thread.
    """

    def __init__(self):
        self._lock         = threading.Lock()
        self._last_alert   = {}     # key: (drone_id, cell) -> last dispatch time
        self._geojson_data = {"type": "FeatureCollection", "features": []}
        self._session      = requests.Session()

        # Ensure output directory exists
        GEOJSON_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def dispatch_alerts(self, gps_alerts: list[dict]) -> None:
        """
        Main entry point. Call once per inference cycle.
        gps_alerts: list of dicts from build_gps_alert() in swarm_manager.py
        """
        for alert in gps_alerts:
            if alert.get("zone") not in DISPATCH_ZONE_THRESHOLD:
                continue

            key = (alert.get("drone_id"), alert.get("cell"))
            now = time.monotonic()

            with self._lock:
                last_t = self._last_alert.get(key, 0.0)
                if now - last_t < ALERT_COOLDOWN_S:
                    continue
                self._last_alert[key] = now

            # Dispatch in background to not block inference
            threading.Thread(
                target=self._do_dispatch,
                args=(alert,),
                daemon=True
            ).start()

    def _do_dispatch(self, alert: dict) -> None:
        """Run all dispatch channels for one alert."""
        self._log_geojson(alert)
        self._log_jsonl(alert)

        msg = self._format_message(alert)
        try:
            print(f"[GEO-ALERT] {msg}")
        except UnicodeEncodeError:
            safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
            print(f"[GEO-ALERT] {safe_msg}")

        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            self._send_telegram(msg)

        if WHATSAPP_PHONE and WHATSAPP_API_KEY:
            self._send_whatsapp(msg)

    def _format_message(self, alert: dict) -> str:
        zone = alert.get("zone", "?")
        emoji = {"HIGH": "⚠️", "CRITICAL": "🚨"}.get(zone, "ℹ️")
        ghat  = alert.get("ghat", "?")
        cell  = alert.get("cell", "?")
        occ   = alert.get("occupancy_pct", 0.0)
        dens  = alert.get("density", 0)
        lat   = alert.get("gps_lat", 0.0)
        lon   = alert.get("gps_lon", 0.0)
        ts    = datetime.now().strftime("%H:%M:%S")

        lines = [
            f"{emoji} PUSHKARALU 2027 ALERT [{ts}]",
            f"Zone: {zone} | Ghat: {ghat} | Cell: {cell}",
            f"Crowd: ~{dens} people | Capacity: {occ:.0f}%",
        ]
        if lat != 0.0:
            lines.append(f"GPS: {lat}N, {lon}E")
            lines.append(f"Maps: https://maps.google.com/?q={lat},{lon}")
        return "\n".join(lines)

    def _log_geojson(self, alert: dict) -> None:
        """Append to GeoJSON file for GIS / Leaflet dashboard overlay."""
        if alert.get("gps_lat", 0.0) == 0.0:
            return
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [alert["gps_lon"], alert["gps_lat"]]
            },
            "properties": {
                "zone":     alert.get("zone"),
                "ghat":     alert.get("ghat"),
                "cell":     alert.get("cell"),
                "density":  alert.get("density"),
                "occ_pct":  alert.get("occupancy_pct"),
                "time":     datetime.now().isoformat(),
            }
        }
        with self._lock:
            self._geojson_data["features"].append(feature)
            with open(GEOJSON_LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._geojson_data, f, indent=2)

    def _log_jsonl(self, alert: dict) -> None:
        """Append one JSON line per alert — easy to stream to dashboard."""
        record = {**alert, "timestamp": datetime.now().isoformat()}
        with open(ALERT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _send_telegram(self, message: str) -> None:
        """Send alert to Telegram bot."""
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            self._session.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text":    message,
                "parse_mode": "HTML",
            }, timeout=5)
        except Exception as e:
            print(f"[GEO-ALERT] Telegram error: {e}")

    def _send_whatsapp(self, message: str) -> None:
        """Send alert via CallMeBot WhatsApp API (free, no credit card)."""
        try:
            url = "https://api.callmebot.com/whatsapp.php"
            self._session.get(url, params={
                "phone": WHATSAPP_PHONE,
                "apikey": WHATSAPP_API_KEY,
                "text":  message,
            }, timeout=5)
        except Exception as e:
            print(f"[GEO-ALERT] WhatsApp error: {e}")


# ── Singleton ──────────────────────────────────────────────────────
_dispatcher = GeoAlertDispatcher()


def dispatch(gps_alerts: list[dict]) -> None:
    """Module-level convenience function."""
    _dispatcher.dispatch_alerts(gps_alerts)
