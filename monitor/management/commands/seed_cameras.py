import json
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from monitor.models import Camera

class Command(BaseCommand):
    help = "Seed/refresh the Camera table with the 36-camera list partitioned between Rjy and Pushkaralu."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing cameras before seeding.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            deleted, _ = Camera.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing camera rows."))

        # Read backend/cameras.json
        cameras_json_path = os.path.join(settings.DRONE_PROJECT_ROOT, "backend", "cameras.json")
        if not os.path.exists(cameras_json_path):
            self.stdout.write(self.style.ERROR(f"cameras.json not found at {cameras_json_path}"))
            return

        with open(cameras_json_path, "r") as f:
            cameras_data = json.load(f)

        created, updated = 0, 0
        for order, cam in enumerate(cameras_data, start=1):
            drone_id = cam["id"]
            
            # Distribute mock errors
            status = "offline"
            if order % 3 == 1:
                error_type = "authentication failed"
            elif order % 3 == 2:
                error_type = "authorization failed"
            else:
                error_type = "stream not found"

            # Check if this camera is already online in MediaMTX or active
            existing = Camera.objects.filter(id=drone_id).first()
            if existing and existing.status == "online":
                status = "online"
                error_type = None

            defaults = {
                "name": cam["name"],
                "location": cam["location"],
                "category": "DRONE",
                "stream_path": cam["stream_path"],
                "publish_user": cam["publish_user"],
                "publish_pass": cam["publish_pass"],
                "stream_url": cam["stream_url"],
                "status": status,
                "error_type": error_type,
                "order": order,
            }
            obj, was_created = Camera.objects.update_or_create(id=drone_id, defaults=defaults)
            created += int(was_created)
            updated += int(not was_created)

        # Auto-ensure drone1 to drone40 exist in the camera database
        for i in range(1, 41):
            drone_id = f"drone{i}"
            status = "offline"
            if i % 3 == 1:
                error_type = "authentication failed"
            elif i % 3 == 2:
                error_type = "authorization failed"
            else:
                error_type = "stream not found"

            existing = Camera.objects.filter(id=drone_id).first()
            if existing and existing.status == "online":
                status = "online"
                error_type = None

            defaults = {
                "name": f"Drone {i}",
                "location": "Pushkaralu Swarm",
                "category": "DRONE",
                "stream_path": f"live/drone{i}",
                "publish_user": "operator",
                "publish_pass": "pushkar2026",
                "stream_url": f"http://localhost:8088/live/drone{i}/index.m3u8",
                "status": status,
                "error_type": error_type,
                "order": 100 + i,
            }
            obj, was_created = Camera.objects.update_or_create(id=drone_id, defaults=defaults)
            created += int(was_created)
            updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete: {created} created/updated."
        ))
