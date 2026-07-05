from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("health", views.health_check, name="health"),
    path("cameras", views.get_cameras, name="get_cameras"),
    path("auth", views.authenticate_publish, name="auth"),
    path("cameras/state", views.update_camera_state, name="camera_state"),
    path("cameras/update_stats", views.update_camera_stats, name="update_stats"),
    path("cameras/<str:drone_id>/start", views.start_camera_stream, name="start_stream"),
    path("cameras/<str:drone_id>/stop", views.stop_camera_stream, name="stop_stream"),
]
