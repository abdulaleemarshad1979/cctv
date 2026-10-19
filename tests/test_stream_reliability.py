"""Unit tests for the stream-reliability modules (no network, no FFmpeg)."""

import json
import os

from src import stream_events as ev
from src.abr_transcoder import AbrManager, AbrUnavailable, build_ffmpeg_command, build_ladder
from src.recording_continuity import RecordingContinuity, parse_segment_start
from src.stream_events import StreamEventLog
from src.stream_telemetry import StreamTelemetry


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- event log

def test_event_log_records_rotates_and_restores(tmp_path):
    path = tmp_path / "events.jsonl"
    log = StreamEventLog(str(path), max_bytes=64 * 1024, backup_count=2, retention_days=30)
    for i in range(1200):
        log.record("drone-1", ev.NETWORK, "uplink_stalled", f"stall {i}", ts=1000.0 + i)
    assert path.exists()
    assert (tmp_path / "events.jsonl.1").exists()
    assert not (tmp_path / "events.jsonl.4").exists()  # bounded by backup_count
    recent = log.recent("drone-1", limit=5)
    assert [e["detail"] for e in recent] == [f"stall {i}" for i in range(1195, 1200)]

    restored = StreamEventLog(str(path), max_bytes=64 * 1024, backup_count=2)
    assert restored.recent("drone-1", limit=1)[0]["detail"] == "stall 1199"


def test_event_log_rejects_unknown_category(tmp_path):
    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    try:
        log.record("drone-1", "weather", "rain")
    except ValueError:
        return
    raise AssertionError("unknown category accepted")


def test_system_events_are_included_in_camera_timeline(tmp_path):
    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    log.record(ev.SYSTEM_ID, ev.INGEST, "server_unreachable", "down", ts=10)
    log.record("drone-2", ev.CAMERA, "publisher_connected", "up", ts=11)
    names = [e["event"] for e in log.recent("drone-2")]
    assert names == ["server_unreachable", "publisher_connected"]


# ---------------------------------------------------------------- telemetry

def _payload(ready=True, received=0, height=720):
    item = {"name": "live/drone1", "ready": ready, "online": ready, "inboundBytes": received,
            "tracks2": [{"codec": "H264", "codecProps": {"width": 1280, "height": height}}], "readers": []}
    return {"items": [item] if ready else []}


def _telemetry(tmp_path, clock):
    log = StreamEventLog(str(tmp_path / "t.jsonl"))
    tel = StreamTelemetry(log, lambda path: "drone-1" if path == "live/drone1" else None,
                          stall_after_seconds=4, server_read_timeout_seconds=10, clock=clock)
    return log, tel


