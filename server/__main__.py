"""Platform-side server: data sources -> store -> web dashboard.

    python3 -m server                       # 4 simulated riders, wait for the real one
    python3 -m server --simulate-real       # board is off: fake rider-01 too (labelled)
    python3 -m server --mqtt-host 127.0.0.1 # subscribe to the real broker
    python3 -m server --board rider-01=http://172.20.10.3:8080   # that rider's camera stream

Several boards: give each its own id on the board side. They show up by
themselves as soon as they publish; to reserve a slot and attach video:
    python3 -m server --mqtt-host 127.0.0.1 --real rider-01,rider-02 \
        --board rider-01=http://172.20.10.3:8080 --board rider-02=http://172.20.10.4:8080

Then open http://localhost:8000/
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import threading

from .api.board_proxy import BoardProxy
from .api.http_server import build_server
from .config import SOURCE_REAL, SOURCE_SIMULATED, Config, RiderSpec
from .core.store import RiderStore
from .sources.mqtt_source import MqttSource
from .sources.simulator import SimulatorSource

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def parse_args(argv=None) -> Config:
    defaults = Config()
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=defaults.http_host)
    p.add_argument("--port", type=int, default=int(env("DASH_PORT", defaults.http_port)))
    p.add_argument("--mqtt-host", default=env("MQTT_HOST", defaults.mqtt_host),
                   help="broker address; omit to run without MQTT")
    p.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", defaults.mqtt_port)))
    p.add_argument("--board", action="append", default=[], metavar="RIDER=URL",
                   help="a rider's rider/stream_server.py base URL; repeat per board")
    p.add_argument("--board-url", default=env("BOARD_URL", ""),
                   help="shortcut for --board <first rider>=URL")
    p.add_argument("--real", default="", metavar="ID[,ID...]",
                   help="rider ids fed by real boards (default: rider-01); the rest are simulated")
    p.add_argument("--no-auto-register", action="store_true",
                   help="reject rider ids that are not in the roster instead of adding them")
    p.add_argument("--board-token", default=env("STREAM_TOKEN", ""))
    p.add_argument("--pause", type=float, default=defaults.pause_threshold)
    p.add_argument("--resume", type=float, default=defaults.resume_threshold)
    p.add_argument("--simulate-real", action="store_true",
                   help="dev only: simulate the real rider too (shown as simulated on the page)")
    p.add_argument("--no-simulator", action="store_true", help="no simulated riders at all")
    a = p.parse_args(argv)

    riders = list(defaults.riders)
    real_ids = [r for r in a.real.split(",") if r] or [riders[0].id]
    known = {spec.id for spec in riders}
    riders = [dataclasses.replace(spec, source=SOURCE_REAL) if spec.id in real_ids else spec for spec in riders]
    riders += [RiderSpec(rid, rid, SOURCE_REAL) for rid in real_ids if rid not in known]
    if a.simulate_real:
        riders[0] = RiderSpec(riders[0].id, riders[0].name, SOURCE_SIMULATED, "demo")

    board_urls = dict(defaults.board_urls)
    if a.board_url:
        board_urls[riders[0].id] = a.board_url
    for item in a.board:
        rider_id, sep, url = item.partition("=")
        if not sep or not url:
            p.error(f"--board expects RIDER=URL, got '{item}'")
        board_urls[rider_id] = url
    return dataclasses.replace(
        defaults, http_host=a.host, http_port=a.port, mqtt_host=a.mqtt_host, mqtt_port=a.mqtt_port,
        board_urls=board_urls, board_token=a.board_token, pause_threshold=a.pause,
        resume_threshold=a.resume, simulate_real_rider=a.simulate_real,
        run_simulator=not a.no_simulator, auto_register=not a.no_auto_register, riders=tuple(riders))


def main(argv=None) -> None:
    config = parse_args(argv)
    store = RiderStore(config)

    sources = []
    if config.run_simulator:
        sources.append(SimulatorSource(store, config))
    if config.mqtt_host:
        sources.append(MqttSource(store, config.mqtt_host, config.mqtt_port, config.mqtt_topic_root))
    for source in sources:
        source.start()

    stop = threading.Event()

    def ticker():
        while not stop.wait(1.0):
            store.tick()

    threading.Thread(target=ticker, name="store-tick", daemon=True).start()

    boards = {rider_id: BoardProxy(url, config.board_token) for rider_id, url in config.board_urls.items()}
    server = build_server(config, store, boards, sources, WEB_ROOT)
    shown_host = "localhost" if config.http_host in ("0.0.0.0", "") else config.http_host
    print(f"dashboard  http://{shown_host}:{config.http_port}/", file=sys.stderr)
    print(f"sources    {', '.join(s.name for s in sources) or 'none (HTTP ingest only)'}", file=sys.stderr)
    for rider_id, url in config.board_urls.items():
        print(f"board      {rider_id} -> {url}", file=sys.stderr)
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for source in sources:
            source.stop()


if __name__ == "__main__":
    main()
