# Counting reliability, head dots, "Drone Monitoring Portal" (Stage C)

Changes only the **app container**. MediaMTX and nginx keep running; live
ingest and recording are never touched. Viewers see "Reconnecting…" while the
app restarts (10–30 s).

As with Stage A: do not `git pull` on the server (it has hand edits). Copy only
the files listed below.

## What was wrong, and what changed

### "Counting unavailable"

The production server was not available while this was written, so these are
the causes found by reading the code and reproducing them on a local copy of
the stack (MediaMTX + app + synthetic drones), not confirmed from your logs.
Check `docker logs pushkaralu-app` and `data/outputs/logs/<drone>.log` after
deploying: the tile now states which one applies.

| Cause found in the code | Fix |
|---|---|
| `MAX_CONCURRENT_AI_FEEDS=4` was documented but **never enforced**: every online drone started its own model process (~0.5 GB RAM, 2 busy CPU threads each). Many online drones on a small server can exhaust memory/CPU, after which workers are killed. | `src/analysis_scheduler.py`: at most N analyzers run; the rest show **Counting queued** and start automatically, oldest first, when a slot frees up. Cores are shared between running analyzers (`AI_TORCH_THREADS`). |
| A worker that ended (crash, out-of-memory kill, SIGSEGV) left the tile on "Counting unavailable" **for ever**; only the next source reconnect started a new one. | Exited workers are restarted with 2, 4, 8 … 60 s back-off; the tile shows *Counting restarting…* and the reason ("killed, usually out of memory", "segmentation fault"). After 6 failures in 5 minutes it stops and says so, retrying every 5 min. |
| A worker whose model failed to load kept playing video but never counted: the tile said "Analysis starting…" for ever. | The worker now exits with a distinct code and a one-line reason; the dashboard shows it (e.g. *Model file 'dm_count/pretrained_models/model_nwpu.pth' is missing on the server*). The server re-checks every 30 s and starts counting by itself once the file is installed. |
| A worker that hung (alive, no counts) was never noticed. | No first count within 150 s, or no new count for 120 s, restarts it. |
| An exception on one frame ended the inference thread silently. | The thread restarts with fresh state; 5 crashes in a minute end the worker so the server restarts it. |
| SIGSEGV after stream reconnects (seen in Stage A tests; not reproducible locally, but the code had a race that fits): `DroneStreamHandler` released the `cv2.VideoCapture` from one thread while another was inside `grab()`, and read `cap.get()` from a third. | Only the grabber thread touches/releases the capture; resolution/fps are cached; `release()` waits for the grabber. |

> `docker-compose.yml` mounts `./dm_count/pretrained_models`. If that folder is
> missing on the host Docker creates it **empty**, and counting cannot start.
> Check with `ls -l dm_count/pretrained_models/model_nwpu.pth`.

### Head dots

The dots already existed in `infer.py`, but only inside the server-rendered
video, which is switched off (`RENDER_VIDEO_OVERLAYS=0`, raw playback). They
never reached the browser. Now:

* the worker publishes where it counted each head (4 bytes per head, normalised
  to the frame) with the capture time of the frame;
* `GET /cameras/<id>/heads` serves the last ~30 s of results as binary, only
  when they change, only for tiles on screen;
* the browser draws one dot per person on a canvas over the video
  (`static/monitor/js/egdms-dots.js`), correct for `object-fit: cover`, tile and
  full-screen view, and **matched to the frame on screen**: viewers play ~5 s
  behind live, so the dots recorded for that frame are drawn, not the newest;
* a **Dots** button in the header switches them on/off (remembered per browser).

Dot positions inside the density map are refined to sub-cell accuracy.

### Efficiency

