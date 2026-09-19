"""MQTT -> store. Topics (plan.md 4.4, full contract in docs/API.md):

    riders/{id}/fatigue_score   {"timestamp", "score"}            always
    riders/{id}/health          {"timestamp", "perception"}       always
    riders/{id}/demo_state      {"timestamp", "ear", "mar", ...}  demo mode only
"""
from __future__ import annotations

import json
import sys

from ..core.models import PayloadError
from ..core.store import RiderStore
from .base import KINDS, Source, ingest_message
from .mqtt_client import MqttClient


class MqttSource(Source):
    name = "mqtt"

    def __init__(self, store: RiderStore, host: str, port: int, topic_root: str = "riders"):
        super().__init__(store)
        self.topic_root = topic_root
        self.rejected = 0
        self.received = 0
        self.last_reject = None
        self.client = MqttClient(host, port, on_message=self._on_message, on_state=self._on_state)
        for kind in KINDS:
            self.client.subscribe(f"{topic_root}/+/{kind}")

    def start(self) -> None:
        self.client.start()

    def stop(self) -> None:
        self.client.stop()

    def status(self) -> dict:
        return {"name": self.name, "broker": f"{self.client.host}:{self.client.port}",
                "connected": self.client.connected, "error": self.client.last_error,
                "received": self.received, "rejected": self.rejected, "last_reject": self.last_reject}

    def _on_state(self, connected: bool, error) -> None:
        where = f"{self.client.host}:{self.client.port}"
        print(f"[mqtt] {'connected to' if connected else 'disconnected from'} {where}"
              + (f" ({error})" if error else ""), file=sys.stderr, flush=True)

    def _on_message(self, topic: str, raw: bytes) -> None:
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != self.topic_root:
            return
        try:
            ingest_message(self.store, parts[1], parts[2], json.loads(raw))
            self.received += 1
        except (PayloadError, ValueError) as exc:  # ValueError covers bad JSON
            self.rejected += 1
            self.last_reject = f"{topic}: {exc}"
