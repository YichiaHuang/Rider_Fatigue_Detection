"""Pure-logic smoke test for rider/imu_reader.py using its mock backend.
No I2C bus, no MPU6050 hardware needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))

from imu_reader import MPU6050Reader  # noqa: E402


def approx(a, b, tol=0.5):
    return abs(a - b) <= tol


def run():
    reader = MPU6050Reader(mock=True, ema_alpha=1.0)  # alpha=1 -> no smoothing, easier to assert
    assert reader.is_mock

    print("=== 靜止不動：pitch 應接近 0 ===")
    reader.inject_pitch(0.0)
    for _ in range(3):
        p = reader.get_head_pitch_deg()
        print(f"pitch={p:.2f}")
        assert approx(p, 0.0), f"expected ~0, got {p}"

    print("\n=== 低頭 30 度：pitch 應接近 30 ===")
    reader.inject_pitch(30.0)
    for _ in range(3):
        p = reader.get_head_pitch_deg()
        print(f"pitch={p:.2f}")
        assert approx(p, 30.0), f"expected ~30, got {p}"

    print("\n=== 抬頭 -20 度：pitch 應接近 -20 ===")
    reader.inject_pitch(-20.0)
    for _ in range(3):
        p = reader.get_head_pitch_deg()
        print(f"pitch={p:.2f}")
        assert approx(p, -20.0), f"expected ~-20, got {p}"

    print("\n=== EMA 平滑：alpha=0.2 時應緩慢趨近新值 ===")
    smoothed = MPU6050Reader(mock=True, ema_alpha=0.2)
    smoothed.inject_pitch(0.0)
    smoothed.get_head_pitch_deg()
    smoothed.inject_pitch(40.0)
    readings = [smoothed.get_head_pitch_deg() for _ in range(10)]
    print("readings:", [f"{r:.1f}" for r in readings])
    assert readings[0] < readings[-1], "should be trending toward 40"
    assert readings[-1] > 30.0, "should have mostly converged after 10 ticks"

    print("\nAll IMU smoke tests passed.")


if __name__ == "__main__":
    run()
