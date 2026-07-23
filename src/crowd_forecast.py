"""Online crowd forecasting with Excel-friendly CSV persistence."""

from __future__ import annotations

import csv
import os
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np


HORIZONS_MINUTES = (1, 5, 15, 30, 60, 120, 180)
MODEL_NAME = "Adaptive champion ensemble"
MIN_MODEL_SCORES = 8


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class CrowdForecaster:
    """Sample crowd counts and fit a small online time-series model per camera."""

    def __init__(
        self,
        csv_path="outputs/crowd_history.csv",
        sample_interval_seconds=15,
        min_samples=12,
        max_samples=5760,
    ):
        self.csv_path = csv_path
        self.sample_interval_seconds = min(
            30.0, max(15.0, float(sample_interval_seconds))
        )
        self.smoothing_samples = max(
            3, int(round(75.0 / self.sample_interval_seconds))
        )
        if self.smoothing_samples % 2 == 0:
            self.smoothing_samples += 1
        self.min_samples = max(4, int(min_samples))
        self.histories = defaultdict(lambda: deque(maxlen=max_samples))
        self.raw_counts = defaultdict(
            lambda: deque(maxlen=self.smoothing_samples)
        )
        self.pending = defaultdict(lambda: deque(maxlen=6000))
        self.candidate_pending = defaultdict(lambda: deque(maxlen=24000))
        self.candidate_errors = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=100))
        )
        self.errors = defaultdict(lambda: deque(maxlen=500))
        self.last_sample = {}
        self.snapshots = {}
        self.sessions = defaultdict(int)
        self.reset_reasons = {}
        self.lock = threading.Lock()
        self.fieldnames = [
            "timestamp_utc",
            "camera_id",
            "session_id",
            "raw_observed_count",
            "observed_count",
            "sample_interval_seconds",
            "model",
            "status",
            "accuracy_percent",
            "validation_samples",
        ]
        for minutes in HORIZONS_MINUTES:
            self.fieldnames.extend(
                [
                    f"predicted_{minutes}m",
                    f"lower_{minutes}m",
                    f"upper_{minutes}m",
                    f"model_{minutes}m",
                    f"target_time_{minutes}m_utc",
                ]
            )
        self._load_history()
        self._ensure_csv()

    def _ensure_csv(self):
        directory = os.path.dirname(os.path.abspath(self.csv_path))
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(self.csv_path) and os.path.getsize(self.csv_path):
            with open(self.csv_path, newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                current_fields = reader.fieldnames or []
            if current_fields == self.fieldnames:
                return
            with open(self.csv_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=self.fieldnames, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(rows)
            return
        with open(self.csv_path, "w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=self.fieldnames).writeheader()

    def _load_history(self):
        if not os.path.exists(self.csv_path):
            return
        try:
            with open(self.csv_path, newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    timestamp = datetime.fromisoformat(
                        row["timestamp_utc"].replace("Z", "+00:00")
                    ).timestamp()
                    camera_id = row["camera_id"]
                    session_id = int(row.get("session_id") or 0)
                    if session_id > self.sessions[camera_id]:
                        self.histories[camera_id].clear()
                        self.sessions[camera_id] = session_id
                    if session_id < self.sessions[camera_id]:
                        continue
                    count = max(0.0, float(row["observed_count"]))
                    self.histories[camera_id].append((timestamp, count))
                    self.last_sample[camera_id] = timestamp
        except (KeyError, TypeError, ValueError, OSError) as exc:
            print(f"[FORECAST] Existing history could not be loaded: {exc}")

    def snapshot(self, camera_id):
        with self.lock:
            return self.snapshots.get(camera_id, self._collecting_snapshot(camera_id))

    def reset(self, camera_id, reason="new feed session"):
        """Start a clean forecasting series without deleting historical CSV rows."""
        with self.lock:
            self.sessions[camera_id] += 1
            self.histories[camera_id].clear()
            self.raw_counts[camera_id].clear()
            self.pending[camera_id].clear()
            self.candidate_pending[camera_id].clear()
            self.candidate_errors[camera_id].clear()
            self.errors[camera_id].clear()
            self.last_sample.pop(camera_id, None)
            self.reset_reasons[camera_id] = reason
            snapshot = self._collecting_snapshot(camera_id)
            self.snapshots[camera_id] = snapshot
            return snapshot

    def record(self, camera_id, count, now=None):
        """Record at most one sample per interval and return the latest forecast."""
        timestamp = float(now) if now is not None else datetime.now().timestamp()
        raw_observed = max(0.0, float(count))
        with self.lock:
            last = self.last_sample.get(camera_id)
            if last is not None and timestamp - last < self.sample_interval_seconds:
                return self.snapshots.get(
                    camera_id, self._collecting_snapshot(camera_id)
                )

            raw_window = self.raw_counts[camera_id]
            raw_window.append(raw_observed)
            observed = float(np.median(raw_window))
            history = self.histories[camera_id]
            history.append((timestamp, observed))
            self.last_sample[camera_id] = timestamp
            self._score_due_predictions(camera_id, observed, timestamp)
            snapshot = self._build_snapshot(camera_id, timestamp)
            self.snapshots[camera_id] = snapshot
            self._append_csv(
                camera_id, raw_observed, observed, snapshot
            )
            return snapshot

    def _collecting_snapshot(self, camera_id):
        samples = len(self.histories[camera_id])
        reset_reason = self.reset_reasons.get(camera_id)
        return {
            "status": "collecting",
            "model": MODEL_NAME,
            "session_id": self.sessions[camera_id],
            "sample_interval_seconds": int(self.sample_interval_seconds),
            "smoothing_window_seconds": int(
                self.smoothing_samples * self.sample_interval_seconds
            ),
            "samples": samples,
            "accuracy_percent": None,
            "validation_samples": len(self.errors[camera_id]),
            "quality_status": "validating",
            "trend": "unknown",
            "predictions": [],
            "message": (
                f"{reset_reason.capitalize()}. Collecting samples "
                f"({samples}/{self.min_samples})"
                if reset_reason
                else f"Collecting samples ({samples}/{self.min_samples})"
            ),
            "reset_reason": reset_reason,
            "download_url": "/forecast/history.csv",
        }

    def _build_snapshot(self, camera_id, timestamp):
        history = self.histories[camera_id]
        if len(history) < self.min_samples:
            return self._collecting_snapshot(camera_id)

        values = np.asarray([point[1] for point in history], dtype=np.float64)
        candidates = self._candidate_predictions(values)
        current = float(values[-1])

        output = []
        for index, minutes in enumerate(HORIZONS_MINUTES):
            target = timestamp + minutes * 60
            selected_model = self._select_model(camera_id, minutes, candidates)
            predicted = candidates[selected_model][index]
            rounded = max(0, int(round(predicted)))
            lower, upper, confidence = self._prediction_interval(
                camera_id, minutes, selected_model, predicted, values
            )
            output.append(
                {
                    "minutes": minutes,
                    "people_count": rounded,
                    "lower_count": lower,
                    "upper_count": upper,
                    "confidence_percent": confidence,
                    "model": selected_model,
                    "target_time_utc": _iso_timestamp(target),
                }
            )
            self.pending[camera_id].append((target, rounded, minutes))
            for model_name, model_predictions in candidates.items():
                self.candidate_pending[camera_id].append(
                    (target, model_predictions[index], minutes, model_name)
                )

        threshold = max(2.0, current * 0.02)
        first_prediction = output[0]["people_count"]
        trend = (
            "rising"
            if first_prediction > current + threshold
            else "falling"
            if first_prediction < current - threshold
            else "stable"
        )

        errors = self.errors[camera_id]
        accuracy = (
            round(max(0.0, 100.0 * (1.0 - float(np.mean(errors)))), 1)
            if errors
            else None
        )
        quality_status = (
            "validating"
            if len(errors) < 20
            else "target_met"
            if accuracy >= 95.0
            else "needs_calibration"
        )
        quality_message = (
            "accuracy is still validating."
            if quality_status == "validating"
            else "the 95% accuracy target is currently met."
            if quality_status == "target_met"
            else "measured accuracy is below 95%; collect more data and calibrate."
        )
        return {
            "status": "ready",
            "model": MODEL_NAME,
            "session_id": self.sessions[camera_id],
            "sample_interval_seconds": int(self.sample_interval_seconds),
            "smoothing_window_seconds": int(
                self.smoothing_samples * self.sample_interval_seconds
            ),
            "samples": len(history),
            "raw_count": int(round(self.raw_counts[camera_id][-1])),
            "stabilized_count": int(round(current)),
            "generated_at_utc": _iso_timestamp(timestamp),
            "accuracy_percent": accuracy,
            "validation_samples": len(errors),
            "quality_status": quality_status,
            "trend": trend,
            "models_selected": sorted({item["model"] for item in output}),
            "predictions": output,
            "message": f"Crowd trend is {trend}; {quality_message}",
            "reset_reason": self.reset_reasons.get(camera_id),
            "download_url": "/forecast/history.csv",
        }

    def _candidate_predictions(self, values):
        """Return several independent forecasts so measured results pick winners."""
        candidates = {
            "holt": self._holt_predictions(values),
            "recent_level": [
                float(np.median(values[-min(8, len(values)) :]))
            ]
            * len(HORIZONS_MINUTES),
        }
        autoregressive = self._autoregressive_predictions(values)
        if autoregressive is not None:
            candidates["autoregressive"] = autoregressive
        seasonal = self._daily_seasonal_predictions(values)
        if seasonal is not None:
            candidates["daily_seasonal"] = seasonal
        candidates["ensemble"] = np.mean(
            np.asarray(list(candidates.values()), dtype=np.float64), axis=0
        ).tolist()
        return candidates

    def _select_model(self, camera_id, minutes, candidates):
        scored = []
        for model_name in candidates:
            errors = self.candidate_errors[camera_id][(minutes, model_name)]
            if len(errors) >= MIN_MODEL_SCORES:
                scored.append((float(np.mean(errors)), model_name))
        return min(scored)[1] if scored else "ensemble"

    def _prediction_interval(
        self, camera_id, minutes, model_name, prediction, values
    ):
        model_errors = self.candidate_errors[camera_id][(minutes, model_name)]
        if len(model_errors) >= MIN_MODEL_SCORES:
            relative_error = float(np.percentile(model_errors, 90))
            confidence = round(
                max(0.0, 100.0 * (1.0 - float(np.mean(model_errors)))), 1
            )
        else:
            changes = np.diff(values[-min(40, len(values)) :])
            noise = float(np.std(changes)) / max(1.0, float(np.mean(values[-8:])))
            steps = max(
                1, int(round(minutes * 60.0 / self.sample_interval_seconds))
            )
            relative_error = min(1.0, max(0.02, noise * np.sqrt(steps)))
            confidence = None
        margin = max(2.0, prediction * relative_error)
        return (
            max(0, int(round(prediction - margin))),
            max(0, int(round(prediction + margin))),
            confidence,
        )

    def _holt_predictions(self, values):
        """Fit Holt's damped-trend model and forecast all configured horizons."""
        level = float(values[0])
        trend = float(values[1] - values[0])
        alpha, beta, damping = 0.45, 0.15, 0.98
        for value in values[1:]:
            previous_level = level
            level = alpha * float(value) + (1.0 - alpha) * (
                level + damping * trend
            )
            trend = beta * (level - previous_level) + (1.0 - beta) * (
                damping * trend
            )

        recent_changes = np.diff(values[-min(len(values), 40) :])
        change_limit = max(1.0, float(np.percentile(np.abs(recent_changes), 90)))
        trend = float(np.clip(trend, -change_limit, change_limit))
        predictions = []
        for minutes in HORIZONS_MINUTES:
            steps = max(
                1, int(round(minutes * 60.0 / self.sample_interval_seconds))
            )
            damped_steps = damping * (1.0 - damping**steps) / (1.0 - damping)
            predictions.append(max(0.0, level + trend * damped_steps))
        return predictions

    def _autoregressive_predictions(self, values):
        """Regularized AR model; enabled only after enough camera history exists."""
        lags = min(24, max(6, len(values) // 5))
        if len(values) < max(36, lags * 3):
            return None
        series = values[-1000:]
        center = float(np.mean(series))
        scale = max(1.0, float(np.std(series)))
        normalized = (series - center) / scale
        x = np.asarray(
            [normalized[index - lags : index] for index in range(lags, len(series))]
        )
        y = normalized[lags:]
        x = np.column_stack((np.ones(len(x)), x))
        regularizer = np.eye(x.shape[1]) * 0.05
        regularizer[0, 0] = 0.0
        coefficients = np.linalg.solve(x.T @ x + regularizer, x.T @ y)

        future = list(normalized[-lags:])
        max_steps = max(
            int(round(minutes * 60.0 / self.sample_interval_seconds))
            for minutes in HORIZONS_MINUTES
        )
        upper_limit = max(50.0, float(np.max(series[-100:])) * 3.0 + 25.0)
        for _ in range(max_steps):
            features = np.asarray([1.0, *future[-lags:]])
            predicted = float(features @ coefficients) * scale + center
            predicted = float(np.clip(predicted, 0.0, upper_limit))
            future.append((predicted - center) / scale)
        return [
            future[
                lags
                + int(round(minutes * 60.0 / self.sample_interval_seconds))
                - 1
            ]
            * scale
            + center
            for minutes in HORIZONS_MINUTES
        ]

    def _daily_seasonal_predictions(self, values):
        period = int(round(24 * 60 * 60 / self.sample_interval_seconds))
        if len(values) < period:
            return None
        return [
            float(
                values[
                    len(values)
                    - period
                    + int(round(minutes * 60.0 / self.sample_interval_seconds))
                    - 1
                ]
            )
            for minutes in HORIZONS_MINUTES
        ]

    def _score_due_predictions(self, camera_id, observed, timestamp):
        queue = self.pending[camera_id]
        waiting = deque(maxlen=queue.maxlen)
        while queue:
            target, predicted, minutes = queue.popleft()
            if target > timestamp:
                waiting.append((target, predicted, minutes))
                continue
            error = (
                0.0
                if observed == predicted == 0
                else min(1.0, abs(predicted - observed) / max(1.0, observed))
            )
            self.errors[camera_id].append(error)
        self.pending[camera_id] = waiting

        candidates = self.candidate_pending[camera_id]
        waiting_candidates = deque(maxlen=candidates.maxlen)
        while candidates:
            target, predicted, minutes, model_name = candidates.popleft()
            if target > timestamp:
                waiting_candidates.append(
                    (target, predicted, minutes, model_name)
                )
                continue
            error = (
                0.0
                if observed == predicted == 0
                else min(1.0, abs(predicted - observed) / max(1.0, observed))
            )
            self.candidate_errors[camera_id][(minutes, model_name)].append(error)
        self.candidate_pending[camera_id] = waiting_candidates

    def _append_csv(self, camera_id, raw_observed, observed, snapshot):
        row = {
            "timestamp_utc": _iso_timestamp(self.last_sample[camera_id]),
            "camera_id": camera_id,
            "session_id": self.sessions[camera_id],
            "raw_observed_count": int(round(raw_observed)),
            "observed_count": int(round(observed)),
            "sample_interval_seconds": int(self.sample_interval_seconds),
            "model": snapshot["model"],
            "status": snapshot["status"],
            "accuracy_percent": snapshot["accuracy_percent"],
            "validation_samples": snapshot["validation_samples"],
        }
        for prediction in snapshot["predictions"]:
            minutes = prediction["minutes"]
            row[f"predicted_{minutes}m"] = prediction["people_count"]
            row[f"lower_{minutes}m"] = prediction["lower_count"]
            row[f"upper_{minutes}m"] = prediction["upper_count"]
            row[f"model_{minutes}m"] = prediction["model"]
            row[f"target_time_{minutes}m_utc"] = prediction["target_time_utc"]
        with open(self.csv_path, "a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=self.fieldnames).writerow(row)
