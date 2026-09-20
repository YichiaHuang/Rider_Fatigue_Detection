"""Circuit breaker + store status rules. Run: python3 -m unittest discover -s tests -v"""
import unittest

from server.config import Config
from server.core.circuit_breaker import DISPATCH_NORMAL, DISPATCH_PAUSED, CircuitBreaker
from server.core.models import (PERCEPTION_NO_FACE, PERCEPTION_OK, DetailSample, HealthSample, PayloadError,
                                ScoreSample, VitalsSample, parse_detail, parse_health, parse_score, parse_vitals)
from server.core.store import RiderStore


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CircuitBreakerTest(unittest.TestCase):
    def test_hysteresis(self):
        breaker = CircuitBreaker(15, 8)
        self.assertFalse(breaker.update(14.9))
        self.assertTrue(breaker.update(15.0))
        self.assertEqual(breaker.state, DISPATCH_PAUSED)
        self.assertFalse(breaker.update(9.0), "between thresholds must NOT resume")
        self.assertFalse(breaker.update(14.0))
        self.assertTrue(breaker.update(8.0))
        self.assertEqual(breaker.state, DISPATCH_NORMAL)
        self.assertFalse(breaker.update(12.0), "between thresholds must NOT pause")

    def test_a_pause_lasts_at_least_the_minimum_rest(self):
        breaker = CircuitBreaker(15, 8, min_rest_sec=60)
        self.assertTrue(breaker.update(16, now=100))
        self.assertFalse(breaker.update(2, now=130), "score is low, but only 30 s rested")
        self.assertEqual((breaker.state, breaker.rest_remaining(130)), (DISPATCH_PAUSED, 30))
        self.assertFalse(breaker.update(9, now=170), "rested, but the score is still above the resume line")
        self.assertEqual(breaker.rest_remaining(170), 0)
        self.assertTrue(breaker.update(7, now=171), "both conditions met")
        self.assertEqual((breaker.state, breaker.rest_remaining(171)), (DISPATCH_NORMAL, 0))
        self.assertTrue(breaker.update(16, now=200))
        self.assertEqual(breaker.rest_remaining(210), 50, "a new pause starts a new rest")

    def test_rejects_inverted_thresholds(self):
        with self.assertRaises(ValueError):
            CircuitBreaker(8, 15)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = RiderStore(Config(), clock=self.clock)
        self.emitted = []
        self.store.subscribe(lambda kind, payload: self.emitted.append((kind, payload)))

    def rider(self, rider_id="rider-01"):
        return next(r for r in self.store.snapshot()["riders"] if r["id"] == rider_id)

    def test_waiting_until_first_score(self):
        r = self.rider()
        self.assertEqual((r["link"], r["status"], r["score"]), ("waiting", "unknown", None))

    def test_operator_reset_lifts_the_pause_without_the_rest(self):
        for score in (3, 16):
            self.store.ingest_score(ScoreSample("rider-01", self.clock.now, score))
            self.clock.now += 1
        self.assertEqual(self.rider()["status"], "paused")
        self.emitted.clear()
        self.assertTrue(self.store.reset_dispatch("rider-01"))
        self.assertFalse(self.store.reset_dispatch("nobody"))
        rider = self.rider()
        self.assertEqual((rider["status"], rider["dispatch"], rider["rest_remaining_sec"]), ("normal", "normal", None))
        self.assertEqual(rider["score"], 16, "the number on screen is the board's until the board sends 0")
        self.assertEqual([k for k, _ in self.emitted], ["event", "rider"])
        self.assertEqual(self.emitted[0][1]["kind"], "score_reset")
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 0.0))
        self.assertEqual(self.rider()["score"], 0.0)

    def test_pause_resume_events(self):
        for score in (3, 16, 12, 7):
            self.store.ingest_score(ScoreSample("rider-01", self.clock.now, score))
            self.clock.now += 1
        rider = self.rider()
        self.assertEqual((rider["status"], rider["rest_remaining_sec"]), ("paused", 57.0),
                         "score is back under the resume line after 3 s, but the minimum rest is not served")
        self.clock.now += 60
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 7))
        kinds = [e["kind"] for e in self.store.snapshot()["events"] if e["kind"] in ("paused", "resumed")]
        self.assertEqual(kinds, ["paused", "resumed"])
        rider = self.rider()
        self.assertEqual((rider["status"], rider["rest_remaining_sec"]), ("normal", None))

    def test_stale_score_is_unknown_not_green(self):
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 2.0))
        self.assertEqual(self.rider()["status"], "normal")
        self.clock.now += 6
        self.store.tick()
        r = self.rider()
        self.assertEqual((r["link"], r["status"]), ("stale", "unknown"))
        self.assertEqual(r["score"], 2.0, "last score is kept, only flagged")
        self.clock.now += 10
        self.store.tick()
        self.assertEqual(self.rider()["link"], "offline")
        kinds = [e["kind"] for e in self.store.snapshot()["events"]]
        self.assertEqual(kinds, ["link_stale", "link_offline"])

    def test_dispatch_frozen_while_unknown_then_restored(self):
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 20.0))
        self.clock.now += 20
        self.store.tick()
        r = self.rider()
        self.assertEqual((r["status"], r["dispatch"]), ("unknown", "paused"))
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 18.0))
        self.assertEqual(self.rider()["status"], "paused")
        self.assertEqual(self.store.snapshot()["events"][-1]["kind"], "link_restored")

    def test_perception_fault_overrides_fresh_score(self):
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 1.0))
        self.store.ingest_health(HealthSample("rider-01", self.clock.now, PERCEPTION_NO_FACE))
        self.assertEqual(self.rider()["status"], "unknown")
        self.store.ingest_health(HealthSample("rider-01", self.clock.now, PERCEPTION_OK))
        self.assertEqual(self.rider()["status"], "normal")

    def test_detail_expires_with_demo_mode(self):
        self.store.ingest_detail(DetailSample("rider-01", self.clock.now, ear=0.2, reasons=("yawn",)))
        self.assertEqual(self.rider()["detail"]["reasons"], ["yawn"])
        self.clock.now += 6
        self.store.tick()
        self.assertIsNone(self.rider()["detail"])

    def test_vitals_expire_like_details(self):
        self.store.ingest_vitals(VitalsSample("rider-01", self.clock.now, "good", heart_rate_bpm=71.5))
        self.assertEqual(self.rider()["vitals"]["heart_rate_bpm"], 71.5)
        self.clock.now += 6
        self.store.tick()
        self.assertIsNone(self.rider()["vitals"], "a stale heart rate must not stay on screen")

    def test_history_window_and_unknown_rider(self):
        for i in range(5):
            self.store.ingest_score(ScoreSample("rider-01", self.clock.now, float(i)))
            self.clock.now += 1
        self.assertEqual([p[1] for p in self.store.history("rider-01", 2.5)], [3.0, 4.0])
        self.assertIsNone(self.store.history("nobody", 10))

    def test_new_board_registers_itself(self):
        self.assertTrue(self.store.ingest_score(ScoreSample("rider-06", self.clock.now, 16.0)))
        r = self.rider("rider-06")
        self.assertEqual((r["name"], r["source"], r["status"], r["has_board"]), ("騎手 06", "real", "paused", False))
        self.assertTrue(self.rider("rider-01")["has_board"])
        kinds = [e["kind"] for e in self.store.snapshot()["events"]]
        self.assertEqual(kinds, ["registered", "paused"])

    def test_registration_limits(self):
        self.assertFalse(self.store.ingest_score(ScoreSample("bad id/../x", self.clock.now, 1.0)))
        locked = RiderStore(Config(auto_register=False), clock=self.clock)
        self.assertFalse(locked.ingest_score(ScoreSample("rider-06", self.clock.now, 1.0)))
        full = RiderStore(Config(max_riders=5), clock=self.clock)
        self.assertFalse(full.ingest_score(ScoreSample("rider-06", self.clock.now, 1.0)))

    def test_listeners_get_rider_and_event(self):
        self.store.ingest_score(ScoreSample("rider-01", self.clock.now, 16.0))
        self.assertEqual([k for k, _ in self.emitted], ["event", "rider"])
        self.emitted.clear()
        self.store.ingest_score(ScoreSample("rider-07", self.clock.now, 1.0))
        self.assertEqual([k for k, _ in self.emitted], ["event", "rider"], "browser learns of a new board live")


