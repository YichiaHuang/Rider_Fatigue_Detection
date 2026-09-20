"""Heart rate from a MAX30102 IR trace, online, in pure Python (no numpy: the
window is ~500 samples once a second, and this way it unit-tests anywhere).

Same approach as H2's offline /root/sensors/hr_analyze.py, adapted to a
sliding window: band-pass as a difference of moving averages, a rate estimate
from the autocorrelation that does not depend on finding individual beats,
then a beat-by-beat pass whose agreement with it decides whether the number
is trustworthy. A heart rate we are not sure of is reported as None, never
as a plausible-looking guess.
"""
from __future__ import annotations

import math

MIN_BPM, MAX_BPM = 45.0, 200.0
CONTACT_IR_FLOOR = 50000      # same floor as sensor_test -F: below this there is no skin on the sensor
MIN_SECONDS_FOR_RATE = 6.0
# Tuned on real captures from this sensor (scripts/ppg_compare_configs.py). A
# steady finger gave autocorrelation r of only 0.2-0.45 (perfusion index under
# 1 %), so the old r >= 0.3 gate rejected correct readings. r is now a weak
# gate, and trust comes from three checks that must ALL agree instead:
# autocorrelation vs beat timing, first half vs second half of the window, and
# regular beat intervals. Noise passes any one of them now and then, not all.
MIN_AUTOCORR = 0.15
MAX_DISAGREEMENT_BPM = 6.0
MAX_INTERVAL_CV = 0.20        # beat-interval std/mean; a real resting pulse is well under this
MIN_PERFUSION_PCT = 0.15      # below this it is sensor noise on a static surface, not a pulse (noise alone: ~0.05 %)
MAX_PERFUSION_PCT = 6.0       # above this the "pulse" is motion
MIN_SPECTRAL_PEAK = 0.55      # share of 0.7-3.6 Hz energy on the rate's harmonic comb (see spectral_peak)
MAX_BASELINE_STEP = 0.03      # a >3 % jump between half-second means = sensor moved / just placed

# RMSSD from an interrupted signal (SuccessiveDifferenceLog below).
RMSSD_WINDOW_SEC = 60.0       # pool successive differences over this long ...
RMSSD_MIN_PAIRS = 30          # ... and report once there are this many (~30 s of beats; see Munoz et al. 2015)
PAIR_INTERVAL_TOLERANCE = 0.30  # an interval more than 30 % off the window's median is an artefact, not HRV
#                                 (same 30 % rule as the "percentage change" outlier filter in Buendia et al. 2019)
MIN_BEAT_GAP_SEC = 0.25       # two logged beats closer than this are the same beat seen by two overlapping windows
# A window can pass every gate with a second of fidgeting at one end. Measured on
# synthetic bursts: differences within 1 s of a burst had a median of ~130 ms against
# ~20 ms elsewhere — and an RMSSD that RISES is exactly what the fatigue indicator
# reads as drowsiness, so motion must never be able to fake it.
DISTURBANCE_ENERGY_RATIO = 4.0  # a 1 s block with > 4x the window's median pulse-band energy (2x amplitude) is motion
DISTURBANCE_GUARD_SEC = 2.5     # beats this close to such a block are not used for HRV
MAX_PAIR_DIFF_FRACTION = 0.20   # |successive difference| above 20 % of the beat interval: missed/extra beat, not HRV

QUALITY_NO_CONTACT = "no_contact"
QUALITY_SETTLING = "settling"   # skin detected, not enough steady signal yet
QUALITY_WEAK = "weak"           # signal present but the two estimators disagree / periodicity is poor
QUALITY_GOOD = "good"
QUALITY_HOLDING = "holding"     # locked a moment ago; showing that value through a brief dropout


def moving_average(x: list, width: int) -> list:
    """Centred moving average with edge padding, O(n) via a running sum."""
    width = max(1, int(width) | 1)
    pad = width // 2
    padded = [x[0]] * pad + list(x) + [x[-1]] * pad
    total = sum(padded[:width])
    out = [total / width]
    for i in range(width, len(padded)):
        total += padded[i] - padded[i - width]
        out.append(total / width)
    return out


