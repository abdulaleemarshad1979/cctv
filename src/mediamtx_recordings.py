"""Discover finished MediaMTX recordings and build authorized archive links."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx


RETENTION_DAYS = 30
MIN_DURATION_SECONDS = 1.0


class RecordingIndexUnavailable(RuntimeError):
    """The MediaMTX playback service could not be reached."""


def _format_duration(seconds: float) -> str:
    total = round(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"


def _recording_item(camera: dict, path: str, span: dict) -> dict | None:
    try:
        start = datetime.fromisoformat(span["start"].replace("Z", "+00:00"))
        duration = float(span["duration"])
    except (KeyError, TypeError, ValueError):
        return None
    if duration < MIN_DURATION_SECONDS or start.tzinfo is None:
        return None

    start = start.astimezone(timezone.utc)
    local_start = start.astimezone()
    start_value = start.isoformat().replace("+00:00", "Z")
    params = {"path": path, "start": start_value, "duration": f"{duration:.3f}"}
    playback_url = "/api/recordings/play?" + urlencode(params)
    download_url = playback_url + "&download=1"
    name = camera.get("name") or camera.get("id") or path
    location = camera.get("location") or "East Godavari"
    return {
        "id": f"{path}:{start_value}",
        "title": f"{name} — {local_start:%Y-%m-%d %H:%M:%S}",
        "drone_id": camera.get("id", ""),
        "location": location,
        "recorded_at": local_start.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_formatted": _format_duration(duration),
        "duration_seconds": duration,
        "size_formatted": "Recorded video",
        "ai_status": "Drone stream recording",
        "video_url": playback_url,
        "download_url": download_url,
    }


async def list_mediamtx_recordings(
    cameras: list[dict], base_url: str, *, drone: str | None = None,
    search: str | None = None, client: httpx.AsyncClient | None = None,
) -> dict:
    """Read the last 30 days from MediaMTX's private playback index.

    A failed camera query is isolated so one absent feed cannot hide every archive.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    params = {"start": cutoff.isoformat().replace("+00:00", "Z")}
    selected = []
    for camera in cameras:
        path = str(camera.get("source_stream_path") or "").strip("/")
        if not path.startswith("live/"):
            continue
        if drone and drone.lower() not in (str(camera.get("id", "")).lower(), str(camera.get("location", "")).lower()):
            continue
        selected.append((camera, path))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=2.0))
    assert client is not None
    semaphore = asyncio.Semaphore(8)
    failed_queries = 0

    async def fetch(camera: dict, path: str) -> list[dict]:
        nonlocal failed_queries
        async with semaphore:
            try:
                response = await client.get(f"{base_url}/list", params={**params, "path": path})
                if response.status_code == 404:
                    return []
                response.raise_for_status()
                spans = response.json()
                if not isinstance(spans, list):
                    return []
                return [item for span in spans if isinstance(span, dict)
                        if (item := _recording_item(camera, path, span)) is not None]
            except (httpx.HTTPError, ValueError):
                failed_queries += 1
                return []

    try:
        groups = await asyncio.gather(*(fetch(camera, path) for camera, path in selected))
    finally:
        if owns_client:
            await client.aclose()

    if selected and failed_queries == len(selected):
        raise RecordingIndexUnavailable("MediaMTX playback index is unavailable")

    search_text = (search or "").strip().lower()
    items = [item for group in groups for item in group]
    if search_text:
        items = [item for item in items if search_text in item["title"].lower()
                 or search_text in item["location"].lower()
                 or search_text in item["drone_id"].lower()]
    items.sort(key=lambda item: item["id"].partition(":")[2], reverse=True)
    return {"total_recordings": len(items), "recordings": items}
