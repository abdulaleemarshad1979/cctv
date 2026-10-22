from src.analysis_scheduler import (
    AnalysisScheduler,
    BACKOFF,
    BLOCKED,
    EXIT_MODEL_UNAVAILABLE,
    QUEUED,
    RUNNING,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _scheduler(max_active=2, **kwargs):
    clock = Clock()
    return AnalysisScheduler(max_active, clock=clock, **kwargs), clock


def test_never_starts_more_than_max_active_and_queues_the_rest_in_order():
    scheduler, clock = _scheduler(max_active=2)
    for index in (1, 2, 3, 4):
        scheduler.want(f"drone-{index}", f"rtsp://x/{index}")
        clock.now += 1

    due = scheduler.due(active_ids=[])
    assert [e.camera_id for e in due] == ["drone-1", "drone-2"]
    for entry in due:
        scheduler.started(entry.camera_id)

    assert scheduler.due(active_ids=["drone-1", "drone-2"]) == []
    assert scheduler.queue_position("drone-3", ["drone-1", "drone-2"]) == 1
    assert scheduler.queue_position("drone-4", ["drone-1", "drone-2"]) == 2

    # A slot frees up: the oldest waiting camera is next, and only one starts.
    scheduler.drop("drone-1")
    assert [e.camera_id for e in scheduler.due(["drone-2"])] == ["drone-3"]


def test_crashed_worker_restarts_with_growing_backoff_and_heals_when_it_delivers():
    scheduler, clock = _scheduler(max_active=1)
    scheduler.want("drone-1", "rtsp://x/1")
    scheduler.started("drone-1")

    delays = []
    for _ in range(3):
        entry = scheduler.exited("drone-1", -11, "segfault")
        assert entry.state == BACKOFF
        delays.append(round(entry.next_attempt_at - clock.now))
        assert scheduler.due([]) == []              # not before the backoff ends
        clock.now += delays[-1]
        assert [e.camera_id for e in scheduler.due([])] == ["drone-1"]
        scheduler.started("drone-1")
    assert delays == [2, 4, 8]

    scheduler.healthy("drone-1")                     # produced a count
    assert round(scheduler.exited("drone-1", -9, "oom").next_attempt_at - clock.now) == 2


def test_crash_loop_is_blocked_with_a_reason_and_retried_slowly():
    scheduler, clock = _scheduler(max_active=1, max_failures=3, blocked_retry=300)
    scheduler.want("drone-1", "rtsp://x/1")
    entry = None
    for _ in range(3):
        entry = scheduler.exited("drone-1", 1, "boom")
        clock.now += 61
    assert entry.state == BLOCKED
    assert "3 times" in entry.blocked_reason and "boom" in entry.blocked_reason
    assert scheduler.due([]) == []
    clock.now += 300
    assert [e.camera_id for e in scheduler.due([])] == ["drone-1"]


def test_missing_model_files_block_without_a_crash_loop():
    scheduler, clock = _scheduler(max_active=1, model_retry=120)
    scheduler.want("drone-1", "rtsp://x/1")
    entry = scheduler.exited("drone-1", EXIT_MODEL_UNAVAILABLE, "weights missing")
    assert entry.state == BLOCKED and entry.blocked_reason == "weights missing"
    assert scheduler.due([]) == []
    clock.now += 119
    assert scheduler.due([]) == []
    clock.now += 2
    assert [e.camera_id for e in scheduler.due([])] == ["drone-1"]


def test_dropped_camera_is_forgotten_and_snapshot_reports_state():
    scheduler, _ = _scheduler(max_active=1)
    scheduler.want("drone-1", "rtsp://x/1")
    scheduler.want("drone-2", "rtsp://x/2")
    snapshot = scheduler.snapshot(active_ids=["drone-1"])
    assert snapshot["drone-1"]["state"] == RUNNING
    assert snapshot["drone-2"]["state"] == QUEUED
    scheduler.drop("drone-2")
    assert "drone-2" not in scheduler.snapshot()
    assert scheduler.entry("drone-2") is None
