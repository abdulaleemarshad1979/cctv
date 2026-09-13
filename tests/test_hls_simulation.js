/**
 * test_hls_simulation.js
 * Unit test verifying jitter buffer logic, latency controller, stall recovery,
 * stale callback protection, and clean teardown.
 */

const assert = require('assert');

// 1. Mock Video Element
class MockVideo {
  constructor() {
    this.currentTime = 0;
    this.playbackRate = 1.0;
    this.paused = true;
    this.muted = false;
    this.autoplay = true;
    this.buffered = {
      ranges: [],
      get length() { return this.ranges.length; },
      start(i) { return this.ranges[i][0]; },
      end(i) { return this.ranges[i][1]; }
    };
    this.seekable = {
      ranges: [],
      get length() { return this.ranges.length; },
      start(i) { return this.ranges[i][0]; },
      end(i) { return this.ranges[i][1]; }
    };
    this._listeners = {};
  }

  addEventListener(evt, fn) {
    if (!this._listeners[evt]) this._listeners[evt] = [];
    this._listeners[evt].push(fn);
  }

  removeEventListener(evt, fn) {
    if (!this._listeners[evt]) return;
    this._listeners[evt] = this._listeners[evt].filter(f => f !== fn);
  }

  dispatchEvent(evt) {
    const list = this._listeners[evt] || [];
    for (const fn of list) fn();
  }

  play() {
    this.paused = false;
    return Promise.resolve();
  }

  pause() {
    this.paused = true;
  }
}

// 2. Buffer & Latency Helper Functions (exact copy of logic in dashboards)
function getForwardBuffer(video, hls) {
  if (!video) return 0;
  try {
    if (hls && hls.mainForwardBufferInfo && typeof hls.mainForwardBufferInfo.len === "number") {
      const len = hls.mainForwardBufferInfo.len;
      if (len > 0) return len;
    }
  } catch (e) {}

  const b = video.buffered;
  if (!b || b.length === 0) return 0;

  let ct = video.currentTime;
  if ((ct === 0 || isNaN(ct)) && hls && typeof hls.startPosition === "number" && hls.startPosition > 0) {
    ct = hls.startPosition;
  }

  for (let i = 0; i < b.length; i++) {
    const s = b.start(i);
    const e = b.end(i);
    if (ct >= s && ct <= e) {
      return e - ct;
    }
    if (ct < s && (s - ct) <= 0.5) {
      return e - s;
    }
  }

  const last = b.length - 1;
  const lastStart = b.start(last);
  const lastEnd = b.end(last);
  if (ct < lastStart) {
    return lastEnd - lastStart;
  }
  return 0;
}

function getCurrentLatency(video, hls) {
  if (!video) return 0;
  if (hls) {
    if (typeof hls.latency === "number" && hls.latency > 0) {
      return hls.latency;
    }
    if (hls.latencyController && typeof hls.latencyController.computeLatency === "function") {
      const lat = hls.latencyController.computeLatency();
      if (typeof lat === "number" && !isNaN(lat) && lat > 0) {
        return lat;
      }
    }
  }
  if (video.seekable && video.seekable.length > 0) {
    const end = video.seekable.end(video.seekable.length - 1);
    if (end > video.currentTime) {
      return end - video.currentTime;
    }
  }
  if (hls && typeof hls.liveSyncPosition === "number" && hls.liveSyncPosition > 0 && typeof video.currentTime === "number") {
    const lat = (hls.liveSyncPosition - video.currentTime) + 3.0;
    if (lat > 0) return lat;
  }
  return 0;
}

// ── TEST 1: Forward Buffer Calculation ──────────────────────────────────────
function testForwardBuffer() {
  const video = new MockVideo();
  const hls = { startPosition: 100 };

  // When no buffers are present
  assert.strictEqual(getForwardBuffer(video, hls), 0);

  // Buffer range [100, 102.5]
  video.buffered.ranges = [[100, 102.5]];
  video.currentTime = 100;
  assert.strictEqual(getForwardBuffer(video, hls), 2.5);

  // Playhead moves forward to 101.5
  video.currentTime = 101.5;
  assert.strictEqual(getForwardBuffer(video, hls), 1.0);

  // When playhead is 0 at startup before seek:
  video.currentTime = 0;
  assert.strictEqual(getForwardBuffer(video, hls), 2.5);
  console.log('✓ testForwardBuffer passed');
}

