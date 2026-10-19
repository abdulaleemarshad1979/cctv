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
 * - attachQualityMenu(): Auto / per-level selector for adaptive HLS.
 * - report(): batched, best-effort diagnostics sent to /api/stream-events.
 */
(function () {
  "use strict";

  const P = {};

  P.hlsConfig = function (options) {
    const target = Math.max(2, Number((options && options.targetLatency) || 5));
    const abr = Boolean(options && options.abr);
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
      capLevelToPlayerSize: true,
      abrEwmaDefaultEstimate: 1000000,
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
  // restart its stream.  Cards need a data-id attribute.
  P.LiveTileGate = class {
    constructor(options) {
      this.onShow = options.onShow;
      this.onHide = options.onHide;
      this.stopDelayMs = options.stopDelayMs == null ? 15000 : options.stopDelayMs;
      this.visible = new Set();
      this.timers = {};
      this.observer = typeof IntersectionObserver === "undefined" ? null
        : new IntersectionObserver(entries => entries.forEach(e => this.update(e)),
          { rootMargin: options.rootMargin || "200px 0px" });
    }
    observe(card) {
      if (this.observer) this.observer.observe(card);
      else this.visible.add(card.getAttribute("data-id"));
    }
    unobserve(card) {
      const id = card.getAttribute("data-id");
      if (this.observer) this.observer.unobserve(card);
      this.visible.delete(id);
      clearTimeout(this.timers[id]);
      delete this.timers[id];
    }
    isVisible(id) {
      return !this.observer || this.visible.has(id);
    }
    update(entry) {
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

  function prefKey(camId) { return `egdms.quality.${camId}`; }
  function readPref(camId) {
    try { return window.localStorage.getItem(prefKey(camId)); } catch (e) { return null; }
  }
  function writePref(camId, value) {
    try {
      if (value === "auto") window.localStorage.removeItem(prefKey(camId));
      else window.localStorage.setItem(prefKey(camId), value);
    } catch (e) { /* storage unavailable: preference lasts for this page only */ }
  }

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
    hls.on(Hls.Events.LEVEL_SWITCHED, refreshAutoLabel);
    container.appendChild(select);
    const saved = readPref(camId);
    apply(saved && Array.from(select.options).some(o => o.value === saved) ? saved : "auto", false);
    return select;
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
