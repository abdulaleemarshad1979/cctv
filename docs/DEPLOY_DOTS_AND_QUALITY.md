# Dots only while counting, network-aware viewing quality (Stage D)

Changes only the **app container** and the dashboards. MediaMTX and nginx keep
running; live ingest and recording are never touched. Viewers see
"Reconnecting…" while the app restarts (10–30 s).

As before: do not `git pull` on the server (it has hand edits). Copy only the
files listed below.

## What changed

### Head dots belong to Counting mode

* The **Dots** button is gone from both dashboards. In **Counting** mode a dot
  appears on every counted person by itself as soon as a camera is counted; in
  **Viewing** mode there are never dots.
* Switching mode removes or shows the dots at once (before, up to a second
  later). A browser that had switched dots "off" with the old button shows them
  again: that stored choice is deleted.
* Every open dashboard follows a mode switch made on another screen on its next
  1-second poll (`/cameras` now carries an `X-Counting-Mode` header), so dots
  and counts appear or vanish everywhere, not only on the screen that clicked.
* In Viewing mode, or for a tile that is off screen, the dots layer is freed
  completely: no canvas, no observer, no timer, no `/heads` request.

### Viewing quality follows the network

hls.js measures each player on its own, so a grid of nine tiles each believed it
had the whole link and together asked for nine times what it carries: on a weak
network every tile stalled, and on a good one every tile stayed at the size-matched
360p even with bandwidth to spare. The players now share one budget
(`EGDMSPlayer.QualityGovernor`, re-evaluated every 2 s and on any full-view or
connection change):

| Situation | Before | Now |
|---|---|---|
| Good network, grid of tiles | each tile capped to its box size (360p on a typical 1080p screen) | one step sharper (480p) when each tile's share of the measured link pays for it |
| Weak network, grid of tiles | every tile picks from its own (overstated) estimate, all stall together | each tile capped to its share of the link, so together they fit |
| Full view open | the tiles behind the backdrop keep downloading at their usual quality | full view uncapped and served first; tiles behind it drop to their lowest quality at once and **stop after 10 s** (holding their last frame), restarting when the full view closes |
| Nothing measured yet | box-size cap | same box-size cap (no guessing) |

Measured locally (two synthetic 1080p drones, MediaMTX, the real app and the
real dashboards in a browser): a 302 px tile moved from 360p to 480p; opening the
full view took drone 1 to 1080p within 4 s while drone 2 dropped to 360p and
stopped at 10 s; after closing the full view drone 2 was playing again when
checked 9 s later.
The budget split itself was checked with nine simulated tiles: 360p each on a
10 Mbit/s link, 480p each on 20 Mbit/s, and with a full view open the full
view is uncapped while the others are held at 360p.

### Less CPU per adaptive-quality viewer stream

The transcoder decodes the drone stream once and now scales each rung from the
rung above it (1080 → 720 → 480 → 360) instead of each from the full picture.
Measured on a synthetic 1080p25 5 Mbit/s stream on this repo's 4-core box: **72–73 %
→ 68 % of one core per watched camera**, segments and keyframes still aligned
across rungs, picture unchanged (SSIM against a Lanczos reference 0.9817 vs
0.9817 at 480p).

### Counting

The neural network itself is already optimised (frozen, fused CPU graph,
duty-cycled), and accounts for nearly all of an analyzer's time; it is not
changed. Around it:

| Change | Effect |
|---|---|
| Broadcast-overlay cleaning examines only the top/bottom 15 % bands it cleans | 0.37 → 0.17 ms per pass without an overlay, 3.9 → 3.0 ms with one; output byte-identical (tested on 140 random frames) |
| Mode check by each analyzer: one kept-alive connection every 2 s instead of a new connection every 0.5 s | 8 → 2 requests/s for 4 analyzers; stats replies still carry the mode instantly while counting, and the server stops analyzers itself on a switch to Viewing |
| Removed the preprocessing self-test at startup | standalone runs only (the server already disabled it for its workers) |

Not changed, worth knowing: each analyzer still decodes every frame of its
stream (about 15 % of a core for 1080p25). OpenCV does not pass decoder options
through, so reducing that needs a different capture layer; out of scope here.

## Files changed

| File | Change |
|---|---|
| `lite_dashboard.html`, `admin_dashboard.html` | no Dots button; dots follow the mode; follow other screens' mode switches; quality governor; full view pauses the hidden grid |
| `static/monitor/js/egdms-dots.js` | no switch; frees hidden layers; idle timer stops |
| `static/monitor/js/egdms-player.js` | `QualityGovernor`, `tileLevelCap`, `governed` hls.js config |
| `lite_server.py` | `X-Counting-Mode` header on `/cameras` |
| `src/abr_transcoder.py` | cascaded scaling |
| `src/density_filter.py` | band-only overlay cleaning |
| `infer.py` | mode-check interval and connection reuse; self-test removed |
| `tests/…` | updated and new tests |

## Deploy

```bash
cd /opt/pushkaralu/app && git checkout <branch>
FILES="lite_dashboard.html admin_dashboard.html static/monitor/js/egdms-dots.js static/monitor/js/egdms-player.js lite_server.py src/abr_transcoder.py src/density_filter.py infer.py docs/DEPLOY_DOTS_AND_QUALITY.md"
tar -cf - $FILES | ssh -p 2222 drone@117.216.215.157 'mkdir -p /tmp/stage-d && tar -xf - -C /tmp/stage-d'
```

On the server (back up first, as in Stage A):

```bash
cd /opt/pushkaralu/app && sudo cp -a /tmp/stage-d/. . && sudo chown root:root lite_dashboard.html admin_dashboard.html
docker compose -f docker-compose.yml -f docker-compose.override.yml build app
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```

No new settings; do not replace the server's `.env`.

## Verify

```bash
curl -s http://127.0.0.1/health | head -c 300; echo
docker logs --tail 40 pushkaralu-app | grep -E "Traceback|ERROR"
```

In the browser (hard refresh; the dashboards load the new scripts by their
new `?v=` hashes):

* the header has no **Dots** button;
* **Counting**: a red dot on each counted person; **Viewing**: none, and the
  dots return by themselves when Counting is selected again — also on a second
  screen that did not click;
* the quality menu on a tile reads *Auto (480p)* on a good connection;
* open a full view: it climbs to the source quality; after 10 s the grid behind
  it shows still frames; close it and the tiles play again within a few seconds.

## Rollback

```bash
cd /opt/pushkaralu/app && sudo cp -a "$B/." .   # $B from the backup step
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps app
```