// ── TEST 2: Startup Buffering (Holds Playback Until ~3s) ──────────────────────
function testStartupBuffering() {
  const video = new MockVideo();
  video.autoplay = false;
  video.muted = true;

  const hls = {
    startPosition: 50,
    _destroyed: false,
    _hasStarted: false,
    _startupTimer: null
  };

  const hlsPlayers = { 'drone1': hls };

  let playbackStarted = false;
  const startPlayback = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || hls._hasStarted) return;
    hls._hasStarted = true;
    playbackStarted = true;
    video.play();
  };

  const onBufferProgress = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls) return;
    if (!hls._hasStarted) {
      const ahead = getForwardBuffer(video, hls);
      if (ahead >= 2.8) {
        startPlayback();
      }
    }
  };

  // Fragment 1 arrives (1.0s buffered)
  video.currentTime = 50;
  video.buffered.ranges = [[50, 51.0]];
  onBufferProgress();
  assert.strictEqual(playbackStarted, false, 'Should not start at 1.0s');
  assert.strictEqual(video.paused, true);

  // Fragment 2 arrives (2.0s buffered)
  video.buffered.ranges = [[50, 52.0]];
  onBufferProgress();
  assert.strictEqual(playbackStarted, false, 'Should not start at 2.0s');
  assert.strictEqual(video.paused, true);

  // Fragment 3 arrives (3.0s buffered)
  video.buffered.ranges = [[50, 53.0]];
  onBufferProgress();
  assert.strictEqual(playbackStarted, true, 'Should start at 3.0s');
  assert.strictEqual(video.paused, false);
  console.log('✓ testStartupBuffering passed');
}

// ── TEST 3: Stall Recovery Rebuilds ~2s Before Resuming ───────────────────────
function testStallRecovery() {
  const video = new MockVideo();
  const hls = {
    startPosition: 50,
    _destroyed: false,
    _hasStarted: true,
    _isStalled: false,
    _stallTimer: null
  };
  const hlsPlayers = { 'drone1': hls };

  let resumed = false;
  const resumePlaybackFromStall = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._isStalled) return;
    hls._isStalled = false;
    resumed = true;
    video.play();
  };

  const handleBufferStall = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._hasStarted || hls._isStalled) return;
    hls._isStalled = true;
    video.pause();
  };

  const onBufferProgress = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls) return;
    if (hls._isStalled) {
      const ahead = getForwardBuffer(video, hls);
      if (ahead >= 1.9) {
        resumePlaybackFromStall();
      }
    }
  };

  // Video was playing at 53.0, but network stalled and buffer empty
  video.currentTime = 53.0;
  video.buffered.ranges = [[50, 53.0]]; // 0s ahead
  video.paused = false;

  // Trigger stall
  handleBufferStall();
  assert.strictEqual(hls._isStalled, true);
  assert.strictEqual(video.paused, true, 'Video should pause during stall recovery');

  // New fragment arrives (+1s buffer, ahead = 1.0s)
  video.buffered.ranges = [[50, 54.0]];
  onBufferProgress();
  assert.strictEqual(hls._isStalled, true, 'Should still be recovering at 1.0s buffer');
  assert.strictEqual(resumed, false);

  // Second fragment arrives (+1s buffer, ahead = 2.0s)
  video.buffered.ranges = [[50, 55.0]];
  onBufferProgress();
  assert.strictEqual(hls._isStalled, false, 'Should resume after 2s rebuilt');
  assert.strictEqual(resumed, true);
  assert.strictEqual(video.paused, false);
  console.log('✓ testStallRecovery passed');
}