def bandpass(ir: list, fs: float, low_period_s: float = 0.1, high_period_s: float = 1.0) -> list:
    fast, slow = moving_average(ir, fs * low_period_s), moving_average(ir, fs * high_period_s)
    return [a - b for a, b in zip(fast, slow)]


def _autocorr_rate(ac: list, fs: float):
    """(bpm, r) from the first strong autocorrelation peak in the 40-200 bpm lag range."""
    n = len(ac)
    mean = sum(ac) / n
    a = [v - mean for v in ac]
    energy = sum(v * v for v in a)
    if energy <= 0:
        return None, 0.0
    lo, hi = max(2, int(fs * 60.0 / MAX_BPM)), min(n - 2, int(fs * 60.0 / MIN_BPM))
    corr = {}
    for lag in range(lo - 1, hi + 2):
        corr[lag] = sum(a[i] * a[i + lag] for i in range(n - lag)) / energy
    best = max(range(lo, hi + 1), key=lambda k: corr[k])
    # The strongest peak can be a multiple of the true period (every 2nd beat
    # lines up too). Prefer the shortest lag that is a local peak nearly as strong.
    for lag in range(lo, best):
        if corr[lag] >= 0.85 * corr[best] and corr[lag] >= corr[lag - 1] and corr[lag] >= corr[lag + 1]:
            best = lag
            break
    y0, y1, y2 = corr[best - 1], corr[best], corr[best + 1]
    denom = y0 - 2 * y1 + y2
    offset = 0.5 * (y0 - y2) / denom if denom else 0.0   # parabolic sub-sample refinement
    return 60.0 * fs / (best + offset), corr[best]


def _beat_times(ac: list, t: list, fs: float, period_s: float) -> list:
    """One timestamp per beat, taken where the band-passed wave crosses zero on
    its steeper flank. The apex of a pulse is broad and flat, so a little noise
    moves it by tens of milliseconds — enough to turn a perfectly regular pulse
    into an RMSSD of 100 ms. The steep flank pins the same beat to a few ms.
    Hysteresis (the wave must first swing past 0.3 sd the other way) keeps noise
    around zero from registering as extra beats."""
    sd = math.sqrt(sum(v * v for v in ac) / len(ac))
    if sd <= 0:
        return []
    arm_level, min_gap_s = 0.3 * sd, 0.6 * period_s

    def crossings(sign: float):
        times, slopes, armed = [], [], False
        for i in range(1, len(ac)):
            prev, cur = sign * ac[i - 1], sign * ac[i]
            if cur < -arm_level:
                armed = True
            if armed and prev < 0.0 <= cur:
                when = t[i - 1] + (t[i] - t[i - 1]) * (-prev / (cur - prev))  # linear interpolation
                if not times or when - times[-1] >= min_gap_s:
                    times.append(when)
                    slopes.append(cur - prev)
                armed = False
        return times, (sum(slopes) / len(slopes) if slopes else 0.0)

    rising, rising_slope = crossings(+1.0)
    falling, falling_slope = crossings(-1.0)
    return rising if rising_slope >= falling_slope else falling


def spectral_peak(ac: list, fs: float, bpm: float) -> float:
    """Fraction of the 0.7-3.6 Hz energy that sits on the harmonic comb of `bpm`
    (fundamental, 2nd and 3rd harmonic, each +-0.12 Hz). A pulse is a sharp,
    repeating shape: its energy lies on that comb — for a slow pulse mostly on
    the harmonics, which is why the fundamental alone is not enough. Drift and
    fidgeting smear energy across the band instead."""
    n = len(ac)
    mean = sum(ac) / n
    x = [(v - mean) * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i, v in enumerate(ac)]
    f0 = bpm / 60.0
    total = on_comb = 0.0
    for k in range(int((3.6 - 0.7) / 0.04) + 1):
        f = 0.7 + 0.04 * k
        w = 2 * math.pi * f / fs
        re = sum(v * math.cos(w * i) for i, v in enumerate(x))
        im = sum(v * math.sin(w * i) for i, v in enumerate(x))
        power = re * re + im * im
        total += power
        if any(abs(f - h * f0) <= 0.12 for h in (1, 2, 3)):
            on_comb += power
    return on_comb / total if total > 0 else 0.0


