"""Platform-side dispatch circuit breaker.

Consumes fatigue scores per rider and decides pause/resume dispatch
eligibility, with hysteresis so a score oscillating near the threshold
doesn't flap the rider's dispatch status on and off.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class BreakerConfig:
    pause_threshold: float = 15.0
    resume_threshold: float = 8.0  # must be < pause_threshold
    stale_after_sec: float = 30.0  # no score received in this long -> treat as unknown

    def __post_init__(self):
        if self.resume_threshold >= self.pause_threshold:
            raise ValueError("resume_threshold must be < pause_threshold")


@dataclass
class RiderState:
    rider_id: str
    score: float = 0.0
    paused: bool = False
    last_update: Optional[float] = None
    history: list = field(default_factory=list)  # [(timestamp, score)]


class DispatchCircuitBreaker:
    def __init__(self, config: BreakerConfig, history_limit: int = 600):
        self.config = config
        self.history_limit = history_limit
        self._riders: Dict[str, RiderState] = {}

    def ingest(self, rider_id: str, timestamp: float, score: float) -> RiderState:
        state = self._riders.setdefault(rider_id, RiderState(rider_id=rider_id))
        state.score = score
        state.last_update = timestamp

        if not state.paused and score >= self.config.pause_threshold:
            state.paused = True
        elif state.paused and score <= self.config.resume_threshold:
            state.paused = False

        state.history.append((timestamp, score))
        if len(state.history) > self.history_limit:
            state.history.pop(0)

        return state

    def is_stale(self, rider_id: str, now: Optional[float] = None) -> bool:
        state = self._riders.get(rider_id)
        if state is None or state.last_update is None:
            return True
        now = now if now is not None else time.time()
        return (now - state.last_update) > self.config.stale_after_sec

    def get_state(self, rider_id: str) -> Optional[RiderState]:
        return self._riders.get(rider_id)

    def all_states(self) -> Dict[str, RiderState]:
        return dict(self._riders)
