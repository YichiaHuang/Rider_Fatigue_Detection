"""In-memory platform state: the single place every data source writes to and
every API handler reads from.

    sources/*  --ingest_score/detail/health-->  RiderStore  --listeners-->  api (SSE)

Sources never talk to the web layer and the web layer never talks to a source,
so swapping MQTT for the simulator (or adding a new transport) touches nothing
outside server/sources/.

Status rules (plan.md 4.4 — a stale green light is worse than no light):
  link       waiting | online | stale | offline   from time since the last score
  perception ok | no_face | camera_error | unknown  from the board's health message
  dispatch   normal | paused                       circuit breaker; frozen while unknown
  orders     dispatch_block(): "fatigue" while paused, "no_signal" while status is
             unknown — a rider nobody is watching gets no new orders either.
  status     what the UI shows: "unknown" unless link is online AND perception
             isn't reporting a fault; otherwise mirrors dispatch.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque

from ..config import SOURCE_REAL, Config, RiderSpec
from .circuit_breaker import DISPATCH_PAUSED, CircuitBreaker
from .models import PERCEPTION_OK, PERCEPTION_UNKNOWN, DetailSample, HealthSample, ScoreSample, VitalsSample

LINK_WAITING = "waiting"
LINK_ONLINE = "online"
LINK_STALE = "stale"
LINK_OFFLINE = "offline"

STATUS_NORMAL = "normal"
STATUS_PAUSED = "paused"
STATUS_UNKNOWN = "unknown"

BLOCK_FATIGUE = "fatigue"
BLOCK_NO_SIGNAL = "no_signal"
BLOCK_UNKNOWN_RIDER = "unknown_rider"

MAX_EVENTS = 200
RIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class _Rider:
    def __init__(self, spec: RiderSpec, config: Config):
        self.spec = spec
        self.breaker = CircuitBreaker(config.pause_threshold, config.resume_threshold, config.min_rest_sec)
        self.history = deque()  # (received_at, score)
        self.score = None
        self.timestamp = None       # board clock
        self.received_at = None     # server clock; used for staleness and chart x
        self.detail = None
        self.detail_received_at = None
        self.vitals = None
        self.vitals_received_at = None
        self.perception = PERCEPTION_UNKNOWN
        self.health_received_at = None
        self.link = LINK_WAITING
        self.status = STATUS_UNKNOWN


class RiderStore:
    def __init__(self, config: Config, clock=time.time):
        self.config = config
        self._clock = clock
        self._lock = threading.RLock()
        self._riders = {spec.id: _Rider(spec, config) for spec in config.riders}
        self._events = deque(maxlen=MAX_EVENTS)
        self._event_seq = 0
        self._listeners = []

    # ---- subscriptions (api layer) -------------------------------------
    def subscribe(self, listener) -> None:
        """listener(kind, payload) with kind in {"rider", "event"}. Called on
        the ingesting thread — must not block."""
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _emit(self, kind: str, payload: dict) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:  # a broken browser connection must not stop ingestion
                pass

    def _get_or_register(self, rider_id: str) -> "_Rider | None":
        """Multi-board: a new board only has to start publishing under its own
        riders/{id}/... topics — no config change on the platform."""
        rider = self._riders.get(rider_id)
        if rider is not None or not self.config.auto_register:
            return rider
        if not RIDER_ID_PATTERN.match(rider_id) or len(self._riders) >= self.config.max_riders:
            return None
        number = rider_id.rsplit("-", 1)[-1]
        name = f"騎手 {number}" if number.isdigit() else rider_id
        rider = _Rider(RiderSpec(rider_id, name, SOURCE_REAL), self.config)
        self._riders[rider_id] = rider
        self._add_event(rider, "registered", None, self._clock())
        return rider

    # ---- ingestion (sources layer) -------------------------------------
    def ingest_score(self, sample: ScoreSample) -> bool:
        with self._lock:
            rider = self._get_or_register(sample.rider_id)
            if rider is None:
                return False
            now = self._clock()
            rider.score = sample.score
            rider.timestamp = sample.timestamp
            rider.received_at = now
            rider.history.append((now, sample.score))
            self._trim(rider, now)
            link_was_lost = rider.link in (LINK_STALE, LINK_OFFLINE)  # "waiting" is a first contact, not a recovery
            changed = rider.breaker.update(sample.score, now)
            self._refresh(rider, now)
            if changed:
                paused = rider.breaker.state == DISPATCH_PAUSED
                self._add_event(rider, "paused" if paused else "resumed", sample.score, now)
            elif link_was_lost:
                self._add_event(rider, "link_restored", sample.score, now)
            self._emit("rider", self._rider_dict(rider, now))
            return True

    def ingest_detail(self, sample: DetailSample) -> bool:
        with self._lock:
            rider = self._get_or_register(sample.rider_id)
            if rider is None:
                return False
            now = self._clock()
            rider.detail = sample
            rider.detail_received_at = now
            self._emit("rider", self._rider_dict(rider, now))
            return True

    def ingest_vitals(self, sample: VitalsSample) -> bool:
        with self._lock:
            rider = self._get_or_register(sample.rider_id)
            if rider is None:
                return False
            now = self._clock()
            rider.vitals = sample
            rider.vitals_received_at = now
            self._emit("rider", self._rider_dict(rider, now))
            return True

    def ingest_health(self, sample: HealthSample) -> bool:
        with self._lock:
            rider = self._get_or_register(sample.rider_id)
            if rider is None:
                return False
            now = self._clock()
            previous = rider.perception
            rider.perception = sample.perception
            rider.health_received_at = now
            self._refresh(rider, now)
            if previous != sample.perception and sample.perception != PERCEPTION_OK:
                self._add_event(rider, f"perception_{sample.perception}", rider.score, now)
            self._emit("rider", self._rider_dict(rider, now))
            return True

    def tick(self) -> None:
        """Call ~1 Hz. Nothing arrives when a link dies, so staleness has to be
        noticed by the clock rather than by a message."""
        with self._lock:
            now = self._clock()
            for rider in self._riders.values():
                before = (rider.link, rider.status, rider.detail is None, rider.vitals is None)
                self._refresh(rider, now)
                if before[0] != rider.link and rider.link in (LINK_STALE, LINK_OFFLINE):
                    self._add_event(rider, f"link_{rider.link}", rider.score, now)
                if before != (rider.link, rider.status, rider.detail is None, rider.vitals is None):
                    self._emit("rider", self._rider_dict(rider, now))

    # ---- reads (api layer) ---------------------------------------------
    def snapshot(self, history_seconds: "float | None" = None) -> dict:
        with self._lock:
            now = self._clock()
            riders = []
            for rider in self._riders.values():
                self._refresh(rider, now)
                d = self._rider_dict(rider, now)
                if history_seconds is not None:
                    d["history"] = self._history(rider, now, history_seconds)
                riders.append(d)
            return {"server_time": now, "riders": riders, "events": list(self._events)}

    def history(self, rider_id: str, seconds: float) -> "list | None":
        with self._lock:
            rider = self._riders.get(rider_id)
            if rider is None:
                return None
            return self._history(rider, self._clock(), seconds)

    def rider_ids(self) -> list:
        return list(self._riders)

    def rider(self, rider_id: str) -> "dict | None":
        """One rider's current state (same shape as snapshot()'s riders[])."""
        with self._lock:
            rider = self._riders.get(rider_id)
            if rider is None:
                return None
            now = self._clock()
            self._refresh(rider, now)
            return self._rider_dict(rider, now)

    def dispatch_block(self, rider_id: str) -> "str | None":
        """The order side's only question: may this rider be offered an order?
        None = yes. Otherwise why not:
          "fatigue"    the breaker has paused them (it stays paused while the signal
                       is lost, so this outranks "no_signal")
          "no_signal"  nothing trustworthy is watching them right now — detector not
                       connected, link stale/offline, no face, camera fault. Without
                       this, unplugging the camera would be the way to keep working.
          "unknown_rider"
        """
        with self._lock:
            rider = self._riders.get(rider_id)
            if rider is None:
                return BLOCK_UNKNOWN_RIDER
            self._refresh(rider, self._clock())
            if rider.breaker.state == DISPATCH_PAUSED:
                return BLOCK_FATIGUE
            return BLOCK_NO_SIGNAL if rider.status == STATUS_UNKNOWN else None

    def record_event(self, rider_id: str, kind: str) -> bool:
        """Event-log entry that doesn't come from a board message (rider app:
        duty on/off, alert acknowledged, offer withdrawn)."""
        with self._lock:
            rider = self._riders.get(rider_id)
            if rider is None:
                return False
            self._add_event(rider, kind, rider.score, self._clock())
            return True

    def reset_dispatch(self, rider_id: str) -> bool:
        """Operator pressed "reset fatigue score" and the board accepted it: lift
        the pause right away (the board's next score is 0) and log it. The score
        shown is left to the next board message so the two never disagree."""
        with self._lock:
            rider = self._riders.get(rider_id)
            if rider is None:
                return False
            now = self._clock()
            rider.breaker.reset()
            self._add_event(rider, "score_reset", rider.score, now)
            self._refresh(rider, now)
            self._emit("rider", self._rider_dict(rider, now))
            return True

    # ---- internals ------------------------------------------------------
    def _history(self, rider: _Rider, now: float, seconds: float) -> list:
        cutoff = now - seconds
        return [[round(t, 3), s] for t, s in rider.history if t >= cutoff]

    def _trim(self, rider: _Rider, now: float) -> None:
        cutoff = now - self.config.history_seconds
        while rider.history and rider.history[0][0] < cutoff:
            rider.history.popleft()

    def _refresh(self, rider: _Rider, now: float) -> None:
        if rider.received_at is None:
            rider.link = LINK_WAITING
        else:
            age = now - rider.received_at
            if age < self.config.stale_after_sec:
                rider.link = LINK_ONLINE
            elif age < self.config.offline_after_sec:
                rider.link = LINK_STALE
            else:
                rider.link = LINK_OFFLINE

        # A health report older than the offline window says nothing about now.
        if rider.health_received_at is not None and now - rider.health_received_at >= self.config.offline_after_sec:
            rider.perception = PERCEPTION_UNKNOWN
        # Demo details must not outlive demo mode on screen.
        if rider.detail_received_at is not None and now - rider.detail_received_at >= self.config.stale_after_sec:
            rider.detail = None
            rider.detail_received_at = None

        # Same for vitals: a heart rate from a minute ago is not a heart rate.
        if rider.vitals_received_at is not None and now - rider.vitals_received_at >= self.config.stale_after_sec:
            rider.vitals = None
            rider.vitals_received_at = None

        perception_fault = rider.perception not in (PERCEPTION_OK, PERCEPTION_UNKNOWN)
        if rider.link != LINK_ONLINE or perception_fault:
            rider.status = STATUS_UNKNOWN
        elif rider.breaker.state == DISPATCH_PAUSED:
            rider.status = STATUS_PAUSED
        else:
            rider.status = STATUS_NORMAL

    def _add_event(self, rider: _Rider, kind: str, score, now: float) -> None:
        self._event_seq += 1
        event = {"seq": self._event_seq, "time": round(now, 3), "rider_id": rider.spec.id,
                 "rider_name": rider.spec.name, "kind": kind, "score": score}
        self._events.append(event)
        self._emit("event", event)

    def _rider_dict(self, rider: _Rider, now: float) -> dict:
        return {
            "id": rider.spec.id,
            "name": rider.spec.name,
            "source": rider.spec.source,
            "primary": rider.spec.id == self.config.riders[0].id,  # shown large by default
            "has_board": rider.spec.id in self.config.board_urls,  # a camera stream is configured
            "score": rider.score,
            "timestamp": rider.timestamp,
            "received_at": round(rider.received_at, 3) if rider.received_at is not None else None,
            "age_sec": round(now - rider.received_at, 2) if rider.received_at is not None else None,
            "link": rider.link,
            "perception": rider.perception,
            "dispatch": rider.breaker.state,
            # paused only: seconds of the minimum rest still to serve (0 = served, now waiting for the score)
            "rest_remaining_sec": (round(rider.breaker.rest_remaining(now), 1)
                                   if rider.breaker.state == DISPATCH_PAUSED else None),
            "status": rider.status,
            "detail": rider.detail.to_dict() if rider.detail is not None else None,
            "vitals": rider.vitals.to_dict() if rider.vitals is not None else None,
        }
