"""Stage A: rule-based fatigue scoring (leaky integrator).

Fallback / primary scorer per plan.md section 4.1. Pure rules, no model
training. Runs on CPU, must work standalone if Stage B (MLP) is unavailable
or disqualified at the Friday-night go/no-go.

Rules:
  - PERCLOS > perclos_threshold                          -> +perclos_add PER SECOND it stays
    above (a continuous signal, so it accrues by elapsed time, not per call —
    otherwise the score would climb 15x faster at 15 FPS than at 1 FPS)
  - yawn event (MAR crosses yawn threshold, edge-triggered) -> +yawn_add
  - head drop, sustained > head_sustained_sec             -> +head_add (or a
    discounted weight if IMU isn't available to cross-validate — see below)
  - audio anomaly co-occurring with an already-firing rule -> +audio_bonus_add
  - otherwise: score decays by decay_per_sec every second (leaky integrator)

Multi-modal fusion (plan.md section 4.3): head-drop is the one rule that
gets corroborated across modalities, because vision alone can't tell
"nodding off" from "looking down at navigation" (section 9's own example).
When imu_pitch_deg is supplied, the head-drop rule only fires at full
weight if BOTH the visual head pitch and the IMU-derived tilt cross their
thresholds at the same time, sustained. If no IMU reading is available
(not wired in yet, or the device is running degraded), the rule falls back
to visual-only at a discounted weight — never silently promoted to the
same confidence as a cross-validated reading. PERCLOS and yawn stay
visual-only per plan.md (IMU has no equivalent signal for eye closure or
mouth opening). Audio is bonus-only: it can only add to a score that some
other rule already started, and does nothing added on top of an otherwise
zero-anomaly tick — this is a structural guarantee, not a tuned threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StageAConfig:
    # 2026-09-19 (team decision, demo tuning): 0.15 -> 0.10. With the 30 s demo
    # window that is ~3 s of closed eyes instead of ~4.5 s before points accrue.
    perclos_threshold: float = 0.10
    perclos_add: float = 2.0  # per SECOND above threshold (same rate the old per-call rule gave at 1 Hz)

    # Longest gap between two updates that still counts as continuous time.
    # After a longer hole (face lost, camera stall) we neither accrue PERCLOS
    # points nor decay for the whole hole: missing data is not evidence of
    # closed eyes, and not evidence of recovery either (plan.md 4.2).
    max_dt_sec: float = 1.0

    # mar_yawn_threshold and head_pitch_threshold_deg are calibrated to the
    # real ratios from /root/guardian_helmet/dms (H1's validated NXP DMS
    # pipeline, see rider/guardian_helmet_bridge.py), not this project's own
    # generic 468-landmark formulas — H1's mouth/eye ratios use different
    # landmark picks and a different scale than layer_b_features.py's, so
    # these thresholds only make sense paired with the real pipeline's
    # output. Re-tune after Thursday's calibration recordings.
    # 0.3 is main.py's own yawn line. It was tried at 0.1 on 2026-09-19 and
    # reverted the same day: measured on the live camera, resting MAR has a
    # median of 0.05 and a 75th percentile of 0.14, so at 0.1 ordinary talking
    # scored (37 % of sampled seconds) — at 0.3 only real mouth-opening does
    # (10 %). The 1 s cooldown from that tuning round is kept.
    # Then raised to 0.4 (team decision, same day): a clearly open mouth, with some
    # margin above main.py's 0.3 line; recorded yawns on this camera peak at 0.4-0.9.
    # Both are overridable at start-up: --mar-threshold / --yawn-cooldown.
    mar_yawn_threshold: float = 0.4
    # 2026-09-19 evening, tried on the live board and reverted: 7 (the yawn weight of NXP's
    # GoPoint DMS demo, YAWN_PENALTY = 7) and 5. Against the app's warning line (10) and the
    # pause line (15), at 7 two yawns warned and a third paused; at 5 three paused. With the
    # DMS mouth line (0.3) a big laugh counts as a yawn too, so both were too heavy. Back to 3:
    # a yawn nudges the score, sustained eye closure (PERCLOS) is what carries it over a line.
    yawn_add: float = 3.0
    yawn_cooldown_sec: float = 1.0  # min gap between counted mouth-open events

    # 2026-09-19 (team decision): head pose is DISPLAYED but not SCORED by default.
    # Measured on the demo set-up: with the camera mounted low and the rider
    # looking at a laptop, head pitch sat at a median of 34 deg (14 of 16 sampled
    # seconds above the 13 deg line), so this rule fired almost continuously and
    # drove the score by itself — it was measuring the camera mount, not fatigue.
    # Re-enable with --score-head-down once the mount angle is fixed and the
    # threshold is calibrated against it (ideally with the IMU cross-check).
    score_head_down: bool = False
    head_pitch_threshold_deg: float = 13.0  # main.py: pitch < -13 -> "Down" (sign-flipped here, see analyze.py)
    head_sustained_sec: float = 2.0
    head_add: float = 5.0

    imu_pitch_threshold_deg: float = 20.0  # still a guess — no real MPU6050 calibration data yet, unlike head_pitch_threshold_deg above
    head_drop_no_imu_penalty: float = 0.6  # weight multiplier when IMU isn't available to corroborate

    audio_bonus_add: float = 1.0  # only ever applied on top of an already-firing rule

    # 2026-09-19 (team decision): 1.0 -> 0.3. At 1.0 a score of 10 was back to
    # zero in 10 s, faster than anyone could read it off the dashboard. At 0.3:
    # pause line (15) -> resume line (8) takes ~23 s; cap (30) -> resume ~73 s.
    # Override at start-up with --decay-per-sec.
    decay_per_sec: float = 0.3
    # Cap close above the platform's pause threshold (15): with the old cap of
    # 100 a rider who had been flagged for a while needed ~90 s of normal
    # behaviour before dispatch could resume; at 30 it is ~22 s.
    max_score: float = 30.0


@dataclass
class StageAScorer:
    """Rider-side scorer. Emits a score only — pause/resume hysteresis is a
    platform-side decision (see platform/circuit_breaker.py) so the rider
    device never decides to stop its own dispatch eligibility.
    """
    config: StageAConfig
    score: float = 0.0
    _last_timestamp: Optional[float] = None
    _head_drop_start: Optional[float] = None
    _last_yawn_event_time: Optional[float] = None
    _was_mar_above: bool = False

    def reset(self) -> None:
        self.score = 0.0
        self._last_timestamp = None
        self._head_drop_start = None
        self._last_yawn_event_time = None
        self._was_mar_above = False

    def update(self, timestamp: float, ear: float, mar: float,
               head_pitch_deg: float, perclos: float,
               imu_pitch_deg: Optional[float] = None,
               audio_anomaly: bool = False) -> dict:
        cfg = self.config
        dt = 0.0 if self._last_timestamp is None else max(0.0, timestamp - self._last_timestamp)
        dt = min(dt, cfg.max_dt_sec)
        self._last_timestamp = timestamp

        added = 0.0
        reasons = []

        continuous = 0.0  # time-accrued part; event rules below add fixed amounts
        if perclos > cfg.perclos_threshold:
            continuous = cfg.perclos_add * dt
            reasons.append("perclos")

        is_mar_above = mar > cfg.mar_yawn_threshold
        if is_mar_above and not self._was_mar_above:
            cooldown_ok = (
                self._last_yawn_event_time is None
                or timestamp - self._last_yawn_event_time >= cfg.yawn_cooldown_sec
            )
            if cooldown_ok:
                added += cfg.yawn_add
                reasons.append("yawn")
                self._last_yawn_event_time = timestamp
        self._was_mar_above = is_mar_above

        visual_drop = head_pitch_deg > cfg.head_pitch_threshold_deg
        if imu_pitch_deg is None:
            head_confirmed = visual_drop
            head_weight = cfg.head_add * cfg.head_drop_no_imu_penalty
            head_reason = "head_drop_visual_only_no_imu"
        else:
            head_confirmed = visual_drop and imu_pitch_deg > cfg.imu_pitch_threshold_deg
            head_weight = cfg.head_add
            head_reason = "head_drop"

        if not cfg.score_head_down:
            self._head_drop_start = None
        elif head_confirmed:
            if self._head_drop_start is None:
                self._head_drop_start = timestamp
            elif timestamp - self._head_drop_start > cfg.head_sustained_sec:
                added += head_weight
                reasons.append(head_reason)
                self._head_drop_start = timestamp  # avoid re-firing every tick
        else:
            self._head_drop_start = None

        if audio_anomaly and reasons:
            added += cfg.audio_bonus_add
            reasons.append("audio_bonus")

        added += continuous
        if not reasons:
            self.score = max(0.0, self.score - cfg.decay_per_sec * dt)
        else:
            self.score = min(cfg.max_score, self.score + added)

        return {
            "timestamp": timestamp,
            "score": round(self.score, 2),
            "added": round(added, 3),  # this update's rule contribution (for offline Stage A vs B comparison)
            "reasons": reasons,
        }
