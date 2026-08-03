import numpy as np
import torch

from src.crowd_forecast import CrowdForecaster, HORIZONS
from src.lstm_forecast import FORMAT_VERSION, CountLSTM, LSTMForecaster


def _checkpoint(path, approved=True, accuracy=100.0):
    model = CountLSTM(len(HORIZONS), hidden_size=8)
    for parameter in model.parameters():
        torch.nn.init.zeros_(parameter)
    torch.save(
        {
            "format_version": FORMAT_VERSION,
            "approved": approved,
            "state_dict": model.state_dict(),
            "window_size": 4,
            "hidden_size": 8,
            "horizons_seconds": [seconds for _, seconds in HORIZONS],
            "sample_interval_seconds": 15.0,
            "validation_accuracy": {label: accuracy for label, _ in HORIZONS},
            "training_samples": {label: 64 for label, _ in HORIZONS},
            "validation_samples": {label: 30 for label, _ in HORIZONS},
        },
        path,
    )


def test_approved_lstm_checkpoint_predicts_every_horizon(tmp_path):
    path = tmp_path / "crowd_lstm.pt"
    _checkpoint(path)
    forecaster = LSTMForecaster(
        path, [seconds for _, seconds in HORIZONS], 15.0
    )

    predictions = forecaster.predict([100, 100, 100, 100])

    assert len(predictions) == len(HORIZONS)
    assert np.allclose(predictions, 100.0, atol=1e-3)


def test_unapproved_lstm_checkpoint_loads_in_shadow_mode(tmp_path):
    path = tmp_path / "crowd_lstm.pt"
    _checkpoint(path, approved=False)
    forecaster = LSTMForecaster(
        path, [seconds for _, seconds in HORIZONS], 15.0
    )

    assert forecaster.approved is False


def test_lstm_joins_existing_adaptive_candidates(tmp_path):
    path = tmp_path / "crowd_lstm.pt"
    _checkpoint(path)
    forecaster = CrowdForecaster(
        tmp_path / "history.csv", lstm_model_path=path
    )

    candidates = forecaster._candidate_predictions(
        np.asarray([100, 100, 100, 100], dtype=np.float64)
    )

    assert forecaster.lstm_status == "active"
    assert "lstm" in candidates


def test_shadow_lstm_cannot_win_below_accuracy_gate(tmp_path):
    path = tmp_path / "crowd_lstm.pt"
    _checkpoint(path, approved=False)
    forecaster = CrowdForecaster(
        tmp_path / "history.csv", lstm_model_path=path
    )
    candidates = forecaster._candidate_predictions(
        np.asarray([100, 100, 100, 100], dtype=np.float64)
    )
    forecaster.candidate_errors["camera"][("15s", "lstm")].extend([1.0] * 8)
    forecaster.candidate_errors["camera"][("15s", "ensemble")].extend([0.2] * 8)

    selected = forecaster._select_model("camera", "15s", candidates)

    assert forecaster.lstm_status == "shadow validation"
    assert "lstm" in candidates
    assert selected != "lstm"


def test_15_second_lstm_uses_85_percent_offline_gate(tmp_path):
    path = tmp_path / "crowd_lstm.pt"
    _checkpoint(path, approved=False, accuracy=85.4)
    forecaster = CrowdForecaster(
        tmp_path / "history.csv", lstm_model_path=path
    )
    candidates = forecaster._candidate_predictions(
        np.asarray([100, 100, 100, 100], dtype=np.float64)
    )

    selected = forecaster._select_model(
        "camera", "15s", candidates, target_accuracy=85.0
    )

    assert selected == "lstm"
