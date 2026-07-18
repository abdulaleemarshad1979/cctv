# Connecting a remote 4G/cellular CCTV camera

This covers cameras that are NOT on the same network as your monitoring
server — the common case for 4G/cellular ("SIM card") CCTV, since carrier
networks put the camera behind NAT you can't reach from outside.

## 1. Find the camera's local RTSP URL (if it has one)

You need a device briefly on the SAME local network as the camera — its
own setup Wi-Fi hotspot, or the LAN side of whatever 4G router it's
plugged into.

```bash
python tools/probe_camera_rtsp.py 192.168.1.108
# or with credentials:
python tools/probe_camera_rtsp.py 192.168.1.108 --user admin --password 12345
```

This tries the RTSP path patterns common across cheap 4G/"CamHi"-chipset
cameras (which is what most rebadged AliExpress-style 4G cams run,
regardless of the storefront brand name printed on the box). If one
responds, you're set — skip to step 3.

Also check the camera's own app: Settings → Device/Camera Settings →
look for "RTSP", "ONVIF", or an "Advanced" submenu — some firmwares only
start the RTSP server after you toggle it on there.

## 2. No RTSP? Check for a direct push option first

Before assuming you need a relay device, check the app for something
like "Custom RTMP Server" / "Push to Cloud" / "Custom Live Server". Some
4G DVR-style cameras support pushing straight to a server URL you type
in — if yours does, point it at `rtmp://<your-server-or-tunnel>:1935/live/cam1`
and skip straight to step 4, no extra hardware needed.

If there's genuinely no RTSP and no custom push, you'll need to
capture the app's video output some other way (screen-mirroring an old
Android device running the vendor app, or swapping the camera for an
ONVIF/RTSP-capable model) — not covered by the scripts here.

## 3. Bridge it: relay device at the camera's site

Put a small always-on device (Raspberry Pi, mini PC, old laptop) on the
same local network as the camera. It pulls the local RTSP stream and
pushes it out to your remote server.

### On Linux / macOS (Bash)
```bash
./tools/relay_to_cloud.sh \
    rtsp://admin:pass@192.168.1.108:554/11 \
    rtmp://your-server-or-tunnel:1935/live/cam1
```

### On Windows (Command Prompt / batch)
```cmd
tools\relay_to_cloud.bat ^
    rtsp://admin:pass@192.168.1.108:554/11 ^
    rtmp://your-server-or-tunnel:1935/live/cam1
```

If your remote server has no public IP, put a tunnel in front of its
`:1935` port first (pinggy, ngrok, Cloudflare Tunnel, Tailscale funnel —
same idea as the pinggy line already saved in `sources.txt`), and use
the tunnel's address as `your-server-or-tunnel`.

For a permanent setup, run this under `systemd`/`tmux`/`nohup` so it
survives reboots — it already retries on drops, which matters on
cellular links.

## 4. Point the pipeline at it

On the machine running `infer.py` (your remote server):

```bash
CCTV_SOURCE=rtsp://your-server-or-tunnel:8554/live/cam1 USE_FUSION=1 python infer.py
```

Or save it as a named preset first, same as any other source:

```bash
python manage_rtsp.py     # paste the URL, test it, name it e.g. "cam1"
DRONE=cam1 USE_FUSION=1 python infer.py
```

## Multiple 4G cameras

Repeat steps 1–3 for each camera (each needs its own relay device at its
own site, and its own `/live/camN` stream name), then set them as
`CCTV_SOURCE_1`, `CCTV_SOURCE_2`, ... env vars (or edit `CCTV_SOURCES` in
`config.py`) and run `swarm_infer.py` instead of `infer.py`.
