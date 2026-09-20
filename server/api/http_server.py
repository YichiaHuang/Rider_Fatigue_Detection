"""HTTP layer: REST + Server-Sent Events + static files + board proxy.
Reads from RiderStore only; knows nothing about where the data came from.

  GET  /api/config                    thresholds etc. for the UI
  GET  /api/state?history=600         full snapshot (riders + history + events)
  GET  /api/riders/{id}/history?seconds=600
  GET  /api/events                    SSE: "rider" and "event" messages, live
  GET  /api/sources                   data-source health (MQTT connected? …)
  POST /api/ingest/{rider}/{kind}     HTTP fallback for the MQTT topics
  GET  /api/board/{id}/status         that rider's stream server status (never 5xx)
  POST /api/board/{id}/mode           {"demo": bool} -> forwarded to that board
  POST /api/board/{id}/reset          zero that board's fatigue score + lift the pause here
  GET  /api/board/{id}/stream.mjpg    proxied MJPEG (403 in normal mode)
  GET  /api/debug/delay?ms=           sleeps, returns a 1px GIF (screenshot aid)
  GET  /api/app/riders                rider app: who can sign in
  GET  /api/app/{id}/state            rider app: everything one phone needs (polled ~1 Hz)
  GET  /api/app/{id}/route?from=lat,lng   rider app: road route for the order on screen
  POST /api/app/{id}/duty|offer|advance|ack   rider app actions; each returns the new state
  GET  /app/*                         the rider app (PWA) from app/
  GET  /*                             static files from web/

Full payload shapes: docs/API.md
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..core.models import PayloadError
from ..core.orders import ORDER_PICKED_UP, OrderBook, OrderError
from ..core.store import RiderStore
from ..sources.base import ingest_message
from ..sources.routing import RoutePlanner
from .board_proxy import StreamUnavailable

SSE_HEARTBEAT_SEC = 10.0
SSE_QUEUE_SIZE = 500
MAX_BODY = 64 * 1024
MJPEG_BOUNDARY = "frame"
STREAM_RESUME_WINDOW_SEC = 45.0   # how long a viewer's stream waits for the board to come back
TRANSPARENT_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
                   b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")

APP_PREFIX = "/app"
ALERT_LEVELS = ("warning", "paused")


def make_handler(config: Config, store: RiderStore, boards: dict, sources: list, web_root: str,
                 orders: "OrderBook | None" = None, app_root: "str | None" = None,
                 planner: "RoutePlanner | None" = None):
    web_root = os.path.realpath(web_root)
    app_root = os.path.realpath(app_root) if app_root else None
    orders = orders or OrderBook(config, store)
    planner = planner or RoutePlanner(config.routing_url, config.routing_timeout_sec)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not self.path.startswith("/api/") or "ingest" in self.path:
                return  # static + per-second traffic would drown the console
            sys.stderr.write("[http] %s %s\n" % (self.address_string(), fmt % args))

        # ---- helpers ----------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _cors_headers(self) -> None:
            # Only the rider-app API: a store-packaged build of the app (Capacitor)
            # runs from its own origin. The board controls stay same-origin.
            if self.path.startswith(("/api/app/", "/api/config")):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise PayloadError("body too large")
            return json.loads(self.rfile.read(length) or b"{}")

        # ---- routing ----------------------------------------------------
        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            parts = [p for p in url.path.split("/") if p]
            try:
                if parts[:1] == ["app"] and app_root:
                    if url.path == APP_PREFIX:  # relative URLs in the app need the trailing slash
                        self.send_response(301)
                        self.send_header("Location", APP_PREFIX + "/")
                        self.send_header("Content-Length", "0")
                        return self.end_headers()
                    return self._static(url.path[len(APP_PREFIX):], app_root)
                if parts[:1] != ["api"]:
                    return self._static(url.path, web_root)
                route = parts[1:]
                if route[:1] == ["app"]:
                    return self._app_get(route[1:], query)
                if route == ["config"]:
                    return self._json(200, config.public_dict())
                if route == ["state"]:
                    seconds = float(query.get("history", [config.history_seconds])[0])
                    return self._json(200, store.snapshot(history_seconds=seconds))
                if len(route) == 3 and route[0] == "riders" and route[2] == "history":
                    seconds = float(query.get("seconds", [config.history_seconds])[0])
                    history = store.history(route[1], seconds)
                    if history is None:
                        return self._json(404, {"error": f"unknown rider '{route[1]}'"})
                    return self._json(200, {"rider_id": route[1], "points": history})
                if route == ["events"]:
                    return self._sse()
                if route == ["sources"]:
                    return self._json(200, {"sources": [s.status() for s in sources] + [planner.status()]})
                if route == ["debug", "delay"]:
                    return self._delay(float(query.get("ms", ["1000"])[0]))
                if len(route) == 3 and route[0] == "board" and route[2] == "status":
                    board = boards.get(route[1])
                    if board is None:
                        return self._json(200, {"reachable": False, "configured": False})
                    return self._json(200, {**board.status(), "configured": True})
                if len(route) == 3 and route[0] == "board" and route[2] == "stream.mjpg":
                    return self._board_stream(boards.get(route[1]))
                return self._json(404, {"error": "not found"})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                if len(parts) == 4 and parts[:2] == ["api", "app"]:
                    return self._app_post(parts[2], parts[3], self._read_json())
                if len(parts) == 4 and parts[:2] == ["api", "ingest"]:
                    ingest_message(store, parts[2], parts[3], self._read_json())
                    return self._json(200, {"ok": True})
                if len(parts) == 4 and parts[:2] == ["api", "board"] and parts[3] == "mode":
                    board = boards.get(parts[2])
                    if board is None:
                        return self._json(404, {"error": f"no board configured for '{parts[2]}'"})
                    body = self._read_json()
                    if not isinstance(body, dict) or not isinstance(body.get("demo"), bool):
                        return self._json(400, {"error": 'expected {"demo": true|false}'})
                    code, result = board.set_mode(body["demo"])
                    return self._json(code, result)
                if len(parts) == 4 and parts[:2] == ["api", "board"] and parts[3] == "reset":
                    board = boards.get(parts[2])
                    if board is None:
                        return self._json(404, {"error": f"no board configured for '{parts[2]}'"})
                    self._read_json()  # body is "{}"; left unread it would prefix the next request on this keep-alive connection
                    code, result = board.reset_score()
                    if code == 200:  # only when the board really zeroed: a pause must not lift on its own
                        store.reset_dispatch(parts[2])
                    return self._json(code, result)
                return self._json(404, {"error": "not found"})
            except (PayloadError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})

        # ---- rider app (docs/API.md section 4) ---------------------------
        def _app_get(self, route: list, query: dict) -> None:
            if route == ["riders"]:
                riders = store.snapshot()["riders"]
                return self._json(200, {"riders": [{k: r[k] for k in ("id", "name", "source", "link")}
                                                   for r in riders]})
            if len(route) == 2 and route[1] == "state":
                state = orders.app_state(route[0])
                if state is None:
                    return self._json(404, {"error": f"unknown rider '{route[0]}'"})
                return self._json(200, state)
            if len(route) == 2 and route[1] == "route":
                return self._app_route(route[0], query.get("from", [""])[0])
            return self._json(404, {"error": "not found"})

        def _app_route(self, rider_id: str, origin: str) -> None:
            """Rider's position (optional) -> pickup -> dropoff. Once the food is on
            the bike the pickup is dropped — unless there is no position to start
            from, where shop -> customer is still the useful picture."""
            order = orders.current_order(rider_id)
            if order is None or not order.pickup_pos or not order.dropoff_pos:
                return self._json(404, {"error": "no order with coordinates for this rider"})
            stops = [("pickup", order.pickup_pos), ("dropoff", order.dropoff_pos)]
            if order.state == ORDER_PICKED_UP and origin:
                stops = stops[1:]
            if origin:
                lat, lng = (float(v) for v in origin.split(","))  # ValueError -> 400
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError("from: expected lat,lng")
                stops.insert(0, ("rider", (lat, lng)))
            plan = planner.plan([pos for _, pos in stops])
            legs = [{**leg, "to": name} for leg, (name, _) in zip(plan["legs"], stops[1:])]
            # from_rider: the line starts at the rider, so the app may navigate along it
            # (a shop -> customer line is only a picture of the trip).
            return self._json(200, {**plan, "legs": legs, "order_id": order.id, "order_state": order.state,
                                    "from_rider": bool(origin)})

        def _app_post(self, rider_id: str, action: str, body) -> None:
            if not isinstance(body, dict):
                raise PayloadError("body must be a JSON object")
            try:
                if action == "duty":
                    if not isinstance(body.get("on"), bool):
                        raise PayloadError('expected {"on": true|false}')
                    orders.set_duty(rider_id, body["on"])
                elif action == "offer":
                    if not isinstance(body.get("order_id"), str) or not isinstance(body.get("accept"), bool):
                        raise PayloadError('expected {"order_id": "...", "accept": true|false}')
                    orders.answer_offer(rider_id, body["order_id"], body["accept"])
                elif action == "advance":
                    if not isinstance(body.get("order_id"), str):
                        raise PayloadError('expected {"order_id": "..."}')
                    orders.advance(rider_id, body["order_id"])
                elif action == "ack":
                    if body.get("level") not in ALERT_LEVELS:
                        raise PayloadError(f'expected {{"level": one of {ALERT_LEVELS}}}')
                    orders.acknowledge_alert(rider_id, body["level"])
                else:
                    return self._json(404, {"error": "not found"})
            except OrderError as exc:
                return self._json(409, {"error": str(exc), "state": orders.app_state(rider_id)})
            return self._json(200, orders.app_state(rider_id))

        def _delay(self, ms: float) -> None:
            """Debug aid for headless screenshots (see web/js/main.js ?once)."""
            time.sleep(min(max(ms, 0.0), 10000.0) / 1000.0)
            self._send(200, TRANSPARENT_GIF, "image/gif")

        # ---- static -----------------------------------------------------
        def _static(self, path: str, root: str) -> None:
            rel = path.lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(root, rel))
            if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
                return self._json(404, {"error": "not found"})
            content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/json":
                content_type += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), content_type)

        # ---- server-sent events -----------------------------------------
        def _sse(self) -> None:
            outbox = queue.Queue(maxsize=SSE_QUEUE_SIZE)

            def listener(kind, payload):
                try:
                    outbox.put_nowait((kind, payload))
                except queue.Full:
                    pass  # slow browser: drop updates rather than block ingestion

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            store.subscribe(listener)
            try:
                self.wfile.write(b"retry: 1500\n\n")
                self.wfile.flush()
                while True:
                    try:
                        kind, payload = outbox.get(timeout=SSE_HEARTBEAT_SEC)
                        data = json.dumps(payload, ensure_ascii=False)
                        self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                pass
            finally:
                store.unsubscribe(listener)

        # ---- MJPEG proxy ------------------------------------------------
        def _board_stream(self, board) -> None:
            if board is None:
                return self._json(404, {"error": "no board configured for this rider"})
            frames = board.subscribe_frames()
            try:
                first = next(frames, None)
            except StreamUnavailable as exc:
                reason = "normal mode: board is not streaming" if exc.code == 403 else "board unreachable"
                return self._json(exc.code, {"error": reason})
            if first is None:
                return self._json(502, {"error": "board stream ended"})
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                jpeg = first
                while True:
                    while jpeg is not None:
                        self.wfile.write(
                            f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                            + jpeg + b"\r\n")
                        jpeg = next(frames, None)
                    # Upstream ended. If the board runner was merely restarted (deploy,
                    # crash + supervisor) the picture should come back by itself: keep
                    # this response open and re-attach, instead of leaving the browser
                    # with a frozen last frame. Demo mode switched off (403) ends it.
                    frames.close()
                    jpeg, deadline = None, time.time() + STREAM_RESUME_WINDOW_SEC
                    while jpeg is None and time.time() < deadline:
                        time.sleep(1.0)
                        frames = board.subscribe_frames()
                        try:
                            jpeg = next(frames, None)
                        except StreamUnavailable as exc:
                            if exc.code == 403:
                                return
                    if jpeg is None:
                        return
            except (OSError, TimeoutError):
                pass
            finally:
                frames.close()  # releases the shared relay; last viewer out closes the upstream

    return Handler


def build_server(config: Config, store: RiderStore, boards: dict, sources: list, web_root: str,
                 orders: "OrderBook | None" = None, app_root: "str | None" = None,
                 planner: "RoutePlanner | None" = None):
    server = ThreadingHTTPServer((config.http_host, config.http_port),
                                 make_handler(config, store, boards, sources, web_root, orders, app_root, planner))
    server.daemon_threads = True
    return server
