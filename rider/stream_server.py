"""MJPEG demo stream + mode switch (plan.md 4.5).

Two modes:
  normal (startup default) - the image endpoints answer 403. The video never
                             leaves the board; this is enforced here, not
                             just hidden in the UI.
  demo (operator turns on) - /stream.mjpg serves the live camera. Nothing is
                             recorded. Turning demo off ends streams that are
                             already open, not just new requests.

Adaptive quality: the link decides the picture, not the other way round. If
viewers can't take frames as fast as they are produced (slow Wi-Fi, Tailscale
relay), the encoder steps down a quality tier — smaller, more compressed
frames — so motion stays fluid; when the link keeps up it steps back up. The
socket send buffer is kept small so stale frames can't queue up as latency.

JPEG encoding happens once per frame in a single encoder thread no matter how
many browsers are connected, and only while demo mode is on AND someone is
watching, so an idle stream costs the inference loop nothing.

Endpoints:
  GET  /             built-in test page (the real dashboard just embeds
                     <img src="http://<board>:8080/stream.mjpg">)
  GET  /stream.mjpg  multipart MJPEG            (demo mode only)
  GET  /snapshot.jpg single JPEG                (demo mode only)
  GET  /status       JSON: mode, camera health, fps, viewers
  POST /mode         {"demo": true|false}       (needs the token if one is set)

Run:
    python3 rider/stream_server.py                       # USB camera, normal mode
    python3 rider/stream_server.py --demo                # start with stream on
    python3 rider/stream_server.py --device /root/guardian_helmet/yawn_eye_test.avi --demo
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frame_source import LatestFrameSource  # noqa: E402

BOUNDARY = "frame"

# (max width, JPEG quality), best first. Rough sizes for a webcam scene:
# ~25 KB, ~12 KB, ~6 KB, ~3 KB per frame.
QUALITY_TIERS = [(640, 70), (480, 55), (320, 45), (240, 35)]
ADAPT_WINDOW_SEC = 3.0
STEP_DOWN_BELOW = 0.6   # viewers received < 60 % of produced frames -> link is the bottleneck
STEP_UP_ABOVE = 0.9
STEP_UP_AFTER_WINDOWS = 4  # be slow to go back up, so quality doesn't flap
SEND_BUFFER_BYTES = 32 * 1024


class StreamState:
    def __init__(self, source: LatestFrameSource, stream_fps: float, jpeg_quality: int,
                 stream_width: int, demo: bool, token: str, adaptive: bool = True):
        self.source = source
        self.stream_fps = stream_fps
        self.token = token
        # Tier 0 is whatever the operator asked for; lower tiers only ever shrink it.
        self.tiers = [(stream_width or 640, jpeg_quality)] + [
            t for t in QUALITY_TIERS[1:] if t[0] < (stream_width or 640) or t[1] < jpeg_quality]
        self.tier = 0
        self.adaptive = adaptive
        # Optional frame_bgr -> frame_bgr hook run before encoding (live_runner
        # uses it to draw the DMS overlay). Must return a NEW image.
        self.annotate = None
        self._delivered = 0

        self._lock = threading.Lock()
        self._demo = demo
        self._viewers = 0

        self._jpeg_cond = threading.Condition()
        self._jpeg = None
        self._jpeg_seq = 0
        self.stream_fps_measured = 0.0

    @property
    def demo(self) -> bool:
        return self._demo

    def set_demo(self, on: bool) -> None:
        with self._lock:
            self._demo = bool(on)
        if not on:
            with self._jpeg_cond:
                self._jpeg = None
                self._jpeg_cond.notify_all()  # wake streaming clients so they hang up

    def add_viewer(self, delta: int) -> None:
        with self._lock:
            self._viewers += delta

    @property
    def viewers(self) -> int:
        return self._viewers

    def wait_for_jpeg(self, after_seq: int, timeout: float = 1.0):
        with self._jpeg_cond:
            self._jpeg_cond.wait_for(lambda: self._jpeg_seq > after_seq or not self._demo, timeout=timeout)
            if self._jpeg_seq > after_seq:
                return self._jpeg_seq, self._jpeg
            return after_seq, None

    def note_delivered(self) -> None:
        with self._lock:
            self._delivered += 1

    def _adapt(self, produced: int, good_windows: int) -> int:
        """Called once per ADAPT_WINDOW_SEC. Returns the new good-window streak."""
        with self._lock:
            delivered, self._delivered = self._delivered, 0
            viewers = max(1, self._viewers)
        if not self.adaptive or produced == 0:
            return 0
        ratio = delivered / (produced * viewers)
        if ratio < STEP_DOWN_BELOW and self.tier < len(self.tiers) - 1:
            self.tier += 1
            sys.stderr.write(f"adaptive: link slow ({ratio:.0%} delivered) -> tier {self.tier} {self.tiers[self.tier]}\n")
            return 0
        if ratio >= STEP_UP_ABOVE and self.tier > 0:
            if good_windows + 1 >= STEP_UP_AFTER_WINDOWS:
                self.tier -= 1
                sys.stderr.write(f"adaptive: link ok -> tier {self.tier} {self.tiers[self.tier]}\n")
                return 0
            return good_windows + 1
        return 0

    def encode(self, frame) -> "bytes | None":
        width, quality = self.tiers[self.tier]
        if self.annotate is not None:
            try:
                frame = self.annotate(frame)
            except Exception as exc:  # a drawing bug must never take the stream down
                sys.stderr.write(f"annotate failed: {exc!r}\n")
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def encoder_loop(self) -> None:
        interval = 1.0 / self.stream_fps
        last_frame_seq = 0
        window_start, window_frames = time.time(), 0
        adapt_start, adapt_frames, good_windows = time.time(), 0, 0
        while True:
            if not self._demo or self._viewers <= 0:
                self.stream_fps_measured = 0.0
                window_start, window_frames = time.time(), 0
                adapt_start, adapt_frames, good_windows = time.time(), 0, 0
                self._delivered = 0
                time.sleep(0.1)
                continue
            tick = time.time()
            seq, _, frame = self.source.wait_for_frame(after_seq=last_frame_seq, timeout=1.0)
            if frame is None:
                continue
            last_frame_seq = seq
            jpeg = self.encode(frame)
            if jpeg is None or not self._demo:
                continue
            with self._jpeg_cond:
                self._jpeg = jpeg
                self._jpeg_seq += 1
                self._jpeg_cond.notify_all()
            window_frames += 1
            adapt_frames += 1
            elapsed = time.time() - window_start
            if elapsed >= 2.0:
                self.stream_fps_measured = window_frames / elapsed
                window_start, window_frames = time.time(), 0
            if time.time() - adapt_start >= ADAPT_WINDOW_SEC:
                good_windows = self._adapt(adapt_frames, good_windows)
                adapt_start, adapt_frames = time.time(), 0
            time.sleep(max(0.0, interval - (time.time() - tick)))

    def status(self) -> dict:
        return {
            "mode": "demo" if self._demo else "normal",
            "viewers": self._viewers,
            "stream_fps": round(self.stream_fps_measured, 1),
            "quality": {"tier": self.tier, "width": self.tiers[self.tier][0],
                        "jpeg_quality": self.tiers[self.tier][1], "adaptive": self.adaptive},
            "camera": self.source.status(),
        }


PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rider camera</title>
<style>
 body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px;max-width:720px}
 #view{width:100%;aspect-ratio:4/3;background:#000;display:flex;align-items:center;justify-content:center;border-radius:8px;overflow:hidden}
 #view img{width:100%;display:block}
 #badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:14px;background:#444}
 #badge.demo{background:#b3261e}
 button{font-size:16px;padding:8px 16px;margin-top:12px}
 pre{color:#aaa;font-size:13px;white-space:pre-wrap}
</style></head><body>
<h2>騎手攝影機 <span id="badge">…</span></h2>
<div id="view"><span id="placeholder">一般模式：影像不離開板子</span></div>
<button id="toggle">…</button>
<pre id="status"></pre>
<script>
const view=document.getElementById('view'),badge=document.getElementById('badge'),
      btn=document.getElementById('toggle'),st=document.getElementById('status');
const token=new URLSearchParams(location.search).get('token')||'';
let demo=null;
function render(on){
  if(on===demo)return; demo=on;
  badge.textContent=on?'展示串流中':'一般模式'; badge.className=on?'demo':'';
  btn.textContent=on?'關閉展示模式':'開啟展示模式';
  view.innerHTML=on?'<img src="/stream.mjpg?t='+Date.now()+'">':'<span>一般模式：影像不離開板子</span>';
}
async function poll(){
  try{const s=await (await fetch('/status')).json(); render(s.mode==='demo');
      st.textContent=JSON.stringify(s,null,1);}
  catch(e){st.textContent='連線中斷: '+e;}
}
btn.onclick=async()=>{
  const r=await fetch('/mode',{method:'POST',headers:{'Content-Type':'application/json','X-Token':token},
                              body:JSON.stringify({demo:!demo})});
  if(!r.ok)alert('切換失敗: '+r.status); poll();
};
poll(); setInterval(poll,1000);
</script></body></html>
"""


