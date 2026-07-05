from django.contrib import admin

from .models import Camera


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "location", "category", "status", "error_type", "order", "is_active")
    list_filter = ("status", "category", "location", "is_active")
    search_fields = ("id", "name", "location", "stream_path")
    ordering = ("order", "name")
