"""
presets.py  —  Unified Drone / RTSP Preset Database
"""

DRONE_DB = {

    # ─── DJI ─────────────────────────────────────────────────────────
    "dji_phantom4":     ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-PHANTOM-XXXX"),
    "dji_mavic2":       ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-MAVIC-XXXX"),
    "dji_mavic3":       ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MAVIC3-XXXX"),
    "dji_mini2":        ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI2-XXXX"),
    "dji_mini3":        ("rtsp://192.168.0.1:8554/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI3-XXXX"),
    "dji_mini4pro":     ("rtsp://192.168.0.1:8554/live",
                         "DJI Fly app  -> Wi-Fi: DJI-MINI4PRO-XXXX"),
    "dji_air2s":        ("rtsp://192.168.42.1/live",
                         "DJI Fly app  -> Wi-Fi: DJI-AIR2S-XXXX"),
    # NOTE: Air 3 and Air 3S do NOT expose a direct RTSP server.
    # The DJI Fly app PUSHES RTMP → you must run MediaMTX locally first.
    # Step 1: Install & start MediaMTX (see SYSTEM_GUIDE.md or README).
    # Step 2: In DJI Fly → Transmission → Custom RTMP → rtmp://<YOUR_PC_IP>:1935/live
    # Step 3: MediaMTX re-exposes the stream at rtsp://localhost:8554/live
    # Step 4: Launch with:  DRONE=dji_air3 or DRONE=dji_air3s python infer.py
    "dji_air3":         ("rtsp://localhost:8554/live",
                         "DJI Fly app -> Custom RTMP -> rtmp://YOUR_PC_IP:1935/live  (MediaMTX required — see README)"),
    "dji_air3s":        ("rtsp://localhost:8554/live",
                         "DJI Fly app -> Custom RTMP -> rtmp://YOUR_PC_IP:1935/live  (MediaMTX required — see README)"),
    "dji_spark":        ("rtsp://192.168.0.1/live",
                         "DJI GO 4 app -> Wi-Fi: DJI-SPARK-XXXX"),
    "dji_avata":        ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: FPV Goggles 2"),
    "dji_avata2":       ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: Goggles 3"),
    "dji_fpv":          ("rtsp://10.0.0.22/live",
                         "DJI Fly app  -> Wi-Fi: FPV Goggles"),
    "dji_m300":         ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_m350":         ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_m30":          ("rtsp://192.168.0.1/live",
                         "DJI Pilot 2  -> Wi-Fi: RC Enterprise"),
    "dji_go4":          ("rtsp://192.168.0.1/live",
                         "Any DJI GO 4 drone (Phantom/Mavic 2/Spark)"),
    "dji_fly":          ("rtsp://192.168.42.1/live",
                         "Any DJI Fly drone (Mini 2/3/Air 2S/Mavic 3)"),

    # ─── Parrot ──────────────────────────────────────────────────────
    "parrot_anafi":     ("rtsp://192.168.42.1/live",
                         "FreeFlight 6 -> Wi-Fi: ANAFI-XXXXXX"),
    "parrot_anafi_usa": ("rtsp://192.168.42.1/live",
                         "FreeFlight 6 -> Wi-Fi: ANAFI-USA-XXXX"),
    "parrot_bebop2":    ("rtsp://192.168.42.1/arstream",
                         "FreeFlight Pro -> Wi-Fi: Bebop-XXXXXXXX"),
    "parrot_disco":     ("rtsp://192.168.42.1/live",
                         "FreeFlight Pro -> Wi-Fi: disco-XXXXXXXX"),

    # ─── Autel ───────────────────────────────────────────────────────
    "autel_evo2":       ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-XXXXXX"),
    "autel_evo_lite":   ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-LITE-XXXX"),
    "autel_evo_nano":   ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-NANO-XXXX"),
    "autel_evo_max":    ("rtsp://192.168.0.80/live/ch01",
                         "Autel Sky app -> Wi-Fi: EVO-MAX-XXXX"),

    # ─── Skydio ──────────────────────────────────────────────────────
    "skydio2":          ("rtsp://192.168.110.1/mpeg_ts.264",
                         "Skydio SDK -> Wi-Fi: Skydio-XXXXXX"),
    "skydio_x10":       ("rtsp://192.168.110.1/mpeg_ts.264",
                         "Skydio SDK -> Wi-Fi: SkydioX10-XXXX"),

    # ─── Yuneec ──────────────────────────────────────────────────────
    "yuneec_h520":      ("rtsp://192.168.0.1/live",
                         "DataPilot -> Wi-Fi: YUNEEC-XXXX"),
    "yuneec_typhoonh":  ("rtsp://192.168.0.1:8080/live",
                         "Controller Wi-Fi -> YUNEEC-XXXX"),

    # ─── Freefly ─────────────────────────────────────────────────────
    "freefly_altax":    ("rtsp://192.168.0.1/live",
                         "Freefly app -> Wi-Fi: AltaX-XXXX"),

    # ─── Custom FPV (RPi / OrangePi onboard) ─────────────────────────
    "fpv_rpi":          ("rtsp://192.168.1.100:8554/fpv",
                         "Raspberry Pi onboard running MediaMTX -> confirm IP"),
    "fpv_orange_pi":    ("rtsp://192.168.1.101:8554/fpv",
                         "Orange Pi onboard running MediaMTX -> confirm IP"),

    # ─── IP Cameras (ground-mounted surveillance) ─────────────────────
    "hikvision":        ("rtsp://admin:admin@192.168.1.64:554/h264/ch1/main/av_stream",
                         "Change admin:admin to your credentials"),
    "dahua":            ("rtsp://admin:admin@192.168.1.65:554/cam/realmonitor?channel=1&subtype=0",
                         "Change admin:admin to your credentials"),
    "reolink":          ("rtsp://admin:@192.168.1.66:554/h264Preview_01_main",
                         "Change admin: to your password"),
    "amcrest":          ("rtsp://admin:admin@192.168.1.67:554/cam/realmonitor?channel=1",
                         "Change credentials to yours"),
    "axis":             ("rtsp://192.168.1.68/axis-media/media.amp",
                         "Axis cam — no credentials needed by default"),

    # ─── Phone as camera ─────────────────────────────────────────────
    "android_ipwebcam": ("rtsp://192.168.1.X:8080/h264_ulaw.sdp",
                         "Install 'IP Webcam' (free) -> Start Server -> replace X with shown IP"),
    "iphone_epoccam":   ("rtsp://192.168.1.X:8554/live",
                         "Install 'EpocCam' + PC driver -> replace X with shown IP"),
    "iphone_camo":      ("rtsp://192.168.1.X:8080/live",
                         "Install 'Camo' -> enable RTSP -> replace X with shown IP"),

    # ─── Relay / Restream ────────────────────────────────────────────
    "mediamtx_local":   ("rtsp://localhost:8554/drone",
                         "MediaMTX on same machine -> drone app pushes to it"),
    "mediamtx_server":  ("rtsp://SERVER_IP:8554/drone",
                         "MediaMTX on another machine -> replace SERVER_IP"),
    "obs_studio":       ("rtsp://localhost:8554/obs",
                         "OBS -> RTSP Server plugin -> stream key = obs"),
}
