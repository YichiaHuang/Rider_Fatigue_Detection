"""End-to-end validation against H1's real recorded test footage
(/root/guardian_helmet/*.avi) — not a smoke test with synthetic data, this
runs the actual NXP DMS models on actual camera frames through the whole
rider-side stack (DMS analyze -> PERCLOS -> Stage A -> mock IMU/audio).

Only runs on a machine with /root/guardian_helmet checked out.
"""
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "rider"))

from guardian_helmet_bridge import build_dms_frame_source  # noqa: E402
from pipeline import RiderPipeline  # noqa: E402
from stage_a_scoring import StageAConfig  # noqa: E402


def run(video_path: str, sample_every_n_frames: int = 3, rider_id: str = "validation-rider"):
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    frame_source = build_dms_frame_source(img_size=(max(h, w), max(h, w)))
    pipeline = RiderPipeline(rider_id, stage_a_config=StageAConfig())

    print(f"=== {video_path} ({w}x{h} @ {fps:.1f}fps, sampling every {sample_every_n_frames} frames) ===")

    frame_idx = 0
    processed = 0
    faces_found = 0
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        if frame_idx % sample_every_n_frames == 0:
            timestamp = frame_idx / fps
            image_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            analysis = frame_source.analyze(image_rgb)
            if analysis is None:
                print(f"t={timestamp:5.2f}s  no face detected")
            else:
                faces_found += 1
                result = pipeline.process_frame(
                    timestamp, ear=analysis["ear"], mar=analysis["mar"],
                    head_pitch_deg=analysis["head_pitch_deg"],
                )
                print(f"t={timestamp:5.2f}s  ear={analysis['ear']:.3f} mar={analysis['mar']:.3f} "
                      f"head_pitch={analysis['head_pitch_deg']:6.1f}  "
                      f"score={result['score']:6.2f} reasons={result['reasons']}")
            processed += 1
        frame_idx += 1

    cap.release()
    print(f"--- sampled {processed} frames, face detected in {faces_found} ({faces_found / max(processed, 1) * 100:.0f}%) ---\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", nargs="+")
    parser.add_argument("--every", type=int, default=3)
    args = parser.parse_args()
    for v in args.videos:
        run(v, sample_every_n_frames=args.every)
