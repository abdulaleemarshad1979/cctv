# Deploying the stream-reliability changes (Stage A)

Stage A changes **only the app container** (`pushkaralu-app`). MediaMTX keeps
running, so live ingest and recording are never interrupted. Viewers see a
short "Reconnecting…" while the app restarts (about 10–30 s).

Do not run `git pull`, `git reset` or `git stash` on the server: the server
tree has hand edits and stashes. Copy only the files listed below.

## Files changed

| File | Change |
|---|---|
| `lite_server.py` | grace period, raw-feed playback, diagnostics/continuity/ABR endpoints, health watcher |
| `src/stream_state_monitor.py` | optional observer callbacks (no behaviour change) |
| `src/stream_events.py` *(new)* | rotating per-camera event log (`data/logs/stream_events.jsonl`) |
| `src/stream_telemetry.py` *(new)* | bitrate, last packet, uplink stall vs app-close classification |
| `src/recording_continuity.py` *(new)* | recording gaps, integrity, recorder stall, disk checks (read-only) |
| `src/abr_transcoder.py` *(new)* | on-demand adaptive quality (source copy + 480p/720p) |
| `static/monitor/js/egdms-player.js` *(new)* | shared player helpers (valid hls.js config, backoff, held frame, quality menu) |
| `lite_dashboard.html`, `admin_dashboard.html` | player fixes only; layout unchanged |
| `stream_diagnostics.html` *(new)* | admin page `/admin/diagnostics` |
| `tests/…` | new tests; two tests updated from the broken hls.js config |

## 1. Back up (server)

```bash
cd /opt/pushkaralu/app
B=/opt/pushkaralu/app/backups/stream-reliability-$(date -u +%Y%m%dT%H%M%SZ)
sudo mkdir -p "$B/src" "$B/static/monitor/js" "$B/tests"
sudo cp -a lite_server.py lite_dashboard.html admin_dashboard.html "$B/"
sudo cp -a src/stream_state_monitor.py "$B/src/"
sudo cp -a tests/test_hls_playback_controller.py tests/test_video_playback_behavior.py "$B/tests/"
docker image tag app-app:latest app-app:before-stream-reliability
echo "$B"
```

## 2. Copy the new files (from the workstation)

```bash
cd /opt/pushkaralu/app && git checkout fix/stream-reliability
FILES="lite_server.py lite_dashboard.html admin_dashboard.html stream_diagnostics.html src/stream_state_monitor.py src/stream_events.py src/stream_telemetry.py src/recording_continuity.py src/abr_transcoder.py static/monitor/js/egdms-player.js tests/test_stream_reliability.py tests/test_stream_grace_and_diagnostics.py tests/test_hls_playback_controller.py tests/test_video_playback_behavior.py docs/DEPLOY_STREAM_RELIABILITY.md"
tar -cf - $FILES | ssh -p 2222 drone@117.216.215.157 'mkdir -p /tmp/stage-a && tar -xf - -C /tmp/stage-a'
```

On the server:

```bash
cd /opt/pushkaralu/app && sudo cp -a /tmp/stage-a/. . && sudo chown root:root lite_dashboard.html admin_dashboard.html
```

## 3. Rebuild and restart only the app

```bash
cd /opt/pushkaralu/app
docker compose -f docker-compose.yml -f docker-compose.override.yml build app
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

`--no-deps` keeps `pushkaralu-mediamtx` and `pushkaralu-nginx` running.

## 4. Verify

```bash
curl -s http://127.0.0.1/health | head -c 300; echo
docker exec pushkaralu-mediamtx true && docker ps --format '{{.Names}} {{.Status}}'
docker logs --tail 30 pushkaralu-app
```

Then in a browser: log in, check live tiles play, open `/admin/diagnostics`.

## Rollback (about 1 minute)

```bash
cd /opt/pushkaralu/app && sudo cp -a "$B/." .
sudo rm -f src/stream_events.py src/stream_telemetry.py src/recording_continuity.py src/abr_transcoder.py static/monitor/js/egdms-player.js stream_diagnostics.html
docker image tag app-app:before-stream-reliability app-app:latest
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

## Settings (optional, in `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `STREAM_GRACE_SECONDS` | 20 | hold last frame before showing "waiting" (0 = old behaviour) |
| `PLAYER_LATENCY_TARGET_SECONDS` | 5 | viewer delay target |
| `PLAYBACK_PREFER_RAW` | 1 | 0 = old live/analyzed switching |
| `ABR_MAX_SESSIONS` | 4 | concurrent adaptive-quality transcoders (0 disables) |
| `ABR_IDLE_SECONDS` | 45 | stop a transcoder after this long without viewers |
| `ABR_ENCODE_720` | 1 | add a 720p rung for sources taller than 720p |
| `STREAM_EVENT_RETENTION_DAYS` | 30 | diagnostic log retention (only these logs are ever deleted) |
| `CONTINUITY_SCAN_DAYS` | 14 | how far back recordings are indexed |
| `DISK_WARN_FREE_GB` / `DISK_CRITICAL_FREE_GB` | 50 / 10 | disk alerts |

WebRTC is still available per browser with `?transport=webrtc`.

## Stage B (needs approval: restarts MediaMTX, ~5 s gap in all streams and recordings)

- `readTimeout: 20s` in `deployment/mediamtx.yml` (then set `MEDIAMTX_READ_TIMEOUT_SECONDS=20`): keeps 4G uplinks that stall 10–20 s from being dropped.
- Rename deprecated `runOnReady`/`runOnNotReady` to `runOnAvailable`/`runOnUnavailable`.
- Mount the MediaMTX config as a directory so edits take effect without stale-inode problems.
- Optional: publish SRT (`8890/udp`) for drones that support it (better than RTMP over lossy 4G).