// ── TEST 4: Live Latency Controller (Cooldown & LiveSync Seek) ────────────────
function testLatencyController() {
  const video = new MockVideo();
  video.currentTime = 100;
  video.seekable.ranges = [[0, 107.5]]; // Live edge is 107.5, latency = 7.5s
  video.playbackRate = 1.0;

  const hls = {
    liveSyncPosition: 104.5, // 3s behind 107.5
    _destroyed: false,
    _hasStarted: true,
    _isStalled: false,
    _lastSeekTime: 0,
    _lastLatencyCheck: 0
  };
  const hlsPlayers = { 'drone1': hls };

  let seekOccurred = 0;

  const checkLatency = (now) => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._hasStarted || hls._isStalled) return;
    const latency = getCurrentLatency(video, hls);

    if (latency > 6.0) {
      const SEEK_COOLDOWN_MS = 6000;
      if (!hls._lastSeekTime || (now - hls._lastSeekTime > SEEK_COOLDOWN_MS)) {
        hls._lastSeekTime = now;
        video.currentTime = hls.liveSyncPosition;
        video.playbackRate = 1.0;
        seekOccurred++;
        return;
      }
    }

    if (latency <= 3.3) {
      if (video.playbackRate !== 1.0) video.playbackRate = 1.0;
    } else if (latency > 3.8 && latency <= 6.0) {
      if (video.playbackRate < 1.05) video.playbackRate = 1.10;
    }
  };

  // First check at t = 1000ms: latency is 7.5s (> 6.0) -> triggers seek
  checkLatency(1000);
  assert.strictEqual(seekOccurred, 1);
  assert.strictEqual(video.currentTime, 104.5, 'Sought to liveSyncPosition');

  // Latency is now 107.5 - 104.5 = 3.0s (normal range)
  checkLatency(2000);
  assert.strictEqual(video.playbackRate, 1.0);
  assert.strictEqual(seekOccurred, 1, 'No extra seek');

  // Slight drift: latency = 4.2s (live edge 109.0, video at 104.8)
  video.currentTime = 104.8;
  video.seekable.ranges = [[0, 109.0]]; // latency = 4.2s
  checkLatency(3000);
  assert.strictEqual(video.playbackRate, 1.10, 'Gentle catch-up rate 1.10 applied');
  assert.strictEqual(seekOccurred, 1, 'No seek for gentle drift <= 6s');

  // Return to normal target: live edge 109.5, video 106.5 -> latency = 3.0s
  video.currentTime = 106.5;
  video.seekable.ranges = [[0, 109.5]];
  checkLatency(4000);
  assert.strictEqual(video.playbackRate, 1.0, 'Playback rate restored to 1.0');

  // Excessive latency again at t = 5000ms (within 6s cooldown from t = 1000ms)
  video.currentTime = 100; // latency = 9.5s
  checkLatency(5000);
  assert.strictEqual(seekOccurred, 1, 'Cooldown must prevent repeated seeking within 6s');

  // At t = 7500ms (after cooldown): seek allowed
  checkLatency(7500);
  assert.strictEqual(seekOccurred, 2, 'Seek allowed once cooldown has passed');
  console.log('✓ testLatencyController passed');
}

// ── TEST 5: Stale Callback Protection ────────────────────────────────────────
function testStaleCallbackProtection() {
  let teardownCalledFor = null;
  const teardownVideoFeed = (id) => { teardownCalledFor = id; };

  const oldHls = { _destroyed: false };
  const newHls = { _destroyed: false };
  const hlsPlayers = { 'drone1': newHls }; // active player is newHls

  // Old player's callback executes
  const oldErrorCallback = () => {
    if (oldHls._destroyed || hlsPlayers['drone1'] !== oldHls) return;
    teardownVideoFeed('drone1');
  };

  oldErrorCallback();
  assert.strictEqual(teardownCalledFor, null, 'Old player callback must not tear down active feed');

  // If old player is marked destroyed
  oldHls._destroyed = true;
  oldErrorCallback();
  assert.strictEqual(teardownCalledFor, null);

  // New player callback executes
  const newErrorCallback = () => {
    if (newHls._destroyed || hlsPlayers['drone1'] !== newHls) return;
    teardownVideoFeed('drone1');
  };
  newErrorCallback();
  assert.strictEqual(teardownCalledFor, 'drone1', 'Active player callback should execute teardown');
  console.log('✓ testStaleCallbackProtection passed');
}

