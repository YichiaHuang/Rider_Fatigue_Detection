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


def replay(ir, seconds_per_step=1.0):
    """Feed a stream through analyze() + RateTracker the way the board does."""
    tracker, shown = ppg_dsp.RateTracker(), []
    for end in range(int(FS * 3), len(ir) + 1, int(FS * seconds_per_step)):
        lo = max(0, end - int(12 * FS))
        window = ppg_dsp.analyze([i / FS for i in range(lo, end)], ir[lo:end])
        shown.append(tracker.update(end / FS, window))
    return shown


class TrackerTest(unittest.TestCase):
    def test_weak_pulse_locks_and_stays(self):
        _, ir = pulse_wave(72, seconds=45.0, amplitude=360.0, noise=25.0, drift=800.0)   # perfusion ~0.3 %
        shown = replay(ir)
        rates = [o["heart_rate_bpm"] for o in shown if o["heart_rate_bpm"] is not None]
        self.assertGreater(len(rates), 0.6 * len(shown), [o["quality"] for o in shown])
        self.assertTrue(all(abs(r - 72) < 4 for r in rates), rates)

    def test_single_window_is_only_a_candidate(self):
        tracker = ppg_dsp.RateTracker()
        window = ppg_dsp.analyze(*pulse_wave(72))
        self.assertEqual(window["quality"], "good")
        first = tracker.update(100.0, window)
        self.assertEqual((first["quality"], first["heart_rate_bpm"]), ("weak", None), "one good window must not show a rate")

    def test_holds_through_a_dropout_then_gives_up(self):
        tracker = ppg_dsp.RateTracker()
        good = ppg_dsp.analyze(*pulse_wave(72))
        weak = dict(good, quality="weak", heart_rate_bpm=None)
        for i in range(6):
            out = tracker.update(100.0 + i, good)
        self.assertEqual(out["quality"], "good")
        held = tracker.update(110.0, weak)
        self.assertEqual((held["quality"], held["heart_rate_bpm"] is not None, held["held_sec"]), ("holding", True, 5.0))
        gone = tracker.update(118.0, weak)
        self.assertEqual((gone["quality"], gone["heart_rate_bpm"]), ("weak", None), "a stale rate must be dropped")

    def test_contact_loss_unlocks_immediately(self):
        tracker = ppg_dsp.RateTracker()
        good = ppg_dsp.analyze(*pulse_wave(72))
        for i in range(6):
            tracker.update(100.0 + i, good)
        out = tracker.update(106.0, dict(good, quality="no_contact", heart_rate_bpm=None))
        self.assertEqual((out["quality"], out["heart_rate_bpm"]), ("no_contact", None))

    def test_jumping_candidates_never_lock(self):
        tracker, template = ppg_dsp.RateTracker(), ppg_dsp.analyze(*pulse_wave(72))
        for i, bpm in enumerate((52, 88, 61, 120, 47, 95, 70, 110, 58, 83)):
            out = tracker.update(100.0 + i, dict(template, heart_rate_bpm=float(bpm)))
            self.assertIsNone(out["heart_rate_bpm"])

    def test_static_surface_noise_shows_nothing(self):
        rng = random.Random(7)
        level, ir = 120000.0, []
        for _ in range(int(60 * FS)):   # sensor pressed on a desk: IR high, only electronics noise
            level += rng.gauss(0, 2)
            ir.append(level + rng.gauss(0, 25))
        self.assertTrue(all(o["heart_rate_bpm"] is None for o in replay(ir)))

    def test_placement_step_does_not_block_the_window(self):
        _, ir = pulse_wave(72, seconds=14.0)
        ir[:150] = [2000.0 + (v - 120000.0) * 0.01 for v in ir[:150]]     # first 3 s: finger not on yet
        result = ppg_dsp.analyze([i / FS for i in range(len(ir))], ir)
        self.assertEqual(result["quality"], "good", result)



def beating_finger(seconds, sd_ms, rsa_ms, interruptions=(), bpm=78.0, seed=7):
    """A pulse wave with KNOWN beat-to-beat variability (random + breathing-linked),
    plus what happens on a real finger: ("lift", start, length) = contact lost,
    ("move", start, length) = fidgeting. Returns (t, ir, true RMSSD in ms)."""
    rng = random.Random(seed)
    beats, now = [0.0], 0.0
    while now < seconds + 2:
        now += max(0.35, 60.0 / bpm + rsa_ms / 1000.0 * math.sin(2 * math.pi * now / 4.2) + rng.gauss(0, sd_ms / 1000.0))
        beats.append(now)
    rr = [b - a for a, b in zip(beats, beats[1:])]
    truth = 1000.0 * math.sqrt(sum((y - x) ** 2 for x, y in zip(rr, rr[1:])) / (len(rr) - 1))
    t, ir, k = [], [], 0
    for i in range(int(seconds * FS)):
        now = i / FS
        while beats[k + 1] <= now:
            k += 1
        p = (now - beats[k]) / (beats[k + 1] - beats[k])
        value = (120000.0 - 900.0 * (math.exp(-((p - 0.18) / 0.07) ** 2) + 0.35 * math.exp(-((p - 0.45) / 0.10) ** 2))
                 + 1500.0 * math.sin(2 * math.pi * now / 9.0) + rng.gauss(0, 60.0))
        for kind, start, length in interruptions:
            if start <= now < start + length:
                value = 2000.0 + rng.gauss(0, 30) if kind == "lift" else value + rng.uniform(-6000, 6000)
        t.append(now)
        ir.append(value)
    return t, ir, truth


