"""PPG drowsiness indicator: the physiological half of the multimodal score.

    MAX30102 -> ppg_reader (HR, RMSSD once a second) -> PpgFatigueIndicator -> bonus
    camera   -> DMS -> Stage A (visual score) -------------------------------+-> published score

What it looks for — and why (full citations: docs/PPG_FATIGUE.md):

  * Falling asleep shifts the autonomic balance towards the parasympathetic
    branch: HEART RATE FALLS and beat-to-beat VARIABILITY RISES. On 76 drivers on
    real roads, "with increasing sleepiness, the heart rate decreased, whereas
    heart rate variability overall increased ... parameters representing the
    parasympathetic branch ... increased" (Buendia et al. 2019). RMSSD is the
    standard time-domain index of that parasympathetic activity.
  * The change shows BEFORE behaviour does: an HRV anomaly detector flagged 12 of
    13 episodes ahead of EEG-scored sleep onset (Fujiwara et al. 2019). That is
    the point of adding this to a camera that can only see eyes already closing.
  * It is measured against THE RIDER'S OWN awake baseline, not absolute numbers:
    Fujiwara's method is anomaly detection — departures from normal HRV, not fixed
    cut-offs — and baseline HRV depends on the person, their age and sex, and the
    recording context, so published norms are "not interchangeable" (Shaffer &
    Ginsberg 2017).
  * BOTH indices must move: single-parameter detectors do worse than
    multi-parameter ones (Burlacu et al. 2021). A heart rate that falls without a
    rise in variability is what stopping at a red light looks like.

What it is NOT allowed to do — and why:

  * HRV alone is a weak detector outside the lab: 61 % three-class accuracy on
    86 drivers on real roads, "heart rate is modulated by many different factors,
    and not just by sleepiness" (Persson et al. 2019); a systematic review found
    accuracies from 44 % to 100 % and inconsistent results between studies (Lu et al. 2022).
    So the contribution is CAPPED BELOW THE APP'S WARNING LINE: on its own it can
    never warn a rider or pause their dispatch. It shortens the distance the
    visual score has to travel — a prior, not a verdict.
  * Pulse-rate variability from PPG matches ECG at rest but degrades with motion
    (Schäfer & Vagedes 2013). Only seconds the DSP marked "good" are used; missing
    data freezes the indicator instead of counting for or against the rider.

Window lengths follow the measurement literature: RMSSD from 120 s agrees almost
perfectly with the 5-minute standard (r = 0.986; even three averaged 10 s
recordings reach r = 0.941 — Munoz et al. 2015), and the baseline uses the
5-minute short-term standard. 50 Hz sampling is
the minimum for RMSSD without interpolation (Béres & Hejjel 2021); ppg_dsp
interpolates beat times between samples.

The baseline SURVIVES RESTARTS (export_state / restore_state, saved to a small file
by the live runner): this board reboots and its runner gets restarted often, and a
5-minute baseline that starts over each time is never finished. A finished baseline
is reused for up to 12 h — one working shift; HRV has a circadian swing, so
yesterday's numbers are not today's "awake". An unfinished one is resumed if it is
less than 30 min old. Both limits are engineering choices, not from the literature.
--ppg-new-baseline discards the file: needed whenever a DIFFERENT PERSON puts the
sensor on, because the whole method is relative to one person's own numbers.

THE THRESHOLDS BELOW ARE ENGINEERING STARTING VALUES, NOT TAKEN FROM A PAPER. The
literature supports the direction of the effect and the personal-baseline
approach; none of it gives a transferable "-8 % / +25 %" rule. They must be
calibrated on recordings of real riders (docs/PPG_FATIGUE.md, "Validation").
"""
from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass

STATE_NO_SIGNAL = "no_signal"    # not enough trustworthy pulse data to say anything
STATE_LEARNING = "learning"      # collecting this rider's awake baseline
STATE_NORMAL = "normal"
STATE_PATTERN = "pattern"        # drowsiness pattern present, not yet sustained long enough to count
STATE_ELEVATED = "elevated"      # sustained: currently adding to the fatigue score

QUALITY_GOOD = "good"            # ppg_dsp: locked AND confirmed this second ("holding" is a carried-over value)


