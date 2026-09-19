"""Stage A must give the same answer at any frame rate (plan.md 4.2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rider"))
from stage_a_scoring import StageAConfig, StageAScorer  # noqa: E402
from layer_b_features import PerclosTracker  # noqa: E402

CLOSED = dict(ear=0.10, mar=0.05, head_pitch_deg=2.0, perclos=0.40)
NORMAL = dict(ear=0.30, mar=0.05, head_pitch_deg=2.0, perclos=0.02)


def run(fps: float, script) -> float:
    """script: [(seconds, features), ...] played back at the given frame rate."""
    scorer, t, result = StageAScorer(StageAConfig()), 0.0, None
    for seconds, features in script:
        for _ in range(int(seconds * fps)):
            t += 1.0 / fps
            result = scorer.update(timestamp=t, **features)
    return result["score"]


class TimescaleTest(unittest.TestCase):
    def test_perclos_accrual_is_fps_independent(self):
        scores = [run(fps, [(10, CLOSED)]) for fps in (1, 5, 15, 30)]
        for score in scores:
            self.assertAlmostEqual(score, 20.0, delta=2.1, msg=scores)  # 2 points/s * 10 s

    def test_decay_is_fps_independent(self):
        scores = [run(fps, [(10, CLOSED), (6, NORMAL)]) for fps in (1, 15)]
        self.assertAlmostEqual(scores[0], scores[1], delta=2.1, msg=scores)
        expected = 20.0 - 6.0 * StageAConfig().decay_per_sec        # 10 s of accrual, then 6 s of decay
        self.assertAlmostEqual(scores[1], expected, delta=2.5, msg=scores)

    def test_decay_rate_is_the_configured_one(self):
        scorer = StageAScorer(StageAConfig(decay_per_sec=0.3))
        scorer.score = 15.0
        for i in range(1, 25):                                     # 23 s at 1 Hz after the first (dt = 0) update
            result = scorer.update(timestamp=float(i), **NORMAL)
        self.assertAlmostEqual(result["score"], 15.0 - 23 * 0.3, delta=0.05)
        self.assertLessEqual(result["score"], 8.1, "pause line -> resume line should take about 23 s")

    def test_gap_neither_accrues_nor_decays_for_the_whole_hole(self):
        scorer = StageAScorer(StageAConfig())
        for i in range(1, 6):
            scorer.update(timestamp=float(i), **CLOSED)
        before = scorer.score
        after_gap = scorer.update(timestamp=65.0, **CLOSED)["score"]   # face lost for a minute
        self.assertLessEqual(after_gap - before, 2.0 + 1e-6, "a 60 s hole must not count as 60 s of closed eyes")
        recovered = StageAScorer(StageAConfig())
        recovered.score = 20.0
        recovered.update(timestamp=1.0, **NORMAL)
        self.assertGreaterEqual(recovered.update(timestamp=61.0, **NORMAL)["score"], 19.0,
                                "a 60 s hole must not count as 60 s of recovery either")

    def test_yawn_is_an_event_not_a_rate(self):
        yawn = dict(NORMAL, mar=0.6)
        for fps in (1, 15):
            scorer, t, total = StageAScorer(StageAConfig()), 0.0, 0.0
            for _ in range(int(2 * fps)):
                t += 1.0 / fps
                total += scorer.update(timestamp=t, **yawn)["added"]
            self.assertEqual(total, 3.0, f"one yawn = +3 regardless of fps ({fps})")


if __name__ == "__main__":
    unittest.main()


class DemoTuningTest(unittest.TestCase):
    """Stage A defaults use tuned MAR 0.4; the runner selects DMS MAR 0.3."""

    def test_mouth_events_one_second_apart_each_score(self):
        scorer, total = StageAScorer(StageAConfig()), 0.0
        # mouth opens above the DMS 0.3 boundary twice, 1.2 s apart
        for i in range(60):
            t = i / 20.0
            opened = (0.5 <= t < 0.9) or (1.7 <= t < 2.1)
            total += scorer.update(timestamp=t, **dict(NORMAL, mar=0.45 if opened else 0.05))["added"]
        self.assertEqual(total, 6.0)

    def test_reopening_within_the_cooldown_does_not_score_twice(self):
        scorer, total = StageAScorer(StageAConfig()), 0.0
        for i in range(40):
            t = i / 20.0
            opened = (0.5 <= t < 0.7) or (1.0 <= t < 1.2)      # second opening only 0.5 s later
            total += scorer.update(timestamp=t, **dict(NORMAL, mar=0.45 if opened else 0.05))["added"]
        self.assertEqual(total, 3.0)

    def test_held_open_mouth_scores_once(self):
        scorer = StageAScorer(StageAConfig())
        total = sum(scorer.update(timestamp=i / 20.0, **dict(NORMAL, mar=0.5))["added"] for i in range(100))
        self.assertEqual(total, 3.0, "edge-triggered: 5 s of open mouth is one event, not five")

    def test_talking_below_dms_mouth_threshold_does_not_score(self):
        scorer = StageAScorer(StageAConfig())
        result = scorer.update(timestamp=1.0, **dict(NORMAL, mar=0.15))
        self.assertEqual(result["score"], 0.0)

    def test_dms_requires_both_eyes_closed_for_perclos(self):
        tracker = PerclosTracker(window_seconds=30)
        for t in range(8):
            ratio = tracker.update(float(t), ear=0.1, closed=False)
        self.assertEqual(ratio, 0.0, "one closed eye must not count as DMS eyes_closed")
        for t in range(8, 16):
            ratio = tracker.update(float(t), ear=0.1, closed=True)
        self.assertGreater(ratio, 0.4)

    def test_perclos_between_old_and_new_threshold_now_accrues(self):
        scorer = StageAScorer(StageAConfig())
        for i in range(1, 6):
            result = scorer.update(timestamp=float(i), **dict(NORMAL, perclos=0.12))
        self.assertEqual(result["reasons"], ["perclos"])
        self.assertAlmostEqual(result["score"], 8.0, delta=0.01)   # 2 points/s over 4 s
