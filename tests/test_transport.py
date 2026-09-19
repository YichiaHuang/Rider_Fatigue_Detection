"""MQTT client/source against a tiny in-process fake broker, and the HTTP API
end to end. No external broker or packages needed."""
import json
import socket
import struct
import threading
import time
import unittest
import urllib.error
import urllib.request

from server.api.board_proxy import BoardProxy
from server.api.http_server import build_server
from server.config import Config
from server.core.store import RiderStore
from server.sources.mqtt_source import MqttSource


class FakeBroker:
    """Accepts one client: CONNACK, SUBACK, then publishes whatever push() is given."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.subscriptions = []
        self.conn = None
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _read_packet(self):
        header = self.conn.recv(1)
        if not header:
            return None, b""
        length, multiplier = 0, 1
        while True:
            byte = self.conn.recv(1)[0]
            length += (byte & 0x7F) * multiplier
            multiplier *= 128
            if not byte & 0x80:
                break
        body = b""
        while len(body) < length:
            body += self.conn.recv(length - len(body))
        return header[0] >> 4, body

    def _run(self):
        self.conn, _ = self.server.accept()
        while True:
            try:
                packet_type, body = self._read_packet()
            except OSError:
                return
            if packet_type is None:
                return
            if packet_type == 1:    # CONNECT
                self.conn.sendall(bytes([0x20, 2, 0, 0]))
            elif packet_type == 8:  # SUBSCRIBE
                offset, count = 2, 0
                while offset < len(body):
                    n = struct.unpack("!H", body[offset:offset + 2])[0]
                    self.subscriptions.append(body[offset + 2:offset + 2 + n].decode())
                    offset += 2 + n + 1
                    count += 1
                self.conn.sendall(bytes([0x90, 2 + count]) + body[:2] + bytes(count))
                self.ready.set()
            elif packet_type == 12:  # PINGREQ
                self.conn.sendall(bytes([0xD0, 0]))

    def push(self, topic: str, payload: dict):
        raw_topic, raw = topic.encode(), json.dumps(payload).encode()
        body = struct.pack("!H", len(raw_topic)) + raw_topic + raw
        assert len(body) < 128
        self.conn.sendall(bytes([0x30, len(body)]) + body)

    def close(self):
        for s in (self.conn, self.server):
            try:
                s and s.close()
            except OSError:
                pass


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class MqttSourceTest(unittest.TestCase):
    def test_topics_reach_the_store(self):
        broker = FakeBroker()
        store = RiderStore(Config())
        source = MqttSource(store, "127.0.0.1", broker.port)
        source.start()
        try:
            self.assertTrue(broker.ready.wait(3.0), "client never subscribed")
            self.assertEqual(sorted(broker.subscriptions),
                             ["riders/+/demo_state", "riders/+/fatigue_score", "riders/+/health"])
            now = time.time()
            broker.push("riders/rider-01/fatigue_score", {"timestamp": now, "score": 16.5})
            broker.push("riders/rider-01/health", {"timestamp": now, "perception": "ok"})
            broker.push("riders/rider-01/demo_state", {"timestamp": now, "ear": 0.19, "reasons": ["perclos"]})
            broker.push("riders/rider-01/fatigue_score", {"timestamp": now})       # bad: no score
            broker.push("riders/bad id/fatigue_score", {"timestamp": now, "score": 1})  # bad: invalid rider id
            broker.push("riders/rider-09/fatigue_score", {"timestamp": now, "score": 3})  # a second board
            self.assertTrue(wait_until(lambda: source.received == 4 and source.rejected == 2), source.status())
            self.assertEqual(store.snapshot()["riders"][-1]["id"], "rider-09")
            rider = store.snapshot()["riders"][0]
            self.assertEqual((rider["score"], rider["status"], rider["perception"]), (16.5, "paused", "ok"))
            self.assertEqual(rider["detail"]["reasons"], ["perclos"])
        finally:
            source.stop()
            broker.close()


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Config(http_host="127.0.0.1", http_port=0)
        cls.store = RiderStore(cls.config)
        boards = {"rider-01": BoardProxy("http://127.0.0.1:9", "")}  # discard port: always unreachable
        cls.server = build_server(cls.config, cls.store, boards, [], "web")
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()

    def post(self, path, obj):
        request = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_ingest_state_history(self):
        code, _ = self.post("/api/ingest/rider-02/fatigue_score", {"timestamp": time.time(), "score": 4.5})
        self.assertEqual(code, 200)
        state = json.loads(self.get("/api/state?history=60")[2])
        rider = next(r for r in state["riders"] if r["id"] == "rider-02")
        self.assertEqual((rider["score"], rider["source"], rider["history"][-1][1]), (4.5, "simulated", 4.5))
        points = json.loads(self.get("/api/riders/rider-02/history?seconds=60")[2])["points"]
        self.assertEqual(points[-1][1], 4.5)

    def test_bad_ingest_is_400(self):
        self.assertEqual(self.post("/api/ingest/rider-02/fatigue_score", {"score": 1})[0], 400)
        self.assertEqual(self.post("/api/ingest/bad%20id/fatigue_score", {"timestamp": 1, "score": 1})[0], 400)
        self.assertEqual(self.post("/api/ingest/rider-02/nonsense", {"timestamp": 1})[0], 400)

    def test_sse_pushes_live_updates(self):
        resp = urllib.request.urlopen(self.base + "/api/events", timeout=5)
        self.assertTrue(resp.headers.get("Content-Type").startswith("text/event-stream"))
        resp.readline(); resp.readline()  # "retry:" preamble
        self.post("/api/ingest/rider-03/fatigue_score", {"timestamp": time.time(), "score": 7.0})
        self.assertEqual(resp.readline().strip(), b"event: rider")
        data = json.loads(resp.readline().decode()[len("data: "):])
        self.assertEqual((data["id"], data["score"]), ("rider-03", 7.0))
        resp.close()

    def test_board_unreachable_is_a_state_not_an_error(self):
        code, _, body = self.get("/api/board/rider-01/status")
        self.assertEqual(code, 200)
        self.assertEqual((json.loads(body)["reachable"], json.loads(body)["configured"]), (False, True))
        self.assertFalse(json.loads(self.get("/api/board/rider-02/status")[2])["configured"])
        self.assertEqual(self.post("/api/board/rider-02/mode", {"demo": True})[0], 404)

    def test_static_and_traversal(self):
        code, content_type, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"js/main.js", body)
        self.assertIn("javascript", self.get("/js/main.js")[1])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../server/config.py")
        self.assertEqual(ctx.exception.code, 404)


class FakeBoard:
    """Minimal rider/stream_server.py stand-in: counts upstream connections."""

    def __init__(self, demo=True):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        board = self
        self.connections = 0
        self.demo = demo

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if not board.demo:
                    self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers()
                    return
                board.connections += 1
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    for i in range(200):
                        jpeg = b"\xff\xd8" + str(i).encode() + b"\xff\xd9"
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                        time.sleep(0.02)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class RelayTest(unittest.TestCase):
    def test_many_viewers_one_upstream(self):
        board = FakeBoard()
        proxy = BoardProxy(board.url)
        a, b = proxy.subscribe_frames(), proxy.subscribe_frames()
        frames_a = [next(a) for _ in range(5)]
        frames_b = [next(b) for _ in range(5)]
        self.assertTrue(all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in frames_a + frames_b))
        self.assertEqual(board.connections, 1, "two browsers must share one connection to the board")
        a.close(); b.close()
        c = proxy.subscribe_frames()  # everyone left, so a new viewer reconnects
        next(c); c.close()
        self.assertEqual(board.connections, 2)
        board.server.shutdown()

    def test_normal_mode_is_403(self):
        from server.api.board_proxy import StreamUnavailable
        board = FakeBoard(demo=False)
        with self.assertRaises(StreamUnavailable) as ctx:
            next(BoardProxy(board.url).subscribe_frames())
        self.assertEqual(ctx.exception.code, 403)
        board.server.shutdown()


if __name__ == "__main__":
    unittest.main()