@dataclass
class PpgFatigueConfig:
    baseline_sec: float = 300.0        # seconds of good signal that define "this rider, awake"
    window_sec: float = 120.0          # "recent" = median over this much time
    min_window_valid_sec: float = 60.0  # ...of which at least this much must be good signal
    min_rmssd_samples: int = 30        # RMSSD needs ~40 s of steady signal, so it is present less often than HR
    hr_drop_pct: float = 8.0           # recent HR at least this far BELOW baseline ...
    rmssd_rise_pct: float = 25.0       # ... AND recent RMSSD at least this far ABOVE baseline
    sustain_sec: float = 120.0         # the pattern must hold this long before it adds anything
    bonus_rate_per_sec: float = 1.0 / 30.0   # then +1 point per 30 s ...
    bonus_cap: float = 6.0                   # ... up to here: below the app's warning line (10) by design
    bonus_decay_per_sec: float = 1.0 / 15.0  # pattern gone: back down twice as fast as it came
    stale_after_sec: float = 120.0     # no good signal for this long: stop freezing, let the bonus decay
    baseline_max_age_sec: float = 12 * 3600.0   # a saved, finished baseline is reused for one shift
    partial_max_age_sec: float = 30 * 60.0      # a saved, unfinished one is resumed if this recent
    max_dt_sec: float = 2.0            # longest gap between updates that still counts as continuous time

    @classmethod
    def demo(cls) -> "PpgFatigueConfig":
        """Short time constants so the mechanism can be shown in a few minutes.
        NOT for real use: a 45 s baseline and a 30 s window are far noisier than
        the standards above."""
        return cls(baseline_sec=45.0, window_sec=30.0, min_window_valid_sec=15.0, min_rmssd_samples=8,
                   sustain_sec=20.0, bonus_rate_per_sec=1.0 / 5.0, bonus_decay_per_sec=1.0 / 3.0)


def load_state(path: str) -> "dict | None":
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None  # no file yet, or a half-written one from a power cut: start fresh


def save_state(path: str, state: dict) -> bool:
    """Atomic: a power cut mid-write (this board loses power a lot) must leave the old file intact."""
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False  # read-only / full disk: the indicator still works, it just won't survive a restart


def _median(values: list) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])


