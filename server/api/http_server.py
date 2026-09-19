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
  GET  /api/board/{id}/stream.mjpg    proxied MJPEG (403 in normal mode)
  GET  /api/debug/delay?ms=           sleeps, returns a 1px GIF (screenshot aid)
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
from ..core.store import RiderStore
from ..sources.base import ingest_message
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


def make_handler(config: Config, store: RiderStore, boards: dict, sources: list, web_root: str):
    web_root = os.path.realpath(web_root)

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
            self.end_headers()
            self.wfile.write(body)

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
                if parts[:1] != ["api"]:
                    return self._static(url.path)
                route = parts[1:]
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
                    return self._json(200, {"sources": [s.status() for s in sources]})
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

        def do_POST(self):
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
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
                return self._json(404, {"error": "not found"})
            except (PayloadError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})

        def _delay(self, ms: float) -> None:
            """Debug aid for headless screenshots (see web/js/main.js ?once)."""
            time.sleep(min(max(ms, 0.0), 10000.0) / 1000.0)
            self._send(200, TRANSPARENT_GIF, "image/gif")

        # ---- static -----------------------------------------------------
        def _static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(web_root, rel))
            if not (full == web_root or full.startswith(web_root + os.sep)) or not os.path.isfile(full):
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


def build_server(config: Config, store: RiderStore, boards: dict, sources: list, web_root: str):
    server = ThreadingHTTPServer((config.http_host, config.http_port),
                                 make_handler(config, store, boards, sources, web_root))
    server.daemon_threads = True
    return server
