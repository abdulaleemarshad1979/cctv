"""Accuracy helpers for stable, internally consistent crowd counts."""

from collections import deque

import cv2
import numpy as np


class TemporalCountStabilizer:
    """Robustly combine a density estimate with YOLO's detected-person floor.

    The density model remains the primary counter for occluded crowds. YOLO
    guarantees that individually detected people are never lost, while a
    short median window and EMA suppress one-frame spikes.
    """

    def __init__(
        self,
        window_size=3,
        ema_alpha=0.65,
        calibration_scale=1.0,
        calibration_bias=0.0,
    ):
        self.window_size = max(1, int(window_size))
        self.ema_alpha = float(np.clip(ema_alpha, 0.0, 1.0))
        self.calibration_scale = max(0.0, float(calibration_scale))
        self.calibration_bias = float(calibration_bias)
        self._history = deque(maxlen=self.window_size)
        self._value = None

    def reset(self):
        self._history.clear()
        self._value = None

    def update(self, density_count, yolo_count):
        calibrated_density = max(
            0.0,
            float(density_count) * self.calibration_scale + self.calibration_bias,
        )
        detected_people = max(0.0, float(yolo_count))
        raw_count = max(calibrated_density, detected_people)
        self._history.append(raw_count)

        target = float(np.median(np.asarray(self._history, dtype=np.float32)))
        if self._value is None:
            self._value = target
        else:
            self._value += self.ema_alpha * (target - self._value)

        # Never report fewer people than YOLO can directly see now.
        self._value = max(self._value, detected_people)
        return self._value


def detections_to_density_map(boxes, map_shape, frame_shape, sigma=2.0):
    """Convert detection centers to a normalized pseudo-density map.

    The previous implementation visited a 13x13 Python loop around every
    detection (O(detections * radius^2) Python operations). This version bins
    all centers once and performs the Gaussian work in optimized OpenCV code.
    Its retained memory is one ``map_shape`` float array.
    """
    map_h, map_w = (int(map_shape[0]), int(map_shape[1]))
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    if map_h <= 0 or map_w <= 0 or frame_h <= 0 or frame_w <= 0:
        raise ValueError("map and frame dimensions must be positive")

    density = np.zeros((map_h, map_w), dtype=np.float32)
    coordinates = np.asarray(boxes, dtype=np.float32)
    if coordinates.size == 0:
        return density
    coordinates = coordinates.reshape(-1, 4)

    center_x = (coordinates[:, 0] + coordinates[:, 2]) * 0.5
    center_y = (coordinates[:, 1] + coordinates[:, 3]) * 0.5
    valid = np.isfinite(center_x) & np.isfinite(center_y)
    if not np.any(valid):
        return density
    map_x = np.floor(center_x[valid] * (map_w / frame_w)).astype(np.intp)
    map_y = np.floor(center_y[valid] * (map_h / frame_h)).astype(np.intp)
    map_x = np.clip(map_x, 0, map_w - 1)
    map_y = np.clip(map_y, 0, map_h - 1)

    np.add.at(density, (map_y, map_x), 1.0)
    density = cv2.GaussianBlur(
        density,
        ksize=(0, 0),
        sigmaX=max(0.1, float(sigma)),
        sigmaY=max(0.1, float(sigma)),
        borderType=cv2.BORDER_CONSTANT,
    )
    density_sum = float(density.sum())
    if density_sum > 0.0:
        density *= float(np.count_nonzero(valid)) / density_sum
    return density


def detection_zone_counts(boxes, frame_shape, rows=3, columns=3):
    """Count detection centers into a grid in O(number of detections)."""
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    rows, columns = int(rows), int(columns)
    if frame_h <= 0 or frame_w <= 0 or rows <= 0 or columns <= 0:
        raise ValueError("frame and grid dimensions must be positive")

    result = np.zeros((rows, columns), dtype=np.float32)
    coordinates = np.asarray(boxes, dtype=np.float32)
    if coordinates.size == 0:
        return result
    coordinates = coordinates.reshape(-1, 4)

    center_x = (coordinates[:, 0] + coordinates[:, 2]) * 0.5
    center_y = (coordinates[:, 1] + coordinates[:, 3]) * 0.5
    valid = np.isfinite(center_x) & np.isfinite(center_y)
    grid_x = np.floor(center_x[valid] * (columns / frame_w)).astype(np.intp)
    grid_y = np.floor(center_y[valid] * (rows / frame_h)).astype(np.intp)
    grid_x = np.clip(grid_x, 0, columns - 1)
    grid_y = np.clip(grid_y, 0, rows - 1)
    np.add.at(result, (grid_y, grid_x), 1.0)
    return result


