import time

import numpy as np

from src.stream_output import LatestFrameEncoder, build_ffmpeg_command


class _FakePipe:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakePipe()
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_ffmpeg_command_targets_constant_30_fps_low_latency():
    command = build_ffmpeg_command(
        "ffmpeg",
        "rtmp://127.0.0.1:1935/analyzed/drone1",
        960,
        540,
        30,
    )

    assert command[command.index("-framerate") + 1] == "30"
    assert command[command.index("-g") + 1] == "30"
    assert "zerolatency" in command
    assert command[-1] == "rtmp://127.0.0.1:1935/analyzed/drone1"


def test_encoder_writes_the_latest_frame_without_growing_a_queue():
    fake_process = _FakeProcess()

    def process_factory(*args, **kwargs):
        return fake_process

    encoder = LatestFrameEncoder(
        "ffmpeg",
        "rtmp://127.0.0.1:1935/analyzed/drone1",
        fps=100,
        process_factory=process_factory,
    )
    encoder.start((2, 2, 3))
    first = np.zeros((2, 2, 3), dtype=np.uint8)
    latest = np.full((2, 2, 3), 255, dtype=np.uint8)
    encoder.submit(first)
    encoder.submit(latest)

    deadline = time.monotonic() + 0.5
    while latest.tobytes() not in fake_process.stdin.writes:
        if time.monotonic() >= deadline:
            raise AssertionError("latest frame was not written")
        time.sleep(0.005)

    assert encoder.frames_submitted == 2
    assert encoder.frames_written >= 1
    encoder.stop()
    assert fake_process.terminated is True

