"""Draws the DMS demo view onto a frame for the demo stream: face box + score,
468-point mesh, eye contours + irises, and the same three status lines as
H1's main.py (Yawning / Eye / Face), plus this project's numbers.

Purpose: let the operator SEE whether the face is being picked up at the
current camera angle. It only ever runs in demo mode, on the frames that are
already being streamed — nothing extra leaves the board because of it.

Thresholds for the status lines are main.py's, on purpose: the overlay should
agree with the demo the team already knows. Stage A has its own config.
"""
from __future__ import annotations

import cv2

GREEN, RED, BLUE, WHITE, YELLOW, BLACK = (0, 255, 0), (0, 0, 255), (255, 80, 0), (255, 255, 255), (0, 220, 255), (0, 0, 0)
FONT = cv2.FONT_HERSHEY_SIMPLEX
STALE_AFTER_SEC = 0.6  # older detections are not drawn: a box that lags the face is worse than none


def _text(image, text, origin, color, scale=0.6):
    cv2.putText(image, text, origin, FONT, scale, BLACK, 4, cv2.LINE_AA)  # outline: readable on any background
    cv2.putText(image, text, origin, FONT, scale, color, 1 if scale < 0.6 else 2, cv2.LINE_AA)


def face_direction(f: dict) -> str:
    if f["yaw_deg"] > 15 and f["iris_ratio"] > 1.15:
        return "Left"
    if f["yaw_deg"] < -15 and f["iris_ratio"] < 0.85:
        return "Right"
    if f["pitch_deg"] > 30:
        return "Up"
    if f["pitch_deg"] < -13:
        return "Down"
    return "Forward"


def draw(frame_bgr, detection: "dict | None", now: float):
    """detection: {"at", "geometry", "features", "score", "perclos", "reasons", "inference_fps"}
    as kept by live_runner, or None. Returns a new image; the input frame is
    shared with the inference thread and must not be modified."""
    image = frame_bgr.copy()
    h, w = image.shape[:2]

    fresh = detection is not None and detection.get("geometry") is not None \
        and now - detection["at"] <= STALE_AFTER_SEC
    if not fresh:
        _text(image, "NO FACE", (16, 40), RED, 1.0)
        if detection is not None and detection.get("inference_fps") is not None:
            _text(image, f"infer {detection['inference_fps']:.1f} fps", (16, h - 14), WHITE, 0.5)
        return image

    g, f = detection["geometry"], detection["features"]
    # 468 points in one vectorised write (2x2 px each) instead of 468 cv2.circle calls
    mesh = g["mesh"]
    inside = (mesh[:, 0] >= 0) & (mesh[:, 0] < w - 1) & (mesh[:, 1] >= 0) & (mesh[:, 1] < h - 1)
    xs, ys = mesh[inside, 0], mesh[inside, 1]
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        image[ys + dy, xs + dx] = YELLOW
    for eye in g["eyes"]:
        cv2.polylines(image, [eye[:16].reshape(-1, 1, 2)], True, GREEN, 1, cv2.LINE_AA)
    for iris in g["irises"]:
        center = tuple(int(v) for v in iris[0])
        radius = max(2, int(cv2.norm(iris[1].astype(float) - iris[3].astype(float)) / 2))
        cv2.circle(image, center, radius, BLUE, 1, cv2.LINE_AA)

    x1, y1, x2, y2 = g["bbox"]
    cv2.rectangle(image, (x1, y1), (x2, y2), BLUE, 2)
    _text(image, f"{g['det_score']:.2f}", (x1 + 4, max(18, y1 - 6)), WHITE)

    yawning = f["mar"] > 0.3
    eyes_closed = f["left_eye_ratio"] < 0.2 and f["right_eye_ratio"] < 0.2
    direction = face_direction(f)
    _text(image, "Yawning: " + ("Detected" if yawning else "No"), (16, 30), RED if yawning else GREEN, 0.7)
    _text(image, "Eye: " + ("Closed" if eyes_closed else "Open"), (16, 58), RED if eyes_closed else GREEN, 0.7)
    _text(image, "Face: " + direction, (16, 86), GREEN if direction == "Forward" else RED, 0.7)

    if detection.get("turned_away"):
        _text(image, f"TURNED AWAY (yaw {f['yaw_deg']:+.0f}): eyes/mouth not scored", (16, 114), YELLOW, 0.6)
    lines = [f"EAR {f['ear']:.2f}  MAR {f['mar']:.2f}  yaw {f['yaw_deg']:+.0f}",
             f"PERCLOS {detection['perclos']:.2f}  pitch {f['head_pitch_deg']:+.0f}",
             f"score {detection['score']:.1f}  {' '.join(detection['reasons'])}"]
    for i, line in enumerate(lines):
        _text(image, line, (16, h - 14 - 22 * (len(lines) - 1 - i)), WHITE, 0.55)
    _text(image, f"{detection['inference_fps']:.1f} fps", (w - 90, 24), WHITE, 0.5)
    return image
