from django.db import models


class Camera(models.Model):
    """Represents a single drone / CCTV feed shown on the monitoring wall."""

    STATUS_CHOICES = [
        ("online", "Online"),
        ("offline", "Offline"),
        ("error", "Error"),
    ]

    id = models.SlugField(primary_key=True, max_length=80)
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(max_length=60, blank=True, default="")

    # MediaMTX / RTMP publishing details
    stream_path = models.CharField(max_length=200, help_text="MediaMTX path, e.g. live/gunadala_ps")
    publish_user = models.CharField(max_length=80, blank=True, default="")
    publish_pass = models.CharField(max_length=80, blank=True, default="")

    # Playback (HLS) URL served by MediaMTX
    stream_url = models.URLField(max_length=300)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="offline")
    error_type = models.CharField(max_length=120, blank=True, null=True, default="stream not found")

    people_count = models.IntegerField(default=0)
    comp_zone = models.CharField(max_length=40, default="SAFE")

    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
            "category": self.category,
            "stream_path": self.stream_path,
            "stream_url": self.stream_url,
            "status": self.status,
            "error_type": self.error_type,
            "people_count": self.people_count,
            "comp_zone": self.comp_zone,
        }