def box_head_points(boxes):
    """Return one ``(x, y)`` head point per person box, in frame pixels.

    Standing people seen obliquely have their head near the top of the box;
    people seen from directly overhead appear as a near-square blob whose
    centre is the head. The head height fraction is interpolated between the
    two by box aspect ratio. O(detections), fully vectorised.
    """
    coordinates = np.asarray(boxes, dtype=np.float32).reshape(-1, 4)
    if coordinates.size == 0:
        return np.empty((0, 2), dtype=np.float32)
    width = np.maximum(coordinates[:, 2] - coordinates[:, 0], 1e-3)
    height = np.maximum(coordinates[:, 3] - coordinates[:, 1], 1e-3)
    # aspect <= 1.2 -> overhead (head at 50% height); >= 2.0 -> upright (10%).
    blend = np.clip((height / width - 1.2) / 0.8, 0.0, 1.0)
    head_fraction = 0.5 - 0.4 * blend
    points = np.column_stack((
        (coordinates[:, 0] + coordinates[:, 2]) * 0.5,
        coordinates[:, 1] + height * head_fraction,
    ))
    return points[np.all(np.isfinite(points), axis=1)].astype(np.float32)


def density_head_points(density_map, count, frame_shape, exclude_points=None,
                        exclude_radius=1):
    """Place exactly ``round(count)`` head points on a density map.

    DM-Count and CSRNet are trained on head-point annotations, so local maxima
    of the density map sit on heads. Points are chosen in two stages:

    1. Strict 3x3 local maxima, strongest first (one head per peak). In sparse
       and medium crowds this stage alone places every point.
    2. In dense crowds several heads merge into one map cell, so fewer peaks
       exist than people. The remainder is distributed over all cells in
       proportion to density (largest-remainder rounding) and spread inside
       each cell on a fixed sub-cell pattern so dots do not stack.

    ``exclude_points`` (frame pixels, e.g. YOLO heads already placed) zero the
    density within ``exclude_radius`` map cells so a person is not dotted
    twice. Runs in O(map cells) time with O(map cells) scratch memory.
    """
    target = max(0, int(round(float(count))))
    if target == 0 or density_map is None:
        return np.empty((0, 2), dtype=np.float32)

    dmap = np.clip(np.asarray(density_map, dtype=np.float32), 0.0, None)
    if dmap.ndim != 2 or dmap.size == 0:
        return np.empty((0, 2), dtype=np.float32)
    map_h, map_w = dmap.shape
    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    if frame_h <= 0 or frame_w <= 0:
        raise ValueError("frame dimensions must be positive")
    cell_w, cell_h = frame_w / map_w, frame_h / map_h

    if exclude_points is not None and len(exclude_points):
        taken = np.asarray(exclude_points, dtype=np.float32).reshape(-1, 2)
        mask = np.zeros(dmap.shape, dtype=np.uint8)
        mask[
            np.clip((taken[:, 1] / cell_h).astype(np.intp), 0, map_h - 1),
            np.clip((taken[:, 0] / cell_w).astype(np.intp), 0, map_w - 1),
        ] = 1
        if exclude_radius > 0:
            size = 2 * int(exclude_radius) + 1
            mask = cv2.dilate(mask, np.ones((size, size), np.uint8))
        masked = np.where(mask > 0, 0.0, dmap).astype(np.float32)
        # Keep the original map when the detections already cover every cell
        # with density; the remaining people must still be placed somewhere.
        if float(masked.sum()) > 0.0:
            dmap = masked

    total_mass = float(dmap.sum())
    if total_mass <= 0.0:
        return np.empty((0, 2), dtype=np.float32)

    flat = dmap.ravel()
    # A peak must carry a meaningful share of a person; this rejects the
    # near-zero ripples DM-Count leaves on background texture.
    floor = max(1e-4, 0.05 * float(flat.max()))
    dilated = cv2.dilate(dmap, np.ones((3, 3), np.uint8)).ravel()
    peaks = np.flatnonzero((flat >= dilated) & (flat >= floor))
    if peaks.size > target:
        strongest = np.argpartition(flat[peaks], peaks.size - target)
        peaks = peaks[strongest[peaks.size - target:]]

    indices = peaks
    offsets = _refine_peak_offsets(dmap, peaks)

    remaining = target - peaks.size
    if remaining > 0:
        quota = flat * (remaining / total_mass)
        extra = np.floor(quota).astype(np.int64)
        leftover = remaining - int(extra.sum())
        if leftover > 0:
            remainder = quota - extra
            top = np.argpartition(remainder, remainder.size - leftover)
            extra[top[remainder.size - leftover:]] += 1
        cells = np.flatnonzero(extra)
        repeats = extra[cells]
        fill_indices = np.repeat(cells, repeats)
        # Rank of each extra point inside its cell (0, 1, 2, ...); peaks
        # already occupy the cell centre, so ranks start one slot later.
        starts = np.repeat(np.cumsum(repeats) - repeats, repeats)
        rank = np.arange(fill_indices.size) - starts
        rank = rank + np.isin(fill_indices, peaks)
        indices = np.concatenate((indices, fill_indices))
        offsets = np.concatenate((offsets, _sub_cell_offsets(rank)))

    rows, cols = np.divmod(indices, map_w)
    return np.column_stack((
        (cols + offsets[:, 0]) * cell_w,
        (rows + offsets[:, 1]) * cell_h,
    )).astype(np.float32)


