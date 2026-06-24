import yt_dlp

url = "https://youtu.be/L-YyR1oN66w?si=8inB6ka-rew7VVCw"

ydl_opts = {
    "format": "best",
    "outtmpl": "./Videos/mecca.mp4"
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("\nDownload completed!")

except Exception as e:
    print("\nERROR:")
    print(e)