def _disturbed_times(ac: list, t: list, fs: float) -> list:
    """Centre times of the 1 s blocks whose pulse-band energy stands far above the
    rest of the window: someone moved the sensor there."""
    block = max(2, int(fs))
    energies = [(sum(v * v for v in ac[i:i + block]) / block, t[i + block // 2])
                for i in range(0, len(ac) - block + 1, block)]
    if len(energies) < 4:
        return []
    reference = sorted(e for e, _ in energies)[len(energies) // 2]
    return [when for e, when in energies if reference > 0 and e > DISTURBANCE_ENERGY_RATIO * reference]


def stable_tail(t: list, ir: list):
    """The most recent stretch with skin contact and no baseline jump. Putting a
    finger on the sensor steps the IR level from ~2k to ~100k; with that step
    inside the window nothing periodic can be found, so a fixed 12 s window
    stayed useless for 12 s after every touch or shift. Walk back from the
    newest sample in half-second blocks and stop at the first jump."""
    if len(t) < 4 or t[-1] <= t[0]:
        return t, ir
    fs = (len(t) - 1) / (t[-1] - t[0])
    block = max(2, int(fs * 0.5))
    start, newer = 0, None
    for end in range(len(ir), block - 1, -block):
        mean = sum(ir[end - block:end]) / block
        if mean < CONTACT_IR_FLOOR or (newer is not None and abs(mean - newer) / newer > MAX_BASELINE_STEP):
            start = end
            break
        newer = mean
    return t[start:], ir[start:]


def analyze(t: list, ir: list) -> dict:
    """t: sample times (s), ir: raw IR counts; both the same length, oldest first.
    Returns {"quality", "heart_rate_bpm", "rmssd_ms", "perfusion_index", "ir_dc", "autocorr", "beat_pairs"}.
    beat_pairs: [(time of the middle beat, interval before it, interval after it), ...] for every
    three consecutive clean beats in a GOOD window — the raw material of SuccessiveDifferenceLog."""
    result = {"quality": QUALITY_NO_CONTACT, "heart_rate_bpm": None, "rmssd_ms": None,
              "perfusion_index": None, "ir_dc": None, "autocorr": None, "sample_hz": None,
              "spectral_peak": None, "beat_pairs": []}
    if len(t) >= 2 and t[-1] > t[0]:
        result["sample_hz"] = round((len(t) - 1) / (t[-1] - t[0]), 1)  # < 50 means samples are being lost
    if len(ir) < 10:
        return result
    dc = sum(ir) / len(ir)
    result["ir_dc"] = round(dc)
    recent = ir[-max(5, len(ir) // 10):]
    if dc < CONTACT_IR_FLOOR or min(recent) < CONTACT_IR_FLOOR:
        return result

    result["quality"] = QUALITY_SETTLING
    t, ir = stable_tail(t, ir)
    if len(t) < 10:
        return result
    dc = sum(ir) / len(ir)
    duration = t[-1] - t[0]
    if duration < MIN_SECONDS_FOR_RATE:
        return result
    fs = (len(t) - 1) / duration

    ac = bandpass(ir, fs)
    trim = int(fs * 0.75)               # the slow moving average is unreliable at the window edges
    ac_core, t_core = ac[trim:-trim], t[trim:-trim]
    ordered = sorted(ac_core)
    p2p = ordered[int(len(ordered) * 0.99)] - ordered[int(len(ordered) * 0.01)]
    result["perfusion_index"] = round(100.0 * p2p / dc, 2)

    bpm_corr, r = _autocorr_rate(ac_core, fs)
    result["autocorr"] = round(r, 2)
    result["quality"] = QUALITY_WEAK
    if bpm_corr is None or r < MIN_AUTOCORR:
        return result
    if not MIN_PERFUSION_PCT <= result["perfusion_index"] <= MAX_PERFUSION_PCT:
        return result
    peak = spectral_peak(ac_core, fs, bpm_corr)
    result["spectral_peak"] = round(peak, 2)
    if peak < MIN_SPECTRAL_PEAK:
        return result

    # second pass: re-filter around the estimated rate, then time individual beats
    period = 60.0 / bpm_corr
    narrow = bandpass(ir, fs, period / 3.0, period / 0.6)[trim:-trim]
    beats = _beat_times(narrow, t_core, fs, period)
    intervals = [b - a for a, b in zip(beats, beats[1:])]
    if len(intervals) < 4:
        return result
    median = sorted(intervals)[len(intervals) // 2]
    good = [v for v in intervals if 0.6 * median < v < 1.4 * median]
    if len(good) < 4:
        return result
    mean_interval = sum(good) / len(good)
    bpm_beats = 60.0 / mean_interval
    if abs(bpm_beats - bpm_corr) > MAX_DISAGREEMENT_BPM:
        return result
    if len(good) < 0.75 * len(intervals):          # too many skipped / doubled beats
        return result
    cv = math.sqrt(sum((v - mean_interval) ** 2 for v in good) / len(good)) / mean_interval
    if cv > MAX_INTERVAL_CV:
        return result
    result["quality"] = QUALITY_GOOD
    result["heart_rate_bpm"] = round(bpm_beats, 1)
    lo, hi = (1.0 - PAIR_INTERVAL_TOLERANCE) * median, (1.0 + PAIR_INTERVAL_TOLERANCE) * median
    disturbed = _disturbed_times(ac, t, fs)   # on the untrimmed trace: a burst in the trimmed margin still bends the beats next to it
    result["beat_pairs"] = [
        (beats[i + 1], intervals[i], intervals[i + 1]) for i in range(len(intervals) - 1)
        if lo < intervals[i] < hi and lo < intervals[i + 1] < hi
        and abs(intervals[i + 1] - intervals[i]) <= MAX_PAIR_DIFF_FRACTION * median
        and all(abs(beats[i + 1] - when) > DISTURBANCE_GUARD_SEC for when in disturbed)]
    if len(good) >= 20:  # short-term HRV needs a meaningful number of beats
        diffs = [(b - a) ** 2 for a, b in zip(good, good[1:])]
        result["rmssd_ms"] = round(1000.0 * math.sqrt(sum(diffs) / len(diffs)), 1)
    return result


def display_waveform(t: list, ir: list, seconds: float = 6.0, points: int = 150) -> list:
    """Band-passed pulse wave, last `seconds`, resampled to `points` values in
    [-1, 1] (inverted: more blood = less light, so a beat points up)."""
    if len(ir) < 20 or t[-1] - t[0] < 1.0:
        return []
    fs = (len(t) - 1) / (t[-1] - t[0])
    ac = bandpass(ir, fs)
    keep = int(fs * seconds)
    tail = ac[-keep - int(fs * 0.75):-int(fs * 0.75)] if len(ac) > keep + int(fs * 0.75) else ac
    if len(tail) < 2:
        return []
    scale = max(abs(v) for v in tail) or 1.0
    step = (len(tail) - 1) / (points - 1)
    return [round(-tail[int(round(i * step))] / scale, 2) for i in range(points)]


class SuccessiveDifferenceLog:
    """RMSSD from a signal that keeps getting interrupted.

    RMSSD is the root mean square of the differences between SUCCESSIVE beat
    intervals. Nothing in that definition needs one unbroken recording — only that
    each difference is taken between two intervals that really were adjacent. The
    old path asked for 40 uninterrupted seconds that passed every gate as a whole;
    on a real finger that happened in ~5 % of good seconds, because any fidget, FIFO
    overflow or contact blip restarted the 40 s.

    So: every GOOD 12 s window hands over its (interval, next interval) pairs; each
    beat is logged once (windows overlap by 11 s); the RMSSD is pooled over the
    pairs of the last RMSSD_WINDOW_SEC. An interruption simply contributes no
    pairs — and no difference is ever taken ACROSS it, which is what would
    otherwise turn a missed beat into a huge fake "variability".

    What missing data costs is accuracy, and that cost has been measured: Kim et al.
    2007 removed up to 100 s from 2,615 five-minute recordings and reported the
    relative error of each time-domain index (mean interval most robust, pNN50 most
    sensitive). Pairs within one window share one filter setting, so the sub-sample
    timing is consistent inside every difference.
    """

    def __init__(self, window_sec: float = RMSSD_WINDOW_SEC, min_pairs: int = RMSSD_MIN_PAIRS):
        self.window_sec, self.min_pairs = window_sec, min_pairs
        self._pairs = []          # (t_mid, diff_seconds), oldest first
        self._last_t = None

    def add(self, beat_pairs: list) -> int:
        added = 0
        for t_mid, before, after in beat_pairs:
            if self._last_t is not None and t_mid < self._last_t + MIN_BEAT_GAP_SEC:
                continue          # already logged from an earlier, overlapping window
            self._pairs.append((t_mid, after - before))
            self._last_t = t_mid
            added += 1
        return added

    def rmssd_ms(self, now: float):
        """-> (RMSSD in ms | None while there are too few pairs, number of pairs in the window)."""
        cutoff = now - self.window_sec
        self._pairs = [p for p in self._pairs if p[0] >= cutoff]
        n = len(self._pairs)
        if n < self.min_pairs:
            return None, n
        return round(1000.0 * math.sqrt(sum(d * d for _, d in self._pairs) / n), 1), n


class RateTracker:
    """Turns per-second single-window candidates into a displayed heart rate.

    At this sensor's signal level (perfusion index under 1 %) one 6-12 s window
    cannot be both permissive and safe: real pulses score autocorrelation 0.2-0.4,
    and so does slow baseline noise. What noise cannot do is agree with itself
    over time — its "rates" jump around (43, 80, 50 ...) while a pulse stays put.

      acquire : LOCK_COUNT candidates within LOCK_SPAN_SEC, all within LOCK_SPREAD_BPM
      track   : once locked, accept candidates within TRACK_BAND_BPM of the tracked
                rate (smoothed); the rate is reported as "good"
      hold    : no acceptable candidate this second -> keep showing the tracked rate
                as "holding" (with its age) for up to HOLD_SEC, then unlock
    Losing skin contact unlocks immediately.
    """
    LOCK_COUNT, LOCK_SPAN_SEC, LOCK_SPREAD_BPM = 5, 8.0, 5.0
    TRACK_BAND_BPM, HOLD_SEC, SMOOTHING = 12.0, 10.0, 0.3

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._candidates = []       # (time, bpm)
        self.rate = None
        self._last_accept = None

    def update(self, now: float, window: dict) -> dict:
        """window: analyze() output. Returns a copy with quality / heart_rate_bpm
        rewritten by the tracker, plus held_sec when holding."""
        out = dict(window)
        out["held_sec"] = None
        if window["quality"] == QUALITY_NO_CONTACT:
            self.reset()
            return out
        candidate = window["heart_rate_bpm"]
        out["heart_rate_bpm"] = None
        if out["quality"] == QUALITY_GOOD:
            out["quality"] = QUALITY_WEAK   # a lone window is only a candidate until the tracker agrees

        if candidate is not None:
            self._candidates.append((now, candidate))
        self._candidates = [(t, v) for t, v in self._candidates if now - t <= self.LOCK_SPAN_SEC]

        if self.rate is None:
            values = [v for _, v in self._candidates]
            if len(values) >= self.LOCK_COUNT and max(values) - min(values) <= self.LOCK_SPREAD_BPM:
                self.rate = sorted(values)[len(values) // 2]
                self._last_accept = now
        elif candidate is not None and abs(candidate - self.rate) <= self.TRACK_BAND_BPM:
            self.rate += self.SMOOTHING * (candidate - self.rate)
            self._last_accept = now

        if self.rate is not None:
            age = now - self._last_accept
            if age > self.HOLD_SEC:
                self.reset()
            else:
                out["heart_rate_bpm"] = round(self.rate, 1)
                out["quality"] = QUALITY_GOOD if age < 1.5 else QUALITY_HOLDING
                out["held_sec"] = None if age < 1.5 else round(age, 1)
        if out["quality"] != QUALITY_GOOD:
            out["rmssd_ms"] = None
        return out
