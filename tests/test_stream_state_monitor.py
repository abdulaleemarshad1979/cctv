from src.stream_state_monitor import MediaMTXStateMonitor


def _camera(camera_id="drone-1", source_path="live/drone1", online=False):
    return {
        "id": camera_id,
        "source_stream_path": source_path,
        "source_online": online,
    }


def _monitor(cameras, payloads, events, offline_confirmations=2):
    payload_iter = iter(payloads)

    def fetch_json(_url, _timeout):
        payload = next(payload_iter)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def resolve_camera(path):
        for camera in cameras:
            if camera["source_stream_path"] == path:
                return camera, "source"
        return None, None

    def apply_state(path, status):
        events.append((path, status))
        camera, _ = resolve_camera(path)
        camera["source_online"] = status == "online"

    return MediaMTXStateMonitor(
        api_url="http://mediamtx:9997/v3/paths/list",
        list_cameras=lambda: cameras,
        resolve_camera=resolve_camera,
        apply_state=apply_state,
        offline_confirmations=offline_confirmations,
        fetch_json=fetch_json,
    )


def test_active_publisher_is_recovered_after_app_or_ui_restart():
    cameras = [_camera()]
    events = []
    monitor = _monitor(
        cameras,
        [{"items": [{"name": "live/drone1", "ready": True, "online": True}]}],
        events,
    )

    assert monitor.sync_once() == {"live/drone1"}
    assert events == [("live/drone1", "online")]
    assert cameras[0]["source_online"] is True


def test_transient_api_failure_never_marks_healthy_stream_offline():
    cameras = [_camera(online=True)]
    events = []
    monitor = _monitor(cameras, [TimeoutError("temporary timeout")], events)

    assert monitor.sync_once() is None
    assert events == []
    assert cameras[0]["source_online"] is True
    assert "temporary timeout" in monitor.snapshot()["last_error"]


def test_stream_requires_confirmed_absence_before_offline_transition():
    cameras = [_camera(online=True)]
    events = []
    monitor = _monitor(
        cameras,
        [{"items": []}, {"items": []}],
        events,
        offline_confirmations=2,
    )

    monitor.sync_once()
    assert events == []
    assert cameras[0]["source_online"] is True

    monitor.sync_once()
    assert events == [("live/drone1", "offline")]
    assert cameras[0]["source_online"] is False


def test_non_online_recorded_path_is_not_treated_as_live_publisher():
    cameras = [_camera()]
    events = []
    monitor = _monitor(
        cameras,
        [{"items": [{"name": "live/drone1", "ready": True, "online": False}]}],
        events,
    )

    assert monitor.sync_once() == set()
    assert events == []
