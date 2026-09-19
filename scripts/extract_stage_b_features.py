"""Sliding-window statistical feature extraction for Stage B (plan.md
section 4.1). Turns per-frame Layer B signals into per-window feature
vectors a small MLP can classify.

Input schema (one row per frame, e.g. from record_calibration_session.py):
  person_id, session_id, timestamp, label, ear, mar, head_pitch_deg, perclos

`label` is the raw calibration-segment label (normal / eyes_closed / yawn /
head_drop — see record_calibration_session.SEGMENT_PLAN). Stage B trains a
binary fatigue classifier, so `binarize_label` collapses that down to
fatigue_label in {0, 1}; the raw label is kept alongside it for error
analysis (which specific state does the model actually miss?).

Windows never span across a (person_id, session_id) boundary — the whole
point is per-person separation for later LOPO evaluation, so blending two
people's frames into one window would corrupt that.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

FATIGUE_LABELS = {"eyes_closed", "yawn", "head_drop"}

RAW_FIELDS = ["person_id", "session_id", "timestamp", "label", "ear", "mar", "head_pitch_deg", "perclos"]

FEATURE_COLUMNS = [
    "ear_mean", "ear_std", "ear_min", "ear_max", "ear_below_ratio",
    "mar_mean", "mar_std", "mar_max", "mar_above_ratio",
    "perclos_mean", "perclos_max",
    "head_pitch_mean", "head_pitch_std", "head_pitch_max", "head_pitch_above_ratio",
    "blink_rate_per_min",
]


def binarize_label(label: str) -> int:
    return 1 if label in FATIGUE_LABELS else 0


def extract_features(frames_df: pd.DataFrame, window_sec: float = 3.0, stride_sec: float = 1.0,
                      ear_closed_threshold: float = 0.21, mar_yawn_threshold: float = 0.6,
                      head_pitch_threshold_deg: float = 20.0) -> pd.DataFrame:
    rows = []

    for (person_id, session_id), group in frames_df.groupby(["person_id", "session_id"]):
        group = group.sort_values("timestamp").reset_index(drop=True)
        t_min, t_max = group["timestamp"].min(), group["timestamp"].max()

        window_start = t_min
        while window_start + window_sec <= t_max + 1e-9:
            window_end = window_start + window_sec
            mask = (group["timestamp"] >= window_start) & (group["timestamp"] < window_end)
            w = group[mask]
            window_start += stride_sec
            if w.empty:
                continue

            ear = w["ear"].to_numpy(dtype=float)
            mar = w["mar"].to_numpy(dtype=float)
            perclos = w["perclos"].to_numpy(dtype=float)
            head = w["head_pitch_deg"].to_numpy(dtype=float)

            closed = ear < ear_closed_threshold
            # count rising edges (open->closed transitions) as blink events,
            # treating a closed first frame as one event already in progress
            edges = np.diff(closed.astype(int), prepend=0)
            blink_count = int(np.sum(edges == 1))

            majority_label = w["label"].mode().iloc[0]

            rows.append({
                "person_id": person_id,
                "session_id": session_id,
                "window_start": window_start - stride_sec,
                "label": majority_label,
                "fatigue_label": binarize_label(majority_label),
                "ear_mean": ear.mean(), "ear_std": ear.std(), "ear_min": ear.min(), "ear_max": ear.max(),
                "ear_below_ratio": float(np.mean(closed)),
                "mar_mean": mar.mean(), "mar_std": mar.std(), "mar_max": mar.max(),
                "mar_above_ratio": float(np.mean(mar > mar_yawn_threshold)),
                "perclos_mean": perclos.mean(), "perclos_max": perclos.max(),
                "head_pitch_mean": head.mean(), "head_pitch_std": head.std(), "head_pitch_max": head.max(),
                "head_pitch_above_ratio": float(np.mean(head > head_pitch_threshold_deg)),
                "blink_rate_per_min": blink_count / window_sec * 60.0,
            })

    return pd.DataFrame(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-csv", required=True, nargs="+", help="one or more per-frame CSVs to concatenate")
    parser.add_argument("--out-csv", default="features_v1.csv")
    parser.add_argument("--window-sec", type=float, default=3.0)
    parser.add_argument("--stride-sec", type=float, default=1.0)
    args = parser.parse_args(argv)

    frames = pd.concat([pd.read_csv(p) for p in args.in_csv], ignore_index=True)
    features = extract_features(frames, window_sec=args.window_sec, stride_sec=args.stride_sec)
    features.to_csv(args.out_csv, index=False)
    print(f"wrote {len(features)} feature windows from {len(frames)} frames to {args.out_csv}")


if __name__ == "__main__":
    main()
