"""Small optional LSTM used by the online crowd-forecast ensemble."""

from __future__ import annotations

import numpy as np
import torch
from torch import nn


FORMAT_VERSION = 1


class CountLSTM(nn.Module):
    def __init__(self, horizon_count, hidden_size=32):
        super().__init__()
        self.lstm = nn.LSTM(2, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, horizon_count)
        # A zero residual is the safe persistence forecast. Horizons without
        # training examples keep this behavior instead of random predictions.
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)

    def forward(self, values):
        _, (hidden, _) = self.lstm(values)
        return self.head(hidden[-1])


def encode_window(values, window_size):
    window = np.asarray(values[-window_size:], dtype=np.float32)
    if window.size != window_size:
        return None
    logged = np.log1p(np.maximum(window, 0.0))
    center = float(logged[-1])
    scale = max(0.05, float(np.std(logged)))
    relative = (logged - center) / scale
    level = np.full(window_size, center / 10.0, dtype=np.float32)
    return np.column_stack((relative, level)), center, scale


def decode_predictions(normalized, center, scale):
    logged = center + np.asarray(normalized, dtype=np.float64) * scale
    return np.maximum(0.0, np.expm1(np.clip(logged, 0.0, 20.0)))


class LSTMForecaster:
    """Load an approved checkpoint and provide low-cost CPU predictions."""

    def __init__(self, checkpoint_path, expected_horizons, sample_interval_seconds):
        checkpoint = torch.load(
            checkpoint_path, map_location="cpu", weights_only=True
        )
        if checkpoint.get("format_version") != FORMAT_VERSION:
            raise ValueError("unsupported LSTM checkpoint format")
        horizons = tuple(int(value) for value in checkpoint["horizons_seconds"])
        if horizons != tuple(int(value) for value in expected_horizons):
            raise ValueError("LSTM checkpoint horizons do not match the server")
        if abs(
            float(checkpoint["sample_interval_seconds"])
            - float(sample_interval_seconds)
        ) > 1e-6:
            raise ValueError("LSTM checkpoint sample interval does not match")

        self.window_size = int(checkpoint["window_size"])
        self.approved = bool(checkpoint.get("approved", False))
        self.validation_accuracy = checkpoint.get("validation_accuracy", {})
        self.training_samples = checkpoint.get("training_samples", {})
        self.validation_samples = checkpoint.get("validation_samples", {})
        hidden_size = int(checkpoint["hidden_size"])
        self.model = CountLSTM(len(horizons), hidden_size)
        self.model.load_state_dict(checkpoint["state_dict"], strict=True)
        self.model.eval()

    def approved_for(self, label, target_accuracy):
        accuracy = self.validation_accuracy.get(label)
        return bool(
            accuracy is not None
            and float(accuracy) >= float(target_accuracy)
            and int(self.training_samples.get(label, 0)) >= 64
            and int(self.validation_samples.get(label, 0)) >= 30
        )

    def predict(self, values):
        encoded = encode_window(values, self.window_size)
        if encoded is None:
            return None
        features, center, scale = encoded
        tensor = torch.from_numpy(features).unsqueeze(0)
        with torch.no_grad():
            normalized = self.model(tensor).squeeze(0).numpy()
        return decode_predictions(normalized, center, scale).tolist()
