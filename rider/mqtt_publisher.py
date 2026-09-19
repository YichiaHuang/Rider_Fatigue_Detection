"""Publishes fatigue scores to the platform broker.

Payload is intentionally minimal per plan.md section 4.4: only
{timestamp, score}. No image, no location, no raw sensor data ever
leaves the device — that is an architectural guarantee, not a policy.
"""
from __future__ import annotations

import json
import time

import paho.mqtt.client as mqtt


class FatigueScorePublisher:
    def __init__(self, rider_id: str, broker_host: str = "localhost",
                 broker_port: int = 1883, qos: int = 0):
        self.rider_id = rider_id
        self.topic = f"riders/{rider_id}/fatigue_score"
        self.qos = qos
        self.client = mqtt.Client(client_id=f"rider-{rider_id}")
        self.client.connect(broker_host, broker_port)
        self.client.loop_start()

    def publish(self, timestamp: float, score: float) -> None:
        payload = json.dumps({"timestamp": timestamp, "score": score})
        self.client.publish(self.topic, payload, qos=self.qos)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Smoke-test publisher: sends a rising score.")
    parser.add_argument("--rider-id", default="7")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    args = parser.parse_args()

    pub = FatigueScorePublisher(args.rider_id, args.broker_host, args.broker_port)
    try:
        score = 0.0
        while True:
            score = min(100.0, score + 1.0)
            pub.publish(time.time(), score)
            print(f"published score={score}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        pub.close()
