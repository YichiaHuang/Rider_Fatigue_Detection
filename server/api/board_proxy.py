"""Same-origin proxy to the board's MJPEG server (plan.md 4.5: "網頁後端可代理
串流，讓瀏覽器使用同來源路徑"). The browser only ever talks to this laptop, so
the projector machine doesn't need its own route to the board, and the mode
token never reaches the page.

Board side: rider/stream_server.py  ->  /status, /stream.mjpg, POST /mode
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

STATUS_TIMEOUT = 4.0
STREAM_CONNECT_TIMEOUT = 15.0  # a relayed Tailscale link can take this long to open
CHUNK = 16 * 1024


class BoardProxy:
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._relay = None
        self._relay_lock = threading.Lock()

    def status(self) -> dict:
        """Never raises: an unreachable board is a normal, displayable state."""
        if not self.base_url:
            return {"reachable": False, "error": "board_stream_url not configured"}
        try:
            with urllib.request.urlopen(f"{self.base_url}/status", timeout=STATUS_TIMEOUT) as resp:
                body = json.loads(resp.read())
            body["reachable"] = True
            return body
        except (OSError, ValueError) as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    def set_mode(self, demo: bool) -> "tuple[int, dict]":
        request = urllib.request.Request(
            f"{self.base_url}/mode", data=json.dumps({"demo": bool(demo)}).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Token": self.token})
        try:
            with urllib.request.urlopen(request, timeout=STATUS_TIMEOUT) as resp:
                body = json.loads(resp.read())
            body["reachable"] = True
            return 200, body
        except urllib.error.HTTPError as exc:
            return exc.code, {"reachable": True, "error": f"board answered {exc.code}"}
        except (OSError, ValueError) as exc:
            return 502, {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---- shared MJPEG relay ---------------------------------------------
    # However many browsers are watching, the board sees ONE viewer. The link
    # to the board is the scarce resource (Wi-Fi hotspot / Tailscale relay);
    # two tabs must not halve each other's frame rate.
    def subscribe_frames(self):
        """Generator of JPEG bytes (latest-frame semantics: a slow browser skips
        frames instead of delaying them). Raises StreamUnavailable before the
        first frame if the board refuses or can't be reached."""
        relay = self._relay_acquire()
        try:
            seq = 0
            while True:
                seq, jpeg = relay.wait_next(seq)
                if jpeg is None:
                    if seq == 0:
                        raise StreamUnavailable(relay.error_code or 502)
                    return  # upstream ended (demo mode switched off / link lost)
                yield jpeg
        finally:
            self._relay_release(relay)

    def _relay_acquire(self):
        with self._relay_lock:
            if self._relay is None or self._relay.finished:
                self._relay = _Relay(self)
            self._relay.clients += 1
            return self._relay

    def _relay_release(self, relay) -> None:
        with self._relay_lock:
            relay.clients -= 1
            if relay.clients <= 0:
                relay.stop()
                if self._relay is relay:
                    self._relay = None

    def open_stream(self):
        """Returns (http_status, content_type, response|None). The caller copies
        response.read(CHUNK) to the browser and closes it."""
        try:
            resp = urllib.request.urlopen(f"{self.base_url}/stream.mjpg", timeout=STREAM_CONNECT_TIMEOUT)
            return 200, resp.headers.get("Content-Type", "multipart/x-mixed-replace"), resp
        except urllib.error.HTTPError as exc:  # 403 = board is in normal mode
            return exc.code, "application/json", None
        except OSError:
            return 502, "application/json", None


class StreamUnavailable(Exception):
    def __init__(self, code: int):
        super().__init__(f"board stream unavailable ({code})")
        self.code = code


class _Relay:
    """One upstream MJPEG connection, fanned out to every subscribed browser."""

    def __init__(self, proxy: BoardProxy):
        self.clients = 0
        self.finished = False
        self.error_code = None
        self._cond = threading.Condition()
        self._jpeg = None
        self._seq = 0
        self._upstream = None
        self._stopping = False
        threading.Thread(target=self._run, args=(proxy,), name="mjpeg-relay", daemon=True).start()

    def wait_next(self, after_seq: int, timeout: float = 20.0):
        """(seq, jpeg) for a frame newer than after_seq; jpeg None = stream over."""
        deadline = time.time() + timeout
        with self._cond:
            while self._seq <= after_seq and not self.finished:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return after_seq, None
                self._cond.wait(remaining)
            if self._seq > after_seq:
                return self._seq, self._jpeg
            return after_seq, None

    def stop(self) -> None:
        self._stopping = True
        upstream = self._upstream
        if upstream is not None:
            try:
                upstream.close()
            except OSError:
                pass

    def _run(self, proxy: BoardProxy) -> None:
        try:
            code, _, upstream = proxy.open_stream()
            if upstream is None:
                self.error_code = code
                return
            self._upstream = upstream
            while not self._stopping:
                length = None
                while True:  # part headers
                    line = upstream.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line and length is not None:
                        break
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                jpeg = upstream.read(length)
                if len(jpeg) < length:
                    return
                with self._cond:
                    self._jpeg = jpeg
                    self._seq += 1
                    self._cond.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            with self._cond:
                self.finished = True
                self._cond.notify_all()
