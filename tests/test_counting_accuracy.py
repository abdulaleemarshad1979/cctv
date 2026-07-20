import numpy as np

from src.counting_accuracy import (
    TemporalCountStabilizer,
    allocate_zone_counts,
    count_accuracy_percent,
    detection_zone_counts,
    detections_to_density_map,
    fit_linear_calibration,
)
from src.zone_monitor import ZoneMonitor


def test_stabilizer_never_drops_below_visible_people():
    stabilizer = TemporalCountStabilizer(window_size=3, ema_alpha=0.5)
    assert stabilizer.update(density_count=4, yolo_count=11) >= 11


def test_stabilizer_rejects_a_single_density_spike():
    stabilizer = TemporalCountStabilizer(window_size=3, ema_alpha=1.0)
    stabilizer.update(100, 20)
    stabilizer.update(102, 21)
    stable = stabilizer.update(900, 22)
    assert stable == 102


def test_stabilizer_applies_camera_calibration():
    stabilizer = TemporalCountStabilizer(
        window_size=1,
        ema_alpha=1.0,
        calibration_scale=1.1,
        calibration_bias=5,
    )
    assert stabilizer.update(100, 20) == 115


def test_zone_allocation_preserves_detections_and_matches_total():
    density = np.array([[1, 2, 3], [2, 4, 2], [1, 2, 1]], dtype=np.float32)
    yolo = np.array([[2, 0, 1], [0, 3, 0], [1, 0, 0]], dtype=np.float32)
    zones = allocate_zone_counts(density, yolo, total_count=37.6)

    assert int(zones.sum()) == 38
    assert np.all(zones >= yolo)


def test_density_zones_use_the_same_equal_thirds_as_yolo():
    dmap = np.repeat(np.arange(1, 7, dtype=np.float32)[:, None], 3, axis=1)
    scores, _ = ZoneMonitor().analyze_zones(dmap)

    # Rows are [0:2], [2:4], [4:6], and each column is one pixel wide.
    np.testing.assert_array_equal(scores[:, 0], np.array([3, 7, 11]))


def test_field_calibration_recovers_known_scale_and_bias():
    predicted = np.array([10, 20, 30, 40, 50], dtype=np.float64)
    actual = predicted * 1.2 + 4.0
    scale, bias = fit_linear_calibration(predicted, actual)
    np.testing.assert_allclose([scale, bias], [1.2, 4.0], rtol=1e-8)
    assert count_accuracy_percent(predicted * scale + bias, actual) == 100.0


def test_accuracy_score_penalizes_false_counts_in_empty_scene():
    assert count_accuracy_percent([1], [0]) == 0.0


def test_vectorized_detection_density_preserves_total_count():
    boxes = np.array([
        [0, 0, 10, 10],
        [40, 20, 60, 40],
        [90, 90, 100, 100],
    ], dtype=np.float32)
    density = detections_to_density_map(boxes, (25, 25), (100, 100))

    assert density.shape == (25, 25)
    np.testing.assert_allclose(density.sum(), 3.0, rtol=1e-5)


def test_vectorized_detection_zone_counts_use_box_centers():
    boxes = np.array([
        [0, 0, 10, 10],
        [40, 40, 50, 50],
        [90, 90, 100, 100],
        [91, 91, 99, 99],
    ], dtype=np.float32)
    zones = detection_zone_counts(boxes, (100, 100))

    assert zones[0, 0] == 1
    assert zones[1, 1] == 1
    assert zones[2, 2] == 2
    assert zones.sum() == 4