def test_telemetry_measures_bitrate_and_last_packet(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.observe(_payload(received=0))
    clock.now += 2
    tel.observe(_payload(received=500_000))  # 4 Mbit in 2 s
    snap = tel.snapshot("drone-1")
    assert snap["online"] and not snap["stalled"]
    assert 1500 < snap["bitrate_kbps"] <= 2000
    assert snap["seconds_since_last_packet"] == 0.0
    assert tel.source_height("drone-1") == 720
    assert [e["event"] for e in log.recent("drone-1")] == ["publisher_connected"]


def test_uplink_stall_then_server_timeout_is_classified_as_network(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.observe(_payload(received=100))
    for _ in range(5):            # bytes frozen for 10 s
        clock.now += 2
        tel.observe(_payload(received=100))
    clock.now += 1
    tel.observe(_payload(ready=False))
    events = [(e["category"], e["event"]) for e in log.recent("drone-1")]
    assert (ev.NETWORK, "uplink_stalled") in events
    assert events[-1] == (ev.NETWORK, "publisher_timed_out")


def test_disconnect_while_media_flows_is_classified_as_camera(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.observe(_payload(received=100))
    clock.now += 2
    tel.observe(_payload(received=900))
    clock.now += 2
    tel.observe(_payload(ready=False))
    last = log.recent("drone-1")[-1]
    assert (last["category"], last["event"]) == (ev.CAMERA, "publisher_disconnected")

    clock.now += 7
    tel.observe(_payload(received=50))
    reconnect = log.recent("drone-1")[-1]
    assert reconnect["event"] == "publisher_connected" and reconnect["gap_seconds"] == 9.0
    assert tel.snapshot("drone-1")["reconnects"] == 1


def test_stall_recovery_is_logged(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.observe(_payload(received=100))
    for _ in range(3):
        clock.now += 2
        tel.observe(_payload(received=100))
    clock.now += 2
    tel.observe(_payload(received=5000))
    assert log.recent("drone-1")[-1]["event"] == "uplink_recovered"
    assert tel.snapshot("drone-1")["stalled"] is False


def test_slow_uplink_is_flagged_as_degraded_then_restored(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    received = 0
    tel.observe(_payload(received=received))

    def run(seconds, kbps):
        nonlocal received
        for _ in range(int(seconds / 2)):
            clock.now += 2
            received += int(kbps * 1000 / 8 * 2)
            tel.observe(_payload(received=received))

    run(20, 2500)                 # normal flight learns a ~2500 kbps baseline
    assert tel.snapshot("drone-1")["baseline_kbps"] > 2000
    run(30, 1000)                 # throttled uplink: bytes still flow, so no stall
    events = [e["event"] for e in log.recent("drone-1")]
    assert "uplink_stalled" not in events
    assert events[-1] == "uplink_degraded"
    assert tel.snapshot("drone-1")["degraded"] is True
    assert tel.snapshot("drone-1")["baseline_kbps"] > 2000   # throttle did not drag the baseline down

    run(10, 2500)
    assert log.recent("drone-1")[-1]["event"] == "uplink_restored"
    assert tel.snapshot("drone-1")["degraded"] is False


def test_brief_bitrate_dip_is_not_reported(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    received = 0
    tel.observe(_payload(received=received))
    for kbps in [2500] * 10 + [600] + [2500] * 5:
        clock.now += 2
        received += int(kbps * 1000 / 8 * 2)
        tel.observe(_payload(received=received))
    assert "uplink_degraded" not in [e["event"] for e in log.recent("drone-1")]


def test_stream_lost_during_server_outage_is_blamed_on_ingest(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.observe(_payload(received=100))
    clock.now += 2
    tel.observe(_payload(received=900))
    clock.now += 2
    tel.api_failed("Connection refused")
    clock.now += 4
    tel.observe(_payload(ready=False))      # MediaMTX back, publisher not yet
    events = [(e["category"], e["event"]) for e in log.recent("drone-1")]
    assert (ev.INGEST, "publisher_lost_in_server_restart") in events
    assert (ev.CAMERA, "publisher_disconnected") not in events


def test_api_outage_logged_once_and_recovery(tmp_path):
    clock = Clock()
    log, tel = _telemetry(tmp_path, clock)
    tel.api_failed("timeout")
    tel.api_failed("timeout")
    clock.now += 30
    tel.observe({"items": []})
    names = [e["event"] for e in log.recent(ev.SYSTEM_ID)]
    assert names == ["server_unreachable", "server_recovered"]


# ---------------------------------------------------------------- continuity

def _segment(root, cam_dir, name, mtime):
    directory = root / "live" / cam_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * 1000)
    os.utime(path, (mtime, mtime))
    return path


def test_segment_filename_is_parsed_as_utc():
    assert parse_segment_start("2026-10-19_04-59-43-815069.mp4") == 1_792_385_983.815069
    assert parse_segment_start("notes.txt") is None


def test_continuity_reports_gaps_flights_and_integrity(tmp_path):
    t0 = parse_segment_start("2026-10-19_05-00-00-000000.mp4")
    clock = Clock(t0 + 3600)
    durations = {
        "2026-10-19_05-00-00-000000.mp4": (60.0, True),
        "2026-10-19_05-01-10-000000.mp4": (20.0, False),   # 10 s gap, audio-only file
        "2026-10-19_05-01-30-500000.mp4": (100.0, True),   # 0.5 s gap: within tolerance
        "2026-10-19_05-30-00-000000.mp4": (30.0, True),    # > 10 min later: new flight
    }
    for name in durations:
        _segment(tmp_path, "drone4", name, mtime=t0)

    def probe(path):
        duration, has_video = durations[os.path.basename(path)]
        return {"duration": duration, "has_video": has_video, "codec": "h264" if has_video else None,
                "width": 1280 if has_video else None, "height": 720 if has_video else None,
                "fps": 30.0, "probe_error": None}

    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    log.record("drone-4", ev.NETWORK, "publisher_timed_out", "uplink silent", ts=t0 + 61)
    rc = RecordingContinuity(str(tmp_path), str(tmp_path / "cache.json"), events=log,
                             probe=probe, clock=clock, flight_break_seconds=600)
    assert rc.scan()["probed"] == 4
    report = rc.report("drone-4", hours=2)
    cam = report["cameras"][0]
    assert cam["segments"] == 4
    assert cam["recorded_seconds"] == 210.0
    assert len(cam["flights"]) == 2
    assert [g["seconds"] for g in cam["gaps"]] == [10.0]
    assert cam["gaps"][0]["cause"]["event"] == "publisher_timed_out"
    assert cam["missing_seconds_within_flights"] == 10.0
    assert cam["segments_without_video"] == 1
    assert cam["short_segments_under_30s"] == 1
    assert cam["flights"][0]["coverage_percent"] == round(100 * 180 / 190.5, 1)

    # Second scan reuses the cache: nothing is probed again.
    rc2 = RecordingContinuity(str(tmp_path), str(tmp_path / "cache.json"), probe=probe, clock=clock)
    assert rc2.scan()["probed"] == 0


def test_growing_file_is_not_probed_and_new_finished_file_is_reported(tmp_path):
    t0 = parse_segment_start("2026-10-19_06-00-00-000000.mp4")
    clock = Clock(t0 + 30)
    path = _segment(tmp_path, "drone7", "2026-10-19_06-00-00-000000.mp4", mtime=t0 + 28)
    probes = []

    def probe(p):
        probes.append(p)
        return {"duration": 40.0, "has_video": True, "probe_error": None}

    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    rc = RecordingContinuity(str(tmp_path), str(tmp_path / "c.json"), events=log, probe=probe, clock=clock)
    rc.scan()
    assert probes == []
    last = rc.report("drone-7")["cameras"][0]["last_segment"]
    assert last["growing"] is True and last["duration_seconds"] == 28.0

    clock.now = t0 + 60
    os.utime(path, (t0 + 40, t0 + 40))
    rc.scan()
    assert len(probes) == 1
    assert log.recent("drone-7")[-1]["event"] == "segment_finalized"


def test_recorder_stall_detected_when_source_online_but_file_not_growing(tmp_path):
    t0 = 1_800_000_000.0
    clock = Clock(t0)
    _segment(tmp_path, "drone1", "2027-01-15_08-00-00-000000.mp4", mtime=t0 - 120)
    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    rc = RecordingContinuity(str(tmp_path), str(tmp_path / "c.json"), events=log, clock=clock,
                             recorder_stall_seconds=20, probe=lambda p: {})
    assert rc.check_recorder("drone-1", True) == "recording"   # just came online
    clock.now += 25
    assert rc.check_recorder("drone-1", True) == "stalled"
    assert log.recent("drone-1")[-1]["event"] == "recording_stalled"
    assert rc.check_recorder("drone-1", False) == "idle"


def test_disk_level_transitions_are_logged(tmp_path):
    log = StreamEventLog(str(tmp_path / "e.jsonl"))
    rc = RecordingContinuity(str(tmp_path), str(tmp_path / "c.json"), events=log, probe=lambda p: {},
                             disk_warn_free_gb=10 ** 9, disk_critical_free_gb=0)
    assert rc.check_disk()["level"] == "low"
    assert log.recent(ev.SYSTEM_ID)[-1]["event"] == "disk_low"


# ---------------------------------------------------------------- ABR

def test_ladder_depends_on_source_height():
    assert [v["name"] for v in build_ladder(720)] == ["src", "480p"]
    assert [v["name"] for v in build_ladder(1296)] == ["src", "720p", "480p"]
    assert [v["name"] for v in build_ladder(1296, encode_720=False)] == ["src", "480p"]
    assert [v["name"] for v in build_ladder(None)] == ["src", "480p"]


def test_ffmpeg_command_aligns_variants_and_never_touches_audio_or_recordings():
    cmd = build_ffmpeg_command("ffmpeg", "rtsp://mediamtx:8554/live/drone1", "/tmp/abr/drone-1", build_ladder(1080))
    joined = " ".join(cmd)
    assert "-c:v:0 copy" in joined
    assert joined.count("-force_key_frames") == 2 and "source" in cmd
    assert "select='if(isnan(prev_selected_n),key,1)'" in joined
    assert "-an" in cmd
    assert "omit_endlist" in joined and "program_date_time" in joined
    assert cmd[cmd.index("-var_stream_map") + 1] == "v:0,name:src v:1,name:720p v:2,name:480p"
    assert "/recordings" not in joined


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _manager(tmp_path, clock, online=lambda cam: True, max_sessions=2):
    started = []

    def popen(cmd, **kwargs):
        proc = FakeProcess()
        started.append(proc)
        return proc

    manager = AbrManager("rtsp://mediamtx:8554", str(tmp_path / "abr"), max_sessions=max_sessions,
                         idle_seconds=45, clock=clock, popen=popen, is_source_online=online)
    manager.ffmpeg = "sh"  # any binary on PATH: enabled check only
    return manager, started


def test_abr_never_duplicates_and_respects_capacity(tmp_path):
    clock = Clock()
    manager, started = _manager(tmp_path, clock)
    manager.ensure("drone-1", "live/drone1", 720)
    manager.ensure("drone-1", "live/drone1", 720)
    assert len(started) == 1
    manager.ensure("drone-2", "live/drone2", 720)
    try:
        manager.ensure("drone-3", "live/drone3", 720)
    except AbrUnavailable as exc:
        assert exc.reason == "capacity"
    else:
        raise AssertionError("capacity not enforced")
    try:
        manager.ensure("drone-4", "live/drone4", 360)
    except AbrUnavailable as exc:
        assert exc.reason == "source_small"


def test_abr_stops_idle_sessions(tmp_path):
    clock = Clock()
    manager, started = _manager(tmp_path, clock)
    manager.ensure("drone-1", "live/drone1", 720)
    clock.now += 30
    manager.reap()
    assert not started[0].terminated
    clock.now += 20
    manager.reap()
    assert started[0].terminated
    assert manager.snapshot()["sessions"] == {}


def test_abr_restart_is_bounded_with_backoff(tmp_path):
    clock = Clock()
    manager, started = _manager(tmp_path, clock)
    manager.ensure("drone-1", "live/drone1", 720)
    for _ in range(12):
        started[-1].returncode = 1          # crash
        manager.reap()                       # schedules restart
        clock.now += 31
        manager.touch("drone-1")             # viewers still watching
        manager.reap()                       # restarts (until the cap)
    assert len(started) <= 1 + 6
    assert "drone-1" not in manager.snapshot()["sessions"]


def test_abr_crash_while_source_offline_is_not_restarted(tmp_path):
    clock = Clock()
    manager, started = _manager(tmp_path, clock, online=lambda cam: False)
    manager.ensure("drone-1", "live/drone1", 720)
    started[0].returncode = 0
    manager.reap()
    assert len(started) == 1 and manager.snapshot()["sessions"] == {}


def test_abr_file_resolution_rejects_traversal(tmp_path):
    clock = Clock()
    manager, _ = _manager(tmp_path, clock)
    manager.ensure("drone-1", "live/drone1", 720)
    target = tmp_path / "abr" / "drone-1" / "480p" / "index.m3u8"
    target.write_text("#EXTM3U\n#EXTINF:1,\nseg_000001.ts\n")
    assert manager.resolve_file("drone-1", ["480p", "index.m3u8"]) == str(target)
    assert manager.resolve_file("drone-1", ["..", "index.m3u8"]) is None
    assert manager.resolve_file("drone-1", ["480p", "ffmpeg.log"]) is None
    assert manager.resolve_file("../etc", ["passwd.m3u8"]) is None
