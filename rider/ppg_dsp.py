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

MIN_BPM, MAX_BPM = 40.0, 200.0
CONTACT_IR_FLOOR = 50000      # same floor as sensor_test -F: below this there is no skin on the sensor
MIN_SECONDS_FOR_RATE = 8.0
MIN_AUTOCORR = 0.3            # hr_analyze.py's "periodicity too weak to trust" limit
MAX_DISAGREEMENT_BPM = 8.0

QUALITY_NO_CONTACT = "no_contact"
QUALITY_SETTLING = "settling"   # skin detected, not enough steady signal yet
QUALITY_WEAK = "weak"           # signal present but the two estimators disagree / periodicity is poor
QUALITY_GOOD = "good"


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


def bandpass(ir: list, fs: float, low_period_s: float = 0.1, high_period_s: float = 1.5) -> list:
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


def analyze(t: list, ir: list) -> dict:
    """t: sample times (s), ir: raw IR counts; both the same length, oldest first.
    Returns {"quality", "heart_rate_bpm", "rmssd_ms", "perfusion_index", "ir_dc", "autocorr"}."""
    result = {"quality": QUALITY_NO_CONTACT, "heart_rate_bpm": None, "rmssd_ms": None,
              "perfusion_index": None, "ir_dc": None, "autocorr": None, "sample_hz": None}
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
    bpm_beats = 60.0 / (sum(good) / len(good))
    if abs(bpm_beats - bpm_corr) > MAX_DISAGREEMENT_BPM:
        return result

    result["quality"] = QUALITY_GOOD
    result["heart_rate_bpm"] = round(bpm_beats, 1)
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
