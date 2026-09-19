"""MPU6050 IMU reader — the fallback/cross-validation head-pitch signal
(plan.md section 4.1 "IMU 備援訊號"). Deliberately simpler than the vision
pipeline: raw accelerometer only, no gyro/complementary filter.

Auto-detects a real I2C bus via smbus2; if unavailable (dev machine, no
board attached), falls back to an injectable mock so the rest of the
pipeline can be developed and tested without hardware — same pattern
layer_b_features.py uses for its optional cv2 dependency.
"""
from __future__ import annotations

import math
import random
from typing import Optional, Tuple


class _MockBackend:
    """Stand-in for a real I2C bus. Returns a scripted or jittered pitch."""

    def __init__(self):
        self._injected_pitch: Optional[float] = None
        self._baseline_pitch = 0.0

    def inject_pitch(self, deg: Optional[float]) -> None:
        """Force the next reads to reflect this pitch (degrees). None resumes jitter."""
        self._injected_pitch = deg

    def read_accel(self) -> Tuple[float, float, float]:
        pitch_deg = self._injected_pitch if self._injected_pitch is not None else (
            self._baseline_pitch + random.uniform(-1.0, 1.0)
        )
        pitch_rad = math.radians(pitch_deg)
        # accel vector consistent with get_head_pitch_deg's atan2(-ax, sqrt(ay^2+az^2))
        ax = -math.sin(pitch_rad)
        ay = 0.0
        az = math.cos(pitch_rad)
        return ax, ay, az


class MPU6050Reader:
    PWR_MGMT_1 = 0x6B
    ACCEL_XOUT_H = 0x3B
    ACCEL_SCALE = 16384.0  # LSB/g at default +-2g range

    def __init__(self, bus_num: int = 1, address: int = 0x68,
                 mock: Optional[bool] = None, ema_alpha: float = 0.2):
        self.address = address
        self.ema_alpha = ema_alpha
        self._ema_pitch: Optional[float] = None
        self._mock_backend: Optional[_MockBackend] = None
        self._bus = None

        use_mock = mock
        if use_mock is None:
            try:
                import smbus2
                self._bus = smbus2.SMBus(bus_num)
                self._bus.write_byte_data(self.address, self.PWR_MGMT_1, 0)
                use_mock = False
            except (ImportError, FileNotFoundError, OSError):
                use_mock = True

        if use_mock:
            self._mock_backend = _MockBackend()

    @property
    def is_mock(self) -> bool:
        return self._mock_backend is not None

    def inject_pitch(self, deg: Optional[float]) -> None:
        """Test hook: only valid in mock mode."""
        if self._mock_backend is None:
            raise RuntimeError("inject_pitch is only available in mock mode")
        self._mock_backend.inject_pitch(deg)

    def read_raw_accel(self) -> Tuple[float, float, float]:
        if self._mock_backend is not None:
            return self._mock_backend.read_accel()

        data = self._bus.read_i2c_block_data(self.address, self.ACCEL_XOUT_H, 6)

        def to_signed16(hi, lo):
            val = (hi << 8) | lo
            return val - 65536 if val > 32767 else val

        ax = to_signed16(data[0], data[1]) / self.ACCEL_SCALE
        ay = to_signed16(data[2], data[3]) / self.ACCEL_SCALE
        az = to_signed16(data[4], data[5]) / self.ACCEL_SCALE
        return ax, ay, az

    def get_head_pitch_deg(self) -> float:
        """Positive = head tilted down, matching layer_b_features' head_pitch_deg sign."""
        ax, ay, az = self.read_raw_accel()
        raw_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay ** 2 + az ** 2)))

        if self._ema_pitch is None:
            self._ema_pitch = raw_pitch
        else:
            self._ema_pitch = self.ema_alpha * raw_pitch + (1 - self.ema_alpha) * self._ema_pitch
        return self._ema_pitch
