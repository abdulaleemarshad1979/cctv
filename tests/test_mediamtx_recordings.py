import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import pytest

from src.mediamtx_recordings import RecordingIndexUnavailable, list_mediamtx_recordings


def test_recording_index_uses_real_segments_and_skips_zero_duration():
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    requested_paths = []

    def handle(request):
        requested_paths.append(request.url.params["path"])
        if request.url.params["path"] == "live/drone1":
            return httpx.Response(200, json=[
                {"start": now, "duration": 0},
                {"start": now, "duration": 7.93},
            ])
        return httpx.Response(404, json={"error": "no recording segments found"})

    cameras = [
        {"id": "drone-1", "name": "Drone 1", "location": "Test", "source_stream_path": "live/drone1"},
        {"id": "drone-2", "name": "Drone 2", "location": "Test", "source_stream_path": "live/drone2"},
        {"id": "drone-3", "name": "Analyzed", "source_stream_path": "analyzed/drone3"},
    ]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await list_mediamtx_recordings(cameras, "http://mediamtx:9996", client=client)

    result = asyncio.run(run())
    assert set(requested_paths) == {"live/drone1", "live/drone2"}
    assert result["total_recordings"] == 1
    recording = result["recordings"][0]
    assert recording["duration_seconds"] == 7.93
    assert recording["video_url"].startswith("/api/recordings/play?path=live%2Fdrone1")
    assert recording["download_url"].endswith("&download=1")


def test_recording_index_reports_unavailable_server():
    def fail(request):
        raise httpx.ConnectError("offline", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            return await list_mediamtx_recordings(
                [{"id": "drone-1", "source_stream_path": "live/drone1"}],
                "http://mediamtx:9996", client=client,
            )

    with pytest.raises(RecordingIndexUnavailable):
        asyncio.run(run())


def test_deployment_records_only_raw_paths_for_thirty_days():
    config = (ROOT / "deployment/mediamtx.yml").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "playbackAddress: :9996" in config
    assert "recordDeleteAfter: 720h" in config
    assert "'~^live/':" in config
    assert "record: yes" in config
    assert "./data/recordings:/recordings" in compose
    assert "9996:9996" not in compose


def test_dashboards_play_only_nearby_tiles():
    for page in ("admin_dashboard.html", "lite_dashboard.html"):
        source = (ROOT / page).read_text()
        assert "new IntersectionObserver(entries =>" in source
        assert "visibleTileIds.delete(id);" in source
        assert "tileObserver?.unobserve(card);" in source
        assert "!visibleTileIds.has(cam.id)" in source
