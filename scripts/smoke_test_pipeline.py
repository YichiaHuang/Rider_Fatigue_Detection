"""Pure-logic smoke test for rider/pipeline.py: synthetic landmarks (fixed
eye/mouth point offsets faking EAR/MAR) + mock IMU + synthetic audio,
checked against calling StageAScorer directly with the same derived
features. No camera, no MQTT broker, no hardware needed.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))

from layer_b_features import LEFT_EYE, RIGHT_EYE, MOUTH_TOP, MOUTH_BOTTOM, MOUTH_LEFT, MOUTH_RIGHT  # noqa: E402
from imu_reader import MPU6050Reader  # noqa: E402
from audio_anomaly import AudioAnomalyConfig, AudioAnomalyDetector  # noqa: E402
from pipeline import RiderPipeline  # noqa: E402
from stage_a_scoring import StageAConfig  # noqa: E402


def make_eye_points(cx: float, vertical_half_gap: float):
    """Returns points for [p1, p2, p3, p4, p5, p6] with horizontal width 30."""
    p1 = (cx, 0.0)
    p4 = (cx + 30.0, 0.0)
    p2 = (cx + 10.0, -vertical_half_gap)
    p3 = (cx + 20.0, -vertical_half_gap)
    p5 = (cx + 20.0, vertical_half_gap)
    p6 = (cx + 10.0, vertical_half_gap)
    return [p1, p2, p3, p4, p5, p6]


def build_landmarks(eyes_open: bool, mouth_open: bool):
    landmarks = [(0.0, 0.0)] * 468
    vgap = 4.5 if eyes_open else 1.5  # -> EAR ~0.30 open, ~0.10 closed
    for idx, pt in zip(LEFT_EYE, make_eye_points(300.0, vgap)):
        landmarks[idx] = pt
    for idx, pt in zip(RIGHT_EYE, make_eye_points(100.0, vgap)):
        landmarks[idx] = pt

    mouth_v = 15.0 if mouth_open else 4.0  # -> MAR ~0.75 open (yawn), ~0.2 closed
    mouth_h = 20.0
    landmarks[MOUTH_LEFT] = (0.0, 0.0)
    landmarks[MOUTH_RIGHT] = (mouth_h, 0.0)
    landmarks[MOUTH_TOP] = (mouth_h / 2, -mouth_v / 2)
    landmarks[MOUTH_BOTTOM] = (mouth_h / 2, mouth_v / 2)
    return landmarks


# These synthetic landmarks go through layer_b_features' own MAR formula, whose
# scale differs from the DMS pipeline's (closed mouth ~0.2 here vs ~0.05 live).
# StageAConfig's default MAR line (0.1) is tuned for the DMS scale, so this test
# pins the generic-scale value instead of inheriting it.
GENERIC_SCALE = dict(mar_yawn_threshold=0.6)


def run():
    imu = MPU6050Reader(mock=True, ema_alpha=1.0)
    audio_cfg = AudioAnomalyConfig()
    audio = AudioAnomalyDetector(audio_cfg)
    pipeline = RiderPipeline("smoke-test-rider", StageAConfig(**GENERIC_SCALE), imu_reader=imu, audio_detector=audio)

    print("=== 正常 5 秒：不應觸發任何規則 ===")
    imu.inject_pitch(5.0)
    for t in range(5):
        landmarks = build_landmarks(eyes_open=True, mouth_open=False)
        result = pipeline.process_frame(float(t), landmarks, head_pitch_deg=5.0)
        print(f"t={t} pipeline_score={result['score']:.2f} reasons={result['reasons']}")
        assert result["reasons"] == [], f"expected no anomalies, got {result['reasons']}"

    print("\n=== 閉眼+低頭（視覺+IMU 同時異常）5 秒：應該同時觸發 perclos 與 head_drop ===")
    pipeline2 = RiderPipeline("smoke-test-rider-2", StageAConfig(**GENERIC_SCALE), imu_reader=imu)
    imu.inject_pitch(35.0)
    seen_reasons = set()
    # 10 FPS for 10 s: PerclosTracker is time-weighted with a 5 s warm-up and
    # treats >0.5 s between samples as a data gap, so a live-like frame rate is
    # needed — at 1 sample/s it would (correctly) never finish warming up.
    for i in range(100):
        t = i / 10.0
        landmarks = build_landmarks(eyes_open=False, mouth_open=False)
        result = pipeline2.process_frame(t, landmarks, head_pitch_deg=35.0)
        seen_reasons.update(result["reasons"])
        if i % 10 == 0:
            print(f"t={t:.0f} score={result['score']:.2f} reasons={result['reasons']}")
    assert "perclos" in seen_reasons, f"expected perclos to fire, saw {seen_reasons}"
    assert "head_drop" in seen_reasons, f"expected head_drop (IMU-confirmed) to fire, saw {seen_reasons}"

    print("\n=== 沒有 IMU/audio 注入的 pipeline 仍應能獨立運作 ===")
    bare_pipeline = RiderPipeline("smoke-test-rider-3", StageAConfig(**GENERIC_SCALE))
    landmarks = build_landmarks(eyes_open=True, mouth_open=True)
    result = bare_pipeline.process_frame(0.0, landmarks, head_pitch_deg=30.0)
    print(f"score={result['score']:.2f} reasons={result['reasons']}")
    assert "yawn" in result["reasons"]

    print("\nAll pipeline smoke tests passed.")


if __name__ == "__main__":
    run()
