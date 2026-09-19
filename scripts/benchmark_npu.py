"""CPU vs NPU for the DMS perception stack, on real recorded footage.

Answers two questions with measurements instead of assumptions:
  1. How fast is each model set end to end (analyze() per frame)?
  2. Do the quantized/NPU models agree with the float models the thresholds
     were tuned on (EAR / MAR / head pitch, face detection rate)?

    python3 scripts/benchmark_npu.py /root/guardian_helmet/yawn_eye_test.avi --frames 120
"""
import argparse
import os
import statistics
import sys
import time

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rider"))
from guardian_helmet_bridge import build_dms_frame_source  # noqa: E402


def load_frames(path, count, stride):
    cap, frames, i = cv2.VideoCapture(path), [], 0
    while len(frames) < count:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        i += 1
    cap.release()
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--sets", default="float,ptq,npu")
    args = parser.parse_args()

    frames = load_frames(args.video, args.frames, args.stride)
    h, w = frames[0].shape[:2]
    side = max(h, w)
    print(f"{len(frames)} frames {w}x{h} from {args.video}\n")

    results = {}
    for name in args.sets.split(","):
        t0 = time.time()
        analyzer = build_dms_frame_source(img_size=(side, side), model_set=name)
        load_s = time.time() - t0
        analyzer.analyze(frames[0])  # warm-up (first NPU invoke uploads the command stream)
        times, outputs = [], []
        for frame in frames:
            t = time.perf_counter()
            outputs.append(analyzer.analyze(frame))
            times.append((time.perf_counter() - t) * 1000)
        results[name] = outputs
        found = sum(o is not None for o in outputs)
        print(f"{name:6s} load {load_s:4.1f}s | analyze median {statistics.median(times):6.1f} ms "
              f"p90 {sorted(times)[int(len(times) * 0.9)]:6.1f} ms -> {1000 / statistics.median(times):5.1f} fps "
              f"| faces {found}/{len(frames)}")

    base = results.get("float")
    if base:
        print("\nagreement with float (frames where both found a face): mean |diff|, max |diff|")
        for name, outputs in results.items():
            if name == "float":
                continue
            pairs = [(a, b) for a, b in zip(base, outputs) if a and b]
            line = [f"{name:6s} n={len(pairs)}"]
            for key in ("ear", "mar", "head_pitch_deg"):
                diffs = [abs(a[key] - b[key]) for a, b in pairs]
                if diffs:
                    line.append(f"{key} {statistics.mean(diffs):.3f}/{max(diffs):.3f}")
            print("  ".join(line))
            # What matters for Stage A is whether a frame lands on the same side of
            # each rule's threshold, not the raw difference.
            for label, key, test in (("eyes closed (ear<0.21)", "ear", lambda v: v < 0.21),
                                     ("yawn (mar>0.3)", "mar", lambda v: v > 0.3),
                                     ("head down (pitch>13)", "head_pitch_deg", lambda v: v > 13.0)):
                both = sum(test(a[key]) and test(b[key]) for a, b in pairs)
                only_float = sum(test(a[key]) and not test(b[key]) for a, b in pairs)
                only_this = sum(test(b[key]) and not test(a[key]) for a, b in pairs)
                agree = 100.0 * (len(pairs) - only_float - only_this) / len(pairs)
                print(f"         {label:24s} agree {agree:5.1f}%  both={both} float-only={only_float} {name}-only={only_this}")


if __name__ == "__main__":
    main()
