"""Cheap scene-cut detection for separating unrelated forecast histories."""

import cv2


class SceneChangeDetector:
    def __init__(self, threshold=0.25):
        self.threshold = float(threshold)
        self.previous = None

    def reset(self):
        self.previous = None

    def update(self, frame):
        small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(histogram, histogram)
        if self.previous is None:
            self.previous = histogram
            return False
        similarity = cv2.compareHist(
            self.previous, histogram, cv2.HISTCMP_CORREL
        )
        self.previous = histogram
        return similarity < self.threshold
