"""A Source is anything that produces rider samples and hands them to the store.

To add a transport (serial, WebSocket, replay-from-CSV …): subclass Source,
call self.store.ingest_*() from your own thread, register it in
server/__main__.py. Nothing in core/ or api/ needs to change.
"""
from __future__ import annotations

from ..core.models import PayloadError, parse_detail, parse_health, parse_score, parse_vitals
from ..core.store import RiderStore

KIND_SCORE = "fatigue_score"
KIND_DETAIL = "demo_state"
KIND_HEALTH = "health"
KIND_VITALS = "vitals"
KINDS = (KIND_SCORE, KIND_DETAIL, KIND_HEALTH, KIND_VITALS)


class Source:
    name = "source"

    def __init__(self, store: RiderStore):
        self.store = store

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def status(self) -> dict:
        return {"name": self.name}


def ingest_message(store: RiderStore, rider_id: str, kind: str, payload: dict) -> None:
    """Shared by every transport (MQTT topic, HTTP ingest) so they all accept
    exactly the same payloads. Raises PayloadError on bad input."""
    if not isinstance(payload, dict):
        raise PayloadError("payload must be a JSON object")
    if kind == KIND_SCORE:
        accepted = store.ingest_score(parse_score(rider_id, payload))
    elif kind == KIND_DETAIL:
        accepted = store.ingest_detail(parse_detail(rider_id, payload))
    elif kind == KIND_HEALTH:
        accepted = store.ingest_health(parse_health(rider_id, payload))
    elif kind == KIND_VITALS:
        accepted = store.ingest_vitals(parse_vitals(rider_id, payload))
    else:
        raise PayloadError(f"unknown message kind '{kind}', expected one of {KINDS}")
    if not accepted:
        raise PayloadError(f"rider '{rider_id}' not accepted (unknown id, invalid id, or roster full)")
