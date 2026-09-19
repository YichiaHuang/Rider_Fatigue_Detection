"""Rider-side orchestrator: ties Layer B feature math, the optional IMU and
audio signals, and Stage A scoring into one per-frame call, then publishes.

This is the integration point for Layer A. There are two ways to feed it:

  1. `ear=`/`mar=`/`head_pitch_deg=` directly, already computed upstream.
     This is the real path: /root/guardian_helmet/dms/analyze.py
     (DMSFrameAnalyzer) runs H1's validated NXP eIQ DMS pipeline and
     returns exactly these three values, sign/scale-matched to this
     project's convention — see rider/guardian_helmet_bridge.py for the
     adapter that plugs it in.
  2. `landmarks` (a plain list of 468 (x, y) points), for any other
     source of raw Face Mesh landmarks — falls back to this project's own
     EAR/MAR/head-pose formulas (layer_b_features.py). Mainly useful for
     synthetic/test data; the real pipeline doesn't need this path.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from layer_b_features import (
    PerclosTracker,
    compute_eye_aspect_ratio,
    compute_mouth_aspect_ratio,
    compute_head_pitch_from_landmarks,
)
from stage_a_scoring import StageAConfig, StageAScorer


class RiderPipeline:
    def __init__(self, rider_id: str, stage_a_config: Optional[StageAConfig] = None,
                 imu_reader=None, audio_detector=None, publisher=None,
                 image_size: Tuple[int, int] = (640, 480)):
        self.rider_id = rider_id
        self.image_size = image_size
        self.imu_reader = imu_reader
        self.audio_detector = audio_detector
        self.publisher = publisher

        self.perclos_tracker = PerclosTracker()
        self.scorer = StageAScorer(stage_a_config or StageAConfig())

    def process_frame(self, timestamp: float, landmarks: Optional[Sequence[Tuple[float, float]]] = None, *,
                       ear: Optional[float] = None, mar: Optional[float] = None,
                       head_pitch_deg: Optional[float] = None,
                       audio_window: Optional[np.ndarray] = None) -> dict:
        if ear is None or mar is None:
            if landmarks is None:
                raise ValueError("process_frame needs either landmarks, or explicit ear= and mar=")
            if ear is None:
                ear = compute_eye_aspect_ratio(landmarks)
            if mar is None:
                mar = compute_mouth_aspect_ratio(landmarks)
        perclos = self.perclos_tracker.update(timestamp, ear)

        if head_pitch_deg is None:
            if landmarks is None:
                raise ValueError("process_frame needs either landmarks, or explicit head_pitch_deg=")
            head_pitch_deg = compute_head_pitch_from_landmarks(landmarks, self.image_size)

        imu_pitch_deg = self.imu_reader.get_head_pitch_deg() if self.imu_reader else None

        audio_anomaly = False
        if self.audio_detector is not None and audio_window is not None:
            audio_anomaly = self.audio_detector.update(timestamp, audio_window)

        result = self.scorer.update(
            timestamp=timestamp, ear=ear, mar=mar, head_pitch_deg=head_pitch_deg,
            perclos=perclos, imu_pitch_deg=imu_pitch_deg, audio_anomaly=audio_anomaly,
        )

        if self.publisher is not None:
            self.publisher.publish(result["timestamp"], result["score"])

        return result
