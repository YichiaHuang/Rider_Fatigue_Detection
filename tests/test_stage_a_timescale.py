"""Stage A must give the same answer at any frame rate (plan.md 4.2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rider"))
from stage_a_scoring import StageAConfig, StageAScorer  # noqa: E402
from layer_b_features import PerclosTracker  # noqa: E402

# The weight itself gets tuned (3 -> 7 -> 5 -> 3 in one evening, 2026-09-19); what these tests pin is the
# SHAPE of the rule — one event, one increment — so they follow the configured value.
YAWN = StageAConfig().yawn_add

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
            self.assertEqual(total, YAWN, f"one yawn = one increment regardless of fps ({fps})")


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
        self.assertEqual(total, 2 * YAWN)

    def test_reopening_within_the_cooldown_does_not_score_twice(self):
        scorer, total = StageAScorer(StageAConfig()), 0.0
        for i in range(40):
            t = i / 20.0
            opened = (0.5 <= t < 0.7) or (1.0 <= t < 1.2)      # second opening only 0.5 s later
            total += scorer.update(timestamp=t, **dict(NORMAL, mar=0.45 if opened else 0.05))["added"]
        self.assertEqual(total, YAWN)

    def test_talking_level_mouth_movement_does_not_score(self):
        scorer = StageAScorer(StageAConfig())
        total = sum(scorer.update(timestamp=i / 20.0, **dict(NORMAL, mar=0.14 if (i // 6) % 2 else 0.05))["added"]
                    for i in range(200))                       # 10 s of MAR flapping 0.05 <-> 0.14 (p75 when talking)
        self.assertEqual(total, 0.0)

    def test_half_open_mouth_below_the_line_does_not_score(self):
        scorer = StageAScorer(StageAConfig())
        total = sum(scorer.update(timestamp=i / 20.0, **dict(NORMAL, mar=0.35 if 20 <= i < 60 else 0.05))["added"]
                    for i in range(100))
        self.assertEqual(total, 0.0, "0.35 scored at the old 0.3 line; at 0.4 it must not")

    def test_held_open_mouth_scores_once(self):
        scorer = StageAScorer(StageAConfig())
        total = sum(scorer.update(timestamp=i / 20.0, **dict(NORMAL, mar=0.5))["added"] for i in range(100))
        self.assertEqual(total, YAWN, "edge-triggered: 5 s of open mouth is one event, not five")

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


class DmsCriteriaTest(unittest.TestCase):
    """--criteria dms hands the tracker NXP's own per-frame verdict (both eyes < 0.2)."""

    def test_callers_verdict_overrides_the_mean_ratio_line(self):
        from layer_b_features import PerclosTracker
        mean_rule, dms_rule = PerclosTracker(warmup_sec=1.0), PerclosTracker(warmup_sec=1.0)
        for i in range(40):  # one eye shut (0.10), one open (0.30): mean 0.20 is "closed" for us, not for the DMS
            t = i * 0.1
            a = mean_rule.update(t, 0.20)
            b = dms_rule.update(t, 0.20, closed=(0.10 < 0.2 and 0.30 < 0.2))
        self.assertGreater(a, 0.9)
        self.assertEqual(b, 0.0)


class HeadRuleSwitchTest(unittest.TestCase):
    HEAD_DOWN = dict(ear=0.30, mar=0.05, head_pitch_deg=34.0, perclos=0.02)   # what the low camera mount reads

    def test_head_down_is_not_scored_by_default(self):
        scorer = StageAScorer(StageAConfig())
        for i in range(30):
            result = scorer.update(timestamp=float(i), **self.HEAD_DOWN)
        self.assertEqual((result["score"], result["reasons"]), (0.0, []))

    def test_other_rules_unaffected_while_head_is_down(self):
        scorer = StageAScorer(StageAConfig())
        for i in range(1, 6):
            result = scorer.update(timestamp=float(i), **dict(self.HEAD_DOWN, perclos=0.4))
        self.assertEqual(result["reasons"], ["perclos"])
        self.assertAlmostEqual(result["score"], 8.0, delta=0.01)

    def test_can_be_switched_back_on(self):
        scorer, seen = StageAScorer(StageAConfig(score_head_down=True)), set()
        for i in range(10):
            seen.update(scorer.update(timestamp=float(i), **self.HEAD_DOWN)["reasons"])
        self.assertIn("head_drop_visual_only_no_imu", seen)
