"""Dispatch circuit breaker with hysteresis (plan.md 4.4).

Pause at >= pause_threshold, resume only at <= resume_threshold, so a score
hovering around one threshold can't flap the dispatch state on and off.

NOTE: the board repo already has platform/circuit_breaker.py (BreakerConfig,
15 / 8). This is the laptop-side copy of the same rule; when the two repos are
merged keep ONE and point both sides at it.
"""
from __future__ import annotations

DISPATCH_NORMAL = "normal"
DISPATCH_PAUSED = "paused"


class CircuitBreaker:
    def __init__(self, pause_threshold: float, resume_threshold: float):
        if resume_threshold >= pause_threshold:
            raise ValueError("resume_threshold must be below pause_threshold (hysteresis)")
        self.pause_threshold = pause_threshold
        self.resume_threshold = resume_threshold
        self.state = DISPATCH_NORMAL

    def update(self, score: float) -> bool:
        """Feed one score. Returns True if the dispatch state changed."""
        if self.state == DISPATCH_NORMAL and score >= self.pause_threshold:
            self.state = DISPATCH_PAUSED
            return True
        if self.state == DISPATCH_PAUSED and score <= self.resume_threshold:
            self.state = DISPATCH_NORMAL
            return True
        return False
