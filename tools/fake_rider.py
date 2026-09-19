"""Pretend to be the board: publish fatigue_score / health / demo_state for one
rider, over MQTT or the HTTP fallback. Use it to exercise the REAL ingestion
path (not the built-in simulator) while the board is off.

    python3 tools/fake_rider.py --http http://localhost:8000
    python3 tools/fake_rider.py --mqtt 127.0.0.1 --rider rider-01 --no-detail

Type a number + Enter to jump the score (e.g. 18 to trip the breaker, 0 to
recover); "face" toggles a no-face fault; Ctrl+C quits.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.sources.mqtt_client import MqttClient  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--http", metavar="URL", help="dashboard base URL")
    target.add_argument("--mqtt", metavar="HOST", help="broker host")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--rider", default="rider-01")
    p.add_argument("--hz", type=float, default=1.0)
    p.add_argument("--no-detail", action="store_true", help="normal mode: never send demo_state")
    p.add_argument("--wave", action="store_true",
                   help="drive the score up past the pause line and back down by itself (~2 min cycle)")
    a = p.parse_args()

    client = None
    if a.mqtt:
        client = MqttClient(a.mqtt, a.mqtt_port, client_id=f"fake-{a.rider}")
        client.start()
        if not client.wait_connected(5.0):
            sys.exit(f"cannot reach broker {a.mqtt}:{a.mqtt_port} ({client.last_error})")

    def send(kind: str, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        if client is not None:
            client.publish(f"riders/{a.rider}/{kind}", raw)
            return
        request = urllib.request.Request(f"{a.http.rstrip('/')}/api/ingest/{a.rider}/{kind}", data=raw,
                                         method="POST", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=3).read()

    state = {"score": 0.0, "face": True}

    def keyboard():
        for line in sys.stdin:
            line = line.strip()
            if line == "face":
                state["face"] = not state["face"]
                print(f"face detected -> {state['face']}")
            else:
                try:
                    state["score"] = float(line)
                except ValueError:
                    print("type a number, or 'face'")

    threading.Thread(target=keyboard, daemon=True).start()
    print(f"publishing as {a.rider}; type a score, or 'face'")
    try:
        while True:
            now = time.time()
            send("health", {"timestamp": now, "perception": "ok" if state["face"] else "no_face"})
            if state["face"]:  # per docs/API.md: no valid perception -> no score
                send("fatigue_score", {"timestamp": now, "score": round(state["score"], 2)})
                if not a.no_detail:
                    send("demo_state", {"timestamp": now, "ear": 0.27, "mar": 0.09, "perclos": 0.06,
                                        "head_pitch_deg": 2.0, "inference_fps": 12.0, "reasons": []})
                if a.wave:
                    state["score"] = max(0.0, 10.0 - 10.0 * math.cos(now / 20.0))
                else:
                    state["score"] = max(0.0, state["score"] - 0.2 / a.hz)
            time.sleep(1.0 / a.hz)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