| Change | Effect (measured on this repo's local stack, 4-core box) |
|---|---|
| Analyzer rests between passes (`COUNT_MAX_DUTY=0.5`), no re-encoded "analyzed" video, capture converts ~4 frames/s instead of 25, no idle polling | One analyzer: **210 % → 93 % CPU**; results every ~1.9 s instead of ~0.9 s (set `COUNT_MAX_DUTY=0.9` for the old rate). Same stream, same model, 2 threads. |
| No second MediaMTX started inside the app container (it downloaded a binary at import and rewrote `mediamtx.yml`) | Faster, quieter start when `MEDIAMTX_HOST` is not localhost. |
| `/cameras` sends no forecast for cameras without a live count; unchanged tiles are not re-rendered each second | Payload scales with cameras counted, not configured. |
| Adaptive quality: transcoders run at lower priority with bounded x264 threads; the **full-screen view can take a slot from a grid tile**; a tile that fell back to the direct stream is upgraded when opened full-screen; the measured bandwidth is remembered so a restarted player starts at the right quality (browser `downlink` estimate otherwise); faster reaction to a worsening network. | Slots end up where quality matters; no more starting at the bottom after every scroll/reconnect. |

### Name

The site is now **Drone Monitoring Portal** (page titles, headers, login page,
diagnostics page, API title).

## Files changed

| File | Change |
|---|---|
| `lite_server.py` | scheduler glue, preflight, restart, heads store + endpoint, ABR priority, `/cameras` trim, title |
| `src/analysis_scheduler.py` *(new)* | cap/queue/restart/back-off policy |
| `infer.py`, `config.py`, `src/drone_stream.py`, `src/optical_flow.py`, `src/counting_accuracy.py` | worker supervision, dots payload, duty cycle, capture fix, sub-cell dots |
| `src/abr_transcoder.py`, `static/monitor/js/egdms-player.js` | adaptive-quality changes |
| `static/monitor/js/egdms-dots.js` *(new)* | dots drawing |
| `lite_dashboard.html`, `admin_dashboard.html`, `login.html`, `stream_diagnostics.html` | dots, status wording, rename |
| `tools/refresh_asset_versions.py` *(new)* | refreshes the `?v=` hashes after editing a script |
| `tests/…` | new and updated tests |

## Deploy

```bash
cd /opt/pushkaralu/app && git checkout <branch>
FILES="lite_server.py infer.py config.py lite_dashboard.html admin_dashboard.html login.html stream_diagnostics.html src/analysis_scheduler.py src/abr_transcoder.py src/drone_stream.py src/optical_flow.py src/counting_accuracy.py static/monitor/js/egdms-player.js static/monitor/js/egdms-dots.js tools/refresh_asset_versions.py docs/DEPLOY_COUNTING_AND_DOTS.md .env.example"
tar -cf - $FILES | ssh -p 2222 drone@117.216.215.157 'mkdir -p /tmp/stage-c && tar -xf - -C /tmp/stage-c'
```

On the server (back up first, as in Stage A):

```bash
cd /opt/pushkaralu/app && sudo cp -a /tmp/stage-c/. . && sudo chown root:root lite_dashboard.html admin_dashboard.html
ls -l dm_count/pretrained_models/model_nwpu.pth        # must exist
docker compose -f docker-compose.yml -f docker-compose.override.yml build app
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

Do **not** replace the server's `.env`; add the new optional settings from
`.env.example` if you want to change a default.

## Verify

```bash
curl -s http://127.0.0.1/health | head -c 300; echo
docker logs --tail 40 pushkaralu-app | grep -E "MediaMTX|Analysis|FATAL"
```

In the browser (hard refresh): the tab reads *Drone Monitoring Portal*; with a
live drone the **Dots** button is red and a red dot sits on each counted person;
a drone beyond the cap shows *Counting queued* and a short reason on the tile.

## Settings (optional, `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_CONCURRENT_AI_FEEDS` | 4 | analyzers running at once; others queue |
| `AI_TORCH_THREADS` | cores ÷ cap | CPU threads per analyzer |
| `COUNT_MAX_DUTY` | 0.5 CPU / 0.9 GPU | share of time an analyzer may keep its cores busy |
| `COUNT_CAPTURE_INTERVAL_SECONDS` | 0.25 | frame conversion interval when nothing renders the frames |
| `ANALYTICS_START_TIMEOUT_SECONDS` | 150 | restart a worker with no first count |
| `ANALYTICS_RESTART_AFTER_STALE_SECONDS` | 120 | restart a worker whose counts stopped |
| `HEAD_SYNC_OFFSET_MS` | 700 | dots-to-video time alignment |
| `HEAD_HISTORY_SECONDS` | 30 | how long dot results are kept for alignment |
| `ABR_MAX_SESSIONS` | 4 | adaptive-quality transcoders |

## Limits to know

* Dots mark where the density model counts a head. In very dense crowds several
  heads share one density cell, so dots there are spread inside the crowd rather
  than placed on individual faces; sparse/medium crowds land on the heads. Only
  YOLO (GPU or `CPU_USE_HYBRID=1`) detects individual people.
* Dots are drawn for the frame on screen, but an analysis takes ~1–2 s, so
  fast-moving people can be a few pixels off their dot.
* Counting more than `MAX_CONCURRENT_AI_FEEDS` drones needs more CPU/RAM (or a
  GPU); the queue is the honest alternative to crashing everything.

## Rollback

```bash
cd /opt/pushkaralu/app && sudo cp -a "$B/." .   # $B from the backup step
sudo rm -f src/analysis_scheduler.py static/monitor/js/egdms-dots.js
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```
