import csv
import os
import time

class CrowdLogger:
    def __init__(self, csv_path, enabled=True):
        self.csv_path = csv_path
        self.enabled = enabled
        self.csv_file = None
        self.csv_writer = None

    def log(self, zone, risk_score, density_score, peak_density, hotspot_ratio, infer_t, age, stride):
        if not self.enabled:
            return

        if self.csv_file is None:
            out_dir = os.path.dirname(self.csv_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow([
                "timestamp",
                "zone",
                "risk_score",
                "density_score",
                "peak_density",
                "hotspot_ratio",
                "inference_time_s",
                "frame_age_s",
                "stride"
            ])

        self.csv_writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            zone,
            f"{risk_score:.4f}",
            f"{density_score:.2f}",
            f"{peak_density:.6f}",
            f"{hotspot_ratio:.4f}",
            f"{infer_t:.4f}",
            f"{age:.4f}",
            stride
        ])
        self.csv_file.flush()

    def close(self):
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
