import time
import os
import cv2
import numpy as np
import config
from src.swarm_manager import SwarmManager

def main():
    print("Starting Offline Swarm Manager Verification...")
    video_path = os.path.join("Videos", "Kumbh.mp4")
    if not os.path.exists(video_path):
        print(f"Error: {video_path} not found.")
        return

    # Use the same local video file for all 4 drone sources for testing
    sources = [video_path] * 4

    print("Initializing SwarmManager...")
    mgr = SwarmManager(sources=sources)
    
    print("Starting Swarm Pipelines...")
    mgr.start()

    # Let it run for 10 seconds and print out unified status periodically
    for i in range(10):
        time.sleep(1)
        state = mgr.get_unified_state()
        print(f"\n--- Unified State Update {i+1}/10 ---")
        print(f"Timestamp: {state.get('timestamp')}")
        print(f"Worst Zone: {state.get('worst_zone')}")
        print(f"Worst Pressure: {state.get('worst_pressure')}%")
        print(f"Worst Drone ID: {state.get('worst_drone_id')}")
        
        # Check active drone summaries
        summaries = state.get('drone_summaries', [])
        for ds in summaries:
            print(f"  Drone {ds['drone_id']+1} ({ds['name']}): Online={ds['online']}, Zone={ds['zone']}, Uptime={ds['uptime_s']}s")
            if ds.get('alerts'):
                print(f"    Alerts: {ds['alerts']}")
            if ds.get('gps_alerts'):
                print(f"    GPS Alerts: {ds['gps_alerts']}")

    print("\nStopping Swarm Pipelines...")
    mgr.stop()
    print("Done. Verification completed successfully!")

if __name__ == "__main__":
    main()
