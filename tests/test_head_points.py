import cv2
import numpy as np
import pytest

from src.counting_accuracy import (
    box_head_points,
    density_head_points,
    locate_heads,
    point_zone_counts,
)
from src.overlay import HeadDotLayer


def _density_from_heads(heads, map_shape, frame_shape, sigma=1.0):
    """Synthesize a DM-Count style map: one unit of mass per head."""
    density = np.zeros(map_shape, dtype=np.float32)
    sy = map_shape[0] / frame_shape[0]
    sx = map_shape[1] / frame_shape[1]
    for x, y in heads:
        density[int(y * sy), int(x * sx)] += 1.0
    density = cv2.GaussianBlur(density, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    return density * (len(heads) / density.sum())


def _matched_fraction(predicted, truth, radius):
    """Greedy one-to-one match within ``radius`` pixels."""
    remaining = list(map(tuple, predicted))
    hits = 0
    for tx, ty in truth:
        if not remaining:
            break
        d = [np.hypot(px - tx, py - ty) for px, py in remaining]
        best = int(np.argmin(d))
        if d[best] <= radius:
            hits += 1
            remaining.pop(best)
    return hits / max(1, len(truth))


def test_box_head_point_is_top_of_upright_box_and_centre_of_overhead_box():
    heads = box_head_points([[100, 100, 120, 160], [0, 0, 20, 20]])
    upright, overhead = heads
    assert upright[0] == pytest.approx(110)
    assert upright[1] == pytest.approx(106)        # 10% down a 60px box
    assert overhead == pytest.approx([10, 10])      # square blob -> centre


def test_sparse_crowd_dots_land_on_heads_and_match_count():
    rng = np.random.default_rng(0)
    frame_shape, map_shape = (576, 1024), (72, 128)
    # Heads at least 3 map cells (24px) apart, as in a sparse scene.
    heads = []
    while len(heads) < 40:
        p = rng.uniform([16, 16], [1008, 560])
        if all(np.hypot(*(p - q)) > 24 for q in heads):
            heads.append(p)
    dmap = _density_from_heads(heads, map_shape, frame_shape)

    points = density_head_points(dmap, dmap.sum(), frame_shape)

    assert len(points) == 40
    assert _matched_fraction(points, heads, radius=8) == 1.0


def test_dense_crowd_places_exactly_count_dots_inside_the_crowd():
    rng = np.random.default_rng(1)
    frame_shape, map_shape = (576, 1024), (72, 128)
    # 900 people packed into a 200x150px region: several heads per map cell.
    heads = rng.uniform([400, 200], [600, 350], size=(900, 2))
    dmap = _density_from_heads(heads, map_shape, frame_shape)

    points = density_head_points(dmap, 900, frame_shape)

    assert len(points) == 900
    assert np.all((points[:, 0] > 380) & (points[:, 0] < 620))
    assert np.all((points[:, 1] > 180) & (points[:, 1] < 370))
    # Dots are spread out, not stacked on cell centres.
    assert len(np.unique(np.round(points), axis=0)) > 850


def test_locate_heads_keeps_detections_and_does_not_dot_them_twice():
    frame_shape, map_shape = (576, 1024), (72, 128)
    heads = [(100, 100), (300, 300), (700, 400)]
    dmap = _density_from_heads(heads, map_shape, frame_shape)
    detected = np.array([[100, 100], [300, 300]], dtype=np.float32)

    points = locate_heads(dmap, 3, frame_shape, detected_heads=detected)

    assert len(points) == 3
    assert np.allclose(points[:2], detected)
    assert np.hypot(*(points[2] - (700, 400))) < 8


def test_zero_count_or_empty_map_yields_no_dots():
    assert len(density_head_points(np.zeros((9, 16)), 0, (72, 128))) == 0
    assert len(density_head_points(np.zeros((9, 16)), 5, (72, 128))) == 0


def test_zone_counts_from_points_sum_to_dot_count():
    points = np.array([[10, 10], [500, 10], [1000, 570], [1000, 570]], dtype=np.float32)
    zones = point_zone_counts(points, (576, 1024))
    assert zones.sum() == 4
    assert zones[0, 0] == 1 and zones[0, 1] == 1 and zones[2, 2] == 2


def test_head_dot_layer_paints_once_per_point_and_caches_by_key():
    layer = HeadDotLayer()
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    points = np.array([[100, 100], [1000, 600]], dtype=np.float32)
    layer.update(points, (720, 1280), img.shape[:2], key=1)
    layer.draw(img)

    green = np.all(img == (0, 255, 0), axis=2).astype(np.uint8)
    n_blobs, _ = cv2.connectedComponents(green)
    assert n_blobs - 1 == 2
    assert tuple(img[50, 50]) == (0, 255, 0)            # 100/2, 100/2
    cached = layer._fill_idx
    layer.update(np.empty((0, 2)), (720, 1280), img.shape[:2], key=1)
    assert layer._fill_idx is cached
