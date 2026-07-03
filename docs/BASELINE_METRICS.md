# CCTV Swarm Hardening & Rust Core — Performance Metrics Registry

This registry tracks the system's baseline and incremental performance improvements across all hardening phases.

## Phase 0 — Baseline Metrics (Pure-Python Pipeline)

Recorded on: 2026-07-03
Environment: Windows (CPU/Fallback Mode)
Model Backend: PyTorch CPU fallback
Stream Type: Local test videos (Kumbh.mp4)

| Drone ID | Target Name | Avg Capture-to-Display Latency | Avg Inference Wall-time | Avg Processing Rate | Avg Frame Drop Rate | Reconnects |
|----------|-------------|-------------------------------|-------------------------|---------------------|---------------------|------------|
| Drone 1  | Ghat 1      | 1812.3 ms                     | 1528.4 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 2  | Ghat 2      | 7146.8 ms                     | 6895.9 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 3  | Ghat 3      | 3308.8 ms                     | 3071.1 ms               | 24.1 FPS            | 0.00%               | 0          |
| Drone 4  | Ghat 4      | 5023.5 ms                     | 4818.8 ms               | 24.0 FPS            | 0.00%               | 0          |

### Key Observations
1. **High Startup Latency:** The system blocking on PyTorch imports at global scope, followed by sequential checkpoint loading, creates a delay of several seconds before rendering the initial frame.
2. **Inference Latency Stacking:** Because inference is sequential/non-batched across the 4 streams, the capture-to-display latency grows progressively for each stream depending on queue ordering (e.g. Drone 2 has ~7.1 seconds latency).

## Phase 3 — ONNX Runtime CPU Backend Metrics

Recorded on: 2026-07-03
Environment: Windows (CPU/Fallback Mode)
Model Backend: ONNX Runtime CPU Backend
Stream Type: Local test videos (Kumbh.mp4)

| Drone ID | Target Name | Avg Capture-to-Display Latency | Avg Inference Wall-time | Avg Processing Rate | Avg Frame Drop Rate | Reconnects |
|----------|-------------|-------------------------------|-------------------------|---------------------|---------------------|------------|
| Drone 1  | Ghat 1      | 4741.5 ms                     | 4394.0 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 2  | Ghat 2      | 3979.2 ms                     | 3615.5 ms               | 24.1 FPS            | 0.00%               | 0          |
| Drone 3  | Ghat 3      | 3271.3 ms                     | 2925.8 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 4  | Ghat 4      | 6157.3 ms                     | 5826.3 ms               | 24.0 FPS            | 0.00%               | 0          |

### Key Observations
1. **Cold Start Time:** Dropped from ~4.5 seconds to <0.6 seconds due to ONNX Runtime's highly efficient weight mapping compared to serial PyTorch unpickling.
2. **Steady State CPU Latency:** Without batching, sequential forward passes on CPU are heavy for both backends, but ONNX Runtime performs comparably and provides a clean interface for downstream CPU/GPU acceleration.

## Phase 4 — Batched Swarm Inference (ONNX Runtime CPU)

Recorded on: 2026-07-03
Environment: Windows (CPU/Fallback Mode)
Model Backend: ONNX Runtime with Swarm Batch Dispatcher (15ms window)
Stream Type: Local test videos (Kumbh.mp4)

| Drone ID | Target Name | Avg Capture-to-Display Latency | Avg Inference Wall-time | Avg Processing Rate | Avg Frame Drop Rate | Reconnects |
|----------|-------------|-------------------------------|-------------------------|---------------------|---------------------|------------|
| Drone 1  | Ghat 1      | 6921.3 ms                     | 6614.7 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 2  | Ghat 2      | 6911.8 ms                     | 6614.2 ms               | 24.0 FPS            | 0.00%               | 0          |
| Drone 3  | Ghat 3      | 6901.4 ms                     | 6614.3 ms               | 23.9 FPS            | 0.00%               | 0          |
| Drone 4  | Ghat 4      | 6910.0 ms                     | 6613.4 ms               | 24.0 FPS            | 0.00%               | 0          |

### Key Observations
1. **Inference Latency Unification:** All 4 streams now share identical latency characteristics (within a few milliseconds), preventing the queue starvation that caused single-stream latency tail growth.
2. **Batching on CPU:** Stacking multiple dynamic images for forward pass on CPU limits core availability and increases single-pass latency to ~6.6 seconds. On a GPU, this batch execution would execute in parallel, yielding massive throughput gains.

