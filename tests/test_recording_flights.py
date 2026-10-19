import os
import shutil
import subprocess
import time

import pytest

from src.recording_flights import FlightPlayback, group_flights


def _item(drone, name, duration, size=1000, codec="H264", width=1280, height=720):
    rel = f"live/{drone.replace('-', '')}/{name}.mp4"
    return {
        "drone_id": drone, "filename": f"{name}.mp4", "relative_path": rel,
        "duration_seconds": duration, "size_bytes": size, "codec": codec,
        "width": width, "height": height, "location": drone, "video_url": f"/recordings/{rel}",
    }


def test_short_gaps_join_one_flight_and_long_gaps_split():
    items = [
        _item("drone-1", "2026-10-19_11-00-00-000000", 15),
        _item("drone-1", "2026-10-19_11-00-17-000000", 1),     # 2 s gap: drift reset
        _item("drone-1", "2026-10-19_11-00-20-000000", 40),    # 2 s gap
        _item("drone-1", "2026-10-19_11-30-00-000000", 60),    # new flight
        _item("drone-3", "2026-10-19_11-00-05-000000", 30),    # other drone
    ]
    flights = group_flights(items, gap_seconds=120)
    assert [(f["drone_id"], f["segment_count"]) for f in flights] == [
        ("drone-1", 1), ("drone-3", 1), ("drone-1", 3)]
    joined = flights[2]
    assert joined["duration_seconds"] == pytest.approx(56)
    assert joined["gap_seconds"] == pytest.approx(4)
    assert joined["size_bytes"] == 3000
    assert joined["thumbnail_path"].endswith("11-00-20-000000.mp4")
    # Same first segment -> same id, so links stay valid as a flight grows.
    again = group_flights(items[:2], gap_seconds=120)
    assert again[0]["id"] == joined["id"]


def test_format_change_starts_a_new_flight():
    items = [
        _item("drone-1", "2026-10-19_11-00-00-000000", 10),
        _item("drone-1", "2026-10-19_11-00-11-000000", 10, width=1920, height=1080),
    ]
    assert len(group_flights(items)) == 2


def test_unparseable_names_are_ignored():
    assert group_flights([_item("drone-1", "clip", 10)]) == []


def test_source_paths_cannot_escape_recordings(tmp_path):
    playback = FlightPlayback(str(tmp_path / "rec"), str(tmp_path / "cache"))
    with pytest.raises(ValueError):
        playback._source("../secret.mp4")


def test_eviction_removes_oldest_copies_only(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    playback = FlightPlayback(str(tmp_path), str(cache), max_cache_bytes=250)
    for index, name in enumerate(["old.mp4", "mid.mp4", "new.mp4"]):
        path = cache / name
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + index, 1000 + index))
    (cache / "notes.txt").write_bytes(b"y" * 500)
    playback._evict(keep=str(cache / "new.mp4"))
    assert sorted(p.name for p in cache.iterdir()) == ["mid.mp4", "new.mp4", "notes.txt"]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_segment(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=25:duration={seconds}", "-c:v", "libx264",
         "-preset", "ultrafast", "-g", "25", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         str(path)],
        check=True,
    )


@needs_ffmpeg
def test_flight_is_copied_into_one_seekable_mp4(tmp_path):
    rec = tmp_path / "rec"
    names = ["2026-10-19_11-00-00-000000", "2026-10-19_11-00-03-000000"]
    for name in names:
        _make_segment(rec / "live" / "drone1" / f"{name}.mp4", 2)
    flight = group_flights([_item("drone-1", n, 2) for n in names])[0]
    playback = FlightPlayback(str(rec), str(tmp_path / "cache"))

    status = playback.status(flight)
    deadline = time.time() + 30
    while status["state"] == "preparing" and time.time() < deadline:
        time.sleep(0.1)
        status = playback.status(flight)
    assert status["state"] == "ready", status

    target = playback.video_path(flight)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", target],
        capture_output=True, text=True, check=True,
    )
    assert float(probe.stdout) == pytest.approx(4, abs=0.2)
    with open(target, "rb") as f:
        head = f.read(4096)
    assert b"moov" in head and b"moof" not in head  # index first, not fragmented
    assert sorted(os.listdir(tmp_path / "cache")) == [os.path.basename(target)]

    # A segment that changes on disk invalidates the cached copy.
    _make_segment(rec / "live" / "drone1" / f"{names[1]}.mp4", 3)
    assert playback.video_path(flight) != target


@needs_ffmpeg
def test_thumbnail_is_generated_once(tmp_path):
    rec = tmp_path / "rec"
    _make_segment(rec / "live" / "drone1" / "2026-10-19_11-00-00-000000.mp4", 2)
    playback = FlightPlayback(str(rec), str(tmp_path / "cache"))
    first = playback.thumbnail("live/drone1/2026-10-19_11-00-00-000000.mp4", 2)
    assert first and open(first, "rb").read(2) == b"\xff\xd8"
    mtime = os.stat(first).st_mtime_ns
    assert playback.thumbnail("live/drone1/2026-10-19_11-00-00-000000.mp4", 2) == first
    assert os.stat(first).st_mtime_ns == mtime
    assert playback.thumbnail("live/drone1/missing.mp4", 2) is None
