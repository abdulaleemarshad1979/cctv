"""Rewrite the ?v= content hash on the dashboards' /static/monitor/js scripts.

The site sits behind a CDN that caches /static for hours, so every script URL
carries the first 10 hex digits of the file's SHA-1; a changed script gets a
new URL and can never be served stale. tests/test_video_playback_behavior.py
fails when a hash is out of date. After editing a script run:

    python tools/refresh_asset_versions.py
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["egdms-player.js"]
PAGES = ["lite_dashboard.html", "admin_dashboard.html"]


def version(script):
    return hashlib.sha1((ROOT / "static/monitor/js" / script).read_bytes()).hexdigest()[:10]


def main():
    for page in PAGES:
        path = ROOT / page
        text = path.read_text(encoding="utf-8")
        for script in SCRIPTS:
            pattern = re.compile(rf'(/static/monitor/js/{re.escape(script)}\?v=)[0-9A-Za-z]+')
            text, count = pattern.subn(rf"\g<1>{version(script)}", text)
            print(f"{page}: {script} -> {version(script)} ({count} reference{'s' * (count != 1)})")
        path.write_text(text, encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