class PayloadTest(unittest.TestCase):
    def test_score(self):
        self.assertEqual(parse_score("r", {"timestamp": 1, "score": 2}).score, 2.0)
        for bad in ({}, {"timestamp": 1}, {"timestamp": 1, "score": "3"}, {"timestamp": 1, "score": True}):
            with self.assertRaises(PayloadError):
                parse_score("r", bad)

    def test_detail_fields_optional(self):
        d = parse_detail("r", {"timestamp": 1, "ear": 0.2, "reasons": ["yawn"]})
        self.assertEqual((d.ear, d.mar, d.reasons), (0.2, None, ("yawn",)))

    def test_vitals_rate_only_when_good(self):
        good = parse_vitals("r", {"timestamp": 1, "quality": "good", "heart_rate_bpm": 72, "waveform": [0, 0.5, -1]})
        self.assertEqual((good.heart_rate_bpm, good.waveform), (72.0, (0.0, 0.5, -1.0)))
        weak = parse_vitals("r", {"timestamp": 1, "quality": "weak", "heart_rate_bpm": 72})
        self.assertIsNone(weak.heart_rate_bpm, "a rate sent with a non-good quality is dropped, not shown")
        for bad in ({"timestamp": 1, "quality": "great"}, {"timestamp": 1, "quality": "good", "heart_rate_bpm": 400},
                    {"timestamp": 1, "quality": "good", "waveform": ["x"]},
                    {"timestamp": 1, "quality": "good", "waveform": [0] * 401}):
            with self.assertRaises(PayloadError):
                parse_vitals("r", bad)

    def test_ppg_fatigue_block_is_validated_and_kept(self):
        block = {"state": "pattern", "baseline_hr_bpm": 80.0, "hr_change_pct": -10.0, "recent_rmssd_ms": None, "bonus": 0}
        sample = parse_vitals("r", {"timestamp": 1, "quality": "good", "heart_rate_bpm": 72, "fatigue": block})
        self.assertEqual(sample.to_dict()["fatigue"], block)
        self.assertIsNone(parse_vitals("r", {"timestamp": 1, "quality": "good"}).fatigue, "older boards don't send it")
        for bad in ({"state": "sleepy"}, {"state": "normal", "bonus": "lots"}, "pattern", {"bonus": 1.0}):
            with self.assertRaises(PayloadError):
                parse_vitals("r", {"timestamp": 1, "quality": "good", "fatigue": bad})
        detail = parse_detail("r", {"timestamp": 1, "score_visual": 9.5, "ppg_bonus": 2.0, "reasons": ["perclos", "ppg"]})
        self.assertEqual((detail.score_visual, detail.ppg_bonus, detail.reasons), (9.5, 2.0, ("perclos", "ppg")))

    def test_health_enum(self):
        self.assertEqual(parse_health("r", {"timestamp": 1, "perception": "ok"}).perception, "ok")
        with self.assertRaises(PayloadError):
            parse_health("r", {"timestamp": 1, "perception": "fine"})


if __name__ == "__main__":
    unittest.main()
