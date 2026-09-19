"""Single shared camera capture (plan.md 4.5 item 1).

Exactly one LatestFrameSource opens the camera; inference, calibration
recording and the MJPEG stream all read from it instead of each opening
/dev/videoN themselves. Only the newest frame is kept (bounded buffer of 1),
so a slow consumer drops stale frames rather than back-pressuring capture.

Usage:
    src = LatestFrameSource("auto", width=640, height=480, fps=30)
    src.start()
    seq, ts, frame_bgr = src.wait_for_frame(after_seq=last_seq, timeout=1.0)
"""
from __future__ import annotations

import glob
import os
import threading
import time

import cv2

RETRY_SECONDS = 2.0


def find_camera_device() -> "str | None":
    """USB webcams get a stable /dev/v4l/by-id path; the bare /dev/video0-1
    nodes on this board are the i.MX93 ISI (MIPI CSI), which has no sensor
    attached, so they are never picked automatically."""
    for path in sorted(glob.glob("/dev/v4l/by-id/*index0")):
        return os.path.realpath(path)
    return None


class LatestFrameSource:
    def __init__(self, device: str = "auto", width: int = 640, height: int = 480,
                 fps: int = 30, loop_file: bool = True):
        """device: "auto", a /dev/videoN path, or a video file (looped, paced
        at its own frame rate — for testing without a camera)."""
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.loop_file = loop_file

        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._ts = 0.0
        self._stop = threading.Event()
        self._thread = None

        self.opened_device = None
        self.last_error = "not started"
        self.capture_fps = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="frame-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def latest(self):
        """(seq, timestamp, frame_bgr) — frame is None until the first capture."""
        with self._cond:
            return self._seq, self._ts, self._frame

    def wait_for_frame(self, after_seq: int = 0, timeout: float = 1.0):
        """Block until a frame newer than after_seq exists, or timeout.
        Returns (seq, timestamp, frame_bgr); frame is None on timeout."""
        with self._cond:
            if self._cond.wait_for(lambda: self._seq > after_seq, timeout=timeout):
                return self._seq, self._ts, self._frame
            return after_seq, 0.0, None

    def status(self) -> dict:
        seq, ts, frame = self.latest()
        age = time.time() - ts if frame is not None else None
        return {
            "device": self.opened_device,
            "ok": frame is not None and age is not None and age < 2.0,
            "frame_age_sec": round(age, 3) if age is not None else None,
            "capture_fps": round(self.capture_fps, 1),
            "frames": seq,
            "error": self.last_error,
        }

    def _open(self):
        device = find_camera_device() if self.device == "auto" else self.device
        if device is None:
            self.last_error = "no USB camera found under /dev/v4l/by-id"
            return None, False
        is_file = not device.startswith("/dev/")
        if is_file:
            cap = cv2.VideoCapture(device)
        else:
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            self.last_error = f"cannot open {device}"
            return None, False
        self.opened_device = device
        self.last_error = None
        return cap, is_file

    def _run(self) -> None:
        while not self._stop.is_set():
            cap, is_file = self._open()
            if cap is None:
                self._stop.wait(RETRY_SECONDS)
                continue
            frame_interval = 0.0
            if is_file:
                file_fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
                frame_interval = 1.0 / file_fps
            window_start, window_frames = time.time(), 0
            reopen_now = False
            while not self._stop.is_set():
                loop_start = time.time()
                ok, frame = cap.read()
                if not ok:
                    # Looping a file means reopening it: this board's GStreamer
                    # backend can't seek (CAP_PROP_POS_FRAMES is a no-op).
                    reopen_now = is_file and self.loop_file
                    if not reopen_now:
                        self.last_error = f"read failed on {self.opened_device}"
                    break
                with self._cond:
                    self._frame = frame
                    self._seq += 1
                    self._ts = time.time()
                    self._cond.notify_all()
                window_frames += 1
                elapsed = time.time() - window_start
                if elapsed >= 2.0:
                    self.capture_fps = window_frames / elapsed
                    window_start, window_frames = time.time(), 0
                if frame_interval:
                    self._stop.wait(max(0.0, frame_interval - (time.time() - loop_start)))
            cap.release()
            if reopen_now:
                continue
            self.opened_device = None
            self._stop.wait(RETRY_SECONDS)
