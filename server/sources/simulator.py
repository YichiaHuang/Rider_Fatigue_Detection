"""Simulated riders (plan.md: 1 real + 4 simulated, always labelled as such).

Each simulated rider runs the same shape of maths as Stage A — a leaky
integrator that gains on "events" and decays every second — so the curves on
the dashboard look like what the real board will send, including pause/resume
cycles and a rider whose link drops out.

Profiles:
  steady   low noise, never pauses
  rising   slow build-up -> pauses -> rests -> resumes (~3.5 min cycle)
  yawny    sporadic yawn-sized jumps, occasionally crosses the pause line
  dropout  steady, but goes silent 20 s out of every 90 s -> "unknown"
  demo     dev stand-in for the REAL rider while the board is off; also sends
           demo_state + health so the detail tiles can be built and tested
"""
from __future__ import annotations

import math
import random
import threading
import time

from ..config import SOURCE_SIMULATED, Config
from ..core.models import PERCEPTION_NO_FACE, PERCEPTION_OK, DetailSample, HealthSample, ScoreSample, VitalsSample
from ..core.store import RiderStore
from .base import Source

DECAY_PER_SEC = 0.2
SCORE_CAP = 30.0


class _SimRider:
    def __init__(self, rider_id: str, profile: str, seed: int):
        self.rider_id = rider_id
        self.profile = profile
        self.rng = random.Random(seed)
        self.score = 0.0
        self.t = self.rng.uniform(0, 60)  # desynchronise the riders

    def step(self, dt: float):
        """Advance dt seconds. Returns (score | None if silent, reasons, fatigued)."""
        self.t += dt
        added, reasons, silent, fatigued = 0.0, [], False, False
        p = self.profile
        if p == "steady":
            if self.rng.random() < 0.05 * dt:
                added, reasons = 1.5, ["perclos"]
        elif p == "rising":
            fatigued = (self.t % 210.0) < 120.0
            if fatigued:
                added, reasons = (0.35 + self.rng.uniform(-0.1, 0.1)) * dt, ["perclos"]
        elif p == "yawny":
            if self.rng.random() < 0.05 * dt:
                added, reasons = 3.0, ["yawn"]
        elif p == "dropout":
            silent = (self.t % 90.0) > 70.0
            if self.rng.random() < 0.05 * dt:
                added, reasons = 1.5, ["perclos"]
        elif p == "demo":
            phase = self.t % 150.0
            fatigued = 30.0 <= phase < 95.0
            silent = 125.0 <= phase < 137.0  # "camera covered" part of the demo script
            if fatigued:
                added, reasons = 0.45 * dt, ["perclos"]
                if self.rng.random() < 0.08 * dt:
                    added, reasons = added + 3.0, reasons + ["yawn"]
        self.score = min(SCORE_CAP, max(0.0, self.score - DECAY_PER_SEC * dt) + added)
        return (None if silent else round(self.score, 2)), reasons, fatigued


class SimulatorSource(Source):
    name = "simulator"

    def __init__(self, store: RiderStore, config: Config):
        super().__init__(store)
        self.hz = config.simulator_hz
        self._riders = [
            _SimRider(spec.id, spec.profile, seed=i)
            for i, spec in enumerate(config.riders) if spec.source == SOURCE_SIMULATED
        ]
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="simulator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"name": self.name, "riders": [r.rider_id for r in self._riders], "hz": self.hz}

    def _run(self) -> None:
        dt = 1.0 / self.hz
        while not self._stop.wait(dt):
            now = time.time()
            for rider in self._riders:
                score, reasons, fatigued = rider.step(dt)
                if rider.profile == "demo":
                    self._send_demo_extras(rider, now, score is None, reasons, fatigued)
                if score is not None:
                    self.store.ingest_score(ScoreSample(rider.rider_id, now, score))

    def _send_demo_extras(self, rider: _SimRider, now: float, silent: bool, reasons, fatigued: bool) -> None:
        self.store.ingest_health(HealthSample(
            rider.rider_id, now, PERCEPTION_NO_FACE if silent else PERCEPTION_OK))
        if silent:
            return
        rng, wobble = rider.rng, math.sin(rider.t / 3.0)
        yawning = "yawn" in reasons
        self.store.ingest_detail(DetailSample(
            rider_id=rider.rider_id, timestamp=now,
            ear=round((0.17 if fatigued else 0.29) + 0.02 * wobble + rng.uniform(-0.01, 0.01), 3),
            mar=round((0.55 if yawning else 0.08) + rng.uniform(0, 0.04), 3),
            perclos=round(max(0.0, (0.32 if fatigued else 0.05) + 0.03 * wobble), 3),
            head_pitch_deg=round((9.0 if fatigued else 2.0) + 2.0 * wobble, 1),
            inference_fps=round(12.0 + rng.uniform(-1, 1), 1),
            reasons=tuple(reasons),
        ))
        # Simulated pulse: slower when "fatigued"; sensor "off the skin" for 15 s of every 75 s.
        off_skin = (rider.t % 75.0) > 60.0
        bpm = (62.0 if fatigued else 76.0) + 3.0 * wobble
        wave = () if off_skin else tuple(
            round(math.exp(-((((now - 6.0 + i / 25.0) * bpm / 60.0) % 1.0 - 0.2) / 0.09) ** 2) * 1.6 - 0.6, 2)
            for i in range(150))
        self.store.ingest_vitals(VitalsSample(
            rider_id=rider.rider_id, timestamp=now, quality="no_contact" if off_skin else "good",
            heart_rate_bpm=None if off_skin else round(bpm, 1), rmssd_ms=None if off_skin else 38.0,
            perfusion_index=None if off_skin else 0.8, waveform=wave))