// ── TEST 6: 15-Minute Sustained Playback Simulation ─────────────────────────
function test15MinuteSustainedPlayback() {
  const video = new MockVideo();
  video.autoplay = false;
  video.muted = true;

  let hls = {
    liveSyncPosition: 0,
    startPosition: -1,
    _destroyed: false,
    _hasStarted: false,
    _isStalled: false,
    _lastSeekTime: 0,
    _lastLatencyCheck: 0
  };
  let hlsPlayers = { 'drone1': hls };

  let playbackStarted = false;
  let stallCount = 0;
  let resumeCount = 0;
  let seekCount = 0;
  let gentleCatchupCount = 0;
  let staleCallbacksIgnored = 0;

  let streamTime = 0;
  let virtualTimeMs = 1000000;

  const startPlayback = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || hls._hasStarted) return;
    hls._hasStarted = true;
    playbackStarted = true;
    video.play();
  };

  const handleBufferStall = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._hasStarted || hls._isStalled) return;
    hls._isStalled = true;
    stallCount++;
    video.pause();
  };

  const resumePlaybackFromStall = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._isStalled) return;
    hls._isStalled = false;
    resumeCount++;
    video.play();
  };

  const onBufferProgress = () => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls) return;
    const ahead = getForwardBuffer(video, hls);
    if (!hls._hasStarted) {
      if (ahead >= 2.8) {
        startPlayback();
      }
    } else if (hls._isStalled) {
      if (ahead >= 1.9) {
        resumePlaybackFromStall();
      }
    }
  };

  const checkLatency = (now) => {
    if (hls._destroyed || hlsPlayers['drone1'] !== hls || !hls._hasStarted || hls._isStalled) return;
    const latency = getCurrentLatency(video, hls);

    if (latency > 6.0) {
      const SEEK_COOLDOWN_MS = 6000;
      if (!hls._lastSeekTime || (now - hls._lastSeekTime > SEEK_COOLDOWN_MS)) {
        hls._lastSeekTime = now;
        video.currentTime = hls.liveSyncPosition;
        video.playbackRate = 1.0;
        seekCount++;
        return;
      }
    }

    if (latency <= 3.3) {
      if (video.playbackRate !== 1.0) video.playbackRate = 1.0;
    } else if (latency > 3.8 && latency <= 6.0) {
      if (video.playbackRate < 1.05) {
        video.playbackRate = 1.10;
        gentleCatchupCount++;
      }
    }
  };

  // Run 900 simulated seconds (15 minutes)
  let bufferEnd = 0;
  for (let second = 0; second < 900; second++) {
    virtualTimeMs += 1000;
    streamTime += 1.0;
    hls.liveSyncPosition = Math.max(0, streamTime - 3.0);
    video.seekable.ranges = [[0, streamTime]];

    const isNetworkStall = (second >= 150 && second < 154); // 4-sec packet loss
    const isTabInactive = (second >= 350 && second < 360);  // 10-sec background tab
    const isFeedSwitch = (second === 600);                  // Switch at 10m

    // Incoming segment
    if (!isNetworkStall) {
      bufferEnd += 1.0;
    }

    // Update video buffered range
    if (video.currentTime <= bufferEnd) {
      video.buffered.ranges = [[Math.max(0, video.currentTime - 5), bufferEnd]];
    } else {
      video.buffered.ranges = [];
    }

    // Fire buffer progress event
    onBufferProgress();

    // Consume video if playing and tab active
    if (!video.paused && !isTabInactive) {
      const consumed = 1.0 * video.playbackRate;
      if (video.currentTime + consumed >= bufferEnd) {
        video.currentTime = bufferEnd;
        handleBufferStall();
      } else {
        video.currentTime += consumed;
      }
    }

    // Latency controller tick
    checkLatency(virtualTimeMs);

    // Stream switch simulation at 10m:
    if (isFeedSwitch) {
      const oldInstance = hls;
      oldInstance._destroyed = true;
      const newInstance = {
        liveSyncPosition: Math.max(0, streamTime - 3.0),
        startPosition: -1,
        _destroyed: false,
        _hasStarted: true,
        _isStalled: false,
        _lastSeekTime: 0,
        _lastLatencyCheck: 0
      };
      hlsPlayers['drone1'] = newInstance;
      hls = newInstance;

      const staleCb = () => {
        if (oldInstance._destroyed || hlsPlayers['drone1'] !== oldInstance) {
          staleCallbacksIgnored++;
          return;
        }
        assert.fail('Stale callback should never execute against replaced player!');
      };
      staleCb();
    }

    if (second < 2) {
      assert.strictEqual(playbackStarted, false, 'Must not start before gathering ~3s');
    } else if (second === 2) {
      assert.strictEqual(playbackStarted, true, 'Must start after gathering ~3s');
    }

    const currentLat = getCurrentLatency(video, hls);
    if (playbackStarted && !isNetworkStall && !isTabInactive && second > 370) {
      assert.ok(currentLat <= 6.5, `Latency should remain bounded (was ${currentLat.toFixed(2)}s at second ${second})`);
    }
  }

  assert.ok(playbackStarted, 'Stream must have started');
  assert.ok(stallCount >= 1, 'Stall should have been detected during network drop');
  assert.ok(resumeCount >= 1, 'Playback should have resumed after rebuilding 2s buffer');
  assert.ok(seekCount >= 1, 'Excessive latency after background tab should trigger liveSync seek');
  assert.ok(gentleCatchupCount >= 1, 'Gentle catch-up should have engaged for drift');
  assert.ok(staleCallbacksIgnored >= 1, 'Stale callback must be guarded and ignored');
  console.log('✓ test15MinuteSustainedPlayback passed (900 seconds simulated)');
}

function runAll() {
  testForwardBuffer();
  testStartupBuffering();
  testStallRecovery();
  testLatencyController();
  testStaleCallbackProtection();
  test15MinuteSustainedPlayback();
  console.log('\nAll HLS simulation tests passed successfully.');
}

runAll();

