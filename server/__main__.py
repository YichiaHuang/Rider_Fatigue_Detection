"""Platform-side server: data sources -> store -> web dashboard.

    python3 -m server                       # 4 simulated riders, wait for the real one
    python3 -m server --simulate-real       # board is off: fake rider-01 too (labelled)
    python3 -m server --mqtt-host 127.0.0.1 # subscribe to the real broker
    python3 -m server --board rider-01=http://172.20.10.3:8080   # that rider's camera stream

Several boards: give each its own id on the board side. They show up by
themselves as soon as they publish; to reserve a slot and attach video:
    python3 -m server --mqtt-host 127.0.0.1 --real rider-01,rider-02 \
        --board rider-01=http://172.20.10.3:8080 --board rider-02=http://172.20.10.4:8080

Then open http://localhost:8000/ (dashboard) and http://<this machine>:8000/app/
on a phone (rider app — see docs/APP.md for how to reach it from a phone).
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
from .core.orders import OrderBook
from .core.store import RiderStore
from .sources.mqtt_source import MqttSource
from .sources.order_simulator import OrderSimulatorSource
from .sources.routing import RoutePlanner
from .sources.simulator import SimulatorSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(REPO_ROOT, "web")  # dispatcher dashboard
APP_ROOT = os.path.join(REPO_ROOT, "app")  # rider app (PWA), served under /app/


def parse_args(argv=None) -> Config:
    defaults = Config()
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=defaults.http_host)
    p.add_argument("--port", type=int, default=int(env("DASH_PORT", defaults.http_port)))
    p.add_argument("--tunnel-port", type=int, default=int(env("TUNNEL_PORT", defaults.tunnel_port)),
                   help="second listener for phone tunnels (see Config.tunnel_port); 0 = off")
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
    p.add_argument("--min-rest", type=float, default=defaults.min_rest_sec,
                   help="seconds a fatigue pause lasts at least, even if the score is already below --resume")
    p.add_argument("--simulate-real", action="store_true",
                   help="dev only: simulate the real rider too (shown as simulated on the page)")
    p.add_argument("--no-simulator", action="store_true", help="no simulated riders at all")
    p.add_argument("--warn", type=float, default=defaults.warn_threshold,
                   help="rider app: early-warning score, below --pause")
    p.add_argument("--no-orders", action="store_true", help="rider app: don't generate simulated orders")
    p.add_argument("--routing-url", default=env("ROUTING_URL", defaults.routing_url),
                   help="rider app map: OSRM server for road routes")
    p.add_argument("--no-routing", action="store_true",
                   help="rider app map: never go online, draw straight-line estimates (venue without internet)")
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
        defaults, http_host=a.host, http_port=a.port, tunnel_port=a.tunnel_port, mqtt_host=a.mqtt_host, mqtt_port=a.mqtt_port,
        board_urls=board_urls, board_token=a.board_token, pause_threshold=a.pause,
        resume_threshold=a.resume, min_rest_sec=a.min_rest, warn_threshold=a.warn, run_order_simulator=not a.no_orders,
        routing_url="" if a.no_routing else a.routing_url,
        simulate_real_rider=a.simulate_real,
        run_simulator=not a.no_simulator, auto_register=not a.no_auto_register, riders=tuple(riders))


def main(argv=None) -> None:
    config = parse_args(argv)
    store = RiderStore(config)
    orders = OrderBook(config, store)

    sources = []
    if config.run_order_simulator:
        sources.append(OrderSimulatorSource(orders, config))
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
            orders.tick()

    threading.Thread(target=ticker, name="store-tick", daemon=True).start()

    boards = {rider_id: BoardProxy(url, config.board_token) for rider_id, url in config.board_urls.items()}
    planner = RoutePlanner(config.routing_url, config.routing_timeout_sec)  # one cache for both listeners
    server = build_server(config, store, boards, sources, WEB_ROOT, orders, APP_ROOT, planner)
    if config.tunnel_port and config.tunnel_port != config.http_port:
        try:
            tunnel = build_server(dataclasses.replace(config, http_port=config.tunnel_port), store, boards, sources,
                                  WEB_ROOT, orders, APP_ROOT, planner)
            threading.Thread(target=tunnel.serve_forever, name="http-tunnel-port", daemon=True).start()
        except OSError as exc:  # port taken: the main listener is what matters
            print(f"tunnel port {config.tunnel_port} not available ({exc}); continuing without it", file=sys.stderr)
            config = dataclasses.replace(config, tunnel_port=0)
    shown_host = "localhost" if config.http_host in ("0.0.0.0", "") else config.http_host
    print(f"dashboard  http://{shown_host}:{config.http_port}/", file=sys.stderr)
    print(f"rider app  http://{shown_host}:{config.http_port}/app/   (phones: docs/APP.md)", file=sys.stderr)
    if config.tunnel_port:
        print(f"tunnels    same app on port {config.tunnel_port} (for Tailscale Funnel etc.)", file=sys.stderr)
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
