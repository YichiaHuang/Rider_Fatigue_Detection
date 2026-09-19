"""Validates the Stage B LOPO (Leave-One-Person-Out) methodology using a
minimal numpy-only MLP — NOT tensorflow.

Why: this sandbox's disk (8.2GB total, shared with the base OS image,
guardian_helmet's models/videos, and the IDE server) can't fit a full
`pip install tensorflow` — confirmed by two separate failed attempts, one
of which ran the disk to literally 0 bytes free. See
SESSION_LOG_2026-09-16.md for details.

scripts/train_stage_b.py is the real pipeline (Keras model, int8 TFLite
export) and needs to run on a machine where `pip install tensorflow`
actually succeeds — M1/M2's own laptop, most likely. What this script
proves instead, independent of that dependency: the LOPO split logic,
per-window feature extraction, and accuracy-range reporting are all
correctly wired — swap TinyMLP for train_stage_b.build_model and nothing
else about this flow changes.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from extract_stage_b_features import extract_features, FEATURE_COLUMNS  # noqa: E402


class TinyMLP:
    """Hand-rolled 2-hidden-layer MLP (ReLU/ReLU/sigmoid), trained with
    plain batch gradient descent on binary cross-entropy. Same shape as
    plan.md's 16->8->1 architecture; only exists to validate the LOPO loop
    without a tensorflow dependency."""

    def __init__(self, input_dim: int, hidden1: int = 16, hidden2: int = 8, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0, 0.5, (input_dim, hidden1))
        self.b1 = np.zeros(hidden1)
        self.W2 = rng.normal(0, 0.5, (hidden1, hidden2))
        self.b2 = np.zeros(hidden2)
        self.W3 = rng.normal(0, 0.5, (hidden2, 1))
        self.b3 = np.zeros(1)

    @staticmethod
    def _sigmoid(x):
        return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))

    def forward(self, X):
        z1 = X @ self.W1 + self.b1
        a1 = np.maximum(0, z1)
        z2 = a1 @ self.W2 + self.b2
        a2 = np.maximum(0, z2)
        z3 = a2 @ self.W3 + self.b3
        out = self._sigmoid(z3).ravel()
        return out, (X, z1, a1, z2, a2)

    def train(self, X, y, epochs: int = 300, lr: float = 0.1):
        n = len(X)
        for _ in range(epochs):
            out, (Xc, z1, a1, z2, a2) = self.forward(X)
            d_out = (out - y).reshape(-1, 1) / n  # dL/dz3 for BCE+sigmoid combined

            dW3 = a2.T @ d_out
            db3 = d_out.sum(axis=0)
            da2 = d_out @ self.W3.T
            dz2 = da2 * (z2 > 0)
            dW2 = a1.T @ dz2
            db2 = dz2.sum(axis=0)
            da1 = dz2 @ self.W2.T
            dz1 = da1 * (z1 > 0)
            dW1 = Xc.T @ dz1
            db1 = dz1.sum(axis=0)

            self.W3 -= lr * dW3
            self.b3 -= lr * db3
            self.W2 -= lr * dW2
            self.b2 -= lr * db2
            self.W1 -= lr * dW1
            self.b1 -= lr * db1

    def predict(self, X):
        out, _ = self.forward(X)
        return out


def _confusion(y_true, y_pred):
    return {
        "tp": int(np.sum((y_true == 1) & (y_pred == 1))),
        "fp": int(np.sum((y_true == 0) & (y_pred == 1))),
        "tn": int(np.sum((y_true == 0) & (y_pred == 0))),
        "fn": int(np.sum((y_true == 1) & (y_pred == 0))),
    }


def run_lopo_numpy(df, feature_columns=FEATURE_COLUMNS, label_column="fatigue_label",
                    person_column="person_id", epochs=300, lr=0.1):
    person_ids = sorted(df[person_column].unique())
    results = []
    for held_out in person_ids:
        train_df = df[df[person_column] != held_out]
        val_df = df[df[person_column] == held_out]

        X_train = train_df[feature_columns].to_numpy(dtype="float64")
        y_train = train_df[label_column].to_numpy(dtype="float64")
        X_val = val_df[feature_columns].to_numpy(dtype="float64")
        y_val = val_df[label_column].to_numpy(dtype="float64")

        mean, std = X_train.mean(axis=0), X_train.std(axis=0) + 1e-6
        X_train_n = (X_train - mean) / std
        X_val_n = (X_val - mean) / std

        model = TinyMLP(X_train.shape[1])
        model.train(X_train_n, y_train, epochs=epochs, lr=lr)

        y_pred = (model.predict(X_val_n) > 0.5).astype(int)
        acc = float(np.mean(y_pred == y_val))
        results.append({
            "held_out_person": held_out, "val_accuracy": acc,
            "n_val": len(y_val), "confusion": _confusion(y_val.astype(int), y_pred),
        })
    return results


def make_synthetic_frames(n_people: int = 5, seed: int = 0) -> pd.DataFrame:
    """Self-contained synthetic per-frame data, same schema record_calibration_session.py
    writes, so this test never depends on any file outside this repo."""
    rng = np.random.default_rng(seed)
    states = {
        "normal":      dict(ear=(0.35, 0.03), mar=(0.15, 0.03), head=(2, 3)),
        "eyes_closed": dict(ear=(0.15, 0.03), mar=(0.15, 0.03), head=(2, 3)),
        "yawn":        dict(ear=(0.35, 0.03), mar=(0.55, 0.08), head=(2, 3)),
        "head_drop":   dict(ear=(0.35, 0.03), mar=(0.15, 0.03), head=(25, 4)),
    }
    rows = []
    t = 0.0
    for p in range(n_people):
        person_id = f"p{p + 1}"
        for state, params in states.items():
            for _seg in range(2):  # 2 segments per state, matches SEGMENT_PLAN
                for _ in range(30):  # 30s per segment @ 1Hz
                    ear = float(np.clip(rng.normal(*params["ear"]), 0.05, 0.6))
                    mar = float(np.clip(rng.normal(*params["mar"]), 0.0, 1.2))
                    head = float(rng.normal(*params["head"]))
                    perclos = max(0.0, (0.25 - ear) * 2) if ear < 0.25 else 0.0
                    rows.append(dict(person_id=person_id, session_id="s1", timestamp=t,
                                      label=state, ear=ear, mar=mar, head_pitch_deg=head,
                                      perclos=perclos))
                    t += 1.0
    return pd.DataFrame(rows)


def run():
    frames = make_synthetic_frames(n_people=5)
    print(f"synthetic frames: {len(frames)} rows, {frames['person_id'].nunique()} people")

    features = extract_features(frames, window_sec=3.0, stride_sec=1.0)
    print(f"extracted {len(features)} feature windows, columns: {list(features.columns)}")
    assert len(features) > 0
    assert set(FEATURE_COLUMNS).issubset(features.columns)

    results = run_lopo_numpy(features)
    print(f"\n=== LOPO ({len(results)} folds) ===")
    for r in results:
        print(f"  held out {r['held_out_person']}: acc={r['val_accuracy']:.2f} "
              f"n={r['n_val']} confusion={r['confusion']}")
        cm = r["confusion"]
        assert cm["tp"] + cm["fp"] + cm["tn"] + cm["fn"] == r["n_val"]

    assert len(results) == frames["person_id"].nunique(), "expected one LOPO fold per person"

    accs = [r["val_accuracy"] for r in results]
    mean_acc = float(np.mean(accs))
    print(f"\naccuracy range: {min(accs):.2f} - {max(accs):.2f} (mean {mean_acc:.2f})")
    assert mean_acc > 0.5, "expected better than chance on this cleanly-separable synthetic data"

    print("\nAll Stage B LOPO methodology smoke tests passed "
          "(numpy-only — train_stage_b.py's real Keras model is untested here, needs tensorflow).")


if __name__ == "__main__":
    run()
