# CCTV Swarm monitor — Network & Tunnel Guide

This document describes the networking options for routing drone streams to remote viewers.

## Current Setup: Free Pinggy SSH Tunnel

The system currently uses **Pinggy.io**'s free-tier TCP tunneling service to expose the local MediaMTX instance to the public internet:

```
[Drone/Simulation] -> RTMP (Local) -> [MediaMTX (Local)] -> Pinggy SSH Tunnel -> [Public Pinggy Server] -> [Remote Viewer]
```

### Limitations of Free Pinggy
1. **No Bandwidth Guarantee:** The free tier shares public relay bandwidth, causing congestion and frame drops.
2. **Periodic Disconnects:** Free tunnels automatically expire or disconnect under prolonged load.
3. **Changing URLs:** The public URL changes on every supervisor restart, making static client configuration impossible.

## Production Recommendation: Dedicated VPS Relay

For staging or production deployments, replace the public tunnel with a self-hosted VPS relay:

```
[Drone Local Wi-Fi] -> WireGuard / Tailscale -> [Lightweight VPS (MediaMTX)] -> [Public Clients]
```

### Advantages
- **Static Domain/IP:** Clients connect to a persistent hostname (e.g. `cctv.my-agency.org`).
- **Low Latency & High Bandwidth:** Directly routed via public cloud provider networks (AWS, GCP, DigitalOcean, etc.).
- **Enhanced Security:** Private feeds are transmitted securely over WireGuard/Tailscale before reaching the public-facing VPS.

### Setup Instructions (VPS)
1. Provision a small Ubuntu VPS (1-2 vCPUs, 2GB RAM is sufficient).
2. Install docker and run MediaMTX:
   ```bash
   docker run --rm -it --network=host bluenviron/mediamtx
   ```
3. Set up WireGuard or Tailscale on the VPS and your local machine.
4. Push RTMP feeds from the local machine directly to the VPS WireGuard IP:
   ```bash
   ffmpeg -re -i my_video.mp4 -f flv rtmp://<VPS_WIREGUARD_IP>:1935/live/drone1
   ```
