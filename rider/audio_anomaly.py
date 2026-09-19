"""Audio anomaly detection — deliberately scoped down (plan.md section 4.2).

This is NOT a trained classifier (YAMNet-based classification is explicit
future-work, see plan.md section 8). It's a simple RMS + dominant-frequency
threshold gate, validated only in an indoor/no-wind environment. Real
motorcycle riding has wind noise, engine noise, and road noise that likely
swamp a yawn/breathing signal picked up by a non-directional mic with no
noise cancellation — plan.md section 9 is explicit about this limitation.

Because of that, this module's output must never be treated as a reliable
standalone signal — see rider/stage_a_scoring.py, where audio_anomaly can
only ever add to a score some other (visual/IMU) rule already started.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AudioAnomalyConfig:
    sample_rate: int = 16000
    rms_threshold: float = 0.05
    dominant_freq_min_hz: float = 250.0
    dominant_freq_max_hz: float = 600.0


class AudioAnomalyDetector:
    def __init__(self, config: AudioAnomalyConfig):
        self.config = config

    def update(self, timestamp: float, audio_window: np.ndarray) -> bool:
        cfg = self.config
        if audio_window.size == 0:
            return False

        rms = float(np.sqrt(np.mean(np.square(audio_window))))
        if rms <= cfg.rms_threshold:
            return False

        spectrum = np.abs(np.fft.rfft(audio_window))
        freqs = np.fft.rfftfreq(audio_window.size, d=1.0 / cfg.sample_rate)
        dominant_freq = freqs[int(np.argmax(spectrum))]

        return bool(cfg.dominant_freq_min_hz <= dominant_freq <= cfg.dominant_freq_max_hz)
