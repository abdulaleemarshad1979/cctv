# Pushkaralu Drone Crowd Monitor & Risk Engine — System Guide

This document provides a conceptual and operational overview of the **Pushkaralu Drone Crowd Monitor & Risk Engine**. It outlines the real-world applications of the platform, the mechanics of how it functions, operational calibration strategies to get the best results, and the physical/system requirements.

---

## 1. System Applications
The platform is designed to assist safety coordinators, event managers, and security command centers in monitoring crowd movement and ensuring safety during large-scale gatherings.

* **Mass Gathering & Pilgrimage Security**: Tailored for crowded areas such as riverbanks (Ghats), religious venues, festivals, and public squares. It maps crowd distribution in real time to prevent overcrowding.
* **Stampede & Panic Prevention**: By continuously analyzing movement speeds, crowd turbulence (abrupt directional shifts), and conflicting pathways, the system flags potential stampede risks early, enabling timely crowd management.
* **Unified Command Center Monitoring**: Merges live feeds from multiple drones into a single consolidated view (mosaic layout), showing risk alerts across different monitored sectors simultaneously.
* **Automated Alert Dispatch**: Dispatches critical risk alerts directly to security field personnel via messaging channels. Messages contain localized detail and Google Maps links to the exact coordinates of the warning.
* **GIS Integration & Post-Event Audits**: Generates map-ready geospatial files and logs event metrics. This allows command centers to overlay alerts on map dashboards (GIS) and analyze historical bottleneck data for future event planning.

---

## 2. Conceptual Architecture & How It Works
The engine processes incoming video feeds dynamically to determine safety levels.

```
                              +-------------------------+
                              |   Incoming Video Feeds  |
                              | (Drones, Security Cams) |
                              +------------+------------+
                                           |
                                           v [Frames]
                              +------------+------------+
                              |   Low-Latency Receiver  |
                              +------------+------------+
                                           |
                                           v [Buffered Stream]
                              +------------+------------+
                              |   AI Density Estimator  |
                              +------------+------------+
                                           |
                                           v [Estimated Density Maps]
  +----------------------------------------+----------------------------------------+
  |                                        |                                        |
  v                                        v                                        v
+------------------------+      +------------------------+      +------------------------+
|      Risk Engine       |      |     Motion Analyzer    |      |      Zone Monitor      |
| • Fuses crowd pressure |      | • Tracks flow speeds   |      | • Divides into a 3x3   |
| • Checks safety levels |      | • Flags turbulence     |      |   spatial grid         |
| • Triggers alert states|      | • Detects head-on flow |      | • Checks max capacity  |
+-----------+------------+      +-----------+------------+      +-----------+------------+
            |                               |                               |
            +-------------------------------+-------------------------------+
                                            |
                                            v [Integrated Metrics]
                              +------------+------------+
                              |   Visual Alert Overlay  | ---> Live Screen & GUI
                              +------------+------------+
                                           |
                                           v [Critical Threshold Exceeded]
                              +------------+------------+
                              |    Alert Dispatcher     | ---> Direct Notifications &
                              +-------------------------+      Geospatial Map Data
```

### Core Core Mechanics
1. **Low-Latency Video Capture**:
   Receives raw video signals from connected aerial transmitters. The pipeline utilizes low-delay settings to capture the live scene with minimal processing lag.
2. **AI-Based Density Estimation**:
   Analyzes video frames using artificial intelligence to estimate crowd distribution. Instead of just counting faces (which can be obscured in thick crowds), the model estimates how closely packed individuals are at any point in the image.
3. **Altitude Scale Correction**:
   As drones fly higher, people appear smaller, which can cause the AI to under-estimate crowd sizes. The system compensates by analyzing the flight altitude and camera field-of-view, scaling up headcount estimates automatically as the drone climbs.
4. **Motion Dynamics (Optical Flow)**:
   Calculates displacement vectors between successive frames to determine:
   * **Movement Speed**: The velocity of the moving crowd.
   * **Turbulence**: High-variance, erratic movements indicating confusion, panic, or bottlenecking.
   * **Opposing Flow**: Pockets where groups of people are walking directly into each other, creating dangerous congestion points.
5. **Spatial Grid Partitioning**:
   Divides the camera frame into a 3x3 grid to monitor individual sectors. This allows safety managers to identify exactly which side of a ghat or plaza is reaching capacity, rather than treating the entire area as one unit.
6. **Stampede Predictor**:
   Looks at history trends (density, speed drops, turbulence spikes, and head-on movement) to determine a stampede risk percentage.

---

## 3. Operational Guidelines for Better Output
To achieve the highest accuracy and performance during live events, follow these operational best practices:

### Flight Operations & Camera Alignment
* **Maintain Stable Hover**: Keep the drone stationary over the targeted area. Erratic flight maneuvers or rapid panning can distort optical flow calculations and trigger false speed alerts.
* **Use a Nadir (Top-Down) Camera Angle**: Position the camera facing directly downward (or at a sharp downward angle of 60° to 90°). Diagonal angles warp perspective, making objects in the background harder for the AI to count accurately.
* **Calibrate Sensor Inputs**: Make sure the assumed flight altitude configured in the monitoring software matches the drone's actual height. If you hover at 30 meters but the software assumes 10 meters, the scale correction will yield incorrect numbers.

### Crowd Capacity Limits
* Establish realistic capacities for each zone in the monitoring interface based on actual ground area (e.g. wide open plazas vs narrow walkways). Setting overly strict capacities will cause frequent false alarms, while loose limits may delay critical warnings.

### Video Processing & Network Setup
* **Balance Processing Speed**: Lowering the input resolution analyzed by the AI will increase processing speed (more frames per second) but might miss individuals in distant or highly dense groups. Test the system prior to deployment to find the best balance between frame rate and accuracy.
* **Stream Quality**: Ensure a strong wireless transmission signal between the drone controller and the computer running the software. Packet loss, freezing, and video distortion will affect the system's ability to calculate crowd metrics.

---

## 4. System Prerequisites & Needs
The system requires physical, hardware, and operational resources to function correctly in the field.

### Hardware Infrastructure
* **Processing Server/PC**: A powerful computer equipped with a high-performance graphics card (GPU) is required to run the real-time AI density estimation model, especially when handling multiple camera streams simultaneously.
* **Aerial Equipment**: One or more drones (quadcopters) capable of transmitting a live video stream (RTSP/network stream) back to the receiver base station.
* **Network Infrastructure**: High-bandwidth wireless routers or relays to transmit video feeds from drone controllers to the command station computer without interruption.

### Software & Setup
* **Host Operating System**: Windows, Linux, or macOS environment.
* **Intelligence Model**: The AI crowd-counting model files must be set up in the system directory to perform density evaluations.
* **Alert Integrations**: Active messaging credentials (such as API details and chat destination channels for Telegram or WhatsApp) to receive warnings on mobile devices.
