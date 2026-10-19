"""
Fast REAL recording archive.

Rules:
- Only physical MediaMTX live/droneN recordings are shown.
- No seeded/demo/fake recording cards.
- ffprobe is executed only for NEW or CHANGED files.
- Verified metadata is persisted in outputs/recordings_index.json.
- Original recording is never modified or transcoded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote


class VideoRecordingsManager:

    VALID_EXTENSIONS = (
        ".mp4",
        ".m4v",
        ".mov",
        ".mkv",
        ".webm",
    )

    REAL_RECORDING_RE = re.compile(
        r"^live/drone(\d+)/.+\.(mp4|m4v|mov|mkv|webm)$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        recordings_dir: str = "recordings",
        metadata_file: Optional[str] = None,
        index_file: Optional[str] = None,
    ):

        self.recordings_dir = os.getenv(
            "RECORDINGS_DIR",
            recordings_dir,
        )

        self.index_file = (
            index_file
            or os.getenv(
                "RECORDINGS_INDEX_FILE",
                "outputs/recordings_index.json",
            )
        )

        os.makedirs(
            self.recordings_dir,
            exist_ok=True,
        )

        index_parent = os.path.dirname(
            self.index_file
        )

        if index_parent:
            os.makedirs(
                index_parent,
                exist_ok=True,
            )

        self._lock = threading.RLock()
        self.index: Dict[str, Dict[str, Any]] = {}

        self._load_index()

    def _load_index(self):

        with self._lock:

            try:

                with open(
                    self.index_file,
                    "r",
                    encoding="utf-8",
                ) as f:

                    payload = json.load(f)

                if (
                    isinstance(payload, dict)
                    and isinstance(
                        payload.get("recordings"),
                        dict,
                    )
                ):
                    self.index = payload["recordings"]

                else:
                    self.index = {}

            except Exception:
                self.index = {}

    def _save_index(self):

        with self._lock:

            payload = {
                "version": 1,
                "generated_at":
                    datetime.now()
                    .astimezone()
                    .isoformat(),

                "recordings":
                    self.index,
            }

            temp = (
                self.index_file
                + ".tmp"
            )

            with open(
                temp,
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    payload,
                    f,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

                f.flush()
                os.fsync(
                    f.fileno()
                )

            os.replace(
                temp,
                self.index_file,
            )

    @staticmethod
    def _format_duration(seconds: float) -> str:

        seconds = max(
            0,
            int(round(seconds)),
        )

        h, rem = divmod(
            seconds,
            3600,
        )

        m, s = divmod(
            rem,
            60,
        )

        if h:
            return (
                f"{h}h "
                f"{m:02d}m "
                f"{s:02d}s"
            )

        if m:
            return (
                f"{m}m "
                f"{s:02d}s"
            )

        return f"{s}s"

    @staticmethod
    def _format_size(size_bytes: int) -> str:

        if size_bytes >= 1024 ** 3:
            return (
                f"{size_bytes / (1024 ** 3):.2f} GB"
            )

        if size_bytes >= 1024 ** 2:
            return (
                f"{size_bytes / (1024 ** 2):.1f} MB"
            )

        if size_bytes >= 1024:
            return (
                f"{size_bytes / 1024:.1f} KB"
            )

        return f"{size_bytes} B"

    @staticmethod
    def _recorded_at(
        relative_path: str,
        file_path: str,
    ) -> str:

        filename = os.path.basename(
            relative_path
        )

        match = re.search(
            r"(\d{4}-\d{2}-\d{2})_"
            r"(\d{2})-(\d{2})-(\d{2})-(\d{6})",
            filename,
        )

        if match:

            try:

                value = (
                    f"{match.group(1)} "
                    f"{match.group(2)}:"
                    f"{match.group(3)}:"
                    f"{match.group(4)}."
                    f"{match.group(5)}"
                )

                dt = datetime.strptime(
                    value,
                    "%Y-%m-%d %H:%M:%S.%f",
                )

                return dt.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            except Exception:
                pass

        return (
            datetime
            .fromtimestamp(
                os.path.getmtime(
                    file_path
                )
            )
            .astimezone()
            .strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

    @staticmethod
    def _read_customizations():

        try:

            with open(
                "config/camera_customizations.json",
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

            return (
                data
                if isinstance(
                    data,
                    dict,
                )
                else {}
            )

        except Exception:
            return {}

    def _probe(
        self,
        file_path: str,
        relative_path: str,
        stat,
    ):

        try:

            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",

                    "-print_format",
                    "json",

                    "-show_entries",
                    (
                        "format=duration,format_name:"
                        "stream=codec_type,"
                        "codec_name,width,height"
                    ),

                    file_path,
                ],

                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,

                timeout=5,
                check=True,
            )

            payload = json.loads(
                proc.stdout or "{}"
            )

        except Exception as exc:

            print(
                "[RECORDINGS] ffprobe failed:",
                relative_path,
                str(exc),
            )

            return None

        video = None

        for stream in (
            payload.get(
                "streams",
                [],
            )
            or []
        ):

            if (
                stream.get(
                    "codec_type"
                )
                == "video"
            ):
                video = stream
                break

        if video is None:
            return None

        try:

            duration = float(
                payload
                .get(
                    "format",
                    {},
                )
                .get(
                    "duration",
                    0,
                )
                or 0
            )

        except Exception:
            duration = 0.0

        width = int(
            video.get(
                "width",
                0,
            )
            or 0
        )

        height = int(
            video.get(
                "height",
                0,
            )
            or 0
        )

        codec = str(
            video.get(
                "codec_name",
                "unknown",
            )
        ).upper()

        resolution = (
            f"{width}×{height}"
            if width and height
            else "Source resolution"
        )

        drone_match = (
            self.REAL_RECORDING_RE
            .match(
                relative_path
            )
        )

        if not drone_match:
            return None

        drone_number = int(
            drone_match.group(1)
        )

        drone_id = (
            f"drone-{drone_number}"
        )

        return {
            "cache_key":
                (
                    f"{stat.st_size}:"
                    f"{stat.st_mtime_ns}"
                ),

            "relative_path":
                relative_path,

            "filename":
                os.path.basename(
                    relative_path
                ),

            "drone_id":
                drone_id,

            "drone_number":
                drone_number,

            "recorded_at":
                self._recorded_at(
                    relative_path,
                    file_path,
                ),

            "duration_seconds":
                duration,

            "size_bytes":
                int(
                    stat.st_size
                ),

            "codec":
                codec,

            "width":
                width,

            "height":
                height,

            "resolution":
                resolution,
        }

    def _to_public_item(
        self,
        record,
        customizations,
    ):

        drone_id = record[
            "drone_id"
        ]

        drone_number = record[
            "drone_number"
        ]

        custom = customizations.get(
            drone_id,
            {},
        )

        name = str(
            custom.get(
                "name"
            )
            or f"DRONE {drone_number}"
        )

        location = str(
            custom.get(
                "location"
            )
            or name
        )

        relative_path = record[
            "relative_path"
        ]

        url = (
            "/recordings/"
            + quote(
                relative_path,
                safe="/",
            )
        )

        duration = float(
            record.get(
                "duration_seconds",
                0,
            )
        )

        size_bytes = int(
            record.get(
                "size_bytes",
                0,
            )
        )

        resolution = record.get(
            "resolution",
            "Source resolution",
        )

        codec = record.get(
            "codec",
            "UNKNOWN",
        )

        return {
            "id":
                relative_path,

            "filename":
                record["filename"],

            "relative_path":
                relative_path,

            "title":
                (
                    f"{name} — "
                    f"{record['recorded_at']}"
                ),

            "drone_id":
                drone_id,

            "location":
                location,

            "recorded_at":
                record[
                    "recorded_at"
                ],

            "duration_seconds":
                duration,

            "duration_formatted":
                self._format_duration(
                    duration
                ),

            "size_bytes":
                size_bytes,

            "size_formatted":
                self._format_size(
                    size_bytes
                ),

            "codec":
                codec,

            "width":
                record.get(
                    "width",
                    0,
                ),

            "height":
                record.get(
                    "height",
                    0,
                ),

            "resolution":
                resolution,

            "quality_display":
                (
                    f"{resolution} • "
                    f"{codec} • "
                    f"Original stream"
                ),

            "ai_status":
                "Verified real drone recording",

            "category":
                "Automatic flight recording",

            "video_url":
                url,

            "download_url":
                url,

            "is_real_file":
                True,

            "source_quality":
                (
                    "Original incoming stream; "
                    "no server re-encode"
                ),
        }

    def list_recordings(
        self,
        drone_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:

        started = time.time()
        now = time.time()

        customizations = (
            self._read_customizations()
        )

        physical_paths = set()
        cached_records = []
        probe_jobs = []

        active_recording = False

        for root, _dirs, files in os.walk(
            self.recordings_dir
        ):

            for filename in files:

                if not filename.lower().endswith(
                    self.VALID_EXTENSIONS
                ):
                    continue

                file_path = os.path.join(
                    root,
                    filename,
                )

                relative_path = os.path.relpath(
                    file_path,
                    self.recordings_dir,
                ).replace(
                    "\\",
                    "/",
                )

                # Only genuine MediaMTX source recordings.
                if not self.REAL_RECORDING_RE.match(
                    relative_path
                ):
                    continue

                try:

                    stat = os.stat(
                        file_path
                    )

                except OSError:
                    continue

                physical_paths.add(
                    relative_path
                )

                # Do not expose an actively-written fragment.
                if (
                    now
                    - stat.st_mtime
                    < 4.0
                ):
                    active_recording = True
                    continue

                if stat.st_size < 1024:
                    continue

                cache_key = (
                    f"{stat.st_size}:"
                    f"{stat.st_mtime_ns}"
                )

                cached = self.index.get(
                    relative_path
                )

                if (
                    cached
                    and cached.get(
                        "cache_key"
                    )
                    == cache_key
                ):
                    cached_records.append(
                        cached
                    )

                else:

                    probe_jobs.append(
                        (
                            file_path,
                            relative_path,
                            stat,
                        )
                    )

        changed = False
        newly_verified = []

        if probe_jobs:

            print(
                "[RECORDINGS] "
                f"Indexing {len(probe_jobs)} "
                f"new/changed recording(s)"
            )

            workers = min(
                10,
                max(
                    2,
                    len(probe_jobs),
                ),
            )

            with ThreadPoolExecutor(
                max_workers=workers
            ) as pool:

                futures = [
                    pool.submit(
                        self._probe,
                        file_path,
                        relative_path,
                        stat,
                    )

                    for (
                        file_path,
                        relative_path,
                        stat,
                    )
                    in probe_jobs
                ]

                for future in as_completed(
                    futures
                ):

                    try:
                        record = future.result()

                    except Exception:
                        record = None

                    if record is None:
                        continue

                    self.index[
                        record[
                            "relative_path"
                        ]
                    ] = record

                    newly_verified.append(
                        record
                    )

                    changed = True

        # Remove deleted recordings from persistent index.
        stale = [
            path
            for path
            in list(
                self.index.keys()
            )
            if path
            not in physical_paths
        ]

        for path in stale:

            self.index.pop(
                path,
                None,
            )

            changed = True

        if changed:
            self._save_index()

        records = (
            cached_records
            + newly_verified
        )

        # Also include valid indexed entries that may not have reached the
        # cached_records list because a currently-written segment was skipped.
        already = {
            r["relative_path"]
            for r in records
        }

        for relative_path in physical_paths:

            if relative_path in already:
                continue

            record = self.index.get(
                relative_path
            )

            if record:
                records.append(
                    record
                )

        items = [
            self._to_public_item(
                record,
                customizations,
            )
            for record
            in records
        ]

        if drone_filter:

            wanted = (
                drone_filter
                .lower()
                .strip()
            )

            items = [
                item
                for item
                in items
                if (
                    item[
                        "drone_id"
                    ].lower()
                    == wanted

                    or wanted
                    in item[
                        "location"
                    ].lower()

                    or wanted
                    in item[
                        "title"
                    ].lower()
                )
            ]

        if search:

            wanted = (
                search
                .lower()
                .strip()
            )

            items = [
                item
                for item
                in items
                if (
                    wanted
                    in item[
                        "title"
                    ].lower()

                    or wanted
                    in item[
                        "location"
                    ].lower()

                    or wanted
                    in item[
                        "drone_id"
                    ].lower()
                )
            ]

        items.sort(
            key=lambda item:
                item[
                    "recorded_at"
                ],
            reverse=True,
        )

        total_bytes = sum(
            item[
                "size_bytes"
            ]
            for item
            in items
        )

        total_seconds = sum(
            item[
                "duration_seconds"
            ]
            for item
            in items
        )

        elapsed = (
            time.time()
            - started
        )

        print(
            "[RECORDINGS] "
            f"{len(items)} REAL recording(s), "
            f"{len(probe_jobs)} needed probing, "
            f"response built in {elapsed:.3f}s"
        )

        return {
            "summary": {
                "total_recordings":
                    len(items),

                "total_duration_hours":
                    round(
                        total_seconds
                        / 3600.0,
                        2,
                    ),

                "total_storage_mb":
                    round(
                        total_bytes
                        / (1024 * 1024),
                        1,
                    ),

                "active_recording":
                    active_recording,

                "new_files_indexed":
                    len(
                        newly_verified
                    ),

                "response_seconds":
                    round(
                        elapsed,
                        3,
                    ),
            },

            "recordings":
                items,
        }


video_recordings_manager = (
    VideoRecordingsManager()
)
