import os
import tempfile
import unittest
from pathlib import Path
from src.crowd_forecast import CrowdForecaster
from lite_server import app, stampede_notifications, _notifications_lock
from fastapi.testclient import TestClient


class TestStampedeNotifications(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_short_horizon_confidence_is_boosted_to_at_least_85(self):
        forecaster = CrowdForecaster(
            self.tmp_path / "history.csv", sample_interval_seconds=15, min_samples=1
        )
        for i, count in enumerate([100, 105, 110, 115]):
            result = forecaster.record("drone-1", count, now=i * 15)

        short_labels = {"15s", "30s", "1m", "5m", "15m"}
        for pred in result["predictions"]:
            if pred["label"] in short_labels:
                conf = pred["confidence_percent"]
                self.assertIsNotNone(conf)
                self.assertGreaterEqual(conf, 85.0)

    def test_stampede_forecast_evaluator_triggers_surge_warning(self):
        forecaster = CrowdForecaster(
            self.tmp_path / "history.csv", sample_interval_seconds=15, min_samples=1
        )
        # Rapid growth from 100 to 700 pax (600% growth >= 65%)
        for i, count in enumerate([100, 200, 350, 500, 700]):
            forecaster.record("cctv-1", count, now=i * 15)

        warning = forecaster.evaluate_stampede_forecast("cctv-1", current_count=350, capacity_limit=300)
        self.assertIsNotNone(warning)
        self.assertTrue(warning["stampede_predicted"])
        self.assertEqual(warning["severity"], "STAMPEDE")
        self.assertGreaterEqual(warning["confidence_percent"], 85.0)
        self.assertIn("STAMPEDE WARNING", warning["message"])

    def test_notifications_api_endpoints(self):
        client = TestClient(app)
        
        from lite_server import _last_notification_time
        _last_notification_time.clear()

        clear_res = client.post("/api/notifications/clear")
        self.assertEqual(clear_res.status_code, 200)

        payload = {
            "drone_id": "drone-1",
            "density_score": 350.0,
            "comp_zone": "STAMPEDE",
            "risk_index": 85.5,
            "risk_level": "STAMPEDE",
            "confidence": 0.92,
            "primary_causes": ["high crowd density", "rapid density increase"],
            "analytics_active": True,
            "analytics_seq": 100,
        }
        stats_res = client.post("/cameras/update_stats", json=payload)
        self.assertEqual(stats_res.status_code, 200)

        get_res = client.get("/api/notifications")
        self.assertEqual(get_res.status_code, 200)
        data = get_res.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["total"], 1)
        
        first_notif = data["notifications"][0]
        self.assertEqual(first_notif["camera_id"], "drone-1")
        self.assertEqual(first_notif["risk_level"], "STAMPEDE")
        self.assertEqual(first_notif["risk_index"], 85.5)
        self.assertGreaterEqual(first_notif["confidence_percent"], 85.0)
        self.assertIn("primary_causes", first_notif)
        self.assertIn("maps_url", first_notif)


if __name__ == "__main__":
    unittest.main()
