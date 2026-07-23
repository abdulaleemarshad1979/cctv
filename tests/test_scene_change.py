import numpy as np

from src.scene_change import SceneChangeDetector


def test_scene_change_detector_rejects_same_scene():
    detector = SceneChangeDetector()
    red = np.full((100, 100, 3), (0, 0, 255), dtype=np.uint8)
    green = np.full((100, 100, 3), (0, 255, 0), dtype=np.uint8)
    assert detector.update(red) is False
    assert detector.update(red) is False
    assert detector.update(green) is True
