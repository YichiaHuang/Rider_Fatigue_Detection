#
# Copyright 2020-2023 NXP
# SPDX-License-Identifier: Apache-2.0
#
# Adapted for headless operation (no display attached): supports a single
# image, a video file, or a live camera, and always saves annotated output
# to disk instead of requiring cv2.imshow.
#

import pathlib
import sys
import time
import argparse

import cv2

from face_detection import *
from eye_landmark import EyeMesher
from face_landmark import FaceMesher
from utils import *

MODEL_PATH = pathlib.Path(__file__).resolve().parent.parent / "models"
DETECT_MODEL = "face_detection_short_range.tflite"
LANDMARK_MODEL = "face_landmark.tflite"
EYE_MODEL = "iris_landmark.tflite"

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp'}

parser = argparse.ArgumentParser()
parser.add_argument('-i', '--input', default='/dev/video0',
                     help='camera index/device, or path to an image/video file')
parser.add_argument('-o', '--output', default=None,
                     help='where to save annotated output '
                          '(.jpg for image input, .mp4 for video/camera input)')
parser.add_argument('-n', '--frames', type=int, default=150,
                     help='max frames to process for video/camera input')
parser.add_argument('-d', '--delegate', default='', help='delegate path')
parser.add_argument('--show', action='store_true',
                     help='also try to open a live preview window (requires a display)')
args = parser.parse_args()

is_image = pathlib.Path(str(args.input)).suffix.lower() in IMAGE_EXTS

import re

video_dev_match = re.match(r'^/dev/video(\d+)$', str(args.input))
if args.input.isdigit():
    cap_input = int(args.input)
elif video_dev_match:
    # Force the V4L2 backend with an integer index: OpenCV's GStreamer
    # auto-pipeline for a bare device path is flaky on this platform.
    cap_input = int(video_dev_match.group(1))
else:
    cap_input = args.input

if is_image:
    image = cv2.imread(cap_input)
    if image is None:
        print("Can't read image from", args.input)
        sys.exit(-1)
    ret = True