def make_handler(state: StreamState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # keep the console quiet for per-frame traffic
            if not self.path.startswith(("/status", "/stream")):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/status":
                self._json(200, state.status())
            elif path == "/snapshot.jpg":
                self._snapshot()
            elif path == "/stream.mjpg":
                self._stream()
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/mode":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if state.token and self.headers.get("X-Token") != state.token:
                return self._json(403, {"error": "bad token"})
            try:
                want = json.loads(raw)["demo"]
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": 'expected {"demo": true|false}'})
            state.set_demo(bool(want))
            sys.stderr.write(f"mode -> {'demo' if state.demo else 'normal'} (by {self.address_string()})\n")
            self._json(200, state.status())

        def _snapshot(self):
            if not state.demo:
                return self._json(403, {"error": "normal mode: image endpoints are off"})
            _, _, frame = state.source.latest()
            jpeg = state.encode(frame) if frame is not None else None
            if jpeg is None:
                return self._json(503, {"error": "no camera frame", "camera": state.source.status()})
            self._send(200, jpeg, "image/jpeg")

        def _stream(self):
            if not state.demo:
                return self._json(403, {"error": "normal mode: image endpoints are off"})
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            # Small send buffer: a slow link then blocks the write (so we skip to the
            # newest frame) instead of silently queueing seconds of stale video.
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER_BYTES)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            state.add_viewer(+1)
            seq = 0
            try:
                while state.demo:
                    seq, jpeg = state.wait_for_jpeg(seq, timeout=1.0)
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                        + jpeg + b"\r\n")
                    state.note_delivered()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                state.add_viewer(-1)

    return Handler


