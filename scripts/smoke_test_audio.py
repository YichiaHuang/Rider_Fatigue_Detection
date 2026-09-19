"""Pure-logic smoke test for rider/audio_anomaly.py using synthesized
sine + noise buffers. No microphone, no real motorcycle noise needed —
and this module is honestly scoped to never need that to be trustworthy
(see its docstring).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))

from audio_anomaly import AudioAnomalyConfig, AudioAnomalyDetector  # noqa: E402


def make_tone(freq_hz, duration_sec, sample_rate, amplitude=0.3, noise=0.0):
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    signal = amplitude * np.sin(2 * np.pi * freq_hz * t)
    if noise:
        signal = signal + np.random.normal(0, noise, size=signal.shape)
    return signal.astype(np.float32)


def run():
    cfg = AudioAnomalyConfig()
    detector = AudioAnomalyDetector(cfg)

    print("=== 靜音（低於 RMS 門檻）：不應觸發 ===")
    silence = np.zeros(cfg.sample_rate, dtype=np.float32)
    result = detector.update(0.0, silence)
    print(f"result={result}")
    assert result is False

    print("\n=== 頻段內音調（400Hz，在 250-600Hz 範圍）：應觸發 ===")
    in_band = make_tone(400.0, 1.0, cfg.sample_rate)
    result = detector.update(1.0, in_band)
    print(f"result={result}")
    assert result is True

    print("\n=== 頻段外音調（2000Hz，音量足夠但頻率不對）：不應觸發 ===")
    out_of_band = make_tone(2000.0, 1.0, cfg.sample_rate)
    result = detector.update(2.0, out_of_band)
    print(f"result={result}")
    assert result is False

    print("\n=== 低音量頻段內音調（RMS 不夠）：不應觸發 ===")
    quiet_in_band = make_tone(400.0, 1.0, cfg.sample_rate, amplitude=0.01)
    result = detector.update(3.0, quiet_in_band)
    print(f"result={result}")
    assert result is False

    print("\n=== 空陣列：不應報錯，應回傳 False ===")
    result = detector.update(4.0, np.array([], dtype=np.float32))
    print(f"result={result}")
    assert result is False

    print("\nAll audio smoke tests passed.")


if __name__ == "__main__":
    run()
