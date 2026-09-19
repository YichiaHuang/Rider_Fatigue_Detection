"""MAX30102 PPG reader + once-a-second vitals, for the live runner.

Register setup is H2's validated configuration from /root/sensors/sensor_test.c
(SpO2 mode, 100 sps / 411 us / 18-bit, FIFO average 2 -> 50 Hz, LED_PA 0x24).
The LED rail (VEXP_3V3) must be on: see /root/sensors/vexp-3v3.service.

Only one process can drain the sensor's FIFO. Don't run sensor_test at the same
time as the live runner — each would see half of the samples.

The sensor is read in its OWN PROCESS. The FIFO holds 32 samples = 0.64 s at
50 Hz; polled from a thread inside the live runner, the GIL (inference + JPEG
encoding + capture) delayed polls past that, the FIFO wrapped, and only ~8 of
every 50 samples arrived — far too broken a signal to find a pulse in. A child
process has its own interpreter and polls on time.

    ppg = PpgProcess(bus=0); ppg.start()      # in the live runner
    ppg.latest()  ->  {"quality": "good", "heart_rate_bpm": 72.4, "waveform": [...], ...} or None

    python3 rider/ppg_reader.py --bus 0       # the child: one JSON line per second on stdout
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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

    def read_samples(self):
        """(IR counts oldest first, overflowed). overflowed = the FIFO wrapped
        since the last call, i.e. samples were lost and the trace has a hole."""
        wr, overflow, rd = self._read(REG_FIFO_WR_PTR, 3)
        count = (wr - rd) & 0x1F
        if count == 0:
            if overflow == 0:
                return [], False
            count = 32  # pointers equal + overflow counter set = wrapped exactly full (as in sensor_test.c)
        raw = self._read(REG_FIFO_DATA, count * 6)
        samples = [((raw[i * 6 + 3] << 16) | (raw[i * 6 + 4] << 8) | raw[i * 6 + 5]) & 0x03FFFF
                   for i in range(count)]
        return samples, overflow > 0


class PpgMonitor:
    """Runs in the child process: poll, analyse once a second, hand the result to `emit`."""

    def __init__(self, bus: int = 0):
        self._sensor = Max30102(bus)
        self._samples = deque(maxlen=int(SAMPLE_HZ * HRV_WINDOW_SEC))  # (t, ir)
        self.overflows = 0

    def run(self, emit, should_stop) -> None:
        last_analysis, next_poll = 0.0, time.monotonic()
        while not should_stop():
            next_poll += 0.05                      # 20 polls/s, ~2-3 samples each: far from the 32-sample depth
            time.sleep(max(0.0, next_poll - time.monotonic()))
            try:
                batch, overflowed = self._sensor.read_samples()
                now = time.time()
                if overflowed:                     # a hole in the trace: don't analyse across it
                    self.overflows += 1
                    self._samples.clear()
                for i, ir in enumerate(batch):     # date samples back from the read time, as sensor_test does
                    self._samples.append((now - (len(batch) - 1 - i) / SAMPLE_HZ, ir))
                if now - last_analysis < 1.0:
                    continue
                last_analysis = now
                if not self._sensor.alive():
                    self._sensor._configure()
                    self._samples.clear()
                    continue
                emit(self._analyze(now))
            except OSError as exc:                 # a loose wire must not end the process
                emit({"timestamp": time.time(), "quality": "no_contact", "error": str(exc)})
                time.sleep(1.0)
                next_poll = time.monotonic()

    def _analyze(self, now: float) -> dict:
        samples = list(self._samples)
        # Contact is judged on the recent window only; if it was lost, older
        # samples (another finger position, a different baseline) are dropped.
        recent = [(t, v) for t, v in samples if t >= now - WINDOW_SEC]
        if not recent:
            return {"timestamp": now, "quality": "no_contact"}
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
        result["fifo_overflows"] = self.overflows
        return result


class PpgProcess:
    """Live-runner side: owns the child process and keeps its latest line."""

    def __init__(self, bus: int = 0):
        Max30102(bus)  # probe first so a missing sensor fails here, loudly, not silently in the child
        self._bus = bus
        self._lock = threading.Lock()
        self._latest = None
        self._child = None

    def start(self) -> None:
        self._child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--bus", str(self._bus)],
                                       stdout=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read_lines, name="ppg-lines", daemon=True).start()

    def stop(self) -> None:
        if self._child is not None:
            self._child.terminate()

    def latest(self) -> "dict | None":
        with self._lock:
            return self._latest

    def _read_lines(self) -> None:
        for line in self._child.stdout:   # blocking read: costs the runner nothing between lines
            try:
                result = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._latest = result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MAX30102 reader: one JSON vitals line per second on stdout")
    parser.add_argument("--bus", type=int, default=0)
    args = parser.parse_args()
    parent = os.getppid()
    try:
        os.nice(-5)   # polling on time matters more than throughput; fails harmlessly if not root
    except OSError:
        pass
    monitor = PpgMonitor(args.bus)
    monitor.run(emit=lambda result: print(json.dumps(result), flush=True),
                should_stop=lambda: os.getppid() != parent)   # runner died: don't linger holding the sensor


if __name__ == "__main__":
    main()
