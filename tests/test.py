import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src import (
    overlay,
    risk_engine,
    optical_flow,
    zone_monitor,
    drone_stream,
    logger,
)

print("ALL MODULES IMPORTED SUCCESSFULLY")

def test_clean_density_map_speckle():
    import numpy as np
    from src.density_filter import clean_density_map
    
    # 1. Test relative threshold cap (prevents high peaks from erasing low density regions)
    dmap = np.zeros((10, 10), dtype=np.float32)
    dmap[0, 0] = 10.0  # Massive peak
    dmap[5, 5] = 0.08  # Valid low-density zone
    
    # Without cap, threshold would be 10.0 * 0.015 = 0.15 (erasing 0.08)
    # With cap of 0.05, threshold is 0.05 (preserving 0.08)
    cleaned = clean_density_map(dmap, speckle_ratio=0.015)
    
    assert cleaned[0, 0] == 10.0
    assert cleaned[5, 5] == 0.08, f"Relative cap failed: {cleaned[5, 5]} was wiped out."

    # 2. Test absolute floor (prevents very low peaks from failing to filter noise)
    dmap_low = np.zeros((10, 10), dtype=np.float32)
    dmap_low[0, 0] = 0.05   # Small peak
    dmap_low[5, 5] = 0.001  # Noise speckle
    
    # Without floor, threshold would be 0.05 * 0.015 = 0.00075 (failing to filter 0.001)
    # With floor of 0.002, threshold is 0.002 (filtering 0.001)
    cleaned_low = clean_density_map(dmap_low, speckle_ratio=0.015)
    
    assert cleaned_low[0, 0] == 0.05
    assert cleaned_low[5, 5] == 0.0, f"Absolute floor failed: {cleaned_low[5, 5]} was not filtered."
    
    print("[TEST] test_clean_density_map_speckle passed successfully!")

if __name__ == "__main__":
    test_clean_density_map_speckle()