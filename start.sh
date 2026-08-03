#!/usr/bin/env bash
set -e

echo "[+] Starting Pushkaralu CCTV & Drone Monitoring stack..."
docker compose up -d

echo "[+] Service status:"
docker compose ps
