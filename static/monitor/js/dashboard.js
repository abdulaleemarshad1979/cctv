(function () {
  const POLL_INTERVAL_MS = 3000;
  let activeFilter = "All"; // "All", "Rjy", "Pushkaralu", "Active"
  const tiles = {}; // id -> { tileEl, iframeEl, liveBadgeEl, placeholderEl, errorTextEl, streamUrl, startBtn, stopBtn }
  let hlsPort = "8088";
  let useWebRtc = true;

  // Auto-detect active MediaMTX HLS/WebRTC ports
  async function detectPorts() {
    // Check if WebRTC is available on port 8889 (preferred for instant loading in Chrome)
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 600);
      await fetch(`http://${window.location.hostname}:8889/`, {
        method: "HEAD",
        mode: "no-cors",
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      useWebRtc = true;
      console.log("[CDMP] WebRTC detected on port 8889 (using WebRTC for video)");
      return;
    } catch (e) {
      useWebRtc = false;
    }

    const ports = ["8088", "8888"];
    for (const port of ports) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600);
        await fetch(`http://${window.location.hostname}:${port}/`, {
          method: "HEAD",
          mode: "no-cors",
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        hlsPort = port;
        console.log(`[CDMP] HLS detected on port: ${hlsPort}`);
        break;
      } catch (e) {
        // try next
      }
    }
  }
  detectPorts();

  // Modal elements
  const modalOverlay = document.getElementById("connect-modal");
  const modalClose = document.getElementById("modal-close");
  const modalCancel = document.getElementById("modal-cancel");
  const connectForm = document.getElementById("connect-form");
  const modalDroneIdInput = document.getElementById("modal-drone-id");
  const modalSourceUrlInput = document.getElementById("modal-source-url");

  // Initialize tile elements map
  document.querySelectorAll(".tile").forEach((el) => {
    const id = el.dataset.id;
    tiles[id] = {
      tileEl: el,
      iframeEl: el.querySelector(".tile__iframe"),
      liveBadgeEl: el.querySelector(".tile__live-badge"),
      placeholderEl: el.querySelector(".tile__placeholder"),
      errorTextEl: el.querySelector(".tile__error-text"),
      countEl: el.querySelector(".tile__count"),
      zoneBadgeEl: el.querySelector(".tile__zone-badge"),
      startBtn: el.querySelector('[data-action="start"]'),
      stopBtn: el.querySelector('[data-action="stop"]'),
      streamUrl: el.dataset.streamUrl,
      location: el.dataset.location,
      category: el.dataset.category,
      status: el.dataset.status
    };

    // Connect Source Button action (now connects automatically)
    tiles[id].startBtn.addEventListener("click", () => {
      triggerStartStream(id);
    });

    // Disconnect Button action
    tiles[id].stopBtn.addEventListener("click", () => {
      triggerStopStream(id);
    });
  });

  // Modal actions
  function openConnectModal(droneId) {
    modalDroneIdInput.value = droneId;
    modalSourceUrlInput.value = "Videos/K.mp4"; // Default value
    modalOverlay.classList.remove("hidden");
    modalSourceUrlInput.focus();
  }

  function closeConnectModal() {
    modalOverlay.classList.add("hidden");
    modalDroneIdInput.value = "";
    modalSourceUrlInput.value = "";
  }

  [modalClose, modalCancel].forEach((el) => {
    el.addEventListener("click", closeConnectModal);
  });

  modalOverlay.addEventListener("click", (e) => {
    if (e.target === modalOverlay) closeConnectModal();
  });

  // Handle Start Stream request
  async function triggerStartStream(droneId, sourceUrl = null) {
    const tile = tiles[droneId];
    if (tile) {
      tile.status = "connecting";
      tile.tileEl.dataset.status = "offline";
      tile.iframeEl.classList.add("hidden");
      tile.liveBadgeEl.classList.add("hidden");
      tile.placeholderEl.classList.remove("hidden");
      tile.errorTextEl.textContent = "Connecting…";
      if (tile.countEl) tile.countEl.classList.add("hidden");
      if (tile.zoneBadgeEl) tile.zoneBadgeEl.classList.add("hidden");
    }

    try {
      const payload = {};
      if (sourceUrl) payload.source_url = sourceUrl;

      const res = await fetch(`/cameras/${droneId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error("Failed to start stream");
      console.log(`[CDMP] Stream started successfully for ${droneId}`);
    } catch (err) {
      console.error(`[CDMP] Error starting stream for ${droneId}:`, err);
      alert(`Error connecting feed: ${err.message}`);
    }
    pollStatus();
  }

  // Handle Form Submission (Start Stream manually from modal if opened)
  connectForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const droneId = modalDroneIdInput.value;
    const sourceUrl = modalSourceUrlInput.value;
    closeConnectModal();
    triggerStartStream(droneId, sourceUrl);
  });

  // Handle Stop Stream request
  async function triggerStopStream(droneId) {
    try {
      const res = await fetch(`/cameras/${droneId}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      if (!res.ok) throw new Error("Failed to stop stream");
      console.log(`[CDMP] Stream stopped for ${droneId}`);
    } catch (err) {
      console.error(`[CDMP] Error stopping stream for ${droneId}:`, err);
    }
    pollStatus();
  }

  // Filter selection handler
  const pills = document.querySelectorAll(".filter-group .pill");
  pills.forEach((pill) => {
    if (pill.classList.contains("pill--logout")) return;

    pill.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      activeFilter = pill.dataset.filter;
      applyFiltering();
    });
  });

  function applyFiltering() {
    let visibleCount = 0;

    Object.keys(tiles).forEach((id) => {
      const tile = tiles[id];
      let show = false;

      if (activeFilter === "All") {
        show = true;
      } else if (activeFilter === "Rjy") {
        show = tile.location === "Rjy";
      } else if (activeFilter === "Pushkaralu") {
        show = tile.location === "Pushkaralu";
      } else if (activeFilter === "Active") {
        show = tile.status === "online";
      } else if (activeFilter === "Drone") {
        show = tile.category === "drone";
      } else if (activeFilter === "CCTV") {
        show = tile.category === "cctv";
      }

      if (show) {
        tile.tileEl.classList.remove("hidden");
        visibleCount++;
      } else {
        tile.tileEl.classList.add("hidden");
      }
    });

    const emptyMessage = document.getElementById("empty-message");
    if (visibleCount === 0) {
      emptyMessage.classList.remove("hidden");
    } else {
      emptyMessage.classList.add("hidden");
    }
  }

  // Poll backend status
  async function pollStatus() {
    try {
      const res = await fetch("/cameras");
      if (!res.ok) throw new Error("Backend connection failed");
      const cameras = await res.json();

      cameras.forEach((cam) => {
        const tile = tiles[cam.id];
        if (!tile) return;

        // Skip manual overrides in mid-transition
        if (tile.status === "connecting" && cam.status !== "online") {
          return;
        }

        // Update cache state
        tile.status = cam.status;

        // Apply HTML modifications depending on state
        tile.tileEl.dataset.status = cam.status;

        if (cam.status === "online") {
          // Play state: show iframe, load URL if empty
          if (!tile.iframeEl.src || tile.iframeEl.src === "about:blank") {
            let playbackUrl = tile.streamUrl;
            try {
              const parsedUrl = new URL(tile.streamUrl);
              if (parsedUrl.hostname === "localhost" || parsedUrl.hostname === "127.0.0.1") {
                parsedUrl.hostname = window.location.hostname;
              }
              if (useWebRtc) {
                parsedUrl.port = "8889";
              } else if (parsedUrl.port === "8088" || parsedUrl.port === "8888") {
                parsedUrl.port = hlsPort;
              }
              playbackUrl = parsedUrl.toString();
            } catch (e) {
              console.error("[CDMP] Error parsing stream URL:", e);
            }
            tile.iframeEl.src = playbackUrl;
          }
          tile.iframeEl.classList.remove("hidden");
          tile.liveBadgeEl.classList.remove("hidden");
          tile.placeholderEl.classList.add("hidden");

          // Show passenger count and risk zone badge
          if (tile.countEl) {
            tile.countEl.textContent = `${cam.people_count} pax`;
            tile.countEl.classList.remove("hidden");
          }
          if (tile.zoneBadgeEl) {
            tile.zoneBadgeEl.textContent = cam.comp_zone;
            tile.zoneBadgeEl.className = 'tile__zone-badge';
            tile.zoneBadgeEl.classList.add(cam.comp_zone.toLowerCase());
            tile.zoneBadgeEl.classList.remove("hidden");
          }

          // Show Stop button, hide Start button
          tile.startBtn.classList.add("hidden");
          tile.stopBtn.classList.remove("hidden");
        } else {
          // Stopped state: hide player, clear source
          tile.iframeEl.classList.add("hidden");
          tile.iframeEl.src = "about:blank";
          tile.liveBadgeEl.classList.add("hidden");
          tile.placeholderEl.classList.remove("hidden");

          if (tile.countEl) tile.countEl.classList.add("hidden");
          if (tile.zoneBadgeEl) tile.zoneBadgeEl.classList.add("hidden");

          // Show Start button, hide Stop button
          tile.startBtn.classList.remove("hidden");
          tile.stopBtn.classList.add("hidden");

          tile.errorTextEl.textContent = 'Drone stream not found, retrying in some seconds';
        }
      });

      applyFiltering();

    } catch (err) {
      console.error("[CDMP] Status poll failed:", err);
      // Mark all tiles offline if backend is unreachable
      Object.keys(tiles).forEach((id) => {
        const tile = tiles[id];
        tile.status = "offline";
        tile.tileEl.dataset.status = "offline";
        tile.iframeEl.classList.add("hidden");
        tile.iframeEl.src = "about:blank";
        tile.liveBadgeEl.classList.add("hidden");
        tile.placeholderEl.classList.remove("hidden");
        tile.errorTextEl.textContent = "Drone stream not found, retrying in some seconds";

        if (tile.countEl) tile.countEl.classList.add("hidden");
        if (tile.zoneBadgeEl) tile.zoneBadgeEl.classList.add("hidden");

        tile.startBtn.classList.remove("hidden");
        tile.stopBtn.classList.add("hidden");
      });
      applyFiltering();
    }
  }

  // Initial and interval status loading
  pollStatus();
  setInterval(pollStatus, POLL_INTERVAL_MS);
})();
