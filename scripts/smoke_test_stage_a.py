"""Pure-logic smoke test for Stage A scoring + the platform circuit breaker.
No camera, no MQTT broker, no hardware needed — run this any time to sanity
check the rule thresholds, hysteresis, and multi-modal fusion before wiring
in real landmarks/IMU/audio.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "legacy", "platform_streamlit"))

from stage_a_scoring import StageAConfig, StageAScorer  # noqa: E402
from circuit_breaker import BreakerConfig, DispatchCircuitBreaker  # noqa: E402

NORMAL = dict(ear=0.30, mar=0.05, head_pitch_deg=5.0, perclos=0.05)
EYES_CLOSED = dict(ear=0.10, mar=0.05, head_pitch_deg=5.0, perclos=0.40)
YAWNING = dict(ear=0.30, mar=0.75, head_pitch_deg=5.0, perclos=0.05)
HEAD_DROP_VISUAL_ONLY = dict(ear=0.30, mar=0.05, head_pitch_deg=30.0, perclos=0.05)
HEAD_DROP_VISUAL_AND_IMU = dict(ear=0.30, mar=0.05, head_pitch_deg=30.0, perclos=0.05, imu_pitch_deg=30.0)
HEAD_DROP_VISUAL_ONLY_IMU_DISAGREES = dict(ear=0.30, mar=0.05, head_pitch_deg=30.0, perclos=0.05, imu_pitch_deg=5.0)


def run_scenario(name, frames):
    scorer = StageAScorer(StageAConfig(score_head_down=True))  # these scenarios test the head rule itself
    breaker = DispatchCircuitBreaker(BreakerConfig())
    print(f"\n=== {name} ===")
    for t, features in frames:
        result = scorer.update(timestamp=t, **features)
        state = breaker.ingest("test-rider", t, result["score"])
        print(f"t={t:5.1f}s score={result['score']:6.2f} reasons={result['reasons']} "
              f"breaker_paused={state.paused}")


if __name__ == "__main__":
    run_scenario("正常狀態 10 秒（分數應維持接近 0）", [
        (t, NORMAL) for t in range(0, 10)
    ])

    run_scenario("持續閉眼 20 秒，之後恢復正常（PERCLOS 應觸發並帶動 pause）", [
        (float(t), EYES_CLOSED if t < 20 else NORMAL) for t in range(0, 40)
    ])

    run_scenario("單次打哈欠事件（應只加一次 yawn_add，不因持續張嘴而重複加分）", [
        (float(t), YAWNING if 5 <= t < 8 else NORMAL) for t in range(0, 15)
    ])

    run_scenario("頭部下垂，無 IMU 參數：應在第 2 秒後以折扣權重觸發 head_drop_visual_only_no_imu", [
        (float(t), HEAD_DROP_VISUAL_ONLY if 0 <= t < 5 else NORMAL) for t in range(0, 15)
    ])

    run_scenario("頭部下垂，視覺+IMU 同時異常：應在第 2 秒後以全權重觸發 head_drop", [
        (float(t), HEAD_DROP_VISUAL_AND_IMU if 0 <= t < 5 else NORMAL) for t in range(0, 15)
    ])

    run_scenario("頭部下垂，視覺異常但 IMU 不同意（例如低頭看導航）：不應觸發 head_drop", [
        (float(t), HEAD_DROP_VISUAL_ONLY_IMU_DISAGREES if 0 <= t < 5 else NORMAL) for t in range(0, 15)
    ])

    run_scenario("音訊異常但無其他訊號同時發生：不應加分", [
        (float(t), {**NORMAL, "audio_anomaly": True}) for t in range(0, 5)
    ])

    run_scenario("音訊異常與 PERCLOS 同時發生：應額外加上 audio_bonus", [
        (float(t), {**EYES_CLOSED, "audio_anomaly": True}) for t in range(0, 3)
    ])