else:
    if isinstance(cap_input, int):
        cap = cv2.VideoCapture(cap_input, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(cap_input)
    ret, image = cap.read()
    if not ret:
        print("Can't read frame from source", args.input)
        sys.exit(-1)

h, w, _ = image.shape
target_dim = max(w, h)

# instantiate face models
face_detector = FaceDetector(model_path=str(MODEL_PATH / DETECT_MODEL),
                              delegate_path=args.delegate,
                              img_size=(target_dim, target_dim))
face_mesher = FaceMesher(model_path=str(MODEL_PATH / LANDMARK_MODEL), delegate_path=args.delegate)
eye_mesher = EyeMesher(model_path=str(MODEL_PATH / EYE_MODEL), delegate_path=args.delegate)


def draw_face_box(image, bboxes, landmarks, scores):
    for bbox, landmark, score in zip(bboxes.astype(int), landmarks.astype(int), scores):
        image = cv2.rectangle(image, tuple(bbox[:2]), tuple(bbox[2:]), color=(255, 0, 0), thickness=2)
        landmark = landmark.reshape(-1, 2)

        score_label = "{:.2f}".format(score)
        (label_width, label_height), baseline = cv2.getTextSize(score_label,
                                                                  cv2.FONT_HERSHEY_SIMPLEX,
                                                                  fontScale=1.0,
                                                                  thickness=2)
        label_btmleft = bbox[:2].copy() + 10
        label_btmleft[0] += label_width
        label_btmleft[1] += label_height
        cv2.rectangle(image, tuple(bbox[:2]), tuple(label_btmleft), color=(255, 0, 0), thickness=cv2.FILLED)
        cv2.putText(image, score_label, (bbox[0] + 5, label_btmleft[1] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0, color=(255, 255, 255), thickness=2)
    return image


def main(image):
    status = {"faces": 0, "yawning": False, "eyes_closed": False, "face_dir": "N/A"}

    padded_size = [(target_dim - h) // 2, (target_dim - h + 1) // 2,
                   (target_dim - w) // 2, (target_dim - w + 1) // 2]
    padded = cv2.copyMakeBorder(image.copy(), *padded_size, cv2.BORDER_CONSTANT, value=[0, 0, 0])
    padded = cv2.flip(padded, 3)

    bboxes_decoded, landmarks, scores = face_detector.inference(padded)

    mesh_landmarks_inverse = []
    r_vecs, t_vecs = [], []

    for bbox, landmark in zip(bboxes_decoded, landmarks):
        aligned_face, M, angle = face_detector.align(padded, landmark)
        mesh_landmark, mesh_scores = face_mesher.inference(aligned_face)
        mesh_landmark_inverse = face_detector.inverse(mesh_landmark, M)
        mesh_landmarks_inverse.append(mesh_landmark_inverse)

        r_vec, t_vec = face_detector.decode_pose(landmark)
        r_vecs.append(r_vec)
        t_vecs.append(t_vec)

    status["faces"] = len(bboxes_decoded)

    image_show = padded.copy()
    draw_face_box(image_show, bboxes_decoded, landmarks, scores)
    for mesh_landmark, r_vec, t_vec in zip(mesh_landmarks_inverse, r_vecs, t_vecs):
        mouth_ratio = get_mouth_ratio(mesh_landmark, image_show)
        left_box, right_box = get_eye_boxes(mesh_landmark, padded.shape)

        left_eye_img = padded[left_box[0][1]:left_box[1][1], left_box[0][0]:left_box[1][0]]
        right_eye_img = padded[right_box[0][1]:right_box[1][1], right_box[0][0]:right_box[1][0]]
        left_eye_landmarks, left_iris_landmarks = eye_mesher.inference(left_eye_img)
        right_eye_landmarks, right_iris_landmarks = eye_mesher.inference(right_eye_img)
        left_eye_ratio = get_eye_ratio(left_eye_landmarks, image_show, left_box[0])
        right_eye_ratio = get_eye_ratio(right_eye_landmarks, image_show, right_box[0])

        pitch, roll, yaw = get_face_angle(r_vec, t_vec)
        iris_ratio = get_iris_ratio(left_eye_landmarks, right_eye_landmarks)

        status["yawning"] = mouth_ratio > 0.3
        status["eyes_closed"] = left_eye_ratio < 0.2 and right_eye_ratio < 0.2

        cv2.putText(image_show, "Yawning: " + ("Detected" if status["yawning"] else "No"),
                    (padded_size[2] + 70, padded_size[0] + 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 0, 0) if status["yawning"] else (0, 255, 0), 2)
        cv2.putText(image_show, "Eye: " + ("Closed" if status["eyes_closed"] else "Open"),
                    (padded_size[2] + 70, padded_size[0] + 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 0, 0) if status["eyes_closed"] else (0, 255, 0), 2)

        if yaw > 15 and iris_ratio > 1.15:
            status["face_dir"] = "Left"
        elif yaw < -15 and iris_ratio < 0.85:
            status["face_dir"] = "Right"
        elif pitch > 30:
            status["face_dir"] = "Up"
        elif pitch < -13:
            status["face_dir"] = "Down"
        else:
            status["face_dir"] = "Forward"
        cv2.putText(image_show, "Face: " + status["face_dir"],
                    (padded_size[2] + 70, padded_size[0] + 130), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (0, 255, 0) if status["face_dir"] == "Forward" else (255, 0, 0), 2)

    image_show = image_show[padded_size[0]:target_dim - padded_size[1], padded_size[2]:target_dim - padded_size[3]]
    return image_show, status


if is_image:
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image_show, status = main(image)
    result = cv2.cvtColor(image_show, cv2.COLOR_RGB2BGR)
    out_path = args.output or "dms_result.jpg"
    cv2.imwrite(out_path, result)
    print(f"faces={status['faces']} yawning={status['yawning']} "
          f"eyes_closed={status['eyes_closed']} face_dir={status['face_dir']}")
    print("Saved annotated result to", out_path)
else:
    writer = None
    if args.output:
        # mp4/h264 muxing isn't available in this GStreamer install; MJPG-in-AVI works.
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        writer = cv2.VideoWriter(args.output, fourcc, 15.0, (w, h))
        if not writer.isOpened():
            print("Warning: failed to open video writer for", args.output)
            writer = None

    frame_count = 0
    t_start = time.time()
    while ret and frame_count < args.frames:
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image_show, status = main(image_rgb)
        result = cv2.cvtColor(image_show, cv2.COLOR_RGB2BGR)

        frame_count += 1
        print(f"frame={frame_count} faces={status['faces']} yawning={status['yawning']} "
              f"eyes_closed={status['eyes_closed']} face_dir={status['face_dir']}")

        if writer is not None:
            writer.write(result)
        if args.show:
            cv2.imshow('demo', result)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        ret, image = cap.read()

    elapsed = time.time() - t_start
    print(f"Processed {frame_count} frames in {elapsed:.1f}s ({frame_count / max(elapsed, 1e-6):.1f} fps)")

    cap.release()
    if writer is not None:
        writer.release()
        print("Saved annotated video to", args.output)
    if args.show:
        cv2.destroyAllWindows()
