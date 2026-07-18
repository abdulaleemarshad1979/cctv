import os
import yt_dlp

url = input("Enter YouTube URL: ")

# Get video information
with yt_dlp.YoutubeDL({
    "quiet": True,
    "js_runtimes": {"node": {}},
    "remote_components": {"ejs:github"}
}) as ydl:
    info = ydl.extract_info(url, download=False)

formats = info["formats"]

# Collect unique video qualities
qualities = {}

for f in formats:
    height = f.get("height")

    if height and f.get("vcodec") != "none":
        quality_name = f"{height}p"

        if quality_name not in qualities:
            qualities[quality_name] = f["format_id"]

# Sort qualities
sorted_qualities = sorted(
    qualities.keys(),
    key=lambda x: int(x.replace("p", ""))
)

print("\nAvailable Qualities:\n")

for idx, q in enumerate(sorted_qualities, start=1):
    print(f"{idx}. {q}")

choice = int(input("\nSelect quality number: "))

selected_quality = sorted_qualities[choice - 1]
height = selected_quality.replace("p", "")

print(f"\nSelected: {selected_quality}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Download chosen quality (video only, no audio merging required)
ydl_opts = {
    "format": f"bestvideo[height={height}]/best[height={height}]",
    "outtmpl": os.path.join(BASE_DIR, "Videos", "K.%(ext)s"),
    "js_runtimes": {"node": {}},
    "remote_components": {"ejs:github"}
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])

print("\nDownload Complete!")


##.\run_lite.bat  