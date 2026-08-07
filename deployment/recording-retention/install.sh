#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

sudo install -m 0755 \
    "$SCRIPT_DIR/pushkaralu-recording-retention" \
    /usr/local/sbin/pushkaralu-recording-retention

sudo install -m 0644 \
    "$SCRIPT_DIR/pushkaralu-recording-retention.conf" \
    /etc/default/pushkaralu-recording-retention

sudo install -m 0644 \
    "$SCRIPT_DIR/pushkaralu-recording-retention.service" \
    /etc/systemd/system/pushkaralu-recording-retention.service

sudo install -m 0644 \
    "$SCRIPT_DIR/pushkaralu-recording-retention.timer" \
    /etc/systemd/system/pushkaralu-recording-retention.timer

sudo systemctl daemon-reload
sudo systemctl enable --now pushkaralu-recording-retention.timer
sudo systemctl start pushkaralu-recording-retention.service

for CONTAINER in pushkaralu-mediamtx pushkaralu-app pushkaralu-nginx
do
    if sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
        sudo docker update --restart unless-stopped "$CONTAINER"
    fi
done

echo
echo "Recording retention installed."
sudo systemctl status pushkaralu-recording-retention.timer --no-pager
