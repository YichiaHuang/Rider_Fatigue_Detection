"""Subscribes to all riders' fatigue score streams and feeds the
dispatch circuit breaker. This is the backend process; the dashboard
reads breaker state from here (see platform/simulator.py for how
simulated riders are fed in for the demo).
"""
from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

from circuit_breaker import BreakerConfig, DispatchCircuitBreaker

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mqtt_subscriber")

TOPIC_WILDCARD = "riders/+/fatigue_score"


class RiderScoreSubscriber:
    def __init__(self, breaker: DispatchCircuitBreaker, broker_host: str = "localhost",
                 broker_port: int = 1883):
        self.breaker = breaker
        self.client = mqtt.Client(client_id="platform-backend")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker_host, broker_port)

    def _on_connect(self, client, userdata, flags, rc):
        log.info("connected rc=%s, subscribing to %s", rc, TOPIC_WILDCARD)
        client.subscribe(TOPIC_WILDCARD, qos=0)

    def _on_message(self, client, userdata, msg):
        try:
            rider_id = msg.topic.split("/")[1]
            payload = json.loads(msg.payload.decode("utf-8"))
            timestamp = payload["timestamp"]
            score = payload["score"]
        except (IndexError, KeyError, ValueError) as exc:
            log.warning("dropping malformed message on %s: %s", msg.topic, exc)
            return

        state = self.breaker.ingest(rider_id, timestamp, score)
        log.info("rider=%s score=%.2f paused=%s", rider_id, state.score, state.paused)

    def run_forever(self) -> None:
        self.client.loop_forever()


if __name__ == "__main__":
    breaker = DispatchCircuitBreaker(BreakerConfig())
    sub = RiderScoreSubscriber(breaker)
    sub.run_forever()
