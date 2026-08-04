#!/usr/bin/env bash

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "[+] Running Pushkaralu Stack Health Check..."

# Check Docker Container Status
echo -e "\n--- Container Status ---"
docker compose ps

# Check Nginx (Port 80)
echo -e "\n--- Nginx Web Proxy (Port 80) ---"
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/ || true)
if [ "$HTTP_STATUS" -eq 200 ] || [ "$HTTP_STATUS" -eq 302 ]; then
    echo -e "${GREEN}[✓] Nginx is responding (HTTP ${HTTP_STATUS})${NC}"
else
    echo -e "${RED}[!] Nginx health check failed (HTTP ${HTTP_STATUS})${NC}"
fi

# Check FastAPI Application through Nginx
echo -e "\n--- FastAPI App through Nginx ---"
APP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/cameras || true)
if [ "$APP_STATUS" -eq 200 ]; then
    echo -e "${GREEN}[✓] FastAPI app API endpoint is healthy (HTTP ${APP_STATUS})${NC}"
else
    echo -e "${RED}[!] FastAPI app API check failed (HTTP ${APP_STATUS})${NC}"
fi

# Check MediaMTX RTSP Server (Port 8554)
echo -e "\n--- MediaMTX Stream Server (Port 8554) ---"
if nc -z -w2 127.0.0.1 8554 2>/dev/null || (exec 3<>/dev/tcp/127.0.0.1/8554) 2>/dev/null; then
    echo -e "${GREEN}[✓] MediaMTX RTSP server port 8554 is listening${NC}"
else
    echo -e "${RED}[!] MediaMTX RTSP server port 8554 is not responding${NC}"
fi

echo -e "\n[+] Health check finished."
