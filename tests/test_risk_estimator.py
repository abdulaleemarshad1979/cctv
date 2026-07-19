import os
import sys
import numpy as np
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.history_buffer import HistoryBuffer
from src.crowd_risk_estimator import CrowdRiskEstimator

class TestHistoryBufferGrowthRate(unittest.TestCase):
    def test_timestamp_aware_growth(self):
        buf = HistoryBuffer(max_seconds=30.0, fps_estimate=5.0)
        
        # Scenario 1: constant growth per second with non-uniform time steps (simulating stride changes)
        # Entry format: push(timestamp, density_score, peak_density, hotspot_ratio, motion_speed, turbulence, composite_risk, zone_scores, zone_motions)
        zone_scores = np.ones((3,3)) * 10
        zone_motions = np.zeros((3,3))
        
        # Pushing entries: at t=0, t=1.5, t=2.0
        # Values: 100, 115, 120 (slope is 10 units per second)
        buf.push(0.0, 100.0, 0.5, 0.1, 1.0, 0.2, 0.3, zone_scores, zone_motions)
        buf.push(1.5, 115.0, 0.5, 0.1, 1.0, 0.2, 0.3, zone_scores, zone_motions)
        buf.push(2.0, 120.0, 0.5, 0.1, 1.0, 0.2, 0.3, zone_scores, zone_motions)
        
        entries = buf.window(10.0)
        # Calculate slope: density grows from 100 to 120 over 2 seconds -> slope should be 10.0
        # Mean value: (100 + 115 + 120) / 3 = 111.6667
        # Expected growth rate = 10.0 / 111.6667 ≈ 0.08955
        g_rate = buf.growth_rate(entries, "density")
        self.assertAlmostEqual(g_rate, 10.0 / 111.6667, places=4)

        # Scenario 2: Fewer than 3 entries should return 0.0
        buf_short = HistoryBuffer(max_seconds=10.0)
        buf_short.push(0.0, 100.0, 0.5, 0.1, 1.0, 0.2, 0.3, zone_scores, zone_motions)
        buf_short.push(1.0, 110.0, 0.5, 0.1, 1.0, 0.2, 0.3, zone_scores, zone_motions)
        self.assertEqual(buf_short.growth_rate(buf_short.window(5.0), "density"), 0.0)

    def test_zone_growth_matrix(self):
        buf = HistoryBuffer(max_seconds=30.0)
        # Verify 3x3 matrix growth calculation is timestamp-aware
        for t, val in [(0.0, 10.0), (1.0, 12.0), (2.0, 14.0)]:
            zs = np.ones((3,3)) * val
            zm = np.zeros((3,3))
            buf.push(t, 100.0, 0.5, 0.1, 1.0, 0.2, 0.3, zs, zm)
        
        g_matrix = buf.zone_growth_matrix(10.0)
        # For each cell, value grows from 10 to 14 over 2 seconds -> slope is 2.0
        # Mean is 12.0. Expected growth rate = 2.0 / 12.0 = 0.16667
        self.assertEqual(g_matrix.shape, (3, 3))
        self.assertAlmostEqual(g_matrix[0, 0], 2.0 / 12.0, places=4)
        self.assertAlmostEqual(g_matrix[2, 2], 2.0 / 12.0, places=4)


class TestCrowdRiskEstimator(unittest.TestCase):
    def test_low_density_safe(self):
        buf = HistoryBuffer()
        est = CrowdRiskEstimator(buf)
        res = est.estimate(density_score=50.0, motion_speed=10.0, turbulence=5.0, opposing_score=0.8)
        self.assertEqual(res["risk_level"], "SAFE")
        self.assertEqual(res["risk_index"], 0.0)
        self.assertEqual(len(res["primary_causes"]), 0)
        self.assertEqual(res["confidence"], 1.0)

    def test_critical_risk_causes(self):
        buf = HistoryBuffer()
        # Feed some growth history to history buffer
        zone_scores = np.ones((3,3)) * 50
        zone_motions = np.zeros((3,3))
        # Growing density from 300 to 900
        buf.push(0.0, 300.0, 0.8, 0.4, 5.0, 3.0, 0.7, zone_scores, zone_motions)
        buf.push(1.0, 600.0, 0.8, 0.4, 5.0, 3.0, 0.7, zone_scores, zone_motions)
        buf.push(2.0, 900.0, 0.8, 0.4, 5.0, 3.0, 0.7, zone_scores, zone_motions)

        est = CrowdRiskEstimator(buf)
        # Call multiple times to let the EMA converge
        for _ in range(30):
            res = est.estimate(density_score=1500.0, motion_speed=8.0, turbulence=6.0, opposing_score=0.9)
        
        self.assertEqual(res["risk_level"], "CRITICAL")
        self.assertGreater(res["risk_index"], 75.0)
        
        # Check causes are listed
        causes = res["primary_causes"]
        self.assertIn("high crowd density", causes)
        self.assertIn("rapid crowd movement", causes)
        self.assertIn("high motion turbulence", causes)
        self.assertIn("rapid density increase", causes)
        self.assertIn("opposing pedestrian flow", causes)
        
        # Confidence should be output as a valid float
        self.assertTrue(0.50 <= res["confidence"] <= 0.95)

if __name__ == "__main__":
    unittest.main()
