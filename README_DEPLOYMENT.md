# Pushkaralu CCTV & Drone Monitoring System — Production Deployment Guide
**Target Deployment Date**: August 4, 2026  
**Target Platform**: Ubuntu Server 26.04 LTS

---

## 1. System Architecture Overview

```text
Ubuntu Server 26.04 LTS
├── Docker Engine & Docker Compose
│   ├── pushkaralu-nginx    (Port 80/443 Reverse Proxy & Static Asset Server)
│   ├── pushkaralu-app      (FastAPI, CPU DM-Count / YOLO Inference Engine)
│   └── pushkaralu-mediamtx (RTSP 8554, RTMP 1935, WebRTC 8889, HLS 8888)
```

### UI vs AI Hardware Capacity Strategy
- **Website UI Capacity**: Displays up to **60 Drone Slots** (`MAX_DRONE_SLOTS=60`).
- **CPU AI Hardware Cap**: Hardware caps simultaneous CPU AI inference workers to **4** (`MAX_CONCURRENT_AI_FEEDS=4`).
- **Scalability**: Operators can switch AI counting on/off across any connected stream. Adding a GPU later only requires setting `MAX_CONCURRENT_AI_FEEDS=12` in `.env` — zero dashboard code changes required.

---

## 2. Pre-Deployment Preparation (Before Going to SP Office)

### USB Drive Packing Checklist
Be sure to pack a USB drive containing:
1. `dm_count/pretrained_models/model_nwpu.pth` (VGG19 DM-Count weights, ~80MB-150MB).
2. Offline Docker Base Images: `pushkaralu-base-images.tar` (optional if internet is uncertain).
3. Full repository backup (`cctv-repository.zip`).

#### Creating Offline Image Tarball (Run on your dev machine):
```bash
docker pull nginx:alpine
docker pull bluenviron/mediamtx:latest
docker pull python:3.11-slim

docker save \
  nginx:alpine \
  bluenviron/mediamtx:latest \
  python:3.11-slim \
  -o pushkaralu-base-images.tar
```

---

## 3. Production Installation (August 4, 2026)

### Step 1: Clone Repository & Copy Model Weights
```bash
sudo mkdir -p /opt/pushkaralu
sudo chown -R $USER:$USER /opt/pushkaralu
cd /opt/pushkaralu

git clone https://github.com/abdulaleemarshad1979/cctv.git app
cd app

# Ensure model weight file exists
mkdir -p dm_count/pretrained_models
cp /media/usb/model_nwpu.pth dm_count/pretrained_models/model_nwpu.pth
```

### Step 2: Run One-Command Automated Installer
```bash
sudo bash install.sh
```

`install.sh` automatically:
- Checks Ubuntu Server version & connectivity.
- Installs Docker Engine & Docker Compose plugin.
- Validates model weight presence & size (>10MB).
- Builds `Dockerfile.cpu` and launches containers (`app`, `mediamtx`, `nginx`).
- Enables Docker systemd persistence across server reboots.
- Runs `healthcheck.sh` and prints the local dashboard access URL (`http://<SERVER_IP>`).

---

## 4. Lifecycle & Management Commands

### Check Running Services:
```bash
docker compose ps
```

### View Live Logs:
```bash
docker compose logs -f app
docker compose logs -f nginx
docker compose logs -f mediamtx
```

### Stack Management Scripts:
- **Start Stack**: `bash start.sh`
- **Stop Stack**: `bash stop.sh`
- **Update Code**: `bash update.sh`
- **Run Health Check**: `bash healthcheck.sh`

---

## 5. Host Firewall (UFW) Configuration
```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

---

## 6. Future GPU Hardware Upgrade (When NVIDIA GPU Arrives)
No code reinstallation is required. Simply:
1. Install NVIDIA Drivers & NVIDIA Container Toolkit on Ubuntu.
2. Update `.env`:
   ```env
   DEVICE=cuda
   MAX_CONCURRENT_AI_FEEDS=12
   ```
3. Update `docker-compose.yml` to set `dockerfile: Dockerfile.gpu` and add GPU runtime options.
4. Restart stack: `bash start.sh`.
