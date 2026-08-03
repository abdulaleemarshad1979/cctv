#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN} Pushkaralu CCTV & Drone Monitor - Automated Ubuntu 26.04 LTS Installer ${NC}"
echo -e "${GREEN}======================================================================${NC}"

# 1. Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "[+] OS Detected: ${NAME} ${VERSION}"
else
    echo -e "${RED}[!] Warning: /etc/os-release not found. Proceeding with standard installer...${NC}"
fi

# 2. Check Internet Connectivity
echo -e "[+] Checking internet connectivity..."
if curl -s --head --request GET https://download.docker.com > /dev/null; then
    echo -e "${GREEN}[✓] Internet connected.${NC}"
else
    echo -e "${YELLOW}[!] Warning: Could not reach download.docker.com. Ensure offline docker image tar is loaded if offline.${NC}"
fi

# 3. Install System Packages
echo -e "[+] Installing system prerequisites..."
sudo apt update -y
sudo apt install -y \
  git \
  curl \
  ca-certificates \
  gnupg \
  ffmpeg \
  htop \
  nano \
  unzip \
  net-tools

# 4. Check / Install Docker Engine
if ! command -v docker &> /dev/null; then
    echo -e "[+] Installing Docker Engine..."
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    sudo apt update -y
    sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

sudo systemctl enable --now docker
sudo usermod -aG docker $USER || true

# 5. Create Deployment Directory Structure
echo -e "[+] Creating runtime directories..."
mkdir -p data/outputs data/logs config/certificates deployment dm_count/pretrained_models

# 6. Check Model Weight File
MODEL_PATH="dm_count/pretrained_models/model_nwpu.pth"
echo -e "[+] Verifying DM-Count pretrained model file..."
if [ ! -f "$MODEL_PATH" ]; then
    echo -e "${RED}======================================================================${NC}"
    echo -e "${RED}[ERROR] REQUIRED MODEL WEIGHT FILE IS MISSING!${NC}"
    echo -e "${RED}File expected at: ${MODEL_PATH}${NC}"
    echo -e "${YELLOW}Please copy 'model_nwpu.pth' from your USB drive to:${NC}"
    echo -e "  $(pwd)/dm_count/pretrained_models/model_nwpu.pth"
    echo -e "${RED}======================================================================${NC}"
    exit 1
fi

MODEL_SIZE=$(wc -c <"$MODEL_PATH" | tr -d ' ')
if [ "$MODEL_SIZE" -lt 10485760 ]; then
    echo -e "${RED}[ERROR] Model file ${MODEL_PATH} is incomplete or corrupted (size: ${MODEL_SIZE} bytes).${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] Pretrained model file verified (${MODEL_SIZE} bytes).${NC}"

# 7. Environment File Setup
if [ ! -f .env ]; then
    echo -e "[+] Creating .env configuration from .env.example..."
    cp .env.example .env
fi

# 8. Build & Start Docker Stack
echo -e "[+] Building and starting Docker containers..."
docker compose build --pull
docker compose up -d

# 9. Health Check
echo -e "[+] Waiting for services to initialize..."
sleep 5
bash healthcheck.sh

LOCAL_IP=$(hostname -I | awk '{print $1}')
echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN} Pushkaralu Dashboard successfully deployed! ${NC}"
echo -e "${GREEN} Access Dashboard locally at: http://${LOCAL_IP} ${NC}"
echo -e "${GREEN} Access Dashboard on server at: http://127.0.0.1 ${NC}"
echo -e "${GREEN}======================================================================${NC}"
