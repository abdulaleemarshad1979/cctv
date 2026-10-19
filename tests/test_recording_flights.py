import os
import shutil
import subprocess
import time

import pytest

from src.recording_flights import FlightPlayback, build_vod_playlist, flight_summary, group_flights, read_fmp4_layout


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


def test_summary_leaves_out_the_file_list():
    flight = group_flights([_item("drone-1", "2026-10-19_11-00-00-000000", 10)])[0]
    summary = flight_summary(flight)
    assert "segments" not in summary
    assert summary["segment_count"] == 1 and summary["id"] == flight["id"]


def test_playlist_has_one_discontinuity_per_file():
    layout_a = {"init": [0, 700], "segments": [[700, 5000, 4.0], [5700, 3000, 2.5]]}
    layout_b = {"init": [0, 650], "segments": [[650, 4000, 3.2]]}
    text = build_vod_playlist([("/recordings/a.mp4", layout_a), ("/recordings/b.mp4", layout_b)])
    lines = text.splitlines()
    assert lines[0] == "#EXTM3U" and lines[-1] == "#EXT-X-ENDLIST"
    assert "#EXT-X-TARGETDURATION:4" in lines and "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert lines.count("#EXT-X-DISCONTINUITY") == 1
    assert '#EXT-X-MAP:URI="/recordings/b.mp4",BYTERANGE="650@0"' in lines
    assert lines[lines.index("#EXTINF:2.500,") + 1] == "#EXT-X-BYTERANGE:3000@5700"


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


def _ffprobe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


@needs_ffmpeg
def test_layout_matches_the_file_and_starts_segments_on_keyframes(tmp_path):
    path = tmp_path / "live" / "drone1" / "2026-10-19_11-00-00-000000.mp4"
    _make_segment(path, 9)  # 1 s GOP -> 1 s fragments
    layout = read_fmp4_layout(str(path))
    assert layout["init"][0] == 0
    durations = [seg[2] for seg in layout["segments"]]
    assert sum(durations) == pytest.approx(_ffprobe_duration(path), abs=0.05)
    assert durations[:2] == [4.0, 4.0]
    # Byte ranges are contiguous and end at the end of the file.
    end = layout["init"][1]
    for offset, length, _seconds in layout["segments"]:
        assert offset == end
        end = offset + length
    with open(path, "rb") as f:
        # Anything after the last fragment is index data, not video.
        f.seek(end + 4)
        assert end == os.path.getsize(path) or f.read(4) == b"mfra"
        for offset, _length, _seconds in layout["segments"]:
            f.seek(offset + 4)
            assert f.read(4) == b"moof"


def test_non_fragmented_files_have_no_layout(tmp_path):
    path = tmp_path / "plain.mp4"
    path.write_bytes(b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x00")
    assert read_fmp4_layout(str(path)) is None


@needs_ffmpeg
def test_flight_playlist_and_streamed_download(tmp_path):
    rec = tmp_path / "rec"
    names = ["2026-10-19_11-00-00-000000", "2026-10-19_11-00-03-000000"]
    for name in names:
        _make_segment(rec / "live" / "drone1" / f"{name}.mp4", 2)
    flight = group_flights([_item("drone-1", n, 2) for n in names])[0]
    playback = FlightPlayback(str(rec), str(tmp_path / "cache"))

    text = playback.playlist(flight)
    assert text.count("#EXT-X-MAP:") == 2 and "#EXT-X-DISCONTINUITY" in text
    assert len(os.listdir(tmp_path / "cache" / "layouts")) == 2  # indexed once, tiny
    assert playback.playlist(flight) == text

    out = tmp_path / "joined.mp4"
    with open(out, "wb") as f:
        for chunk in playback.stream_download(flight):
            f.write(chunk)
    assert _ffprobe_duration(out) == pytest.approx(4, abs=0.2)
    assert os.listdir(tmp_path / "cache") == ["layouts"]  # no stored copy, list file removed


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
