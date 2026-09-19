"""Live rider pipeline: ONE camera -> DMS inference -> Stage A -> publish,
with the MJPEG demo stream served from the same process and the same frames.

    camera -> LatestFrameSource -+-> DMSFrameAnalyzer -> PERCLOS -> StageAScorer -> MQTT / HTTP
                                 +-> stream_server (demo mode only)

What leaves the board (contract: docs/API.md):
    riders/{id}/fatigue_score  {"timestamp", "score"}      1 Hz, only while perception is valid
    riders/{id}/health         {"timestamp", "perception"}  1 Hz, always
    riders/{id}/demo_state     EAR/MAR/PERCLOS/pitch/reasons — ONLY while demo mode is on
    riders/{id}/vitals         heart rate / pulse wave from the MAX30102 — ONLY while demo mode is on

Run on the board:
    cd /home/fatigue-detection
    python3 rider/live_runner.py                                   # publish to the local broker
    python3 rider/live_runner.py --http http://<laptop>:8000       # HTTP fallback instead of MQTT
    python3 rider/live_runner.py --device /root/guardian_helmet/yawn_eye_test.avi \\
        --rider-id rider-06 --port 8081                            # a second "rider" replaying footage

Scoring runs on every analysed frame (it is time-based, so the frame rate
doesn't change the result); publishing runs on its own fixed clock.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import urllib.request

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frame_source import LatestFrameSource  # noqa: E402
from guardian_helmet_bridge import build_dms_frame_source  # noqa: E402
import overlay  # noqa: E402
from layer_b_features import PerclosTracker  # noqa: E402
from stage_a_scoring import StageAConfig, StageAScorer  # noqa: E402
from stream_server import add_stream_arguments, start_stream_service  # noqa: E402

PERCEPTION_OK = "ok"
PERCEPTION_NO_FACE = "no_face"
PERCEPTION_CAMERA_ERROR = "camera_error"

NO_FACE_AFTER_SEC = 1.5      # a blink-length detection miss is not a fault
# Beyond this head turn the eye/mouth ratios are measured on a profile and are
# not evidence of anything: seen live, a rider looking sideways at a laptop read
# EAR 0.14 ("closed") for minutes and ran the score to the cap. Such frames are
# treated like a gap in the data — neither closed nor open, no accrual, no decay.
MAX_YAW_FOR_FEATURES_DEG = 25.0
NO_FRAME_AFTER_SEC = 2.0


class MqttTransport:
    def __init__(self, host: str, port: int, rider_id: str):
        import paho.mqtt.client as mqtt  # board has paho-mqtt 1.6.1
        self._client = mqtt.Client(client_id=f"rider-{rider_id}-{os.getpid()}")
        self._client.connect_async(host, port)
        self._client.loop_start()  # reconnects by itself
        self.name = f"mqtt://{host}:{port}"

    def send(self, topic: str, payload: dict) -> None:
        self._client.publish(topic, json.dumps(payload), qos=0)


class HttpTransport:
    """POSTs to the dashboard's /api/ingest. Sends from a worker thread so a
    slow or dead link can never stall the inference loop; when the queue is
    full the oldest message is dropped (stale scores are worthless)."""

    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._queue = queue.Queue(maxsize=20)
        self.name = self._base
        self.errors = 0
        threading.Thread(target=self._run, name="http-publisher", daemon=True).start()

    def send(self, topic: str, payload: dict) -> None:
        _, rider_id, kind = topic.split("/")
        item = (f"{self._base}/api/ingest/{rider_id}/{kind}", json.dumps(payload).encode())
        while True:
            try:
                return self._queue.put_nowait(item)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass

    def _run(self) -> None:
        while True:
            url, body = self._queue.get()
            try:
                request = urllib.request.Request(url, data=body, method="POST",
                                                 headers={"Content-Type": "application/json"})
                urllib.request.urlopen(request, timeout=3).read()
            except OSError:
                self.errors += 1


class LiveState:
    """Latest perception result, written by the inference loop and read by the
    publisher. A lock-protected snapshot keeps the two clocks independent."""

    def __init__(self):
        self._lock = threading.Lock()
        self.result = None          # last Stage A result + features
        self.detection = None       # what the stream overlay draws (geometry + numbers), None = no face
        self.last_face_at = None
        self.inference_fps = 0.0
        self.frames = 0
        self.faces = 0

    def set(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)

    def snapshot(self) -> dict:
        with self._lock:
            return {"result": self.result, "last_face_at": self.last_face_at,
                    "inference_fps": self.inference_fps, "frames": self.frames, "faces": self.faces}


def inference_loop(source: LatestFrameSource, analyzer, live: LiveState, args, stop: threading.Event) -> None:
    perclos_tracker = PerclosTracker(window_seconds=args.perclos_window)
    scorer = StageAScorer(StageAConfig())
    last_seq, frames, faces = 0, 0, 0
    errors, last_error_log = 0, 0.0
    window_start, window_frames = time.time(), 0
    min_interval = 1.0 / args.max_inference_fps if args.max_inference_fps else 0.0

    while not stop.is_set():
        tick = time.time()
        seq, frame_ts, frame = source.wait_for_frame(after_seq=last_seq, timeout=1.0)
        if frame is None:
            continue
        last_seq = seq
        try:
            features = analyzer.analyze(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        except Exception as exc:
            # One degenerate frame (collapsed eye crop, NaN landmarks …) must not
            # take the rider offline. Treat it as "no face in this frame": the
            # health message then reports it honestly if it keeps happening.
            features = None
            errors += 1
            if time.time() - last_error_log >= 5.0:
                last_error_log = time.time()
                print(f"[{args.rider_id}] analyze() failed ({errors} so far): {exc!r}", flush=True)
        frames += 1
        window_frames += 1

        turned_away = features is not None and abs(features["yaw_deg"]) > MAX_YAW_FOR_FEATURES_DEG
        if turned_away:
            faces += 1
            previous = live.result
            live.set(last_face_at=frame_ts, frames=frames, faces=faces, detection={
                "at": frame_ts, "geometry": analyzer.last_geometry, "features": features, "turned_away": True,
                "score": previous["score"] if previous else 0.0, "perclos": previous["perclos"] if previous else 0.0,
                "reasons": [], "inference_fps": live.inference_fps})
        elif features is not None:
            faces += 1
            # frame_ts (capture time), not "now": scoring must follow when the
            # eyes were closed, not when inference happened to finish.
            perclos = perclos_tracker.update(frame_ts, features["ear"])
            result = scorer.update(timestamp=frame_ts, ear=features["ear"], mar=features["mar"],
                                   head_pitch_deg=features["head_pitch_deg"], perclos=perclos)
            result.update(ear=features["ear"], mar=features["mar"], perclos=perclos,
                          head_pitch_deg=features["head_pitch_deg"], yaw_deg=features["yaw_deg"])
            detection = {"at": frame_ts, "geometry": analyzer.last_geometry, "features": features,
                         "score": result["score"], "perclos": perclos, "reasons": result["reasons"],
                         "inference_fps": live.inference_fps}
            live.set(result=result, last_face_at=frame_ts, frames=frames, faces=faces, detection=detection)
        else:
            live.set(frames=frames, faces=faces,
                     detection={"at": frame_ts, "geometry": None, "inference_fps": live.inference_fps})

        elapsed = time.time() - window_start
        if elapsed >= 2.0:
            live.set(inference_fps=window_frames / elapsed)
            window_start, window_frames = time.time(), 0
        if min_interval:
            stop.wait(max(0.0, min_interval - (time.time() - tick)))


def perception_status(source: LatestFrameSource, snapshot: dict, now: float) -> str:
    _, frame_ts, frame = source.latest()
    if frame is None or now - frame_ts > NO_FRAME_AFTER_SEC:
        return PERCEPTION_CAMERA_ERROR
    last_face = snapshot["last_face_at"]
    if last_face is None or now - last_face > NO_FACE_AFTER_SEC:
        return PERCEPTION_NO_FACE
    return PERCEPTION_OK


# Stage A's internal reason names -> the names in docs/API.md
REASON_NAMES = {"head_drop": "head_down", "head_drop_visual_only_no_imu": "head_down", "audio_bonus": "audio"}


def publish_loop(source, stream_state, live: LiveState, transports, args, stop: threading.Event,
                 ppg=None) -> None:
    root = f"riders/{args.rider_id}"
    interval = 1.0 / args.publish_hz
    recent_reasons = {}  # reason -> last time it fired; events are instants, the UI refreshes at 1 Hz
    last_log = 0.0
    while not stop.wait(interval):
        now = time.time()
        snap = live.snapshot()
        perception = perception_status(source, snap, now)
        result = snap["result"]

        def send(kind, payload):
            for transport in transports:
                transport.send(f"{root}/{kind}", payload)

        send("health", {"timestamp": now, "perception": perception})
        if perception == PERCEPTION_OK and result is not None:
            send("fatigue_score", {"timestamp": result["timestamp"], "score": result["score"]})
            for reason in result["reasons"]:
                recent_reasons[REASON_NAMES.get(reason, reason)] = now
            if stream_state.demo:  # details follow the same switch as the video
                shown = sorted(r for r, t in recent_reasons.items() if now - t <= 2.0)
                send("demo_state", {
                    "timestamp": result["timestamp"], "ear": round(result["ear"], 3),
                    "mar": round(result["mar"], 3), "perclos": round(result["perclos"], 3),
                    "head_pitch_deg": round(result["head_pitch_deg"], 1),
                    "yaw_deg": round(result["yaw_deg"], 1),
                    "inference_fps": round(snap["inference_fps"], 1), "reasons": shown})

        # Physiological data is more sensitive than the score, so it follows the
        # same operator switch as the video: nothing in normal mode.
        vitals = ppg.latest() if ppg is not None else None
        if vitals is not None and stream_state.demo and now - vitals["timestamp"] <= 3.0:
            send("vitals", vitals)

        if now - last_log >= 5.0:
            last_log = now
            rate = 100.0 * snap["faces"] / snap["frames"] if snap["frames"] else 0.0
            score = result["score"] if result else None
            print(f"[{args.rider_id}] perception={perception} score={score} "
                  f"infer={snap['inference_fps']:.1f}fps face_rate={rate:.0f}% "
                  f"mode={'demo' if stream_state.demo else 'normal'}"
                  + (f" ppg={vitals['quality']} hr={vitals['heart_rate_bpm']}" if vitals else ""), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_stream_arguments(parser)
    parser.add_argument("--rider-id", default="rider-01", help="each board needs its own id")
    parser.add_argument("--mqtt-host", default="127.0.0.1", help='broker; "" disables MQTT')
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--http", default="", metavar="URL", help="also/instead POST to the dashboard")
    parser.add_argument("--publish-hz", type=float, default=1.0)
    parser.add_argument("--model-set", default="hybrid", choices=["hybrid", "float", "ptq", "npu"],
                        help="hybrid (default): float face detector on CPU + mesh/iris on the Ethos-U65 NPU, "
                             "~21 fps. float: everything on CPU, ~10 fps. npu: all three quantized on the NPU — "
                             "fastest, but its detector misses low-angle faces (see scripts/benchmark_npu.py)")
    parser.add_argument("--max-inference-fps", type=float, default=20.0,
                        help="cap, so inference leaves CPU for capture + streaming; 0 = uncapped")
    parser.add_argument("--ppg", default="auto", choices=["auto", "off"],
                        help="MAX30102 heart-rate sensor; auto = use it if it answers on the I2C bus")
    parser.add_argument("--i2c-bus", type=int, default=0)
    parser.add_argument("--no-overlay", action="store_true",
                        help="stream the raw picture without the DMS face box / mesh / status text")
    parser.add_argument("--perclos-window", type=float, default=30.0,
                        help="seconds. DEMO SETTING: 30 so eye closure shows within a demo; 60 for real use")
    args = parser.parse_args()

    source = LatestFrameSource(args.device, width=args.width, height=args.height, fps=args.capture_fps)
    source.start()
    stream_state, server = start_stream_service(args, source)

    transports = []
    if args.mqtt_host:
        transports.append(MqttTransport(args.mqtt_host, args.mqtt_port, args.rider_id))
    if args.http:
        transports.append(HttpTransport(args.http))
    print(f"rider {args.rider_id}: publishing to {[t.name for t in transports] or 'NOWHERE'}; "
          f"perclos window {args.perclos_window:.0f}s", flush=True)

    # The detector works on the frame padded to a square (see analyze.py), so its
    # img_size is that square — NOT (height, width). Passing the raw frame size
    # squashes every box vertically and wrecks alignment.
    side = max(args.height, args.width)
    try:
        analyzer = build_dms_frame_source(img_size=(side, side), model_set=args.model_set)
        print(f"perception: model set '{args.model_set}'"
              + (" (mesh + iris on the Ethos-U65 NPU)" if args.model_set == "hybrid" else ""), flush=True)
    except Exception as exc:
        # NPU busy / delegate or model missing: a slower rider beats no rider.
        print(f"perception: model set '{args.model_set}' failed to load ({exc!r}); falling back to float on CPU",
              flush=True)
        args.model_set = "float"
        analyzer = build_dms_frame_source(img_size=(side, side), model_set="float")
    live, stop = LiveState(), threading.Event()
    if not args.no_overlay:
        stream_state.annotate = lambda frame: overlay.draw(frame, live.detection, time.time())
    ppg = None
    if args.ppg == "auto":
        try:
            from ppg_reader import PpgProcess
            ppg = PpgProcess(args.i2c_bus)
            ppg.start()
            print("ppg: MAX30102 found, heart rate enabled (published in demo mode only)", flush=True)
        except Exception as exc:  # no sensor / rail off / smbus2 missing: carry on without vitals
            print(f"ppg: not available ({exc}); continuing without heart rate", flush=True)
    threading.Thread(target=publish_loop, name="publisher", daemon=True,
                     args=(source, stream_state, live, transports, args, stop, ppg)).start()
    try:
        inference_loop(source, analyzer, live, args, stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if ppg is not None:
            ppg.stop()
        server.shutdown()
        source.stop()


if __name__ == "__main__":
    main()
