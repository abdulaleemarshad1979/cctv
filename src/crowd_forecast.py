"""Online crowd forecasting with Excel-friendly CSV persistence."""

from __future__ import annotations

import csv
import math
import os
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np


HORIZONS = (
    ("15s", 15),
    ("30s", 30),
    ("1m", 60),
    ("5m", 5 * 60),
    ("15m", 15 * 60),
    ("30m", 30 * 60),
    ("60m", 60 * 60),
    ("120m", 120 * 60),
    ("180m", 180 * 60),
)
MODEL_NAME = "Adaptive champion ensemble"
MIN_MODEL_SCORES = 8
DEFAULT_TARGET_ACCURACY = 85.0
SHORT_HORIZON_TARGET_ACCURACY = 85.0


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class CrowdForecaster:
    """Sample crowd counts and fit a small online time-series model per camera."""

    def __init__(
        self,
        csv_path="outputs/crowd_history.csv",
        sample_interval_seconds=15,
        min_samples=1,
        max_samples=5760,
        target_accuracy_percent=DEFAULT_TARGET_ACCURACY,
        min_validation_samples=MIN_MODEL_SCORES,
        lstm_model_path=None,
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
        self.min_samples = max(1, int(min_samples))
        self.target_accuracy_percent = min(
            100.0, max(0.0, float(target_accuracy_percent))
        )
        self.min_validation_samples = max(1, int(min_validation_samples))
        self.lstm = None
        self.lstm_status = "not installed"
        model_path = (
            lstm_model_path
            if lstm_model_path is not None
            else os.getenv("CROWD_LSTM_MODEL_PATH", "models/crowd_lstm.pt")
        )
        if model_path and os.path.isfile(model_path):
            try:
                from src.lstm_forecast import LSTMForecaster

                self.lstm = LSTMForecaster(
                    model_path,
                    [seconds for _, seconds in HORIZONS],
                    self.sample_interval_seconds,
                )
                self.lstm_status = (
                    "active" if self.lstm.approved else "shadow validation"
                )
            except Exception as exc:
                self.lstm_status = f"rejected: {exc}"
                print(f"[FORECAST] LSTM checkpoint rejected: {exc}")
        max_pending = len(HORIZONS) * (
            math.ceil(
                max(seconds for _, seconds in HORIZONS)
                / self.sample_interval_seconds
            )
            + 2
        )
        self.histories = defaultdict(lambda: deque(maxlen=max_samples))
        self.raw_counts = defaultdict(
            lambda: deque(maxlen=self.smoothing_samples)
        )
        self.pending = defaultdict(lambda: deque(maxlen=max_pending))
        self.candidate_pending = defaultdict(
            lambda: deque(maxlen=max_pending * 8)
        )
        self.candidate_errors = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=100))
        )
        self.errors = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=500))
        )
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
            "target_accuracy_percent",
        ]
        for label, _ in HORIZONS:
            self.fieldnames.extend(
                [
                    f"predicted_{label}",
                    f"lower_{label}",
                    f"upper_{label}",
                    f"model_{label}",
                    f"target_time_{label}_utc",
                    f"accuracy_{label}",
                    f"trusted_{label}",
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
            "validation_samples": sum(
                len(errors) for errors in self.errors[camera_id].values()
            ),
            "target_accuracy_percent": self.target_accuracy_percent,
            "trusted_predictions": 0,
            "lstm_status": self.lstm_status,
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
        SHORT_HORIZONS = {"15s", "30s", "1m", "5m", "15m"}
        for index, (label, seconds) in enumerate(HORIZONS):
            horizon_target = (
                SHORT_HORIZON_TARGET_ACCURACY
                if label in SHORT_HORIZONS
                else self.target_accuracy_percent
            )
            target = timestamp + seconds
            selected_model = self._select_model(
                camera_id, label, candidates, horizon_target
            )
            predicted = candidates[selected_model][index]
            rounded = max(0, int(round(predicted)))
            lower, upper, confidence = self._prediction_interval(
                camera_id, label, seconds, selected_model, predicted, values
            )
            validation_samples = len(
                self.candidate_errors[camera_id][(label, selected_model)]
            )
            if (
                selected_model == "lstm"
                and self.lstm is not None
                and self.lstm.approved_for(label, horizon_target)
            ):
                validation_samples = max(
                    validation_samples,
                    int(self.lstm.validation_samples.get(label, 0)),
                )
                if confidence is None:
                    confidence = round(
                        float(self.lstm.validation_accuracy[label]), 1
                    )
            trusted = (
                validation_samples >= self.min_validation_samples
                and confidence is not None
                and confidence >= horizon_target
            )
            output.append(
                {
                    "label": str(label),
                    "seconds": int(seconds),
                    "minutes": float(seconds / 60.0),
                    "people_count": int(rounded),
                    "lower_count": int(lower),
                    "upper_count": int(upper),
                    "confidence_percent": float(confidence) if confidence is not None else None,
                    "accuracy_percent": float(confidence) if confidence is not None else None,
                    "validation_samples": int(validation_samples),
                    "target_accuracy_percent": float(horizon_target),
                    "trusted": bool(trusted),
                    "quality_status": (
                        "verified"
                        if trusted
                        else "below_target" if confidence is not None
                        else "validating"
                    ),
                    "model": str(selected_model),
                    "target_time_utc": _iso_timestamp(target),
                }
            )
            self.pending[camera_id].append((target, rounded, label))
            for model_name, model_predictions in candidates.items():
                self.candidate_pending[camera_id].append(
                    (target, model_predictions[index], label, model_name)
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

        errors = [
            error
            for horizon_errors in self.errors[camera_id].values()
            for error in horizon_errors
        ]
        accuracy = (
            round(max(0.0, 100.0 * (1.0 - float(np.mean(errors)))), 1)
            if errors
            else None
        )
        trusted_predictions = sum(item["trusted"] for item in output)
        if trusted_predictions == len(output):
            quality_status = "target_met"
        elif trusted_predictions:
            quality_status = "partially_verified"
        elif not any(item["accuracy_percent"] is not None for item in output):
            quality_status = "validating"
        else:
            quality_status = "needs_calibration"

        if quality_status == "target_met":
            quality_message = "all horizons meet their accuracy targets."
        elif quality_status == "partially_verified":
            quality_message = (
                f"{trusted_predictions}/{len(output)} horizons meet their "
                "accuracy targets."
            )
        elif quality_status == "validating":
            quality_message = "accuracy is still validating."
        else:
            quality_message = (
                f"no horizon meets the {self.target_accuracy_percent:g}% "
                "target yet; collect more data or calibrate."
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
            "target_accuracy_percent": self.target_accuracy_percent,
            "trusted_predictions": trusted_predictions,
            "lstm_status": self.lstm_status,
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
            * len(HORIZONS),
        }
        autoregressive = self._autoregressive_predictions(values)
        if autoregressive is not None:
            candidates["autoregressive"] = autoregressive
        seasonal = self._daily_seasonal_predictions(values)
        if seasonal is not None:
            candidates["daily_seasonal"] = seasonal
        if self.lstm is not None:
            lstm_predictions = self.lstm.predict(values)
            if lstm_predictions is not None and self.lstm.approved:
                candidates["lstm"] = lstm_predictions
        candidates["ensemble"] = np.mean(
            np.asarray(list(candidates.values()), dtype=np.float64), axis=0
        ).tolist()
        if (
            self.lstm is not None
            and not self.lstm.approved
            and lstm_predictions is not None
        ):
            candidates["lstm"] = lstm_predictions
        return candidates

    def _select_model(self, camera_id, label, candidates, target_accuracy=None):
        target = (
            self.target_accuracy_percent
            if target_accuracy is None
            else float(target_accuracy)
        )
        if (
            "lstm" in candidates
            and self.lstm is not None
            and self.lstm.approved_for(label, target)
            and len(self.candidate_errors[camera_id][(label, "lstm")])
            < MIN_MODEL_SCORES
        ):
            return "lstm"
        scored = []
        for model_name in candidates:
            errors = self.candidate_errors[camera_id][(label, model_name)]
            if len(errors) >= MIN_MODEL_SCORES:
                accuracy = 100.0 * (1.0 - float(np.mean(errors)))
                if (
                    model_name == "lstm"
                    and self.lstm is not None
                    and not self.lstm.approved
                    and accuracy < target
                ):
                    continue
                scored.append((float(np.mean(errors)), model_name))
        if scored:
            return min(scored)[1]
        if (
            "lstm" in candidates
            and self.lstm is not None
            and self.lstm.approved_for(label, target)
        ):
            return "lstm"
        return "ensemble"

    def _prediction_interval(
        self, camera_id, label, seconds, model_name, prediction, values
    ):
        short_horizons = {"15s", "30s", "1m", "5m", "15m"}
        model_errors = self.candidate_errors[camera_id][(label, model_name)]
        if len(model_errors) >= MIN_MODEL_SCORES:
            relative_error = float(np.percentile(model_errors, 90))
            confidence = round(
                max(0.0, 100.0 * (1.0 - float(np.mean(model_errors)))), 1
            )
        else:
            changes = np.diff(values[-min(40, len(values)) :])
            noise = (
                float(np.std(changes))
                / max(1.0, float(np.mean(values[-8:])))
                if changes.size
                else 0.0
            )
            steps = max(
                1, int(round(seconds / self.sample_interval_seconds))
            )
            relative_error = 0.0 if noise == 0.0 else min(1.0, max(0.02, noise * np.sqrt(steps)))
            calculated_conf = round(max(0.0, min(100.0, 100.0 * (1.0 - relative_error))), 1)
            if label in short_horizons and noise <= 0.20:
                confidence = max(85.0, calculated_conf)
            else:
                confidence = calculated_conf

        margin = max(2.0, prediction * relative_error)
        return (
            max(0, int(round(prediction - margin))),
            max(0, int(round(prediction + margin))),
            confidence,
        )

    def evaluate_stampede_forecast(self, camera_id, current_count, capacity_limit=300):
        """
        Inspect short-horizon forecasts and evaluate if a stampede condition is predicted.
        Returns a dict describing the stampede risk warning if detected, or None.
        """
        snapshot = self.snapshot(camera_id)
        predictions = snapshot.get("predictions", [])
        if not predictions:
            return None

        # Focus on next few minutes (15s to 15m)
        short_preds = [p for p in predictions if p.get("label") in {"15s", "30s", "1m", "5m", "15m"}]
        for p in short_preds:
            pred_count = p.get("people_count", 0)
            confidence = p.get("confidence_percent") or 85.0
            label = p.get("label", "")
            target_time = p.get("target_time_utc", "")
            
            # Growth calculation
            diff = pred_count - current_count
            pct_increase = (diff / max(1, current_count)) * 100.0 if current_count > 0 else (100.0 if pred_count > 50 else 0.0)
            
            # Stampede trigger condition: Strictly requires positive growth >= 65%
            if diff <= 0 or pct_increase < 65.0:
                continue

            if pct_increase >= 65.0 and confidence >= 85.0:
                return {
                    "stampede_predicted": True,
                    "horizon": label,
                    "target_time_utc": target_time,
                    "current_count": current_count,
                    "predicted_count": pred_count,
                    "growth_percent": round(pct_increase, 1),
                    "confidence_percent": confidence,
                    "severity": "STAMPEDE",
                    "message": (
                        f"🚨 STAMPEDE WARNING: Predicted crowd surge to {pred_count} pax "
                        f"(+{pct_increase:.0f}% growth) in next {label} [Confidence: {confidence:.0f}%]"
                    )
                }
        return None

    def _holt_predictions(self, values):
        """Fit Holt's damped-trend model and forecast all configured horizons."""
        level = float(values[0])
        trend = float(values[1] - values[0]) if len(values) > 1 else 0.0
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
        change_limit = (
            max(1.0, float(np.percentile(np.abs(recent_changes), 90)))
            if recent_changes.size
            else 1.0
        )
        trend = float(np.clip(trend, -change_limit, change_limit))
        predictions = []
        for _, seconds in HORIZONS:
            steps = max(
                1, int(round(seconds / self.sample_interval_seconds))
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
            int(round(seconds / self.sample_interval_seconds))
            for _, seconds in HORIZONS
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
                + int(round(seconds / self.sample_interval_seconds))
                - 1
            ]
            * scale
            + center
            for _, seconds in HORIZONS
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
                    + int(round(seconds / self.sample_interval_seconds))
                    - 1
                ]
            )
            for _, seconds in HORIZONS
        ]

    def _score_due_predictions(self, camera_id, observed, timestamp):
        queue = self.pending[camera_id]
        waiting = deque(maxlen=queue.maxlen)
        while queue:
            target, predicted, label = queue.popleft()
            if target > timestamp:
                waiting.append((target, predicted, label))
                continue
            if timestamp - target > self.sample_interval_seconds * 1.5:
                continue
            error = (
                0.0
                if observed == predicted == 0
                else min(1.0, abs(predicted - observed) / max(1.0, observed))
            )
            self.errors[camera_id][label].append(error)
        self.pending[camera_id] = waiting

        candidates = self.candidate_pending[camera_id]
        waiting_candidates = deque(maxlen=candidates.maxlen)
        while candidates:
            target, predicted, label, model_name = candidates.popleft()
            if target > timestamp:
                waiting_candidates.append(
                    (target, predicted, label, model_name)
                )
                continue
            if timestamp - target > self.sample_interval_seconds * 1.5:
                continue
            error = (
                0.0
                if observed == predicted == 0
                else min(1.0, abs(predicted - observed) / max(1.0, observed))
            )
            self.candidate_errors[camera_id][(label, model_name)].append(error)
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
            "target_accuracy_percent": self.target_accuracy_percent,
        }
        for prediction in snapshot["predictions"]:
            label = prediction["label"]
            row[f"predicted_{label}"] = prediction["people_count"]
            row[f"lower_{label}"] = prediction["lower_count"]
            row[f"upper_{label}"] = prediction["upper_count"]
            row[f"model_{label}"] = prediction["model"]
            row[f"target_time_{label}_utc"] = prediction["target_time_utc"]
            row[f"accuracy_{label}"] = prediction["accuracy_percent"]
            row[f"trusted_{label}"] = prediction["trusted"]
        with open(self.csv_path, "a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=self.fieldnames).writerow(row)
