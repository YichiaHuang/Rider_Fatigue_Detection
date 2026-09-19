"""Stage A must give the same answer at any frame rate (plan.md 4.2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rider"))
from stage_a_scoring import StageAConfig, StageAScorer  # noqa: E402

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
        self.assertLess(scores[1], 16.0)

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
