"""PPG heart-rate DSP on synthetic pulse waves: right when the signal is good,
and — more important — silent when it isn't."""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rider"))
import ppg_dsp  # noqa: E402

FS = 50.0


def pulse_wave(bpm, seconds=12.0, dc=120000.0, amplitude=900.0, noise=60.0, drift=1500.0, seed=1, hrv_ms=0.0):
    rng = random.Random(seed)
    t, ir, phase = [], [], 0.0
    period = 60.0 / bpm
    for i in range(int(seconds * FS)):
        now = i / FS
        jitter = 1.0 + (hrv_ms / 1000.0 / period) * math.sin(2 * math.pi * now / 7.0)
        phase += (1.0 / FS) / (period * jitter)
        p = phase % 1.0
        beat = math.exp(-((p - 0.18) / 0.07) ** 2) + 0.35 * math.exp(-((p - 0.45) / 0.10) ** 2)  # systolic + dicrotic
        t.append(now)
        ir.append(dc - amplitude * beat + drift * math.sin(2 * math.pi * now / 9.0) + rng.gauss(0, noise))
    return t, ir


class PpgDspTest(unittest.TestCase):
    def test_rates_across_the_range(self):
        for bpm in (48, 60, 72, 95, 120, 160):
            result = ppg_dsp.analyze(*pulse_wave(bpm))
            self.assertEqual(result["quality"], "good", (bpm, result))
            self.assertAlmostEqual(result["heart_rate_bpm"], bpm, delta=2.0, msg=result)

    def test_no_contact_reports_nothing(self):
        t, ir = pulse_wave(72, dc=2000.0, amplitude=300.0)   # what the bare sensor reads: IR ~2000
        result = ppg_dsp.analyze(t, ir)
        self.assertEqual((result["quality"], result["heart_rate_bpm"]), ("no_contact", None))
        self.assertEqual(ppg_dsp.display_waveform(t, ir)[:1] != [], True)  # waveform helper itself never fails

    def test_noise_is_not_a_heart_rate(self):
        for seed in range(5):
            rng = random.Random(seed)
            t = [i / FS for i in range(600)]
            ir = [120000.0 + rng.gauss(0, 400) for _ in t]
            result = ppg_dsp.analyze(t, ir)
            self.assertIsNone(result["heart_rate_bpm"], result)
            self.assertIn(result["quality"], ("weak", "settling"))

    def test_motion_artifact_is_rejected_not_guessed(self):
        t, ir = pulse_wave(72)
        rng = random.Random(3)
        for i in range(150, 450):       # 6 s of someone fidgeting with the sensor
            ir[i] += rng.uniform(-6000, 6000)
        self.assertIsNone(ppg_dsp.analyze(t, ir)["heart_rate_bpm"])

    def test_needs_enough_signal_first(self):
        result = ppg_dsp.analyze(*pulse_wave(72, seconds=4.0))
        self.assertEqual((result["quality"], result["heart_rate_bpm"]), ("settling", None))

    def test_contact_lost_at_the_end(self):
        t, ir = pulse_wave(72)
        ir[-40:] = [1900.0] * 40
        self.assertEqual(ppg_dsp.analyze(t, ir)["quality"], "no_contact")

    def test_rmssd_tracks_variability(self):
        steady = ppg_dsp.analyze(*pulse_wave(66, seconds=40.0, hrv_ms=0.0))
        varied = ppg_dsp.analyze(*pulse_wave(66, seconds=40.0, hrv_ms=45.0))
        self.assertEqual((steady["quality"], varied["quality"]), ("good", "good"))
        self.assertLess(steady["rmssd_ms"], 25.0, steady)
        self.assertGreater(varied["rmssd_ms"], steady["rmssd_ms"] + 5.0, (steady, varied))

    def test_waveform_shape(self):
        wave = ppg_dsp.display_waveform(*pulse_wave(72))
        self.assertEqual(len(wave), 150)
        self.assertTrue(all(-1.0 <= v <= 1.0 for v in wave))
        self.assertGreater(max(wave), 0.8)   # beats point up


if __name__ == "__main__":
    unittest.main()
