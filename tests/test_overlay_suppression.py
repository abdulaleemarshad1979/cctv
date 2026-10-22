"""Overlay cleaning before inference examines only the margins it cleans."""

import cv2
import numpy as np

from src.density_filter import suppress_broadcast_overlays


def _whole_frame_reference(frame_bgr):
    """The previous implementation: mask the whole frame, then blank its middle."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    bright_sat = (sat > 95) & (val > 135)
    overlay_hues = (
        (hue < 12) | (hue > 168) | ((hue > 24) & (hue < 42)) |
        ((hue > 42) & (hue < 88)) | ((hue > 88) & (hue < 105))
    )
    mask = (bright_sat & overlay_hues).astype(np.uint8) * 255
    margin_h = int(mask.shape[0] * 0.15)
    if margin_h > 0:
        mask[margin_h: mask.shape[0] - margin_h, :] = 0
    if cv2.countNonZero(mask) < 64:
        return frame_bgr
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=1)
    return cv2.inpaint(frame_bgr, mask, 3, cv2.INPAINT_TELEA)


def _frame(rng, height, width):
    frame = cv2.GaussianBlur(
        rng.integers(0, 256, (height, width, 3), dtype=np.uint8), (0, 0), float(rng.uniform(0.5, 4))
    )
    colours = [(0, 0, 255), (0, 255, 255), (0, 255, 0), (255, 255, 0), (40, 40, 250)]
    for _ in range(int(rng.integers(0, 4))):
        cv2.putText(
            frame, "LIVE REC 1080", (int(rng.integers(0, width)), int(rng.integers(0, height))),
            cv2.FONT_HERSHEY_SIMPLEX, float(rng.uniform(0.3, 1.5)),
            colours[int(rng.integers(0, len(colours)))], int(rng.integers(1, 4)),
        )
    return frame


def test_band_only_cleaning_matches_whole_frame_cleaning_exactly():
    rng = np.random.default_rng(7)
    sizes = [(288, 512), (216, 384), (576, 1024), (36, 64), (20, 40), (6, 10), (720, 1280)]
    for trial in range(140):
        height, width = sizes[trial % len(sizes)]
        frame = _frame(rng, height, width)
        if trial % 5 == 0:
            frame[: max(1, height // 6)] = (0, 0, 255)      # a banner across the band edge
        if trial % 9 == 0:
            frame[-max(1, height // 6):] = (0, 255, 255)
        expected = _whole_frame_reference(frame.copy())
        assert np.array_equal(suppress_broadcast_overlays(frame.copy()), expected), (trial, height, width)


def test_clean_frames_are_returned_untouched_and_inputs_never_modified():
    rng = np.random.default_rng(3)
    clean = cv2.GaussianBlur(rng.integers(40, 160, (288, 512, 3), dtype=np.uint8), (0, 0), 3)
    assert suppress_broadcast_overlays(clean) is clean

    marked = clean.copy()
    cv2.putText(marked, "LIVE 1080p", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    before = marked.copy()
    cleaned = suppress_broadcast_overlays(marked)
    assert cleaned is not marked and np.array_equal(marked, before)
    # the watermark is gone, the crowd area in the middle is untouched
    assert not np.array_equal(cleaned[:43], marked[:43])
    assert np.array_equal(cleaned[60:228], marked[60:228])
    assert suppress_broadcast_overlays(None) is None
