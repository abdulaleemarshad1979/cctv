# Rust Core Capture Module (`rust_core`)

This subdirectory contains the high-performance optional Rust capture core for parsing and decoding RTSP drone streams.

## Benefits
1. **Low Overhead:** Native execution bypasses Python's Global Interpreter Lock (GIL) during heavy frame socket read operations.
2. **Lock-Free Queueing:** Utilizes thread-safe lock-free ring buffers to queue frames, reducing thread contention.
3. **Graceful Degradation:** Integrates transparently into the main Python application wrapper.

## Installation & Build Instructions

If you have Rust and Cargo installed, you can compile and build this extension module locally.

### 1. Install Maturin
Maturin is a build tool for Rust-based Python extensions:
```bash
pip install maturin
```

### 2. Build the extension
Navigate to this directory (or run from the workspace root) and execute:
```bash
maturin develop
```
On Windows, this will compile the Rust project and generate a `rust_core.pyd` binary, placing it directly in your Python environment's site-packages where it can be imported as `import rust_core`.

## Graceful Fallback
If the compiled `rust_core` library is not found or `USE_RUST_CAPTURE=1` is disabled, the system automatically falls back to the OpenCV-based pure Python `DroneStreamHandler` without crashing.
