"""Minimal MQTT 3.1.1 client on the standard library: CONNECT, SUBSCRIBE (QoS 0),
PUBLISH (QoS 0), PINGREQ, auto-reconnect. That is everything this project
needs, and it saves installing paho-mqtt on whichever laptop ends up running
the demo. Not a general-purpose client: no TLS, no auth, no QoS 1/2.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

CONNECT, CONNACK, PUBLISH, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 8, 9, 12, 13, 14


def _encode_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def _string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("!H", len(raw)) + raw


def _packet(packet_type: int, flags: int, body: bytes) -> bytes:
    return bytes([(packet_type << 4) | flags]) + _encode_length(len(body)) + body


class MqttClient:
    def __init__(self, host: str, port: int = 1883, client_id: str = "", keepalive: int = 30,
                 on_message=None, on_state=None):
        self.host, self.port = host, port
        self.client_id = client_id or f"fatigue-dash-{int(time.time() * 1000) % 100000}"
        self.keepalive = keepalive
        self.on_message = on_message or (lambda topic, payload: None)
        self.on_state = on_state or (lambda connected, error: None)
        self._subscriptions = []
        self._sock = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.last_error = None

    def subscribe(self, topic_filter: str) -> None:
        """Remembered and re-sent after every reconnect."""
        self._subscriptions.append(topic_filter)
        if self.connected:
            self._send_subscribe([topic_filter])

    def publish(self, topic: str, payload: bytes) -> bool:
        if not self.connected:
            return False
        try:
            self._send(_packet(PUBLISH, 0, _string(topic) + payload))
            return True
        except OSError:
            return False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mqtt-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def wait_connected(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connected:
                return True
            time.sleep(0.02)
        return self.connected

    # ---- internals ------------------------------------------------------
    def _send(self, data: bytes) -> None:
        with self._send_lock:
            self._sock.sendall(data)

    def _send_subscribe(self, filters) -> None:
        body = struct.pack("!H", 1) + b"".join(_string(f) + b"\x00" for f in filters)
        self._send(_packet(SUBSCRIBE, 0b0010, body))

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return bytes(buf)

    def _read_packet(self):
        header = self._recv_exact(1)[0]
        multiplier, length = 1, 0
        while True:
            byte = self._recv_exact(1)[0]
            length += (byte & 0x7F) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
        return header >> 4, header & 0x0F, self._recv_exact(length) if length else b""

    def _set_state(self, connected: bool, error) -> None:
        self.connected, self.last_error = connected, error
        self.on_state(connected, error)

    def _session(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)
        body = _string("MQTT") + bytes([4, 0x02]) + struct.pack("!H", self.keepalive) + _string(self.client_id)
        self._send(_packet(CONNECT, 0, body))
        packet_type, _, payload = self._read_packet()
        if packet_type != CONNACK or len(payload) < 2 or payload[1] != 0:
            raise ConnectionError(f"broker refused connection (CONNACK {payload!r})")
        if self._subscriptions:
            self._send_subscribe(self._subscriptions)
        self._set_state(True, None)

        ping_every = self.keepalive / 2
        self._sock.settimeout(ping_every)
        last_ping = time.time()
        while not self._stop.is_set():
            try:
                packet_type, flags, payload = self._read_packet()
            except socket.timeout:
                packet_type = None
            if packet_type == PUBLISH:
                topic_len = struct.unpack("!H", payload[:2])[0]
                topic = payload[2:2 + topic_len].decode("utf-8", "replace")
                offset = 2 + topic_len + (2 if (flags >> 1) & 0x03 else 0)  # skip packet id if QoS>0
                self.on_message(topic, payload[offset:])
            if time.time() - last_ping >= ping_every:
                self._send(_packet(PINGREQ, 0, b""))
                last_ping = time.time()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 1.0
            except (OSError, ConnectionError, struct.error) as exc:
                if self._stop.is_set():
                    break
                self._set_state(False, f"{type(exc).__name__}: {exc}")
            finally:
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)
        self._set_state(False, None)
