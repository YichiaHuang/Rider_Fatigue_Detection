"""Dispatch circuit breaker with hysteresis (plan.md 4.4).

Pause at >= pause_threshold, resume only at <= resume_threshold, so a score
hovering around one threshold can't flap the dispatch state on and off.

A pause also lasts at least min_rest_sec: resuming needs BOTH a low score AND
the rest time served. A score can fall quickly (it decays while the rider simply
looks away from the camera); a minute off the road cannot be faked that way.

NOTE: the board repo already has platform/circuit_breaker.py (BreakerConfig,
15 / 8). This is the laptop-side copy of the same rule; when the two repos are
merged keep ONE and point both sides at it.
"""
from __future__ import annotations

DISPATCH_NORMAL = "normal"
DISPATCH_PAUSED = "paused"


class CircuitBreaker:
    def __init__(self, pause_threshold: float, resume_threshold: float, min_rest_sec: float = 0.0):
        if resume_threshold >= pause_threshold:
            raise ValueError("resume_threshold must be below pause_threshold (hysteresis)")
        self.pause_threshold = pause_threshold
        self.resume_threshold = resume_threshold
        self.min_rest_sec = min_rest_sec
        self.state = DISPATCH_NORMAL
        self.paused_at = None  # `now` of the update that paused; None while normal

    def update(self, score: float, now: float = 0.0) -> bool:
        """Feed one score. Returns True if the dispatch state changed."""
        if self.state == DISPATCH_NORMAL and score >= self.pause_threshold:
            self.state = DISPATCH_PAUSED
            self.paused_at = now
            return True
        if (self.state == DISPATCH_PAUSED and score <= self.resume_threshold
                and self.rest_remaining(now) <= 0.0):
            self.state = DISPATCH_NORMAL
            self.paused_at = None
            return True
        return False

    def reset(self) -> None:
        """Operator reset (the board's score is being zeroed at the same time):
        back to normal now, without serving the rest of min_rest_sec."""
        self.state = DISPATCH_NORMAL
        self.paused_at = None

    def rest_remaining(self, now: float) -> float:
        """Seconds of the minimum rest still to serve; 0 when not paused or already served."""
        if self.state != DISPATCH_PAUSED or self.paused_at is None:
            return 0.0
        return max(0.0, self.min_rest_sec - (now - self.paused_at))