class PpgFatigueIndicator:
    def __init__(self, config: "PpgFatigueConfig | None" = None):
        self.config = config or PpgFatigueConfig()
        self.reset()

    def reset(self) -> None:
        """New rider / new session: the baseline is personal."""
        self._baseline_samples = []      # (hr, rmssd | None) while learning
        self._baseline_valid_sec = 0.0
        self.baseline_hr = None
        self.baseline_rmssd = None
        self.baseline_learned_at = None  # wall-clock time the baseline was finished
        self.baseline_restored = False   # True = carried over from a previous run of the program
        self._recent = deque()           # (t, dt, hr, rmssd | None): good seconds inside the window
        self._last_update = None
        self._last_valid = None
        self.pattern_sec = 0.0
        self.bonus = 0.0
        self.state = STATE_NO_SIGNAL
        self._snapshot = {}

    def clear_pattern(self) -> None:
        """Operator reset: drop the accumulated pattern time and bonus, keep the
        personal baseline (it is still this rider)."""
        self.pattern_sec = 0.0
        self.bonus = 0.0

    # vitals: ppg_reader's once-a-second dict, or None when there is no sensor / no fresh reading
    def update(self, now: float, vitals: "dict | None") -> float:
        cfg = self.config
        dt = 0.0 if self._last_update is None else min(max(0.0, now - self._last_update), cfg.max_dt_sec)
        self._last_update = now

        valid = bool(vitals) and vitals.get("quality") == QUALITY_GOOD and vitals.get("heart_rate_bpm") is not None
        if valid:
            self._last_valid = now
            hr, rmssd = float(vitals["heart_rate_bpm"]), vitals.get("rmssd_ms")
            if self.baseline_hr is None:
                self._learn(dt, hr, rmssd)
            self._recent.append((now, dt, hr, rmssd))
        while self._recent and self._recent[0][0] < now - cfg.window_sec:
            self._recent.popleft()

        recent_hr = recent_rmssd = hr_change = rmssd_change = None
        pattern = None  # None = can't tell this second
        # Only a second that was itself measured gets a verdict: during a hole the
        # window still holds older samples, and judging from those would keep
        # "detecting" a pattern in data that is no longer arriving.
        if self.baseline_hr is not None and valid:
            valid_sec = sum(d for _, d, _, _ in self._recent)
            rmssd_values = [r for _, _, _, r in self._recent if r is not None]
            if valid_sec >= cfg.min_window_valid_sec and len(rmssd_values) >= cfg.min_rmssd_samples:
                recent_hr = _median([h for _, _, h, _ in self._recent])
                recent_rmssd = _median(rmssd_values)
                hr_change = 100.0 * (recent_hr - self.baseline_hr) / self.baseline_hr
                rmssd_change = 100.0 * (recent_rmssd - self.baseline_rmssd) / self.baseline_rmssd
                pattern = hr_change <= -cfg.hr_drop_pct and rmssd_change >= cfg.rmssd_rise_pct

        if pattern is True:
            self.pattern_sec += dt
            if self.pattern_sec >= cfg.sustain_sec:
                self.bonus = min(cfg.bonus_cap, self.bonus + cfg.bonus_rate_per_sec * dt)
        elif pattern is False:
            self.pattern_sec = 0.0
            self.bonus = max(0.0, self.bonus - cfg.bonus_decay_per_sec * dt)
        elif self._last_valid is None or now - self._last_valid > cfg.stale_after_sec:
            # Sensor off for good: a bonus must not outlive the evidence indefinitely.
            self.pattern_sec = 0.0
            self.bonus = max(0.0, self.bonus - cfg.bonus_decay_per_sec * dt)
        # else: a short hole in the data — neither evidence of drowsiness nor of recovery; freeze.

        if self.baseline_hr is None:
            self.state = STATE_LEARNING if self._baseline_valid_sec > 0 else STATE_NO_SIGNAL
        elif pattern is None:
            self.state = STATE_ELEVATED if self.bonus > 0 else STATE_NO_SIGNAL
        elif self.bonus > 0 and pattern:
            self.state = STATE_ELEVATED
        else:
            self.state = STATE_PATTERN if pattern else STATE_NORMAL

        rounded = lambda v, n=1: None if v is None else round(v, n)  # noqa: E731
        self._snapshot = {
            "state": self.state,
            "baseline_progress": round(min(1.0, self._baseline_valid_sec / cfg.baseline_sec), 2),
            "baseline_hr_bpm": rounded(self.baseline_hr), "baseline_rmssd_ms": rounded(self.baseline_rmssd),
            "recent_hr_bpm": rounded(recent_hr), "recent_rmssd_ms": rounded(recent_rmssd),
            "hr_change_pct": rounded(hr_change), "rmssd_change_pct": rounded(rmssd_change),
            "hr_drop_pct": cfg.hr_drop_pct, "rmssd_rise_pct": cfg.rmssd_rise_pct,
            "pattern_sec": round(self.pattern_sec), "sustain_sec": cfg.sustain_sec,
            "bonus": round(self.bonus, 2), "bonus_cap": cfg.bonus_cap,
            "baseline_age_sec": None if self.baseline_learned_at is None else round(max(0.0, now - self.baseline_learned_at)),
            "baseline_restored": 1 if self.baseline_restored else 0,
        }
        return self.bonus

    # ---- persistence: plain dicts, so the caller decides where they live ----
    def export_state(self, now: float) -> dict:
        """Everything needed to carry the baseline (finished or not) across a restart.
        The pattern timer and the bonus are deliberately NOT saved: they describe the
        last few minutes, and after a restart those minutes are gone."""
        return {
            "version": 1, "saved_at": now, "baseline_sec": self.config.baseline_sec,
            "baseline_hr": self.baseline_hr, "baseline_rmssd": self.baseline_rmssd,
            "baseline_learned_at": self.baseline_learned_at,
            "partial_valid_sec": self._baseline_valid_sec, "partial_samples": self._baseline_samples,
        }

    def restore_state(self, state: dict, now: float) -> str:
        """-> "baseline" (finished one reused) | "partial" (learning resumed) | a reason it was ignored."""
        cfg = self.config
        try:
            if state.get("version") != 1:
                return "ignored: unknown format"
            if float(state["baseline_sec"]) < cfg.baseline_sec:
                return "ignored: it was learned over a shorter time than this run requires"  # e.g. a --ppg-demo baseline
            if state.get("baseline_hr") is not None:
                age = now - float(state["baseline_learned_at"])
                if not 0 <= age <= cfg.baseline_max_age_sec:
                    return f"ignored: baseline is {age / 3600.0:.1f} h old"
                self.baseline_hr, self.baseline_rmssd = float(state["baseline_hr"]), max(1.0, float(state["baseline_rmssd"]))
                self.baseline_learned_at, self.baseline_restored = float(state["baseline_learned_at"]), True
                self._baseline_valid_sec = cfg.baseline_sec
                return "baseline"
            age = now - float(state["saved_at"])
            if not 0 <= age <= cfg.partial_max_age_sec:
                return f"ignored: unfinished baseline is {age / 60.0:.0f} min old"
            samples = [(float(h), None if r is None else float(r)) for h, r in state["partial_samples"]]
            self._baseline_samples, self._baseline_valid_sec = samples, float(state["partial_valid_sec"])
            return "partial"
        except (KeyError, TypeError, ValueError) as exc:
            return f"ignored: unreadable ({exc!r})"

    def snapshot(self) -> dict:
        """For the dashboard: every number the verdict was made from."""
        return dict(self._snapshot)

    def _learn(self, dt: float, hr: float, rmssd) -> None:
        cfg = self.config
        self._baseline_samples.append((hr, rmssd))
        self._baseline_valid_sec += dt
        rmssd_values = [r for _, r in self._baseline_samples if r is not None]
        if self._baseline_valid_sec >= cfg.baseline_sec and len(rmssd_values) >= cfg.min_rmssd_samples:
            self.baseline_hr = _median([h for h, _ in self._baseline_samples])
            self.baseline_rmssd = max(1.0, _median(rmssd_values))  # guards the division above
            self.baseline_learned_at = self._last_update
            self._baseline_samples = []
