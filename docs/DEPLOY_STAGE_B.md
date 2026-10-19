# Stage B: recordings archive, smoother live video, MediaMTX settings

Two parts. Do them in order; they can be on different days.

| Part | What changes | Effect on users |
|---|---|---|
| **B1: app only** | Recordings shown per flight (joined, seekable, no flicker), lazy-loaded archive and live tiles, steady ~5 s live delay, tab switches no longer restart video | Viewers see "Reconnecting…" for 10–30 s while the app restarts. MediaMTX, live ingest and recording keep running. |
| **B2: MediaMTX** | `readTimeout: 20s` (4G stalls of up to 20 s no longer drop the drone), hook names renamed, 12-segment HLS window, config folder mount | **About 5 s gap in every live stream and recording.** Do it when no drone is flying. |

As in Stage A: do not run `git pull`, `git reset` or `git stash` on the server. Copy
only the files listed. Commands marked *workstation* run in the
`abdul-aleem-arshad@` terminal; everything else runs on the server (`drone@drone`).

---

## B1: app only

### 1. Copy the files (workstation)

`git archive` reads the files straight from the branch; nothing is checked out.

```bash
cd /opt/pushkaralu/app
FILES="lite_server.py admin_dashboard.html lite_dashboard.html static/monitor/js/egdms-player.js src/recording_flights.py tests/test_recording_flights.py tests/test_recording_flights_api.py tests/test_video_playback_behavior.py tests/test_hls_playback_controller.py docs/DEPLOY_STAGE_B.md"
git archive stage-b/mediamtx $FILES | ssh -p 2222 drone@117.216.215.157 'rm -rf /tmp/stage-b1 && mkdir -p /tmp/stage-b1 && tar -xf - -C /tmp/stage-b1'
```

### 2. Back up, check, install (server)

```bash
cd /opt/pushkaralu/app
B=/opt/pushkaralu/app/backups/stage-b1-$(date -u +%Y%m%dT%H%M%SZ)
sudo mkdir -p "$B/static/monitor/js" "$B/tests"
sudo cp -a lite_server.py admin_dashboard.html lite_dashboard.html "$B/"
sudo cp -a static/monitor/js/egdms-player.js "$B/static/monitor/js/"
sudo cp -a tests/test_video_playback_behavior.py tests/test_hls_playback_controller.py "$B/tests/"
docker image tag app-app:latest app-app:before-stage-b1
echo "$B"
df -h /opt/pushkaralu/app/data    # joined playback copies use up to 10 GB here
sudo cp -a /tmp/stage-b1/. . && sudo chown root:root lite_dashboard.html admin_dashboard.html
```

### 3. Rebuild and restart only the app (server)

```bash
cd /opt/pushkaralu/app
docker compose -f docker-compose.yml -f docker-compose.override.yml build app
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
docker ps --format '{{.Names}} {{.Status}}'
```

`--no-deps` keeps `pushkaralu-mediamtx` (live ingest and recording) running.

### 4. Check in the browser

- **Recorded Videos**: cards are now flights ("🧩 N parts" when joined); times are
  local time (they were UTC before); open a long flight, wait for
  "Preparing video…", then seek: the time bar should be steady.
- **Active Hub**: video plays about 5 s behind; switch to Recorded Videos and
  back within a minute: the video must not restart.

### Roll back B1 (about 1 minute)

