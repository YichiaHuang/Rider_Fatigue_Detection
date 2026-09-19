"""Layer B: geometric feature extraction from Face Mesh 468 landmarks.

Input landmarks are (x, y) pixel or normalized coordinates in the
MediaPipe Face Mesh 468-point scheme, which the NXP eIQ DMS reference
design also follows. Pure math, no model inference here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections import deque

# Landmark index groups (MediaPipe Face Mesh 468-point topology)
LEFT_EYE = [362, 385, 387, 263, 373, 380]   # p1..p6, same order as classic EAR paper
RIGHT_EYE = [33, 160, 158, 133, 153, 144]
MOUTH_TOP = 13
MOUTH_BOTTOM = 14
MOUTH_LEFT = 61
MOUTH_RIGHT = 291

# solvePnP model points for head pose (generic face model, mm), matched to
# the landmark indices used below. Only needed if the upstream DMS reference
# does not already output head pose directly.
HEAD_POSE_LANDMARK_IDX = {
    "nose_tip": 1,
    "chin": 152,
    "left_eye_corner": 263,
    "right_eye_corner": 33,
    "left_mouth_corner": 291,
    "right_mouth_corner": 61,
}
HEAD_POSE_MODEL_POINTS = {
    "nose_tip": (0.0, 0.0, 0.0),
    "chin": (0.0, -330.0, -65.0),
    "left_eye_corner": (-225.0, 170.0, -135.0),
    "right_eye_corner": (225.0, 170.0, -135.0),
    "left_mouth_corner": (-150.0, -150.0, -125.0),
    "right_mouth_corner": (150.0, -150.0, -125.0),
}


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def compute_ear(landmarks, eye_idx) -> float:
    p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in eye_idx)
    vertical = _dist(p2, p6) + _dist(p3, p5)
    horizontal = 2.0 * _dist(p1, p4)
    if horizontal == 0:
        return 0.0
    return vertical / horizontal


def compute_eye_aspect_ratio(landmarks) -> float:
    """Average EAR across both eyes."""
    left = compute_ear(landmarks, LEFT_EYE)
    right = compute_ear(landmarks, RIGHT_EYE)
    return (left + right) / 2.0


def compute_mouth_aspect_ratio(landmarks) -> float:
    vertical = _dist(landmarks[MOUTH_TOP], landmarks[MOUTH_BOTTOM])
    horizontal = _dist(landmarks[MOUTH_LEFT], landmarks[MOUTH_RIGHT])
    if horizontal == 0:
        return 0.0
    return vertical / horizontal


def compute_head_pitch_from_landmarks(landmarks, image_size, camera_matrix=None) -> float:
    """Head pitch (degrees, positive = looking down) via solvePnP.

    Fallback path for when the DMS reference does not expose head pose
    directly. Requires OpenCV; imported lazily so this module has no hard
    dependency on cv2 when head pose comes from elsewhere.
    """
    import cv2
    import numpy as np

    image_points = np.array(
        [landmarks[HEAD_POSE_LANDMARK_IDX[name]] for name in HEAD_POSE_MODEL_POINTS],
        dtype=np.float64,
    )
    model_points = np.array(list(HEAD_POSE_MODEL_POINTS.values()), dtype=np.float64)

    w, h = image_size
    if camera_matrix is None:
        focal_length = w
        center = (w / 2.0, h / 2.0)
        camera_matrix = np.array(
            [[focal_length, 0, center[0]],
             [0, focal_length, center[1]],
             [0, 0, 1]],
            dtype=np.float64,
        )
    dist_coeffs = np.zeros((4, 1))

    ok, rotation_vec, _ = cv2.solvePnP(
        model_points, image_points, camera_matrix, dist_coeffs,
        flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return 0.0

    rotation_mat, _ = cv2.Rodrigues(rotation_vec)
    sy = math.sqrt(rotation_mat[0, 0] ** 2 + rotation_mat[1, 0] ** 2)
    pitch = math.degrees(math.atan2(-rotation_mat[2, 0], sy))
    return pitch


@dataclass
class PerclosTracker:
    """PERCLOS: fraction of OBSERVED TIME in a sliding window with eyes closed.

    Time-weighted, not sample-counted: live frames arrive unevenly (inference
    time varies, faces get lost), and counting samples would over-weight
    whichever stretch happened to run at a higher frame rate. Each sample
    stands for the time until the next one, capped at max_gap_sec — a hole in
    the data (no face) is simply not observed time: it counts neither as
    open nor as closed (plan.md 4.2).

    Until warmup_sec of observed time has accumulated the ratio is reported
    as 0.0: one closed-eye frame right after start-up is not "100 % PERCLOS".
    """
    window_seconds: float = 60.0
    ear_closed_threshold: float = 0.21
    max_gap_sec: float = 0.5
    warmup_sec: float = 5.0
    _samples: deque = field(default_factory=deque)  # [timestamp, is_closed, duration]

    observed_sec: float = 0.0  # observed time currently inside the window (read-only, for diagnostics)

    def reset(self) -> None:
        self._samples.clear()
        self.observed_sec = 0.0

    def update(self, timestamp: float, ear: float) -> float:
        if self._samples:
            previous = self._samples[-1]
            previous[2] = min(max(0.0, timestamp - previous[0]), self.max_gap_sec)
        self._samples.append([timestamp, ear < self.ear_closed_threshold, 0.0])
        cutoff = timestamp - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self.observed_sec = sum(d for _, _, d in self._samples)
        if self.observed_sec < self.warmup_sec:
            return 0.0
        closed_sec = sum(d for _, closed, d in self._samples if closed)
        return closed_sec / self.observed_sec


@dataclass
class FrameFeatures:
    timestamp: float
    ear: float
    mar: float
    head_pitch_deg: float
    perclos: float
