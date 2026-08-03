#!/usr/bin/env bash
set -e

echo "[+] Stopping Pushkaralu CCTV & Drone Monitoring stack..."
docker compose down

echo "[+] Stack stopped."
