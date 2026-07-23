import csv

from src.crowd_forecast import CrowdForecaster


def test_sampling_forecast_and_csv(tmp_path):
    path = tmp_path / "crowd_history.csv"
    forecaster = CrowdForecaster(path, sample_interval_seconds=5, min_samples=4)

    first = forecaster.record("cctv-1", 10, now=0)
    skipped = forecaster.record("cctv-1", 99, now=10)
    assert first["samples"] == skipped["samples"] == 1
    assert forecaster.sample_interval_seconds == 15

    result = None
    for index, count in enumerate((12, 14, 16, 18, 20, 22), start=1):
        result = forecaster.record("cctv-1", count, now=index * 15)

    assert result["status"] == "ready"
    assert [item["minutes"] for item in result["predictions"]] == [
        1,
        5,
        15,
        30,
        60,
        120,
        180,
    ]
    assert result["stabilized_count"] == 18
    assert result["predictions"][0]["people_count"] >= 0
    assert result["predictions"][0]["lower_count"] <= result["predictions"][0]["people_count"]
    assert result["predictions"][0]["upper_count"] >= result["predictions"][0]["people_count"]
    assert result["predictions"][0]["model"]

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 7
    assert rows[-1]["raw_observed_count"] == "22"
    assert rows[-1]["session_id"] == "0"
    assert rows[-1]["predicted_180m"]


def test_validation_accuracy_is_measured(tmp_path):
    forecaster = CrowdForecaster(
        tmp_path / "crowd_history.csv", sample_interval_seconds=15, min_samples=4
    )
    result = None
    for index in range(12):
        result = forecaster.record("drone-1", 100, now=index * 15)

    assert result["validation_samples"] >= 4
    assert result["accuracy_percent"] == 100.0


def test_adaptive_model_is_scored_against_future_counts(tmp_path):
    forecaster = CrowdForecaster(
        tmp_path / "crowd_history.csv", sample_interval_seconds=15, min_samples=4
    )
    result = None
    for index in range(24):
        result = forecaster.record("cctv-2", 75, now=index * 15)

    one_minute = result["predictions"][0]
    assert one_minute["confidence_percent"] == 100.0
    assert one_minute["model"] in result["models_selected"]


def test_spikes_are_smoothed_and_resets_start_a_new_session(tmp_path):
    path = tmp_path / "crowd_history.csv"
    forecaster = CrowdForecaster(path, sample_interval_seconds=15, min_samples=4)
    result = None
    for index, count in enumerate((100, 102, 1000, 103, 101)):
        result = forecaster.record("drone-1", count, now=index * 15)

    assert result["raw_count"] == 101
    assert result["stabilized_count"] == 102
    old_session = result["session_id"]
    reset = forecaster.reset("drone-1", "video loop restarted")
    assert reset["session_id"] == old_session + 1
    assert reset["samples"] == 0
    assert "video loop restarted" in reset["message"].lower()
