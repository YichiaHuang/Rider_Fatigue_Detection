"""PPG drowsiness indicator (rider/ppg_fatigue.py): the physiological half of the
multimodal score. Synthetic vitals at 1 Hz — what matters here is the logic
(personal baseline, both indices, sustained, capped, honest about missing data)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rider"))
from ppg_fatigue import PpgFatigueConfig, PpgFatigueIndicator  # noqa: E402

from server.config import Config  # noqa: E402


def vitals(hr, rmssd, quality="good"):
    return {"quality": quality, "heart_rate_bpm": hr, "rmssd_ms": rmssd}


class PpgFatigueTest(unittest.TestCase):
    def setUp(self):
        self.cfg = PpgFatigueConfig()
        self.indicator = PpgFatigueIndicator(self.cfg)
        self.now = 1000.0

    def feed(self, seconds, sample):
        bonus = 0.0
        for _ in range(int(seconds)):
            self.now += 1.0
            bonus = self.indicator.update(self.now, sample)
        return bonus

    def awake_baseline(self):
        self.feed(self.cfg.baseline_sec + 5, vitals(80.0, 30.0))
        self.assertEqual((self.indicator.baseline_hr, self.indicator.baseline_rmssd), (80.0, 30.0))

    def test_learns_a_personal_baseline_first(self):
        self.feed(60, vitals(80.0, 30.0))
        snap = self.indicator.snapshot()
        self.assertEqual((snap["state"], snap["baseline_hr_bpm"], snap["bonus"]), ("learning", None, 0.0))
        self.assertAlmostEqual(snap["baseline_progress"], 0.2, places=1)
        self.awake_baseline()
        self.assertEqual(self.indicator.state, "normal")

    def test_sustained_pattern_adds_slowly_and_is_capped(self):
        self.awake_baseline()
        drowsy = vitals(72.0, 42.0)  # HR -10 %, RMSSD +40 % against this rider's own baseline
        self.feed(55, drowsy)
        self.assertEqual(self.indicator.state, "normal", "the 120 s median has not come round yet")
        self.feed(65, drowsy)  # the median flips once drowsy seconds are the majority of the window
        self.assertEqual(self.indicator.state, "pattern")
        self.assertEqual(self.feed(50, drowsy), 0.0, "present, but not yet for the 120 s it has to be sustained")
        bonus = self.feed(130, drowsy)
        self.assertEqual(self.indicator.state, "elevated")
        self.assertTrue(3.5 < bonus < 4.5, bonus)  # ~1 point per 30 s once sustained
        self.assertEqual(self.feed(600, drowsy), self.cfg.bonus_cap)
        snap = self.indicator.snapshot()
        self.assertEqual((snap["hr_change_pct"], snap["rmssd_change_pct"]), (-10.0, 40.0))

    def test_operator_reset_drops_the_bonus_but_keeps_the_baseline(self):
        self.awake_baseline()
        drowsy = vitals(72.0, 40.0)
        self.feed(self.cfg.window_sec + self.cfg.sustain_sec + 120, drowsy)
        self.assertGreater(self.indicator.bonus, 0.0)
        self.indicator.clear_pattern()
        self.assertEqual((self.indicator.bonus, self.indicator.pattern_sec), (0.0, 0.0))
        self.assertEqual(self.indicator.baseline_hr, 80.0, "same rider: the personal baseline stays")
        # The pattern has to be sustained all over again before it adds anything.
        self.assertEqual(self.feed(self.cfg.sustain_sec - 5, drowsy), 0.0)
        self.assertGreater(self.feed(60, drowsy), 0.0)

    def test_cap_stays_below_the_apps_warning_line(self):
        """Physiology alone must never warn a rider or pause their dispatch (HRV alone: 61 % on real roads)."""
        platform = Config()
        self.assertLess(self.cfg.bonus_cap, platform.warn_threshold)
        self.assertLess(PpgFatigueConfig.demo().bonus_cap, platform.warn_threshold)

    def test_heart_rate_falling_alone_is_not_drowsiness(self):
        self.awake_baseline()
        self.assertEqual(self.feed(600, vitals(70.0, 31.0)), 0.0, "red light / relaxing: HR down, variability unchanged")
        self.assertEqual(self.indicator.state, "normal")

    def test_exertion_is_not_drowsiness(self):
        self.awake_baseline()
        self.assertEqual(self.feed(600, vitals(110.0, 12.0)), 0.0)

    def test_recovery_brings_it_back_down_faster_than_it_rose(self):
        self.awake_baseline()
        self.feed(self.cfg.window_sec + self.cfg.sustain_sec + 90, vitals(72.0, 42.0))
        raised = self.indicator.bonus
        self.assertGreater(raised, 2.0)
        self.feed(self.cfg.window_sec, vitals(80.0, 30.0))  # awake again; the median needs a while to follow
        self.assertLess(self.indicator.bonus, raised)
        self.assertEqual(self.feed(120, vitals(80.0, 30.0)), 0.0)

    def test_a_hole_in_the_data_freezes_then_a_long_one_decays(self):
        self.awake_baseline()
        self.feed(self.cfg.window_sec + self.cfg.sustain_sec + 60, vitals(72.0, 42.0))
        raised = self.indicator.bonus
        self.feed(30, None)  # sensor slipped for half a minute
        self.assertEqual(self.indicator.bonus, raised, "missing data is neither drowsiness nor recovery")
        self.feed(self.cfg.stale_after_sec + 120, vitals(None, None, quality="no_contact"))
        self.assertEqual(self.indicator.bonus, 0.0, "sensor off for good: the bonus must not outlive the evidence")

    def test_only_confirmed_seconds_count(self):
        self.feed(400, vitals(80.0, 30.0, quality="holding"))  # a carried-over value, not a measurement
        self.assertEqual(self.indicator.state, "no_signal")
        self.feed(400, vitals(80.0, None))  # rate but never an RMSSD: can't build the second half of the baseline
        self.assertIsNone(self.indicator.baseline_hr)

    def restarted(self, state, after_sec=0.0, config=None):
        """The runner was restarted (board reboot, crash + supervisor): a new indicator, the saved file."""
        self.now += after_sec
        self.indicator = PpgFatigueIndicator(config or PpgFatigueConfig())
        return self.indicator.restore_state(state, self.now)

    def test_finished_baseline_survives_a_restart(self):
        self.awake_baseline()
        saved = json.loads(json.dumps(self.indicator.export_state(self.now)))  # as it comes back from the file
        self.assertEqual(self.restarted(saved, after_sec=40 * 60), "baseline")
        self.assertEqual((self.indicator.baseline_hr, self.indicator.baseline_rmssd), (80.0, 30.0))
        self.feed(70, vitals(80.0, 30.0))
        snap = self.indicator.snapshot()
        self.assertEqual((snap["state"], snap["baseline_restored"]), ("normal", 1), "no second 5-minute wait")
        self.assertGreater(snap["baseline_age_sec"], 40 * 60)
        self.assertEqual(self.indicator.bonus, 0.0, "the bonus itself is never carried over")

    def test_unfinished_baseline_is_resumed_not_restarted(self):
        self.feed(200, vitals(80.0, 30.0))
        saved = json.loads(json.dumps(self.indicator.export_state(self.now)))
        self.assertEqual(self.restarted(saved, after_sec=5 * 60), "partial")
        self.feed(110, vitals(80.0, 30.0))  # 200 s before + 110 s after the reboot
        self.assertEqual((self.indicator.state, self.indicator.baseline_hr), ("normal", 80.0))

    def test_stale_or_unsuitable_files_are_ignored(self):
        self.awake_baseline()
        saved = self.indicator.export_state(self.now)
        self.assertIn("h old", self.restarted(saved, after_sec=13 * 3600), "yesterday's awake is not today's")
        self.assertIsNone(self.indicator.baseline_hr)
        demo = PpgFatigueIndicator(PpgFatigueConfig.demo())
        self.assertIn("shorter", self.restarted(demo.export_state(self.now)), "a 45 s demo baseline is not good enough for real use")
        self.indicator = PpgFatigueIndicator(PpgFatigueConfig())
        partial = dict(self.indicator.export_state(self.now))
        self.assertIn("min old", self.restarted(partial, after_sec=31 * 60))
        for junk in ({}, {"version": 1}, {"version": 1, "baseline_sec": "x"}):
            self.assertTrue(self.restarted(junk).startswith("ignored"))

    def test_file_round_trip_and_a_half_written_file(self):
        import tempfile
        from ppg_fatigue import load_state, save_state
        self.awake_baseline()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "baseline.json")
            self.assertIsNone(load_state(path), "first run: no file")
            self.assertTrue(save_state(path, self.indicator.export_state(self.now)))
            self.assertEqual(self.restarted(load_state(path)), "baseline")
            with open(path, "w") as f:
                f.write('{"version": 1, "baseli')  # power cut mid-write
            self.assertIsNone(load_state(path))
            self.assertFalse(save_state(os.path.join(folder, "missing", "x.json"), {}), "unwritable: carry on without")

    def test_demo_preset_is_the_same_logic_on_a_shorter_clock(self):
        self.indicator = PpgFatigueIndicator(PpgFatigueConfig.demo())
        self.feed(50, vitals(80.0, 30.0))
        self.assertEqual(self.indicator.state, "normal")
        self.assertGreater(self.feed(90, vitals(72.0, 42.0)), 0.0)


if __name__ == "__main__":
    unittest.main()
