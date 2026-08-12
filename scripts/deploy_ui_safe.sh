#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

app_container_before="$(docker compose ps -q app)"
mediamtx_container_before="$(docker compose ps -q mediamtx)"

if [[ -z "$app_container_before" || -z "$mediamtx_container_before" ]]; then
  echo "App and MediaMTX must already be running before a UI-only deployment."
  exit 1
fi

app_image_name="$(docker inspect -f '{{.Config.Image}}' "$app_container_before")"
app_image_before="$(docker inspect -f '{{.Image}}' "$app_container_before")"
rollback_tag="${app_image_name}:ui-rollback"
docker tag "$app_image_before" "$rollback_tag"

rollback_app() {
  echo "UI deployment failed; restoring the previous app image."
  docker tag "$rollback_tag" "$app_image_name"
  docker compose up -d --no-deps --force-recreate app
}

echo "Building the app/UI image without changing MediaMTX..."
docker compose build app

echo "Checking Python syntax inside the newly built image..."
docker compose run --rm --no-deps app \
  python3 -m py_compile /app/lite_server.py /app/src/stream_state_monitor.py

echo "Replacing only the app container..."
docker compose up -d --no-deps app

healthy=0
for _attempt in $(seq 1 30); do
  if docker compose exec -T app python3 -c '
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
    health = json.load(response)

assert health.get("status") == "healthy"
assert health.get("stream_state", {}).get("running") is True
' >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  rollback_app
  exit 1
fi

mediamtx_container_after="$(docker compose ps -q mediamtx)"
if [[ "$mediamtx_container_after" != "$mediamtx_container_before" ]]; then
  echo "Safety check failed: MediaMTX changed during a UI-only deployment."
  rollback_app
  exit 1
fi

docker compose exec -T mediamtx \
  curl -fsS http://127.0.0.1:9997/v3/paths/list >/dev/null

echo "UI deployment completed safely. MediaMTX and active publishers were not restarted."
