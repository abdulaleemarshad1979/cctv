#!/usr/bin/env bash
set -e

echo "[+] Pulling latest repository updates..."
git pull

echo "[+] Rebuilding Docker containers..."
docker compose build

echo "[+] Restarting services..."
docker compose up -d

echo "[+] Update complete. Current status:"
docker compose ps
