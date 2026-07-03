use pyo3::prelude::*;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

#[pyclass]
struct RustDroneCapture {
    url: String,
    running: Arc<Mutex<bool>>,
    frame_data: Arc<Mutex<Option<Vec<u8>>>>,
    thread_handle: Option<thread::JoinHandle<()>>,
}

#[pymethods]
impl RustDroneCapture {
    #[new]
    fn new(url: String) -> Self {
        RustDroneCapture {
            url,
            running: Arc::new(Mutex::new(false)),
            frame_data: Arc::new(Mutex::new(None)),
            thread_handle: None,
        }
    }

    fn start(&mut self) -> PyResult<()> {
        let mut running = self.running.lock().unwrap();
        if *running {
            return Ok(());
        }
        *running = true;

        let running_clone = Arc::clone(&self.running);
        let frame_clone = Arc::clone(&self.frame_data);
        let url_clone = self.url.clone();

        self.thread_handle = Some(thread::spawn(move || {
            println!("[RUST-CAPTURE] Starting ingest thread for URL: {}", url_clone);
            // In a production setup, this thread uses an RTSP client (like retina or ffmpeg)
            // to connect and decode frames into a shared lock-free queue.
            while *running_clone.lock().unwrap() {
                // Simulating frame ingestion from network
                thread::sleep(Duration::from_millis(33)); // ~30 FPS
                
                // For demonstration/verification, we write a dummy payload
                let mut frame = frame_clone.lock().unwrap();
                *frame = Some(vec![0u8; 100]); // placeholder frame buffer
            }
            println!("[RUST-CAPTURE] Ingest thread stopped.");
        }));

        Ok(())
    }

    fn stop(&mut self) -> PyResult<()> {
        {
            let mut running = self.running.lock().unwrap();
            *running = false;
        }
        if let Some(handle) = self.thread_handle.take() {
            let _ = handle.join();
        }
        Ok(())
    }

    fn is_opened(&self) -> bool {
        *self.running.lock().unwrap()
    }

    fn read_frame(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        let frame = self.frame_data.lock().unwrap();
        if let Some(ref data) = *frame {
            let py_bytes = pyo3::types::PyBytes::new_bound(py, data);
            Ok(Some(py_bytes.into()))
        } else {
            Ok(None)
        }
    }
}

#[pymodule]
fn rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustDroneCapture>()?;
    Ok(())
}