```bash
cd /opt/pushkaralu/app && sudo cp -a "$B/." . && sudo rm -f src/recording_flights.py tests/test_recording_flights.py tests/test_recording_flights_api.py
docker image tag app-app:before-stage-b1 app-app:latest
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

The joined copies live in `data/outputs/recording_cache/` and can be deleted at
any time (`sudo rm -rf data/outputs/recording_cache`). Originals are never changed.

---

## B2: MediaMTX (quiet time only, about 5 s gap)

### 1. Copy the files (workstation)

The server's `docker-compose.yml` and `deployment/mediamtx.yml` have hand edits, so
this also sends the Stage A versions to compare against.

```bash
cd /opt/pushkaralu/app
rm -rf /tmp/stage-b2 && mkdir -p /tmp/stage-b2/old
git show origin/main:docker-compose.yml > /tmp/stage-b2/old/docker-compose.yml
git show origin/main:deployment/mediamtx.yml > /tmp/stage-b2/old/mediamtx.yml
git show stage-b/mediamtx:docker-compose.yml > /tmp/stage-b2/docker-compose.yml
git show stage-b/mediamtx:deployment/mediamtx.yml > /tmp/stage-b2/mediamtx.yml
tar -C /tmp/stage-b2 -cf - . | ssh -p 2222 drone@117.216.215.157 'rm -rf /tmp/stage-b2 && mkdir -p /tmp/stage-b2 && tar -xf - -C /tmp/stage-b2'
```

### 2. Check nothing else changed on the server

```bash
cd /opt/pushkaralu/app
diff /tmp/stage-b2/old/docker-compose.yml docker-compose.yml && echo COMPOSE-SAME
diff /tmp/stage-b2/old/mediamtx.yml deployment/mediamtx.yml && echo MEDIAMTX-SAME
```

**Continue only if both print `…-SAME`.** If either prints differences, stop and
send them to me. The new files would otherwise undo those hand edits.

### 3. Back up (server)

```bash
cd /opt/pushkaralu/app
B2=/opt/pushkaralu/app/backups/stage-b2-$(date -u +%Y%m%dT%H%M%SZ)
sudo mkdir -p "$B2/deployment"
sudo cp -a docker-compose.yml .env "$B2/" && sudo cp -a deployment/mediamtx.yml "$B2/deployment/"
echo "$B2"
```

### 4. Install and restart MediaMTX (this is the ~5 s gap)

Write each file next to the old one and `mv` it into place. MediaMTX reads its config
the moment it changes, and a half-copied file would stop it.

```bash
cd /opt/pushkaralu/app
sudo cp /tmp/stage-b2/mediamtx.yml deployment/mediamtx.yml.new && sudo mv deployment/mediamtx.yml.new deployment/mediamtx.yml
sudo cp /tmp/stage-b2/docker-compose.yml docker-compose.yml.new && sudo mv docker-compose.yml.new docker-compose.yml
grep -q '^MEDIAMTX_READ_TIMEOUT_SECONDS=' .env \
  && sudo sed -i 's/^MEDIAMTX_READ_TIMEOUT_SECONDS=.*/MEDIAMTX_READ_TIMEOUT_SECONDS=20/' .env \
  || echo 'MEDIAMTX_READ_TIMEOUT_SECONDS=20' | sudo tee -a .env
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps mediamtx app
```

### 5. Verify (server)

```bash
docker ps --format '{{.Names}} {{.Status}}'
docker logs --since 2m pushkaralu-mediamtx 2>&1 | grep -E "configuration loaded|deprecated|ERR"
docker exec pushkaralu-mediamtx curl -s http://127.0.0.1:9997/v3/config/global/get | grep -o '"readTimeout":"[^"]*"\|"hlsSegmentCount":[0-9]*'
docker logs --since 2m pushkaralu-mediamtx 2>&1 | grep -E "is publishing|recording"
```

Expected: `configuration loaded from /config/mediamtx.yml`, no `deprecated` lines,
`"readTimeout":"20s"`, `"hlsSegmentCount":12`, and drones publishing and
recording again within a few seconds.

### Roll back B2 (another ~5 s gap)

```bash
cd /opt/pushkaralu/app
sudo cp "$B2/deployment/mediamtx.yml" deployment/mediamtx.yml.new && sudo mv deployment/mediamtx.yml.new deployment/mediamtx.yml
sudo cp "$B2/docker-compose.yml" docker-compose.yml.new && sudo mv docker-compose.yml.new docker-compose.yml
sudo cp "$B2/.env" .env
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps mediamtx app
```

### Editing the MediaMTX config later

After B2, the whole `deployment/` folder is mounted, so MediaMTX applies a saved
`mediamtx.yml` within a second (global settings briefly restart its listeners).
Always write a complete new file and `mv` it into place; never edit it in place.

---

## Local test results (2026-10-19)

Local stack: MediaMTX 1.19.3 with the B2 config and folder mount, this branch's app,
ffmpeg "drones" over RTMP.

| Check | Result |
|---|---|
| Drone uplink frozen 15 s, current config | dropped at 10 s (`i/o timeout`), publisher gave up |
| Drone uplink frozen 15 s, B2 config | stays connected, no offline hook |
| Drone uplink frozen 4 s, B2 config | recording continues in **one file**; viewer: one short pause, then back at ~5 s delay |
| Page refreshes, logins, tab switches during recording | recording unaffected (one continuous file, 9+ min) |
| Config replaced with `mv` while running | reloaded live (`reloading configuration (file changed)`) |
| Live delay, 3 tiles, 30 s | steady 3.9–4.3 s behind the live edge (≈5 s end to end) |
| Switch to Recorded Videos for 25 s and back | same players, no restart |
| Off-screen tiles | no player until scrolled into view; stopped 15 s after leaving view |
| Archive of 88 files | 48 flight cards, 24 rendered at a time, thumbnails load as you scroll, no `<video>` elements |
| Flight of 30 fragments (2 min 16 s) | joined in ~1 s, duration known at once, seeks in 0.1–0.2 s, download is one MP4 |
| Test suite | 157 passed; 1 pre-existing failure (needs real recordings on disk) |