def add_stream_arguments(parser: argparse.ArgumentParser) -> None:
    """Camera + stream flags, shared with rider/live_runner.py."""
    parser.add_argument("--device", default="auto", help='"auto" (USB camera), /dev/videoN, or a video file')
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--capture-fps", type=int, default=30)
    parser.add_argument("--stream-fps", type=float, default=10.0, help="plan.md 4.5: start at 5-10")
    parser.add_argument("--stream-width", type=int, default=640, help="downscale wider frames before encoding")
    parser.add_argument("--jpeg-quality", type=int, default=70)
    parser.add_argument("--no-adaptive", action="store_true",
                        help="keep the requested width/quality even when viewers can't keep up")
    parser.add_argument("--demo", action="store_true", help="start in demo mode (default: normal, stream off)")
    parser.add_argument("--token", default=os.environ.get("STREAM_TOKEN", ""),
                        help="if set, POST /mode must send it as X-Token")


def start_stream_service(args, source: LatestFrameSource):
    """Start the encoder + HTTP server on background threads for an already
    running frame source. Returns (state, server). The caller owns the camera
    — this is how the live runner shares ONE capture between inference and
    the stream (plan.md 4.5 item 1)."""
    state = StreamState(source, stream_fps=args.stream_fps, jpeg_quality=args.jpeg_quality,
                        stream_width=args.stream_width, demo=args.demo, token=args.token,
                        adaptive=not args.no_adaptive)
    threading.Thread(target=state.encoder_loop, name="jpeg-encoder", daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="stream-http", daemon=True).start()
    print(f"stream server on http://{args.host}:{args.port}/  mode={'demo' if args.demo else 'normal'}", flush=True)
    return state, server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_stream_arguments(parser)
    args = parser.parse_args()

    source = LatestFrameSource(args.device, width=args.width, height=args.height, fps=args.capture_fps)
    source.start()
    _, server = start_stream_service(args, source)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        source.stop()


if __name__ == "__main__":
    main()
