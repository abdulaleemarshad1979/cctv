import yt_dlp

url = input("Enter YouTube URL: ")

# Get video information
with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
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
selected_format = qualities[selected_quality]

print(f"\nSelected: {selected_quality}")

# Download chosen quality + best audio
ydl_opts = {
    "format": f"{selected_format}+bestaudio/best",
    "merge_output_format": "mp4",
    "ffmpeg_location": r"C:\Users\abdul\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin",
    "outtmpl": "./Videos/%(title)s.%(ext)s",
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])

print("\nDownload Complete!")