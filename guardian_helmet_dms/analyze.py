#
# Continuous-value variant of main.py's per-frame inference.
#
# main.py already runs the full NXP eIQ DMS pipeline (face detect -> align
# -> face mesh -> eye mesh -> pose) but only exposes pre-thresholded
# booleans (yawning, eyes_closed). The fatigue-detection project's Stage A
# scorer needs continuous ratios so it can run its own sliding-window
# PERCLOS / leaky-integrator logic instead of an instantaneous yes/no flag.
#
# Deliberately additive: does not import or modify main.py, so the existing
# CLI tool (`python main.py -i ...`) is untouched. Reuses the same model
# classes and utils.* ratio functions main.py already validated against
# real camera footage — no new landmark indices or thresholds invented here.
#
import pathlib

import cv2
import numpy as np

from face_detection import FaceDetector
from face_landmark import FaceMesher
from eye_landmark import EyeMesher
from utils import get_mouth_ratio, get_eye_ratio, get_eye_boxes, get_face_angle, get_iris_ratio

MODEL_PATH = pathlib.Path(__file__).resolve().parent.parent / "models"
DETECT_MODEL = "face_detection_short_range.tflite"
LANDMARK_MODEL = "face_landmark.tflite"
EYE_MODEL = "iris_landmark.tflite"


def _safe_iris_ratio(left_eye_landmarks, right_eye_landmarks) -> float:
    """utils.get_iris_ratio divides by the right eye's width, which is 0 when
    that eye's landmarks collapse (tiny/occluded eye crop). 1.0 = "no left/right
    evidence", so the Face: Left/Right overlay test simply doesn't fire."""
    try:
        ratio = float(get_iris_ratio(left_eye_landmarks, right_eye_landmarks))
    except ZeroDivisionError:
        return 1.0
    return ratio if np.isfinite(ratio) else 1.0


class DMSFrameAnalyzer:
    def __init__(self, img_size, delegate_path: str = ""):
        """img_size: (height, width) of the frames you'll pass to analyze()."""
        self.face_detector = FaceDetector(model_path=str(MODEL_PATH / DETECT_MODEL),
                                           delegate_path=delegate_path, img_size=img_size)
        self.face_mesher = FaceMesher(model_path=str(MODEL_PATH / LANDMARK_MODEL), delegate_path=delegate_path)
        self.eye_mesher = EyeMesher(model_path=str(MODEL_PATH / EYE_MODEL), delegate_path=delegate_path)
        # Geometry of the most recent analyze() call, in the ORIGINAL frame's pixel
        # coordinates (padding and the detector's mirror flip undone), for drawing
        # a main.py-style overlay on a live stream. None when no face was found.
        #   {"bbox": (x1, y1, x2, y2), "det_score": float, "mesh": Nx2 int array,
        #    "eyes": [Nx2, Nx2], "irises": [5x2, 5x2]}
        self.last_geometry = None

    def analyze(self, image_rgb: np.ndarray) -> "dict | None":
        """image_rgb: HxWx3 RGB array, one video frame. Returns None if no
        face is detected, else a dict of continuous signals for the single
        (MAX_FACE_NUM=1) tracked face:

          mar               - mouth height/width ratio (yawn if > ~0.3, per main.py)
          ear               - mean(left_eye_ratio, right_eye_ratio); smaller = more closed
                              (main.py flags eyes_closed when BOTH ratios < 0.2)
          left_eye_ratio / right_eye_ratio - the two ratios ear is averaged from
          pitch_deg         - raw pose pitch, NXP/OpenCV convention: negative = head down
          head_pitch_deg    - -pitch_deg, i.e. positive = head down, matching
                              fatigue-detection's rider/layer_b_features.py and
                              rider/imu_reader.py sign convention
        """
        h, w, _ = image_rgb.shape
        target_dim = max(w, h)
        padded_size = [(target_dim - h) // 2, (target_dim - h + 1) // 2,
                       (target_dim - w) // 2, (target_dim - w + 1) // 2]
        padded = cv2.copyMakeBorder(image_rgb.copy(), *padded_size, cv2.BORDER_CONSTANT, value=[0, 0, 0])
        padded = cv2.flip(padded, 3)

        self.last_geometry = None
        bboxes_decoded, landmarks, scores = self.face_detector.inference(padded)
        if len(bboxes_decoded) == 0:
            return None

        bbox, landmark = bboxes_decoded[0], landmarks[0]
        aligned_face, M, angle = self.face_detector.align(padded, landmark)
        mesh_landmark, mesh_score = self.face_mesher.inference(aligned_face)
        mesh_landmark_inverse = self.face_detector.inverse(mesh_landmark, M)

        r_vec, t_vec = self.face_detector.decode_pose(landmark)
        pitch, roll, yaw = get_face_angle(r_vec, t_vec)

        mouth_ratio = get_mouth_ratio(mesh_landmark_inverse, padded)

        left_box, right_box = get_eye_boxes(mesh_landmark_inverse, padded.shape)
        left_eye_img = padded[left_box[0][1]:left_box[1][1], left_box[0][0]:left_box[1][0]]
        right_eye_img = padded[right_box[0][1]:right_box[1][1], right_box[0][0]:right_box[1][0]]
        if left_eye_img.size == 0 or right_eye_img.size == 0:
            return None

        left_eye_landmarks, left_iris = self.eye_mesher.inference(left_eye_img)
        right_eye_landmarks, right_iris = self.eye_mesher.inference(right_eye_img)
        left_eye_ratio = get_eye_ratio(left_eye_landmarks, padded, left_box[0])
        right_eye_ratio = get_eye_ratio(right_eye_landmarks, padded, right_box[0])

        def to_frame(points):
            """padded+mirrored coords -> original frame coords"""
            pts = np.asarray(points, dtype=np.float32)[:, :2].copy()
            pts[:, 0] = (target_dim - 1 - pts[:, 0]) - padded_size[2]
            pts[:, 1] = pts[:, 1] - padded_size[0]
            return pts.astype(np.int32)

        corners = to_frame([bbox[:2], bbox[2:4]])
        self.last_geometry = {
            "bbox": (int(corners[:, 0].min()), int(corners[:, 1].min()),
                     int(corners[:, 0].max()), int(corners[:, 1].max())),
            "det_score": float(scores[0]),
            "mesh": to_frame(mesh_landmark_inverse),
            "eyes": [to_frame(left_eye_landmarks[:, :2] + np.array(left_box[0])),
                     to_frame(right_eye_landmarks[:, :2] + np.array(right_box[0]))],
            "irises": [to_frame(left_iris[:, :2] + np.array(left_box[0])),
                       to_frame(right_iris[:, :2] + np.array(right_box[0]))],
        }

        return {
            "yaw_deg": float(yaw),
            "roll_deg": float(roll),
            "iris_ratio": _safe_iris_ratio(left_eye_landmarks, right_eye_landmarks),
            "mar": float(mouth_ratio),
            "ear": float((left_eye_ratio + right_eye_ratio) / 2.0),
            "left_eye_ratio": float(left_eye_ratio),
            "right_eye_ratio": float(right_eye_ratio),
            "pitch_deg": float(pitch),
            "head_pitch_deg": float(-pitch),
        }
