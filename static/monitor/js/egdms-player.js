/*
 * EGDMS live-player helpers shared by lite_dashboard.html and admin_dashboard.html.
 *
 * - hlsConfig(): a valid hls.js configuration that plays a steady ~5 s behind
 *   the drone, so short network hiccups are absorbed by the buffer instead of
 *   stalling (hls.js throws if second-based and count-based live-sync options
 *   are mixed).
 * - LiveTileGate: run live players only for tiles that are on screen.
 * - Backoff: bounded exponential retry delays with jitter.
 * - freezeFrame()/clearFreeze(): keep the last decoded frame on screen while a
 *   stream reconnects, instead of a black tile.  Nothing is written to any
 *   recording; this is display only.
 * - setOverlay()/clearOverlay(): the "Reconnecting…" badge.
 * - attachQualityMenu(): Auto / per-level selector for adaptive HLS; also
 *   remembers the measured bandwidth so the next player starts at the right
 *   quality instead of ramping up from a 1 Mbit/s guess.
 * - QualityGovernor: splits the measured network between all players on the
 *   page (full view first) so together they never ask for more than it
 *   carries, and tiles get a sharper picture when it does.
 * - report(): batched, best-effort diagnostics sent to /api/stream-events.
 */
(function () {
  "use strict";

  const P = {};

  // Starting bandwidth guess (bit/s) for Auto quality, best source first:
  // what this page measured on a stream in the last two minutes (capped by the
  // browser's own, more current, downlink estimate when it has one), else the
  // browser's estimate (discounted: it is an optimistic maximum), else
  // 1 Mbit/s. A tile that scrolls back into view, a reconnect or a switch to
  // full view then starts at a suitable level rather than at the bottom, and a
  // network that has since collapsed is not trusted for long.
  P.bandwidthEstimate = 0;
  P.bandwidthMeasuredAt = 0;
  function startingBandwidth() {
    const connection = typeof navigator !== "undefined" && navigator.connection;
    const downlink = connection && Number(connection.downlink);
    const browser = downlink > 0 ? Math.min(8e6, Math.max(3e5, downlink * 1e6 * 0.6)) : 0;
    const fresh = P.bandwidthEstimate > 0 && Date.now() - P.bandwidthMeasuredAt < 120000;
    if (fresh) return browser ? Math.min(P.bandwidthEstimate, browser * 2) : P.bandwidthEstimate;
    return browser || 1000000;
  }

  P.hlsConfig = function (options) {
    const target = Math.max(2, Number((options && options.targetLatency) || 5));
    const abr = Boolean(options && options.abr);
    // A QualityGovernor sets each player's cap itself (box size and network
    // share); hls.js's own size cap would undo it every second.
    const governed = Boolean(options && options.governed);
    return {
      enableWorker: true,
      // Regular (not partial-segment) HLS is far more tolerant of lossy
      // links; delay is controlled by the live-sync distance below.
      lowLatencyMode: false,
      // Hold playback ~(target - 1) s behind the newest segment (plus ~1 s
      // of segmenting = target end to end).  That gap is the jitter buffer;
      // keepLiveDelay() holds playback there.
      liveSyncDuration: Math.max(2, target - 1),
      // Last-resort jump if keepLiveDelay() cannot keep up.
      liveMaxLatencyDuration: target + 7,
      liveDurationInfinity: true,
      maxBufferLength: target + 5,
      maxMaxBufferLength: 20,
      backBufferLength: 10,
      startPosition: -1,
      // Adaptive bitrate: start conservatively, upgrade only on clear headroom.
      startLevel: -1,
      capLevelToPlayerSize: !governed,
      abrEwmaDefaultEstimate: startingBandwidth(),
      // React to a worsening network within a few seconds (a stall is worse
      // than a softer picture); upgrades stay cautious via abrBandWidthUpFactor.
      abrEwmaFastLive: 2,
      abrEwmaSlowLive: 6,
      abrBandWidthFactor: 0.9,
      abrBandWidthUpFactor: 0.65,
      abrMaxWithRealBitrate: true,
      // Fail fast on the adaptive master playlist so the player can fall back
      // to the direct stream; be patient with media requests.
      manifestLoadingTimeOut: abr ? 15000 : 10000,
      manifestLoadingMaxRetry: abr ? 0 : 4,
      manifestLoadingRetryDelay: 1000,
      levelLoadingMaxRetry: 4,
      levelLoadingRetryDelay: 1000,
      fragLoadingMaxRetry: 4,
      fragLoadingRetryDelay: 1000,
      highBufferWatchdogPeriod: 2,
      xhrSetup: function (xhr) { xhr.withCredentials = true; }
    };
  };

  // Runs live players only for tiles on screen.  A tile that leaves the
  // screen stops after stopDelayMs, so scrolling past briefly does not
  // restart its stream.  Cards need a data-id attribute.  Fails open: if the
  // browser reports no visibility within 3 s (throttled or embedded views),
  // every tile counts as visible until it does.
  P.LiveTileGate = class {
    constructor(options) {
      this.onShow = options.onShow;
      this.onHide = options.onHide;
      this.stopDelayMs = options.stopDelayMs == null ? 15000 : options.stopDelayMs;
      this.visible = new Set();
      this.observed = new Set();
      this.timers = {};
      this.reported = false;
      this.failOpen = false;
      this.failOpenTimer = null;
      this.observer = typeof IntersectionObserver === "undefined" ? null
        : new IntersectionObserver(entries => entries.forEach(e => this.update(e)),
          { rootMargin: options.rootMargin || "200px 0px" });
    }
    observe(card) {
      const id = card.getAttribute("data-id");
      this.observed.add(id);
      if (!this.observer) {
        this.visible.add(id);
        return;
      }
      this.observer.observe(card);
      if (!this.reported && !this.failOpenTimer) {
        this.failOpenTimer = setTimeout(() => {
          if (this.reported) return;
          this.failOpen = true;
          this.observed.forEach(tileId => this.onShow(tileId));
        }, 3000);
      }
    }
    unobserve(card) {
      const id = card.getAttribute("data-id");
      if (this.observer) this.observer.unobserve(card);
      this.observed.delete(id);
      this.visible.delete(id);
      clearTimeout(this.timers[id]);
      delete this.timers[id];
    }
    isVisible(id) {
      return !this.observer || this.failOpen || this.visible.has(id);
    }
    update(entry) {
      this.reported = true;
      this.failOpen = false;
      const id = entry.target.getAttribute("data-id");
      clearTimeout(this.timers[id]);
      delete this.timers[id];
      if (entry.isIntersecting) {
        this.visible.add(id);
        this.onShow(id);
        return;
      }
      this.visible.delete(id);
      this.timers[id] = setTimeout(() => {
        delete this.timers[id];
        if (!this.visible.has(id)) this.onHide(id);
      }, this.stopDelayMs);
    }
  };

  // Hold an HLS player near its live-sync point (the target delay).  hls.js
  // only adjusts speed in low-latency mode, which is off for robustness, so
  // drift is corrected here, and only with video that is really buffered:
  // hls.js keeps advancing liveSyncPosition on the clock even while the
  // drone's uplink is frozen, so "behind" alone would drain the buffer.
  //  - buffer thin (< 1 s): play 5% slower to stretch it through a hiccup
  //  - > 3 s behind with spare video buffered: jump forward inside the buffer
  //  - > 1 s behind with spare video buffered: play 10% faster
  // Call every couple of seconds.
  P.keepLiveDelay = function (hls, video) {
    if (!hls || !video || video.paused || video.seeking) return;
    const sync = hls.liveSyncPosition;
    if (typeof sync !== "number" || !(sync > 0)) return;
    const ranges = video.buffered;
    const bufferEnd = ranges.length ? ranges.end(ranges.length - 1) : video.currentTime;
    const ahead = bufferEnd - video.currentTime;
    const behind = sync - video.currentTime;
    let rate = video.playbackRate;
    if (ahead < 1) {
      rate = 0.95;
    } else if (behind > 3 && ahead > 3) {
      video.currentTime = Math.min(sync, bufferEnd - 1.5);
      rate = 1;
    } else if (behind > 1 && ahead > 2) {
      rate = 1.1;
    } else if (behind < 0.25 || ahead < 2) {
      rate = ahead < 1.5 ? 0.95 : 1;
    }
    if (video.playbackRate !== rate) video.playbackRate = rate;
  };

  P.Backoff = class {
    constructor(baseMs = 1000, maxMs = 15000) {
      this.baseMs = baseMs;
      this.maxMs = maxMs;
      this.attempts = 0;
    }
    next() {
      const raw = Math.min(this.maxMs, this.baseMs * Math.pow(2, this.attempts));
      this.attempts += 1;
      return Math.round(raw * (0.8 + Math.random() * 0.4));
    }
    reset() { this.attempts = 0; }
  };

  function ensureStyles() {
    if (document.getElementById("egdms-player-styles")) return;
    const style = document.createElement("style");
    style.id = "egdms-player-styles";
    style.textContent = `
      .egdms-freeze{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:4;pointer-events:none;background:#000}
      .egdms-overlay{position:absolute;left:50%;bottom:12%;transform:translateX(-50%);z-index:6;pointer-events:none;
        display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:6px;background:rgba(0,0,0,.72);
        color:#fff;font:600 12px/1.3 system-ui,sans-serif;white-space:nowrap;border:1px solid rgba(255,255,255,.25)}
      .egdms-overlay__dot{width:8px;height:8px;border-radius:50%;background:#f5a524;animation:egdms-pulse 1s ease-in-out infinite}
      .egdms-overlay__sub{font-weight:400;opacity:.8}
      @keyframes egdms-pulse{50%{opacity:.25}}
      @media (prefers-reduced-motion: reduce){.egdms-overlay__dot{animation:none}}
      .egdms-quality{position:absolute;top:8px;right:8px;z-index:7;font:600 11px system-ui,sans-serif;color:#fff;
        background:rgba(0,0,0,.6);border:1px solid rgba(255,255,255,.3);border-radius:4px;padding:2px 4px;opacity:.85}
      .egdms-quality:hover,.egdms-quality:focus{opacity:1}
    `;
    document.head.appendChild(style);
  }

  P.freezeFrame = function (container, video) {
    if (!container || !video || !video.videoWidth || video.readyState < 2) return null;
    ensureStyles();
    let canvas = container.querySelector(".egdms-freeze");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "egdms-freeze";
      canvas.setAttribute("aria-hidden", "true");
      container.appendChild(canvas);
    }
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      return canvas;
    } catch (e) {
      canvas.remove();
      return null;
    }
  };

  P.clearFreeze = function (container) {
    const canvas = container && container.querySelector(".egdms-freeze");
    if (canvas) canvas.remove();
  };

  P.setOverlay = function (container, text, sub) {
    if (!container) return;
    ensureStyles();
    let overlay = container.querySelector(".egdms-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "egdms-overlay";
      overlay.setAttribute("role", "status");
      overlay.innerHTML = '<span class="egdms-overlay__dot"></span><span class="egdms-overlay__text"></span><span class="egdms-overlay__sub"></span>';
      container.appendChild(overlay);
    }
    overlay.querySelector(".egdms-overlay__text").textContent = text;
    overlay.querySelector(".egdms-overlay__sub").textContent = sub || "";
  };

  P.clearOverlay = function (container) {
    const overlay = container && container.querySelector(".egdms-overlay");
    if (overlay) overlay.remove();
  };

  P.hasHeldFrame = function (container) {
    return Boolean(container && container.querySelector(".egdms-freeze"));
  };

  // A manual quality choice lasts for this browser session only; Auto is
  // the default every time, because network conditions change.
  function prefKey(camId) { return `egdms.quality.${camId}`; }
  function readPref(camId) {
    try { return window.sessionStorage.getItem(prefKey(camId)); } catch (e) { return null; }
  }
  function writePref(camId, value) {
    try {
      window.localStorage.removeItem(prefKey(camId));  // older builds kept it forever
      if (value === "auto") window.sessionStorage.removeItem(prefKey(camId));
      else window.sessionStorage.setItem(prefKey(camId), value);
    } catch (e) { /* storage unavailable: preference lasts for this page only */ }
  }
  try {
    Object.keys(window.localStorage).filter(k => k.startsWith("egdms.quality.")).forEach(k => window.localStorage.removeItem(k));
  } catch (e) { /* storage unavailable */ }

  function levelLabel(level, index, levels) {
    const height = level.height || 0;
    const tallest = Math.max.apply(null, levels.map(l => l.height || 0));
    if (height && height === tallest && height !== 720 && height !== 480) return `${height}p (source)`;
    return height ? `${height}p` : `Level ${index + 1}`;
  }

  P.attachQualityMenu = function (container, hls, camId, onChange) {
    if (!container || !hls || !hls.levels || hls.levels.length < 2) return null;
    ensureStyles();
    P.removeQualityMenu(container);
    const select = document.createElement("select");
    select.className = "egdms-quality";
    select.setAttribute("aria-label", "Video quality");
    select.title = "Video quality";
    const order = hls.levels.map((level, index) => ({ level, index }))
      .sort((a, b) => (b.level.height || 0) - (a.level.height || 0));
    const auto = document.createElement("option");
    auto.value = "auto";
    select.appendChild(auto);
    order.forEach(({ level, index }) => {
      const option = document.createElement("option");
      option.value = String(level.height || index);
      option.dataset.index = String(index);
      option.textContent = levelLabel(level, index, hls.levels);
      select.appendChild(option);
    });
    const refreshAutoLabel = () => {
      const current = hls.levels[hls.currentLevel];
      auto.textContent = current && current.height ? `Auto (${current.height}p)` : "Auto";
    };
    const apply = (value, persist) => {
      const option = Array.from(select.options).find(o => o.value === value);
      if (!option || value === "auto") {
        hls.currentLevel = -1;
        hls.nextLevel = -1;
        select.value = "auto";
      } else {
        hls.nextLevel = Number(option.dataset.index);
        select.value = value;
      }
      if (persist) writePref(camId, select.value);
      refreshAutoLabel();
      if (typeof onChange === "function") onChange(select.value);
    };
    select.addEventListener("change", () => apply(select.value, true));
    select.addEventListener("click", e => e.stopPropagation());
    // A manually chosen quality that keeps stalling (the network got worse)
    // goes back to Auto so the video does not stop.
    const video = container.querySelector("video");
    let stalls = [];
    if (video) {
      video.addEventListener("waiting", () => {
        if (select.value === "auto" || video.seeking) return;
        const now = Date.now();
        stalls = stalls.filter(t => now - t < 30000).concat(now);
        if (stalls.length < 3) return;
        stalls = [];
        apply("auto", true);
        P.setOverlay(container, "Slow network — switched to Auto quality");
        setTimeout(() => {
          const text = container.querySelector(".egdms-overlay__text");
          if (text && text.textContent.startsWith("Slow network")) P.clearOverlay(container);
        }, 4000);
      });
    }
    hls.on(Hls.Events.LEVEL_SWITCHED, refreshAutoLabel);
    // Remember what the network can do for the next player (see hlsConfig).
    hls.on(Hls.Events.FRAG_LOADED, () => {
      const measured = hls.bandwidthEstimate;
      if (measured > 0 && isFinite(measured)) {
        P.bandwidthEstimate = measured;
        P.bandwidthMeasuredAt = Date.now();
      }
    });
    container.appendChild(select);
    const saved = readPref(camId);
    apply(saved && Array.from(select.options).some(o => o.value === saved) ? saved : "auto", false);
    return select;
  };

  // Bitrate a level needs while it plays (hls.js measures the real one once
  // segments arrive; the playlist's average before that).
  function levelRate(level) {
    return level ? (level.averageBitrate || level.bitrate || 0) : 0;
  }

  // Highest level a tile may use. Like hls.js's player-size cap, the smallest
  // level that covers the box (in device pixels, at most 2x), plus one step
  // when the tile's share of the network pays for it: a tile shows more
  // detail than its pixel count suggests once the bitrate stops being the
  // limit (360p at 450 kbit/s is blocky even on a small tile). Never above
  // the share; never below the lowest level. With no measured share (null),
  // the box size alone, exactly as hls.js would.
  P.tileLevelCap = function (levels, boxWidth, boxHeight, share) {
    if (!levels || !levels.length) return -1;
    const square = Math.max(boxWidth || 0, boxHeight || 0);
    let fit = levels.length - 1;
    for (let i = 0; i < levels.length; i++) {
      if ((levels[i].width || 0) >= square || (levels[i].height || 0) >= square) { fit = i; break; }
    }
    if (share == null) return fit;
    let cap = Math.min(levels.length - 1, fit + 1);
    while (cap > 0 && levelRate(levels[cap]) > share) cap--;
    return cap;
  };

  // Network-aware quality for every live player on the page.
  //
  // hls.js measures each player on its own, so nine tiles each believe they
  // own the whole link and together ask for nine times what it carries: on a
  // weak network every tile stalls. Every 2 s (and at once when the full view
  // opens or closes, or the connection changes) the governor splits the
  // measured link instead:
  //  - the full-view player comes first and is never capped;
  //  - while a full view is open the tiles behind its backdrop drop to their
  //    lowest level, so the full view gets the bandwidth;
  //  - otherwise the tiles share what is left equally (see tileLevelCap);
  //  - one-quality (direct) streams and manual choices only use up budget.
  // hls.js still picks below each cap from its own measurements. The cap is
  // set while the manifest is parsed, before the first segment is chosen, so
  // a tile never starts above its share. Cost: O(players) every 2 s.
  P.QualityGovernor = class {
    constructor(options) {
      this.isFocused = (options && options.isFocused) || (() => false);
      this.headroom = 0.85;          // leave room for polls and bursts
      this.players = new Map();      // camera id -> { hls, container }
      this.timer = null;
      const connection = typeof navigator !== "undefined" && navigator.connection;
      if (connection && typeof connection.addEventListener === "function") {
        connection.addEventListener("change", () => this.apply());
      }
    }

    add(id, hls, container) {
      this.players.set(id, { hls, container });
      this.apply();
      if (!this.timer) this.timer = setInterval(() => { if (!document.hidden) this.apply(); }, 2000);
    }

    remove(id) {
      this.players.delete(id);
      if (!this.players.size && this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }
    }

    // What the link carries: the best rate any player measured recently (a
    // player downloading alone sees the whole link). Null until this page has
    // measured anything in the last two minutes.
    linkEstimate() {
      if (!(P.bandwidthMeasuredAt > 0) || Date.now() - P.bandwidthMeasuredAt > 120000) return null;
      let best = P.bandwidthEstimate > 0 ? P.bandwidthEstimate : 0;
      this.players.forEach(({ hls }) => {
        const measured = Number(hls.bandwidthEstimate);
        if (isFinite(measured) && measured > best) best = measured;
      });
      return best > 0 ? best : null;
    }

    apply() {
      if (!this.players.size) return;
      const link = this.linkEstimate();
      const dpr = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, 2);
      let focusOpen = false;
      let used = 0;
      const tiles = [];
      this.players.forEach(({ hls, container }, id) => {
        let levels;
        try { levels = hls.levels; } catch (e) { levels = null; }   // destroyed player
        if (!levels || !levels.length) return;
        if (this.isFocused(id)) {
          focusOpen = true;
          if (hls.autoLevelCapping !== -1) hls.autoLevelCapping = -1;
          used += levelRate(levels[Math.max(0, hls.loadLevel)]);
          return;
        }
        if (levels.length < 2 || !hls.autoLevelEnabled) {
          used += levelRate(levels[Math.max(0, hls.loadLevel)]);
          return;
        }
        tiles.push({ hls, container });
      });
      const share = link == null ? null : Math.max(0, link * this.headroom - used) / Math.max(1, tiles.length);
      tiles.forEach(({ hls, container }) => {
        const cap = focusOpen ? 0 : P.tileLevelCap(
          hls.levels,
          (container && container.clientWidth || 0) * dpr,
          (container && container.clientHeight || 0) * dpr,
          share
        );
        if (hls.autoLevelCapping !== cap) hls.autoLevelCapping = cap;
      });
    }
  };

  P.removeQualityMenu = function (container) {
    const menu = container && container.querySelector(".egdms-quality");
    if (menu) menu.remove();
  };

  // End-to-end delay estimate: wall clock minus the program-date-time of the
  // frame on screen (both playlists carry EXT-X-PROGRAM-DATE-TIME).
  P.measureLatency = function (hls) {
    if (!hls) return null;
    const playing = hls.playingDate;
    if (playing instanceof Date && !isNaN(playing)) {
      const seconds = (Date.now() - playing.getTime()) / 1000;
      if (seconds > -2 && seconds < 600) return Math.max(0, seconds);
    }
    return typeof hls.latency === "number" && isFinite(hls.latency) ? hls.latency : null;
  };

  const queue = [];
  let flushTimer = null;
  function flush() {
    flushTimer = null;
    if (!queue.length) return;
    const body = JSON.stringify({ events: queue.splice(0, 20) });
    try {
      fetch("/api/stream-events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        keepalive: true,
        body
      }).catch(() => {});
    } catch (e) { /* diagnostics are best effort */ }
    if (queue.length) flushTimer = setTimeout(flush, 2000);
  }
  P.report = function (camId, event, fields) {
    if (!camId || !event) return;
    queue.push(Object.assign({ camera_id: String(camId), event: String(event) }, fields || {}));
    if (queue.length > 60) queue.splice(0, queue.length - 60);
    if (!flushTimer) flushTimer = setTimeout(flush, 3000);
  };
  window.addEventListener("pagehide", flush);

  window.EGDMSPlayer = P;
})();
