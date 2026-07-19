import numpy as np

class ZoneMonitor:
    def __init__(self, rows=3, cols=3):
        self.rows = rows
        self.cols = cols

    def analyze_zones(self, dmap_np):
        """
        Partitions the density map into a 3x3 grid and computes the score/headcount for each.
        Returns:
            scores: 2D numpy array of shape (3, 3) containing the density sums.
            normalized_scores: 2D numpy array of shape (3, 3) normalized by zone area.
        """
        if dmap_np is None:
            return np.zeros((self.rows, self.cols)), np.zeros((self.rows, self.cols))

        h, w = dmap_np.shape[:2]
        scores = np.zeros((self.rows, self.cols))
        normalized_scores = np.zeros((self.rows, self.cols))

        for r in range(self.rows):
            # Use the same exact equal-thirds geometry as YOLO and the overlay.
            # Integer edge calculation also covers every pixel exactly once.
            r_start = r * h // self.rows
            r_end = (r + 1) * h // self.rows
            for c in range(self.cols):
                c_start = c * w // self.cols
                c_end = (c + 1) * w // self.cols

                cell = dmap_np[r_start:r_end, c_start:c_end]
                cell_sum = float(cell.sum())
                cell_area = max(1, cell.size)

                scores[r, c] = cell_sum
                normalized_scores[r, c] = cell_sum / cell_area

        return scores, normalized_scores
