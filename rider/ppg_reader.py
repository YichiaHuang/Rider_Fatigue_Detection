"""MAX30102 PPG reader + once-a-second vitals, for the live runner.

Register setup is H2's validated configuration from /root/sensors/sensor_test.c
(SpO2 mode, 100 sps / 411 us / 18-bit, FIFO average 2 -> 50 Hz, LED_PA 0x24).
The LED rail (VEXP_3V3) must be on: see /root/sensors/vexp-3v3.service.

Only one process can drain the sensor's FIFO. Don't run sensor_test at the same
time as the live runner — each would see half of the samples.

    monitor = PpgMonitor(bus=0); monitor.start()
    monitor.latest()  ->  {"quality": "good", "heart_rate_bpm": 72.4, "waveform": [...], ...} or None
"""
from __future__ import annotations

import threading
import time
from collections import deque

import ppg_dsp

PPG_ADDR = 0x57
REG_FIFO_WR_PTR, REG_FIFO_DATA = 0x04, 0x07
REG_FIFO_CONFIG, REG_MODE_CONFIG, REG_SPO2_CONFIG = 0x08, 0x09, 0x0A
REG_LED1_PA, REG_LED2_PA, REG_PART_ID = 0x0C, 0x0D, 0xFF
FIFO_CONFIG, SPO2_CONFIG, LED_PA, MODE_SPO2 = 0x3F, 0x27, 0x24, 0x03
SAMPLE_HZ = 50.0          # 100 sps / FIFO averaging of 2
WINDOW_SEC = 12.0
HRV_WINDOW_SEC = 40.0     # RMSSD needs more beats than the rate does


class PpgUnavailable(Exception):
    pass


class Max30102:
    def __init__(self, bus: int = 0):
        try:
            from smbus2 import SMBus, i2c_msg
        except ImportError as exc:
            raise PpgUnavailable("smbus2 is not installed") from exc
        self._i2c_msg = i2c_msg
        try:
            self._bus = SMBus(bus)
            part = self._read(REG_PART_ID, 1)[0]
        except OSError as exc:
            raise PpgUnavailable(f"no MAX30102 on /dev/i2c-{bus}: {exc}") from exc
        if part != 0x15:
            raise PpgUnavailable(f"unexpected PART_ID 0x{part:02x} at 0x57")
        self._configure()

    def _read(self, reg: int, length: int) -> list:
        # i2c_rdwr, not SMBus block reads: a full FIFO burst is 192 bytes (> the 32-byte SMBus cap)
        write, read = self._i2c_msg.write(PPG_ADDR, [reg]), self._i2c_msg.read(PPG_ADDR, length)
        self._bus.i2c_rdwr(write, read)
        return list(read)

    def _write(self, reg: int, value: int) -> None:
        self._bus.i2c_rdwr(self._i2c_msg.write(PPG_ADDR, [reg, value]))

    def _configure(self) -> None:
        self._write(REG_MODE_CONFIG, 0x40)  # reset
        time.sleep(0.1)
        for reg in (0x04, 0x05, 0x06):
            self._write(reg, 0x00)
        self._write(REG_FIFO_CONFIG, FIFO_CONFIG)
        self._write(REG_SPO2_CONFIG, SPO2_CONFIG)
        self._write(REG_LED1_PA, LED_PA)
        self._write(REG_LED2_PA, LED_PA)
        self._write(REG_MODE_CONFIG, MODE_SPO2)  # last: starts sampling
        before = self._read(REG_FIFO_WR_PTR, 1)[0]
        time.sleep(0.25)
        if self._read(REG_FIFO_WR_PTR, 1)[0] == before:
            raise PpgUnavailable("FIFO not advancing — LED supply (VEXP_3V3) is probably off")

    def alive(self) -> bool:
        """The part resets its registers to 0 on a supply droop (see sensor_test.c)."""
        return self._read(REG_MODE_CONFIG, 1)[0] == MODE_SPO2

    def read_samples(self) -> list:
        """IR counts, oldest first, since the last call."""
        wr, _, rd = self._read(REG_FIFO_WR_PTR, 3)
        count = (wr - rd) & 0x1F
        if count == 0:
            return []
        raw = self._read(REG_FIFO_DATA, count * 6)
        return [((raw[i * 6 + 3] << 16) | (raw[i * 6 + 4] << 8) | raw[i * 6 + 5]) & 0x03FFFF
                for i in range(count)]


class PpgMonitor:
    def __init__(self, bus: int = 0):
        self._sensor = Max30102(bus)  # raises PpgUnavailable -> the runner simply goes without vitals
        self._samples = deque(maxlen=int(SAMPLE_HZ * HRV_WINDOW_SEC))  # (t, ir)
        self._lock = threading.Lock()
        self._latest = None
        self._stop = threading.Event()
        self.error = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="ppg", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> "dict | None":
        with self._lock:
            return self._latest

    def _run(self) -> None:
        last_analysis = 0.0
        while not self._stop.wait(0.1):   # 5 samples per poll, far from the 32-sample FIFO depth
            try:
                batch = self._sensor.read_samples()
                now = time.time()
                for i, ir in enumerate(batch):  # date samples back from the read time, as sensor_test does
                    self._samples.append((now - (len(batch) - 1 - i) / SAMPLE_HZ, ir))
                if now - last_analysis < 1.0:
                    continue
                last_analysis = now
                if not self._sensor.alive():
                    self._sensor._configure()
                    self._samples.clear()
                    continue
                self._analyze(now)
                self.error = None
            except OSError as exc:  # a loose wire must not take the rider down
                self.error = str(exc)
                with self._lock:
                    self._latest = None
                self._stop.wait(1.0)

    def _analyze(self, now: float) -> None:
        samples = list(self._samples)
        # Contact is judged on the recent window only; if it was lost, older
        # samples (another finger position, a different baseline) are dropped.
        recent = [(t, v) for t, v in samples if t >= now - WINDOW_SEC]
        if not recent:
            return
        result = ppg_dsp.analyze([t for t, _ in recent], [v for _, v in recent])
        if result["quality"] == ppg_dsp.QUALITY_NO_CONTACT:
            self._samples.clear()
        elif result["quality"] == ppg_dsp.QUALITY_GOOD and samples[-1][0] - samples[0][0] >= HRV_WINDOW_SEC * 0.9:
            long_run = ppg_dsp.analyze([t for t, _ in samples], [v for _, v in samples])
            if long_run["quality"] == ppg_dsp.QUALITY_GOOD:
                result["rmssd_ms"] = long_run["rmssd_ms"]
        contact = result["quality"] != ppg_dsp.QUALITY_NO_CONTACT
        result["waveform"] = ppg_dsp.display_waveform([t for t, _ in recent], [v for _, v in recent]) if contact else []
        result["timestamp"] = now
        with self._lock:
            self._latest = result