def _refine_peak_offsets(dmap, peaks):
    """Sub-cell ``(x, y)`` offsets (0..1) for density peaks.

    The density map is 1/8 of the input resolution, so a bare cell centre can
    be 4 input pixels off its head. The mass-weighted centroid of the 3x3
    neighbourhood places the dot where the head actually is. The shift is
    limited to 0.35 cell so a neighbouring person cannot drag it across.
    Vectorised: O(peaks).
    """
    offsets = np.full((peaks.size, 2), 0.5, dtype=np.float32)
    if peaks.size == 0:
        return offsets
    map_h, map_w = dmap.shape
    rows, cols = np.divmod(peaks, map_w)
    weight_sum = np.zeros(peaks.size, dtype=np.float32)
    shift_x = np.zeros(peaks.size, dtype=np.float32)
    shift_y = np.zeros(peaks.size, dtype=np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            weight = dmap[
                np.clip(rows + dy, 0, map_h - 1), np.clip(cols + dx, 0, map_w - 1)
            ]
            weight_sum += weight
            shift_x += weight * dx
            shift_y += weight * dy
    valid = weight_sum > 0.0
    safe_sum = np.where(valid, weight_sum, 1.0)
    offsets[:, 0] += np.clip(np.where(valid, shift_x / safe_sum, 0.0), -0.35, 0.35)
    offsets[:, 1] += np.clip(np.where(valid, shift_y / safe_sum, 0.0), -0.35, 0.35)
    return offsets


# Golden-angle spiral inside a unit cell: rank 0 is the centre and successive
# ranks spread evenly towards the cell edge without repeating positions.
def _sub_cell_offsets(rank):
    rank = np.asarray(rank, dtype=np.float32)
    radius = 0.42 * np.sqrt(rank / (rank + 1.0))
    angle = rank * 2.39996323
    return np.column_stack((
        0.5 + radius * np.cos(angle),
        0.5 + radius * np.sin(angle),
    )).astype(np.float32)


def locate_heads(density_map, count, frame_shape, detected_heads=None):
    """Return exactly ``round(count)`` head points in frame pixels.

    Detected heads (YOLO) are kept as-is; the density map supplies the rest,
    excluding the cells the detections already occupy.
    """
    target = max(0, int(round(float(count))))
    detected = (
        np.empty((0, 2), dtype=np.float32)
        if detected_heads is None
        else np.asarray(detected_heads, dtype=np.float32).reshape(-1, 2)
    )
    if detected.shape[0] >= target or density_map is None:
        return detected[:target] if detected.shape[0] >= target else detected
    rest = density_head_points(
        density_map,
        target - detected.shape[0],
        frame_shape,
        exclude_points=detected,
    )
    return np.concatenate((detected, rest)).astype(np.float32)


def point_zone_counts(points, frame_shape, rows=3, columns=3):
    """Count head points into a grid in O(points)."""
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    rows, columns = int(rows), int(columns)
    if frame_h <= 0 or frame_w <= 0 or rows <= 0 or columns <= 0:
        raise ValueError("frame and grid dimensions must be positive")
    result = np.zeros((rows, columns), dtype=np.float32)
    points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if points.size == 0:
        return result
    grid_x = np.clip((points[:, 0] * (columns / frame_w)).astype(np.intp), 0, columns - 1)
    grid_y = np.clip((points[:, 1] * (rows / frame_h)).astype(np.intp), 0, rows - 1)
    np.add.at(result, (grid_y, grid_x), 1.0)
    return result


def allocate_zone_counts(density_scores, yolo_scores, total_count):
    """Return integer 3x3 zone counts that sum exactly to the total count.

    Every YOLO detection is preserved in its zone. The density model then
    distributes the remaining (typically occluded) people using its spatial
    density map and a largest-remainder allocation.
    """

    density = np.clip(np.asarray(density_scores, dtype=np.float64), 0.0, None)
    detected = np.rint(
        np.clip(np.asarray(yolo_scores, dtype=np.float64), 0.0, None)
    ).astype(np.int64)

    if density.shape != detected.shape:
        raise ValueError("density_scores and yolo_scores must have the same shape")

    requested_total = max(0, int(round(float(total_count))))
    detected_total = int(detected.sum())
    final_total = max(requested_total, detected_total)
    remaining = final_total - detected_total

    result = detected.copy()
    if remaining <= 0:
        return result.astype(np.float32)

    weights = density.copy()
    weight_sum = float(weights.sum())
    if weight_sum <= 0.0:
        weights = np.ones_like(weights, dtype=np.float64)
        weight_sum = float(weights.sum())

    exact_extra = weights / weight_sum * remaining
    extra = np.floor(exact_extra).astype(np.int64)
    result += extra

    leftover = remaining - int(extra.sum())
    if leftover > 0:
        order = np.argsort((exact_extra - extra).ravel())[::-1]
        flat = result.ravel()
        for index in order[:leftover]:
            flat[index] += 1

    return result.astype(np.float32)


def fit_linear_calibration(predicted_counts, actual_counts):
    """Fit ``actual ≈ predicted * scale + bias`` from verified samples."""
    predicted = np.asarray(predicted_counts, dtype=np.float64)
    actual = np.asarray(actual_counts, dtype=np.float64)
    if predicted.shape != actual.shape or predicted.ndim != 1:
        raise ValueError("predicted_counts and actual_counts must be 1-D and equal")
    if predicted.size < 3:
        raise ValueError("at least three verified samples are required")
    if not np.all(np.isfinite(predicted)) or not np.all(np.isfinite(actual)):
        raise ValueError("calibration counts must be finite")
    if np.any(predicted < 0) or np.any(actual < 0):
        raise ValueError("calibration counts must be non-negative")

    design = np.column_stack((predicted, np.ones_like(predicted)))
    scale, bias = np.linalg.lstsq(design, actual, rcond=None)[0]
    return max(0.0, float(scale)), float(bias)


def count_accuracy_percent(predicted_counts, actual_counts):
    """Return a bounded count accuracy based on mean absolute % error."""
    predicted = np.asarray(predicted_counts, dtype=np.float64)
    actual = np.asarray(actual_counts, dtype=np.float64)
    if predicted.shape != actual.shape or predicted.size == 0:
        raise ValueError("predicted_counts and actual_counts must be non-empty and equal")
    if np.any(actual < 0):
        raise ValueError("actual counts must be non-negative")
    # A denominator of one keeps empty-scene false positives visible instead
    # of dropping those important samples from the accuracy score.
    denominator = np.maximum(actual, 1.0)
    mape = np.mean(np.abs(predicted - actual) / denominator) * 100.0
    if mape < 1e-10:
        return 100.0
    return float(np.clip(100.0 - mape, 0.0, 100.0))


MAX_PUBLISHED_HEADS = 8000


def pack_head_points(points, frame_shape, max_points=MAX_PUBLISHED_HEADS):
    """Pack head points as little-endian ``uint16`` ``(x, y)`` pairs.

    Coordinates are normalised to the frame (0..65535 = left..right and
    top..bottom), so the browser can place a dot on any display size and the
    payload is 4 bytes per head. More than ``max_points`` are thinned evenly
    (O(points)). Returns ``(bytes, packed_count)``.
    """
    frame_h, frame_w = (int(frame_shape[0]), int(frame_shape[1]))
    if frame_h <= 0 or frame_w <= 0:
        raise ValueError("frame dimensions must be positive")
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] > max_points > 0:
        pts = pts[np.linspace(0, pts.shape[0] - 1, max_points).astype(np.intp)]
    if pts.shape[0] == 0:
        return b"", 0
    scaled = pts / np.array([frame_w, frame_h], dtype=np.float32)
    packed = np.rint(np.clip(scaled, 0.0, 1.0) * 65535.0).astype("<u2")
    return packed.tobytes(), int(packed.shape[0])


def unpack_head_points(data):
    """Inverse of :func:`pack_head_points`: ``(N, 2)`` floats in 0..1."""
    if not data:
        return np.empty((0, 2), dtype=np.float32)
    if len(data) % 4:
        raise ValueError("head payload must be a multiple of 4 bytes")
    return np.frombuffer(data, dtype="<u2").reshape(-1, 2).astype(np.float32) / 65535.0
