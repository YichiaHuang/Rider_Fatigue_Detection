"""Publishes simulated fatigue scores for the 4 non-live riders in the demo
dashboard (plan.md section 5: 1 real board + 4 simulated). Each simulated
rider is just another MQTT publisher on riders/{id}/fatigue_score — the
platform backend does not distinguish real from simulated.
"""
from __future__ import annotations

import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))
from mqtt_publisher import FatigueScorePublisher  # noqa: E402

SIMULATED_RIDER_IDS = ["sim-1", "sim-2", "sim-3", "sim-4"]


def run(broker_host: str = "localhost", broker_port: int = 1883, tick_seconds: float = 1.0) -> None:
    publishers = {
        rider_id: FatigueScorePublisher(rider_id, broker_host, broker_port)
        for rider_id in SIMULATED_RIDER_IDS
    }
    scores = {rider_id: random.uniform(0, 5) for rider_id in SIMULATED_RIDER_IDS}

    try:
        while True:
            now = time.time()
            for rider_id in SIMULATED_RIDER_IDS:
                # random walk with occasional fatigue spikes, clamped to [0, 100]
                drift = random.uniform(-1.5, 1.5)
                if random.random() < 0.03:
                    drift += random.uniform(8, 15)
                scores[rider_id] = max(0.0, min(100.0, scores[rider_id] + drift))
                publishers[rider_id].publish(now, round(scores[rider_id], 2))
            time.sleep(tick_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        for pub in publishers.values():
            pub.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Simulate 4 riders for the dashboard demo.")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    args = parser.parse_args()
    run(args.broker_host, args.broker_port)
