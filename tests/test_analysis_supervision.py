"""lite_server: analyzer cap, queue, restart and the reasons shown on the tile."""
import time
from unittest.mock import patch

import pytest

with patch("subprocess.Popen"):
    import lite_server

from src.analysis_scheduler import AnalysisScheduler


class FakeProc:
    def __init__(self, log_path=None):
        self.returncode = None
        self.terminated = False
        self.pid = 4242
        self._started_at = time.time()
        self._log_path = log_path

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


@pytest.fixture
def stack(monkeypatch, tmp_path):
    spawned = []

    def fake_start_stream(camera, source_url):
        lite_server.stop_stream(camera["id"], keep_demand=True)
        proc = FakeProc()
        lite_server.running_processes[camera["id"]] = proc
        spawned.append((camera["id"], source_url, proc))
        return proc.pid

    monkeypatch.setattr(lite_server, "stream_events",
                        lite_server.StreamEventLog(str(tmp_path / "events.jsonl")))
    monkeypatch.setattr(lite_server, "get_worker_dependency_error", lambda: None)
    monkeypatch.setattr(lite_server, "start_stream", fake_start_stream)
    monkeypatch.setattr(lite_server, "analysis_scheduler", AnalysisScheduler(2))
    lite_server.running_processes.clear()
    lite_server.global_counting_mode = True
    lite_server.load_cameras()
    yield spawned
    lite_server.running_processes.clear()
    lite_server.load_cameras()


def _go_online(index):
    camera = lite_server.cameras_db[index]
    camera["source_online"] = True
    camera["status"] = "online"
    lite_server.start_live_analysis(camera)
    return camera


def test_only_max_active_analyzers_run_and_the_rest_wait_with_a_reason(stack):
    cams = [_go_online(i) for i in range(3)]

    assert [s[0] for s in stack] == ["drone-1", "drone-2"]
    assert cams[0]["analytics_status"] == "starting"
    assert cams[2]["analytics_status"] == "queued"
    assert "waiting for a free analysis slot" in cams[2]["connection_message"]
    assert "2 of 2" in cams[2]["connection_message"]
    assert cams[2]["error_type"] is None


def test_freed_slot_goes_to_the_oldest_waiting_camera(stack):
    cams = [_go_online(i) for i in range(4)]
    assert [s[0] for s in stack] == ["drone-1", "drone-2"]

    lite_server.stop_stream("drone-1")             # drone-1 went offline
    lite_server.run_analysis_scheduler()

    assert [s[0] for s in stack] == ["drone-1", "drone-2", "drone-3"]
    assert cams[2]["analytics_status"] == "starting"
    assert cams[3]["analytics_status"] == "queued"
    assert "#1" in cams[3]["connection_message"]


def test_crashed_analyzer_is_restarted_not_left_on_counting_unavailable(stack):
    cam = _go_online(0)
    stack[0][2].returncode = -9                    # killed (out of memory)

    lite_server.run_analysis_scheduler()           # reaps and schedules the restart
    assert cam["analytics_status"] == "restarting"
    assert "out of memory" in cam["connection_message"]
    assert len(stack) == 1

    lite_server.analysis_scheduler.entry("drone-1").next_attempt_at = 0.0
    lite_server.run_analysis_scheduler()
    assert len(stack) == 2 and stack[1][0] == "drone-1"
    assert cam["analytics_status"] == "starting"


def test_analyzer_that_never_produces_a_count_is_restarted(stack, monkeypatch):
    cam = _go_online(0)
    stack[0][2]._started_at = time.time() - lite_server.ANALYTICS_START_TIMEOUT_SECONDS - 5

    lite_server.run_analysis_scheduler()

    assert stack[0][2].terminated
    assert cam["analytics_status"] == "restarting"
    assert "no count within" in cam["connection_message"]


def test_frozen_analyzer_is_restarted_after_results_stop(stack):
    cam = _go_online(0)
    cam["analytics_status"] = "stale"
    cam["stats_updated_at"] = time.time() - lite_server.ANALYTICS_RESTART_AFTER_STALE_SECONDS - 5

    lite_server.run_analysis_scheduler()

    assert stack[0][2].terminated and cam["analytics_status"] == "restarting"


def test_source_going_offline_cancels_a_queued_request(stack):
    cams = [_go_online(i) for i in range(3)]
    assert cams[2]["analytics_status"] == "queued"
    lite_server.stop_stream("drone-3")             # what update_camera_state does on offline
    lite_server.stop_stream("drone-1")
    lite_server.run_analysis_scheduler()
    assert "drone-3" not in [s[0] for s in stack]


def test_model_unavailable_exit_code_shows_the_worker_reason_and_waits(stack, tmp_path):
    cam = _go_online(0)
    log = tmp_path / "drone-1.log"
    log.write_text("[INFO] loading\n[FATAL] model_unavailable: DM-Count weights not found at /app/x.pth\n")
    stack[0][2].returncode = 3
    stack[0][2]._log_path = str(log)

    lite_server.run_analysis_scheduler()

    assert cam["analytics_status"] == "error"
    assert cam["error_type"] == "analysis unavailable"
    assert "model could not be loaded" in cam["connection_message"]
    assert "model_nwpu.pth" in cam["connection_message"]
    assert len(stack) == 1                         # no crash loop


def test_missing_weights_are_reported_before_any_process_is_spawned(monkeypatch, tmp_path):
    monkeypatch.setattr(lite_server, "get_worker_dependency_error", lambda: None)
    monkeypatch.setattr(lite_server.config, "WEIGHTS_PATH", str(tmp_path / "pretrained" / "model_nwpu.pth"))
    monkeypatch.setattr(lite_server, "analysis_scheduler", AnalysisScheduler(2))
    lite_server.running_processes.clear()
    lite_server.global_counting_mode = True
    lite_server.load_cameras()
    cam = lite_server.cameras_db[0]
    cam["source_online"] = True
    cam["status"] = "online"

    with patch.object(lite_server.subprocess, "Popen", side_effect=AssertionError("must not spawn")):
        lite_server.start_live_analysis(cam)

    assert cam["analytics_status"] == "error"
    assert "model_nwpu.pth" in cam["connection_message"]
    assert "missing on the server" in cam["connection_message"]
    assert cam["error_type"] == "analysis unavailable"
    assert lite_server.get_model_files_error("CCTV") is None
    lite_server.load_cameras()


def test_worker_exit_messages_name_the_likely_cause(tmp_path):
    proc = FakeProc()
    proc.returncode = -9
    assert "out of memory" in lite_server._worker_exit_message(proc)
    proc.returncode = -11
    assert "segmentation fault" in lite_server._worker_exit_message(proc)
    proc.returncode = 1
    assert "exit code 1" in lite_server._worker_exit_message(proc)


def test_external_mediamtx_host_does_not_start_or_download_a_local_one(monkeypatch):
    monkeypatch.setattr(lite_server, "MEDIAMTX_HOST", "mediamtx")
    with patch.object(lite_server.subprocess, "Popen", side_effect=AssertionError("must not spawn")), \
         patch.object(lite_server, "generate_mediamtx_config", side_effect=AssertionError("must not rewrite")):
        lite_server.cleanup_and_start_mediamtx()
