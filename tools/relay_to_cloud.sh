#!/usr/bin/env bash
# tools/relay_to_cloud.sh
# =========================
# Run this ON A DEVICE AT THE CAMERA'S SITE (Raspberry Pi, mini PC, old
# laptop — anything that can stay powered on and has internet access,
# e.g. plugged into the same 4G router as the camera).
#
# It pulls the camera's LOCAL rtsp stream and re-publishes it as an RTMP
# push to your REMOTE server's MediaMTX instance (already configured in
# this repo's mediamtx.yml, listening on :1935). MediaMTX then re-exposes
# it as rtsp://<remote>:8554/live/<STREAM_NAME> — same pattern already
# used for the DJI Air 3's "Custom RTMP" push in src/presets.py.
#
# If your remote server has no public IP, put a tunnel (pinggy, ngrok,
# Cloudflare Tunnel, Tailscale funnel, etc.) in front of its :1935 port
# and use the tunnel's hostname/port below — exactly like the pinggy
# entry already saved in sources.txt.
#
# Usage:
#   ./tools/relay_to_cloud.sh \
#       rtsp://admin:pass@192.168.1.108:554/11 \
#       rtmp://your-server-or-tunnel:1935/live/cam1
#
# Then, on the machine actually running infer.py:
#   CCTV_SOURCE=rtsp://your-server-or-tunnel:8554/live/cam1 USE_FUSION=1 python infer.py
#
# For a permanent deployment, wrap this in a systemd service (or `screen`/
# `tmux`/`nohup`) so it restarts automatically — ffmpeg is given
# `-reconnect 1` flags below so it retries the source and destination on
# drops, which matters a lot on cellular links.

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <camera_rtsp_url> <remote_rtmp_push_url>"
  echo "e.g.:  $0 rtsp://admin:pass@192.168.1.108:554/11 rtmp://myserver:1935/live/cam1"
  exit 1
fi

SRC="$1"
DST="$2"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install it first (e.g. apt install ffmpeg)."
  exit 1
fi

echo "Relaying:"
echo "  from (camera, local):  $SRC"
echo "  to   (your server):    $DST"
echo "Press Ctrl+C to stop."
echo


while true; do
  ffmpeg -hide_banner -loglevel warning \
    -rtsp_transport tcp \
    -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 \
    -i "$SRC" \
    -c copy \
    -f flv \
    "$DST" || true
  echo "[relay] stream dropped — retrying in 3s..."
  sleep 3
done
