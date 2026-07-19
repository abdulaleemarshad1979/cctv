import numpy as np

from src.counting_accuracy import TemporalCountStabilizer, allocate_zone_counts
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
