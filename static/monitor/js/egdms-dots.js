/*
 * Head dots: one dot on every counted person, drawn over the live video.
 *
 * The analyzer publishes where it counted each head as compact binary
 * (GET /cameras/<id>/heads: records of uint32 seq, uint32 count, float64
 * capture time, then uint16 x/y pairs spanning the frame). The dots are drawn
 * here on a canvas over the video instead of being burned into the stream, so
 * the video stays the untouched raw feed (no re-encode, no extra delay) and
 * the dots can be switched off.
 *
 * Viewers watch several seconds behind live, so a short history of results is
 * kept and the dots recorded for the frame on screen are drawn (matched by the
 * stream's program date-time), not the newest ones.
 *
 * Cost: one request per camera per new result (not per poll), only for tiles
 * on screen; one canvas redraw whenever the matching result, the box size or
 * the video size changes.
 */
(function () {
  "use strict";

  const D = {};
  const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

  // Where the video picture sits inside its box for object-fit cover/contain.
  D.contentRect = function (boxW, boxH, srcW, srcH, fit) {
    if (!(boxW > 0 && boxH > 0 && srcW > 0 && srcH > 0)) return null;
    if (fit === "fill") return { x: 0, y: 0, w: boxW, h: boxH };
    const scale = fit === "cover"
      ? Math.max(boxW / srcW, boxH / srcH)
      : Math.min(boxW / srcW, boxH / srcH);
    const w = srcW * scale;
    const h = srcH * scale;
    return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
  };

  // Concatenated records: 16-byte header (uint32 seq, uint32 count, float64
  // capture epoch seconds, little endian) + count uint16 x/y pairs. Returns an
  // array (oldest first) or null when the body is malformed.
  const RECORD_HEADER = 16;
  D.decodeRecords = function (buffer) {
    if (!buffer || buffer.byteLength < RECORD_HEADER) return null;
    const view = new DataView(buffer);
    const records = [];
    let offset = 0;
    while (offset < buffer.byteLength) {
      if (offset + RECORD_HEADER > buffer.byteLength) return null;
      const seq = view.getUint32(offset, true);
      const count = view.getUint32(offset + 4, true);
      const ts = view.getFloat64(offset + 8, true) * 1000;
      const start = offset + RECORD_HEADER;
      const end = start + count * 4;
      if (end > buffer.byteLength) return null;
      let points;
      if (LITTLE_ENDIAN) {
        points = new Uint16Array(buffer, start, count * 2);
      } else {
        points = new Uint16Array(count * 2);
        for (let i = 0; i < points.length; i++) points[i] = view.getUint16(start + i * 2, true);
      }
      records.push({ seq, count, ts, points });
      offset = end;
    }
    return records;
  };

  // Which result to draw for the frame on screen: the one captured closest to
  // it, so the dots are never more than half an analysis interval out. With no
  // playback time (WebRTC) the newest result. Nothing when the closest result
  // is more than 15 s from the frame: it no longer describes it.
  D.pickRecord = function (records, playMs) {
    if (!records || !records.length) return null;
    if (playMs == null) return records[records.length - 1];
    let chosen = null;
    let best = Infinity;
    for (let i = records.length - 1; i >= 0; i--) {
      const gap = Math.abs(records[i].ts - playMs);
      if (gap < best) { best = gap; chosen = records[i]; }
      else if (records[i].ts < playMs) break;   // older ones only get further away
    }
    return best > 15000 ? null : chosen;
  };

  // Dot radius in CSS px: big and clear for a few people, small enough in a
  // dense crowd that neighbouring dots stay distinguishable.
  D.dotRadius = function (count, w, h) {
    if (!(count > 0) || !(w > 0) || !(h > 0)) return 3;
    const areaPerHead = (w * h) / count;
    return Math.min(4.5, Math.max(1.3, 0.28 * Math.sqrt(areaPerHead)));
  };

  const STORAGE_KEY = "egdms.headDots";
  D.readEnabled = function () {
    try { return window.localStorage.getItem(STORAGE_KEY) !== "off"; } catch (e) { return true; }
  };
  D.writeEnabled = function (on) {
    try { window.localStorage.setItem(STORAGE_KEY, on ? "on" : "off"); } catch (e) { /* session only */ }
  };

  function ensureStyles() {
    if (document.getElementById("egdms-dots-styles")) return;
    const style = document.createElement("style");
    style.id = "egdms-dots-styles";
    style.textContent =
      ".egdms-dots{position:absolute;inset:0;width:100%;height:100%;z-index:5;pointer-events:none}" +
      ".egdms-dots-toggle{background:transparent;border:1px solid #cbd5e1;border-radius:9999px;padding:7px 14px;" +
      "font:700 12px system-ui,sans-serif;color:#475569;cursor:pointer;display:inline-flex;align-items:center;gap:6px}" +
      ".egdms-dots-toggle[aria-pressed=true]{background:#dc2626;border-color:#dc2626;color:#fff}" +
      ".egdms-dots-toggle::before{content:'';width:8px;height:8px;border-radius:50%;background:currentColor}";
    document.head.appendChild(style);
  }

  // One canvas per camera container. The container is a stable element, but
  // the dashboards clear its children when a player restarts, so the canvas is
  // looked up (and re-attached) on every use.
  class Layer {
    constructor(container) {
      this.container = container;
      this.records = [];     // recent results, oldest first
      this.shown = null;     // the result currently drawn
      this.drawnSize = "";   // box + video size of the last draw
      this.raf = 0;
      this.resizeObserver = null;
      this.watchedVideo = null;
      this.onVideoResize = () => this.requestDraw();
      this.canvas = null;
    }

    attach() {
      if (!this.container || !this.container.isConnected) return null;
      ensureStyles();
      let canvas = this.container.querySelector(":scope > .egdms-dots");
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.className = "egdms-dots";
        canvas.setAttribute("aria-hidden", "true");
        this.container.appendChild(canvas);
        this.drawnSize = "";
      } else if (canvas !== this.container.lastElementChild) {
        this.container.appendChild(canvas);  // stay above a newly added <video>
      }
      this.canvas = canvas;
      if (!this.resizeObserver && typeof ResizeObserver !== "undefined") {
        this.resizeObserver = new ResizeObserver(() => this.requestDraw());
        this.resizeObserver.observe(this.container);
      }
      const video = this.container.querySelector("video");
      if (video !== this.watchedVideo) {
        if (this.watchedVideo) {
          this.watchedVideo.removeEventListener("resize", this.onVideoResize);
          this.watchedVideo.removeEventListener("loadedmetadata", this.onVideoResize);
        }
        this.watchedVideo = video;
        if (video) {
          video.addEventListener("resize", this.onVideoResize);
          video.addEventListener("loadedmetadata", this.onVideoResize);
        }
      }
      return canvas;
    }

    needsAttach() {
      return !this.canvas || !this.canvas.isConnected ||
        this.watchedVideo !== this.container.querySelector("video");
    }

    // Add newer results (replace everything when the analyzer restarted).
    addRecords(fresh) {
      if (!fresh.length) return;
      const newest = this.records.length ? this.records[this.records.length - 1].seq : -1;
      if (fresh[0].seq <= newest && fresh[fresh.length - 1].seq < newest) this.records = [];
      const known = this.records.length ? this.records[this.records.length - 1].seq : -1;
      fresh.forEach(record => { if (record.seq > known) this.records.push(record); });
      if (this.records.length > 24) this.records.splice(0, this.records.length - 24);
    }

    // Draw the result matching the frame on screen (called ~4x a second).
    refresh(playMs) {
      const record = D.pickRecord(this.records, playMs);
      if (record !== this.shown) {
        this.shown = record;
        this.requestDraw();
      }
    }

    clear() {
      if (!this.records.length && !this.shown) return;
      this.records = [];
      this.shown = null;
      this.requestDraw();
    }

    requestDraw() {
      if (this.raf) return;
      this.raf = requestAnimationFrame(() => { this.raf = 0; this.draw(); });
    }

    draw() {
      const canvas = this.attach();
      if (!canvas) return;
      const boxW = this.container.clientWidth;
      const boxH = this.container.clientHeight;
      const video = this.watchedVideo;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const pixelW = Math.max(1, Math.round(boxW * dpr));
      const pixelH = Math.max(1, Math.round(boxH * dpr));
      if (canvas.width !== pixelW || canvas.height !== pixelH) {
        canvas.width = pixelW;
        canvas.height = pixelH;
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const heads = this.shown;
      if (!heads || !heads.count || !video || !video.videoWidth) return;
      const fit = getComputedStyle(video).objectFit || "contain";
      const rect = D.contentRect(boxW, boxH, video.videoWidth, video.videoHeight, fit);
      if (!rect) return;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const r = D.dotRadius(heads.count, rect.w, rect.h);
      const points = heads.points;
      const scaleX = rect.w / 65535;
      const scaleY = rect.h / 65535;
      const n = heads.count;
      // Small dots are squares (one rect per dot is far cheaper than an arc);
      // larger ones are round. Each style is one path and one fill: a dark
      // halo first so the red stays visible on bright ground.
      const round = r >= 2.6;
      for (let pass = 0; pass < 2; pass++) {
        const radius = pass === 0 ? r + 1 : r;
        ctx.fillStyle = pass === 0 ? "rgba(0,0,0,0.6)" : "#ff2d2d";
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const x = rect.x + points[2 * i] * scaleX;
          const y = rect.y + points[2 * i + 1] * scaleY;
          if (x < -radius || y < -radius || x > boxW + radius || y > boxH + radius) continue;
          if (round) {
            ctx.moveTo(x + radius, y);
            ctx.arc(x, y, radius, 0, 6.283185307179586);
          } else {
            ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
          }
        }
        ctx.fill();
      }
    }

    destroy() {
      if (this.raf) cancelAnimationFrame(this.raf);
      if (this.resizeObserver) this.resizeObserver.disconnect();
      if (this.watchedVideo) {
        this.watchedVideo.removeEventListener("resize", this.onVideoResize);
        this.watchedVideo.removeEventListener("loadedmetadata", this.onVideoResize);
      }
      if (this.canvas) this.canvas.remove();
    }
  }
  D.Layer = Layer;

  // Keeps every camera's dots current.
  //   container(id)  -> the camera's player container element
  //   wanted(cam)    -> dots should be visible now (counting, active, on screen)
  //   clock(id)      -> epoch ms of the frame being shown, or null if unknown
  D.Manager = class {
    constructor(options) {
      this.getContainer = options.container;
      this.wanted = options.wanted;
      this.clock = options.clock || (() => null);
      this.enabled = options.enabled !== false;
      this.cameras = new Map();
      this.timer = null;
    }

    state(id) {
      let entry = this.cameras.get(id);
      if (!entry) {
        entry = { layer: null, seq: null, inflight: false, controller: null };
        this.cameras.set(id, entry);
      }
      return entry;
    }

    setEnabled(on) {
      this.enabled = Boolean(on);
      D.writeEnabled(this.enabled);
      if (!this.enabled) this.cameras.forEach(entry => { if (entry.layer) entry.layer.clear(); entry.seq = null; });
    }

    // Match every layer to its player's current frame, ~4 times a second.
    startTicking() {
      if (this.timer) return;
      this.timer = setInterval(() => {
        if (document.hidden) return;
        this.cameras.forEach((entry, id) => {
          if (entry.layer && entry.layer.records.length) entry.layer.refresh(this.clock(id));
        });
      }, 250);
    }

    // Call on every camera poll (cheap unless a new result arrived).
    sync(cam) {
      const entry = this.state(cam.id);
      const container = this.getContainer(cam.id);
      const show = this.enabled && container && Number(cam.heads_count) > 0 && this.wanted(cam);
      if (!show) {
        if (entry.layer) entry.layer.clear();
        entry.seq = null;
        if (entry.controller) { try { entry.controller.abort(); } catch (e) { /* done */ } }
        return;
      }
      if (!entry.layer || entry.layer.container !== container) {
        if (entry.layer) entry.layer.destroy();
        entry.layer = new Layer(container);
        entry.seq = null;
      }
      this.startTicking();
      // Re-attach after a player restart cleared the container or swapped the
      // <video>; otherwise nothing to redraw until the matching result changes.
      if (entry.layer.needsAttach()) entry.layer.requestDraw();
      if (entry.seq === cam.heads_seq || entry.inflight) return;
      this.fetchHeads(cam.id, entry);
    }

    fetchHeads(id, entry) {
      entry.inflight = true;
      entry.controller = typeof AbortController === "undefined" ? null : new AbortController();
      const since = entry.seq == null ? "" : `?since=${entry.seq}`;
      fetch(`/cameras/${encodeURIComponent(id)}/heads${since}`, {
        cache: "no-store",
        credentials: "same-origin",
        signal: entry.controller ? entry.controller.signal : undefined
      }).then(response => {
        if (response.status === 204) return { empty: true };
        if (response.status === 304) return { unchanged: true };
        if (!response.ok) throw new Error(`heads ${response.status}`);
        return response.arrayBuffer().then(buffer => ({ buffer }));
      }).then(result => {
        if (!entry.layer) return;
        if (result.empty) { entry.layer.clear(); return; }
        if (result.unchanged) return;
        const records = D.decodeRecords(result.buffer);
        if (!records || !records.length) return;
        entry.layer.addRecords(records);
        entry.seq = records[records.length - 1].seq;
        entry.layer.refresh(this.clock(id));
      }).catch(() => { /* try again on the next poll */ }).finally(() => {
        entry.inflight = false;
      });
    }

    forget(id) {
      const entry = this.cameras.get(id);
      if (!entry) return;
      if (entry.controller) { try { entry.controller.abort(); } catch (e) { /* done */ } }
      if (entry.layer) entry.layer.destroy();
      this.cameras.delete(id);
    }
  };

  // The header toggle button (optional element with this id).
  D.bindToggle = function (button, manager, onChange) {
    if (!button) return;
    ensureStyles();
    const paint = () => {
      button.setAttribute("aria-pressed", manager.enabled ? "true" : "false");
      button.title = manager.enabled ? "Hide the dot on every counted person" : "Show a dot on every counted person";
    };
    button.addEventListener("click", () => {
      manager.setEnabled(!manager.enabled);
      paint();
      if (typeof onChange === "function") onChange(manager.enabled);
    });
    paint();
  };

  window.EGDMSDots = D;
})();
