---
title: CCTV Crowd Backend
emoji: 🛰️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Pushkaralu Drone Crowd Monitor & Risk Engine

An AI-powered CCTV and drone swarm monitoring system designed to estimate crowd density, track risk factors, predict stampedes, and dispatch geotagged alerts for large public gatherings. Powered by the **DM-Count** deep-learning model, this application processes RTSP video feeds from DJI, Autel, Parrot, or custom drones to provide real-time situational awareness.

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Prerequisites & Installation](#prerequisites--installation)
3. [Pre-trained Weights Setup](#pre-trained-weights-setup)
4. [Quick Start (Interactive Launcher)](#quick-start-interactive-launcher)
5. [Single-Drone Monitor](#single-drone-monitor)
6. [Swarm Command (Multi-Drone Monitor)](#swarm-command-multi-drone-monitor)
7. [RTSP Preset Manager](#rtsp-preset-manager)
8. [YouTube Downloader Utility](#youtube-downloader-utility)
9. [Keyboard Shortcuts](#keyboard-shortcuts)
10. [Configuration Guide](#configuration-guide)
11. [GPS & Messaging Integrations](#gps--messaging-integrations)

---

## System Overview

```
                      +-------------------+
                      |   RTSP Streams    |
                      | (Drones/Surv Cam) |
                      +---------+---------+
                                |
                                v
                      +---------+---------+
                      |   Producer Thread |
                      +---------+---------+
                                | (Frame Queue)
                                v
                      +---------+---------+
                      |   Inference Thread| <--- DM-Count (VGG19)
                      +---------+---------+
                                | (Density Map, Metrics)
                                v
  +-----------------------------+-----------------------------+
  |                             |                             |
  v                             v                             v
+------------------+     +------------------+     +------------------+
|  Risk Engine     |     |   Optical Flow   |     |   Zone Monitor   |
|  - Comp Risk     |     |   - Speed Grid   |     |   - 3x3 Grid     |
|  - Thresholding  |     |   - Turbulence   |     |   - Capacity %   |
+---------+--------+     +---------+--------+     +---------+--------+
          |                        |                        |
          +------------------------+------------------------+
                                   |
                                   v
                      +---------+---------+
                      |   Visual Overlay  | ---> Live Screen & GUI
                      +---------+---------+
                                |
                                v
                      +---------+---------+
                      |  Alerts Dispatch  | ---> Telegram / WhatsApp
                      +-------------------+      GeoJSON GIS Overlay
```

---

## Prerequisites & Installation

### System Requirements
* **Operating System**: Windows 10/11, macOS, or Linux.
* **GPU**: CUDA-compatible Nvidia GPU (highly recommended for real-time inference, though CPU fallback is supported).
* **Python**: Python 3.8 to 3.11.

### Installation
1. Clone or copy this repository to your workspace.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## Pre-trained Weights Setup

This project uses the DM-Count model trained on the NWPU crowd dataset.
1. Ensure the weights file `model_nwpu.pth` is placed under the following directory structure:
   ```
   dm_count/
   └── pretrained_models/
       └── model_nwpu.pth
   ```
2. If the directory does not exist, create it:
   ```bash
   mkdir -p dm_count/pretrained_models
   ```
3. Place your `model_nwpu.pth` file inside `dm_count/pretrained_models/`.

---

## Quick Start (Interactive Launcher)

The easiest way to launch the CCTV monitor is via the Master Launcher. It allows you to select from last-used sources, input a new RTSP stream, or pick a preset drone configuration.

```bash
python launch.py
```

### Direct Launcher Arguments
If you know your stream source, you can bypass the interactive menu and launch the pipeline directly:
```bash
# Launch using a DJI preset
python launch.py dji_mini3

# Launch using a custom RTSP stream
python launch.py rtsp://192.168.1.100:554/live

# Launch using a local video file
python launch.py Videos/mecca.mp4
```

---

## Single-Drone Monitor

To run the single-drone pipeline directly with advanced environment variables:

```bash
python infer.py
```

### Environment Variable Overrides
You can customize the source and options without editing `config.py`:
* **CCTV_SOURCE**: Specify the input stream/video (e.g. `CCTV_SOURCE=rtsp://192.168.0.1/live`).
* **DRONE**: Specify a pre-defined shortcut from `DRONE_DB` (e.g. `DRONE=dji_mini4pro`).
* **RTSP_TRANSPORT**: Choose transport protocol: `tcp` (default, reliable) or `udp` (low lag).

Example:
```bash
# Windows (CMD)
set DRONE=dji_mini3&& python infer.py

# Linux / macOS
DRONE=dji_mini3 RTSP_TRANSPORT=udp python infer.py
```

---

## Swarm Command (Multi-Drone Monitor)

For command center setups, you can monitor 4 parallel drone feeds simultaneously in a 2x2 mosaic layout. The Swarm Command Center integrates unified risk assessments and scales density mapping dynamically based on drone altitudes.

```bash
python swarm_infer.py
```

* Feeds are configured in `config.py` under the `DRONE_SOURCES` list.
* Shared GPU execution enables efficient inference across all four streams.
* Unified status reports are printed to the console and can be streamed to a web-based dashboard.

---

## RTSP Preset Manager

The `manage_rtsp.py` utility is a dedicated manager script designed for operators to quickly add, test, list, and remove RTSP configurations from the system database.

```bash
python manage_rtsp.py
```

### Available CLI Commands:
* **`python manage_rtsp.py add`**: Step-by-step assistant to paste a URL, run a connection test, assign a preset key, and save it.
* **`python manage_rtsp.py test`**: Quick RTSP stream checker (opens stream in window without saving).
* **`python manage_rtsp.py list`**: Display all saved presets grouped by manufacturer.
* **`python manage_rtsp.py remove`**: Prompt to delete a preset from the database.

---

## YouTube Downloader Utility

For test environments, you can download sample high-resolution crowd videos using:

```bash
python yt.py
```

* Edit the URL in `yt.py` to change the target video.
* The script automatically downloads the video and saves it to `./Videos/mecca.mp4` for offline simulation.

---

## Keyboard Shortcuts

While the video window is focused, you can control the visualization interactively using the following key bindings:

| Key | Action | Supported Modes |
| :---: | :--- | :--- |
| **`q`** | Quit the monitor pipeline | Single & Swarm |
| **`h`** | Toggle heat-map overlay | Single & Swarm |
| **`s`** | Toggle stampede metrics panel | Single & Swarm |
| **`r`** | Toggle video recording (saves to `outputs/annotated_output.mp4`) | Single Mode Only |
| **`l`** | Toggle CSV logging (saves to `outputs/crowd_log.csv`) | Single Mode Only |
| **`f`** | Return to 2x2 mosaic dashboard | Swarm Mode Only |
| **`1`** | Focus on Drone 1 feed | Swarm Mode Only |
| **`2`** | Focus on Drone 2 feed | Swarm Mode Only |
| **`3`** | Focus on Drone 3 feed | Swarm Mode Only |
| **`4`** | Focus on Drone 4 feed | Swarm Mode Only |

---

## Configuration Guide

Global settings are managed in `config.py`. Key options include:

### Display & Inference Sizes
* `DISPLAY_WIDTH` & `DISPLAY_HEIGHT`: Resolution of the rendering window.
* `INFER_WIDTH` & `INFER_HEIGHT`: Resolution fed to the neural network (lower resolutions increase FPS).

### Risk Thresholds (Normalized 0.0 - 1.0)
* `SAFE_THRESHOLD` (0.25)
* `WATCH_THRESHOLD` (0.50)
* `HIGH_THRESHOLD` (0.75)

### Drone Altitude Correction
DM-Count is calibrated for ground-level camera views. High-altitude drone shots make people appear smaller, leading to under-counting.
* `ENABLE_ALTITUDE_CORRECTION` (True): Automatically adjusts density mapping values.
* `DRONE_ALTITUDES_M`: Set specific altitude per drone to calibrate scale correction.

---

## GPS & Messaging Integrations

Real-time alert dispatching is handled by `src/geo_alert.py`. When a cell in the 3x3 monitor grid enters a `HIGH` or `CRITICAL` risk state, notifications can be routed instantly.

### Dispatch Channels
1. **JSON Line Logs**: Saved to `outputs/alert_log.jsonl`.
2. **GeoJSON Logs**: Saved to `outputs/alerts.geojson`. Perfect for GIS overlays (e.g. Leaflet or QGIS dashboards).
3. **Telegram Bot**:
   * Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables.
   * Notifications will be dispatched with location details and Google Maps links.
4. **WhatsApp**:
   * Integrate using the free CallMeBot API. Set `WHATSAPP_PHONE` and `WHATSAPP_API_KEY` environment variables.

---

## Connecting a DJI Air 3S

The DJI Air 3S (and other modern DJI Fly drones like the Air 3, Mini 4 Pro, Mavic 3 series) does **not** expose a direct RTSP server on its Wi-Fi hotspot. Instead, the DJI Fly app **pushes** an RTMP stream outward to a server you specify. You must bridge that RTMP feed into an RTSP endpoint using **MediaMTX** (free, open-source, single binary).

### Step 1 — Install MediaMTX

| OS | Command |
|---|---|
| **Linux / macOS** | `curl -L https://github.com/bluenviron/mediamtx/releases/latest/download/mediamtx_linux_amd64.tar.gz \| tar xz && ./mediamtx` |
| **Windows** | Download the `.zip` from [github.com/bluenviron/mediamtx/releases](https://github.com/bluenviron/mediamtx/releases), extract, run `mediamtx.exe` |

MediaMTX listens for RTMP on port **1935** and re-exposes every stream as RTSP on port **8554** automatically. No config file edits needed.

### Step 2 — Find your PC's IP address on the drone Wi-Fi

Connect your PC to the **DJI Air 3S hotspot** (SSID: `DJI-AIR3S-XXXX`), then:

```bash
# Linux / macOS
ip route get 1 | awk '{print $7; exit}'    # or: ifconfig | grep 192.168

# Windows
ipconfig | findstr 192.168
```

Your PC will typically receive `192.168.42.X` on DJI's hotspot.

### Step 3 — Configure DJI Fly App

1. Open the **DJI Fly app** and connect to your Air 3S.
2. Tap the **three-dot menu** (top-right of the camera view).
3. Go to **Transmission → Live Streaming → Custom RTMP**.
4. Enter:
   ```
   rtmp://192.168.42.X:1935/live
   ```
   (replace `X` with your PC's actual address from Step 2)
5. Tap **Start** — you should see a streaming indicator in the app.

### Step 4 — Verify the stream (optional)

```bash
ffplay -rtsp_transport tcp rtsp://localhost:8554/live
# or
python src/drone_stream.py dji_air3s
```

### Step 5 — Launch the monitor

```bash
# Linux / macOS
DRONE=dji_air3s python infer.py

# Windows CMD
set DRONE=dji_air3s && python infer.py
```

### Air 3S Camera Specs (set in config.py)

| Parameter | Value |
|---|---|
| Main lens HFOV | 80° |
| Medium lens HFOV | 57° |
| Max video resolution | 4K / 60 fps |
| RTMP stream resolution | 720p or 1080p (DJI Fly limit) |
| Recommended `DRONE_ALTITUDE_M` | 30 m (adjustable) |

> **Tip:** If you see lag, switch to UDP transport:
> `DRONE=dji_air3s RTSP_TRANSPORT=udp python infer.py`
