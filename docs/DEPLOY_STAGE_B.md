# Stage B: one video per flight, smoother live video, MediaMTX settings

Two parts. Do them in order; they can be on different days.

| Part | What changes | Effect on users |
|---|---|---|
| **B1: app only** | Each flight is one video (however many files MediaMTX split it into), plays instantly and seeks anywhere, with no copies stored; recordings in pages; flying-drones badge; adaptive quality now includes the original stream and a 360p level for weak networks; steady ~5 s live delay; lazy live tiles; tab switches no longer restart video; compressed pages | Viewers see "Reconnecting…" for 10–30 s while the app restarts. MediaMTX, live ingest and recording keep running. |
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
FILES="lite_server.py admin_dashboard.html lite_dashboard.html static/monitor/js/egdms-player.js src/recording_flights.py src/abr_transcoder.py tests/test_recording_flights.py tests/test_recording_flights_api.py tests/test_video_playback_behavior.py tests/test_hls_playback_controller.py tests/test_stream_reliability.py docs/DEPLOY_STAGE_B.md"
git archive stage-b/mediamtx $FILES | ssh -p 2222 drone@117.216.215.157 'rm -rf /tmp/stage-b1 && mkdir -p /tmp/stage-b1 && tar -xf - -C /tmp/stage-b1'
```

### 2. Back up, check, install (server)

```bash
cd /opt/pushkaralu/app
B=/opt/pushkaralu/app/backups/stage-b1-$(date -u +%Y%m%dT%H%M%SZ)
sudo mkdir -p "$B/static/monitor/js" "$B/tests" "$B/src"
sudo cp -a lite_server.py admin_dashboard.html lite_dashboard.html "$B/"
sudo cp -a src/abr_transcoder.py "$B/src/"
sudo cp -a static/monitor/js/egdms-player.js "$B/static/monitor/js/"
sudo cp -a tests/test_video_playback_behavior.py tests/test_hls_playback_controller.py tests/test_stream_reliability.py "$B/tests/"
docker image tag app-app:latest app-app:before-stage-b1
echo "$B"
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

- **Recorded Videos**: one card per flight ("🧩 N parts" when MediaMTX split it),
  page numbers at the bottom, times in local time (they were UTC before). Open a
  long flight: it starts at once with its full length; seeking is instant and
  the time bar is steady. Download gives one MP4 of the whole flight.
- **Active Hub**: red badge with the number of drones flying (also in the browser
  tab title); video about 5 s behind; the quality menu offers the original plus
  lower levels; switch to Recorded Videos and back within a minute: the video
  must not restart.

### Roll back B1 (about 1 minute)

```bash
cd /opt/pushkaralu/app && sudo cp -a "$B/." . && sudo rm -f src/recording_flights.py tests/test_recording_flights.py tests/test_recording_flights_api.py
docker image tag app-app:before-stage-b1 app-app:latest
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

`data/outputs/recording_cache/` holds only small fragment indexes and thumbnails
(a few KB per file) and can be deleted at any time. Original recordings are
never changed or copied.

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
| Archive of 92 files | 52 flights, 3 server pages (24/24/4), thumbnails load as you scroll, no `<video>` elements |
| Flight of 30 fragments (2 min 16 s) | starts in ~0.1 s with full duration, plays across all 30 files, seeks in 0.03–0.3 s, download is one 30 MB MP4, nothing stored |
| Real MediaMTX recording (7.5 min) | starts in 0.6 s, normal speed |
| Fragment index vs ffprobe | identical durations; a 2-min MediaMTX file indexes in 5 ms, once |
| Search "2026-10-19 14:30" | finds the flight by local time |
| Flying badge | "4" on Active Hub and Active Feeds, "(4)" in the tab title |
| Adaptive quality, 720p drone, 800 kbit/s network | Auto drops to 360p, **0 stalls**, delay 5.2 s; climbs back when the network recovers |
| Manual 720p on the slow network | back to Auto after 3 stalls, with a "Slow network" notice |
| Before this change | the original-quality level was missing from adaptive quality (FFmpeg drops a copied stream without a bitrate) |
| Test suite | 165 passed; 1 pre-existing failure (needs real recordings on disk) |
