"""Wire-level data shapes. One dataclass per message kind the board can send;
see docs/API.md for the matching MQTT topics and JSON payloads.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

PERCEPTION_OK = "ok"
PERCEPTION_NO_FACE = "no_face"
PERCEPTION_CAMERA_ERROR = "camera_error"
PERCEPTION_UNKNOWN = "unknown"
PERCEPTION_VALUES = (PERCEPTION_OK, PERCEPTION_NO_FACE, PERCEPTION_CAMERA_ERROR)


@dataclass(frozen=True)
class ScoreSample:
    """Normal-mode payload: the only thing that always leaves the board."""
    rider_id: str
    timestamp: float  # board clock, epoch seconds
    score: float


@dataclass(frozen=True)
class DetailSample:
    """Demo-mode only (plan.md 4.4): features + which rules fired."""
    rider_id: str
    timestamp: float
    ear: "float | None" = None
    mar: "float | None" = None
    perclos: "float | None" = None
    head_pitch_deg: "float | None" = None
    inference_fps: "float | None" = None
    reasons: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("rider_id")
        d["reasons"] = list(self.reasons)
        return d


@dataclass(frozen=True)
class HealthSample:
    """Why scores may have stopped: the board keeps reporting this even when
    perception is invalid and it (correctly) publishes no score."""
    rider_id: str
    timestamp: float
    perception: str  # one of PERCEPTION_VALUES


VITALS_QUALITIES = ("no_contact", "settling", "weak", "good")
MAX_WAVEFORM_POINTS = 400


@dataclass(frozen=True)
class VitalsSample:
    """Demo-mode only: PPG heart rate. heart_rate_bpm is None unless the board
    judged the signal good — the platform never invents or carries forward a rate."""
    rider_id: str
    timestamp: float
    quality: str
    heart_rate_bpm: "float | None" = None
    rmssd_ms: "float | None" = None
    perfusion_index: "float | None" = None
    waveform: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("rider_id")
        d["waveform"] = list(self.waveform)
        return d


class PayloadError(ValueError):
    pass


def _number(payload: dict, key: str, required: bool = True) -> "float | None":
    value = payload.get(key)
    if value is None:
        if required:
            raise PayloadError(f"missing '{key}'")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"'{key}' must be a number, got {value!r}")
    return float(value)


def parse_score(rider_id: str, payload: dict) -> ScoreSample:
    return ScoreSample(rider_id, _number(payload, "timestamp"), _number(payload, "score"))


def parse_detail(rider_id: str, payload: dict) -> DetailSample:
    reasons = payload.get("reasons") or ()
    if not isinstance(reasons, (list, tuple)):
        raise PayloadError("'reasons' must be a list")
    return DetailSample(
        rider_id=rider_id,
        timestamp=_number(payload, "timestamp"),
        ear=_number(payload, "ear", required=False),
        mar=_number(payload, "mar", required=False),
        perclos=_number(payload, "perclos", required=False),
        head_pitch_deg=_number(payload, "head_pitch_deg", required=False),
        inference_fps=_number(payload, "inference_fps", required=False),
        reasons=tuple(str(r) for r in reasons),
    )


def parse_health(rider_id: str, payload: dict) -> HealthSample:
    perception = payload.get("perception")
    if perception not in PERCEPTION_VALUES:
        raise PayloadError(f"'perception' must be one of {PERCEPTION_VALUES}, got {perception!r}")
    return HealthSample(rider_id, _number(payload, "timestamp"), perception)


def parse_vitals(rider_id: str, payload: dict) -> VitalsSample:
    quality = payload.get("quality")
    if quality not in VITALS_QUALITIES:
        raise PayloadError(f"'quality' must be one of {VITALS_QUALITIES}, got {quality!r}")
    waveform = payload.get("waveform") or ()
    if not isinstance(waveform, (list, tuple)) or len(waveform) > MAX_WAVEFORM_POINTS or \
            any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in waveform):
        raise PayloadError(f"'waveform' must be a list of at most {MAX_WAVEFORM_POINTS} numbers")
    rate = _number(payload, "heart_rate_bpm", required=False)
    if rate is not None and not 25.0 <= rate <= 250.0:
        raise PayloadError(f"'heart_rate_bpm' {rate} is outside 25-250")
    return VitalsSample(
        rider_id=rider_id, timestamp=_number(payload, "timestamp"), quality=quality,
        heart_rate_bpm=rate if quality == "good" else None,
        rmssd_ms=_number(payload, "rmssd_ms", required=False),
        perfusion_index=_number(payload, "perfusion_index", required=False),
        waveform=tuple(float(v) for v in waveform))
