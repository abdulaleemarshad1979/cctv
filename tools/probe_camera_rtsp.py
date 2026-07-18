"""
tools/probe_camera_rtsp.py
===========================
Run this from a machine that's on the SAME local network as the camera
(the camera's own setup Wi-Fi, or the LAN side of its 4G router) to find
out whether it exposes RTSP, and at what path.

Most cheap 3G/4G "scan-a-QR-code" CCTV cameras (sold under many different
storefront brand names — CamHi, CamHiPro, Hichip, XMeye/XM, Wanscam-clone,
etc.) are built on one of a handful of chipsets, so even when the app never
mentions RTSP, the camera's firmware often still runs an RTSP server on one
of a small set of well-known paths. This script just tries them all.

Usage:
    python tools/probe_camera_rtsp.py 192.168.1.108
    python tools/probe_camera_rtsp.py 192.168.1.108 --user admin --password 12345

Requires ffprobe (ships with ffmpeg) on PATH.
"""

import argparse
import shutil
import subprocess
import sys

# (port, path) — path patterns seen across CamHi/Hichip/XMeye-family and
# generic ONVIF-ish firmware used by many rebadged 3G/4G CCTV cameras.
CANDIDATES = [
    (554,   "/11"),                      # CamHi/Hichip main stream
    (554,   "/12"),                      # CamHi/Hichip sub stream
    (554,   "/stream1"),                 # generic
    (554,   "/stream2"),
    (554,   "/live/ch00_0"),             # XMeye/XM-family main
    (554,   "/live/ch00_1"),             # XMeye/XM-family sub
    (554,   "/h264/ch1/main/av_stream"), # Hikvision-derived firmware
    (554,   "/cam/realmonitor?channel=1&subtype=0"),  # Dahua-derived firmware
    (554,   "/onvif1"),
    (8554,  "/live"),                    # MediaMTX-style / some 4G routers
    (554,   "/tcp/av0_0"),               # older Yoosee/V380-family
    (8557,  "/live/ch00_0"),
]


def try_url(url, timeout=6):
    if shutil.which("ffprobe") is None:
        print("ffprobe not found on PATH — install ffmpeg first.")
        sys.exit(1)
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
             "-timeout", str(timeout * 1_000_000), "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height",
             "-of", "default=noprint_wrappers=1", url],
            capture_output=True, text=True, timeout=timeout + 4,
        )
        return result.returncode == 0 and result.stdout.strip(), result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ip", help="Camera's LOCAL IP (must be reachable from this machine)")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    print(f"Probing {args.ip} for RTSP support (this can take a minute)...\n")
    found = []
    for port, path in CANDIDATES:
        cred = f"{args.user}:{args.password}@" if args.user else ""
        url = f"rtsp://{cred}{args.ip}:{port}{path}"
        display_url = f"rtsp://{args.ip}:{port}{path}"
        ok, info = try_url(url)
        status = "OK" if ok else "no response"
        print(f"  [{status:>11}]  {display_url}")
        if ok:
            found.append((url, info))

    print()
    if found:
        print(f"Found {len(found)} working RTSP endpoint(s):")
        for url, info in found:
            safe_url = url if not args.password else url.replace(args.password, "****")
            print(f"  {safe_url}")
            print(f"    {info.replace(chr(10), ', ')}")
        print("\nUse the first working URL as CCTV_SOURCE / in sources.txt.")
    else:
        print("No RTSP endpoint responded with these credentials/paths.")
        print("Next steps:")
        print("  - Double-check the camera's LOCAL ip (not its cloud UID) and that")
        print("    this machine is really on the same network segment.")
        print("  - Try different credentials with --user/--password.")
        print("  - Check the camera app for a hidden 'RTSP/ONVIF' or 'Advanced'")
        print("    settings menu — some firmwares only enable the RTSP server")
        print("    after you toggle it on there.")
        print("  - If genuinely no RTSP, look for a 'Custom RTMP/Cloud server'")
        print("    push option instead (see docs/CCTV_4G_SETUP.md).")


if __name__ == "__main__":
    main()