def replay_reader(t, ir):
    """What ppg_reader does once a second, offline. -> (good seconds, RMSSD readings on good seconds)."""
    tracker, log, good, readings = ppg_dsp.RateTracker(), ppg_dsp.SuccessiveDifferenceLog(), 0, []
    for sec in range(12, int(t[-1])):
        hi = int(sec * FS)
        window = ppg_dsp.analyze(t[hi - int(12 * FS):hi], ir[hi - int(12 * FS):hi])
        pairs = window.pop("beat_pairs")
        if tracker.update(float(sec), window)["quality"] == "good":
            good += 1
            log.add(pairs)
            value, _ = log.rmssd_ms(float(sec))
            if value is not None:
                readings.append(value)
    return good, readings


INTERRUPTED = tuple(("lift", s, 1.5) for s in range(30, 170, 35)) + tuple(("move", s, 3.0) for s in range(20, 170, 25))


class InterruptedRmssdTest(unittest.TestCase):
    """The sensor on a real finger is interrupted every half minute or so. RMSSD used
    to need 40 unbroken seconds and came out in ~5 % of good seconds on the board."""

    def median(self, values):
        return sorted(values)[len(values) // 2]

    def test_rmssd_is_available_through_interruptions(self):
        t, ir, _ = beating_finger(170, sd_ms=25, rsa_ms=20, interruptions=INTERRUPTED)
        starts = sorted(s for _, s, _ in INTERRUPTED)
        self.assertLess(max(b - a for a, b in zip(starts, starts[1:])), 40, "no 40 s stretch is ever free of an interruption")
        good, readings = replay_reader(t, ir)
        self.assertGreater(good, 40)
        self.assertGreater(len(readings) / good, 0.6, "RMSSD must be there on most good seconds, interrupted or not")

    def test_interruptions_do_not_change_the_answer(self):
        clean = self.median(replay_reader(*beating_finger(170, 25, 20)[:2])[1])
        broken = self.median(replay_reader(*beating_finger(170, 25, 20, INTERRUPTED)[:2])[1])
        self.assertLess(abs(broken - clean) / clean, 0.15, (clean, broken))

    def test_fidgeting_must_not_look_like_rising_hrv(self):
        """A rise in RMSSD is what the fatigue indicator reads as drowsiness."""
        fidgets = tuple(("move", s, 3.0) for s in range(20, 170, 25))
        clean = self.median(replay_reader(*beating_finger(170, 25, 20)[:2])[1])
        moved = self.median(replay_reader(*beating_finger(170, 25, 20, fidgets)[:2])[1])
        self.assertLess(moved, clean * 1.2, (clean, moved))   # was +65 % before beats next to a burst were excluded

    def test_more_variability_reads_higher(self):
        readings = [self.median(replay_reader(*beating_finger(170, sd, rsa, INTERRUPTED)[:2])[1])
                    for sd, rsa in ((10, 8), (25, 20), (40, 35))]   # true RMSSD ~16, ~40, ~64 ms
        self.assertTrue(readings[0] < readings[1] < readings[2], readings)
        self.assertGreater(readings[2] / readings[1], 1.3, "a +60 % rise must stay clearly visible")

    def test_log_counts_each_beat_once_and_waits_for_enough(self):
        log = ppg_dsp.SuccessiveDifferenceLog(window_sec=60.0, min_pairs=5)
        pairs = [(10.0 + i * 0.8, 0.80, 0.83) for i in range(4)]
        self.assertEqual(log.add(pairs), 4)
        self.assertEqual(log.add(pairs), 0, "the next window sees the same beats again")
        self.assertEqual(log.rmssd_ms(14.0), (None, 4))
        log.add([(14.0, 0.80, 0.83)])
        self.assertEqual(log.rmssd_ms(15.0), (30.0, 5))
        self.assertEqual(log.rmssd_ms(200.0), (None, 0), "old beats age out")

if __name__ == "__main__":
    unittest.main()
