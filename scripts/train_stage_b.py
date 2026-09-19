"""Stage B: trains the small MLP fatigue classifier and exports an int8
TFLite model for the Ethos-U65 NPU (plan.md section 4.1 Stage B).

Requires `tensorflow` — this is a training-time-only dependency (not part
of rider/ or platform/, which stay dependency-light for the device side).
No fallback/workaround if it isn't installed: pip install tensorflow first.

Evaluation is Leave-One-Person-Out (LOPO), not a single train/val split —
with only a handful of calibration subjects, sliding-window features are
highly autocorrelated within a person, so a single split can look
deceptively good or bad depending on luck. LOPO gives an accuracy range
across every held-out person instead of one number (see plan.md's
"資料量" discussion and SESSION_LOG_2026-09-16.md).
"""
from __future__ import annotations

import argparse
from typing import Dict, List

import numpy as np
import pandas as pd
import tensorflow as tf

from extract_stage_b_features import FEATURE_COLUMNS


def build_model(X_train: np.ndarray, l2: float = 1e-3) -> tf.keras.Model:
    """Normalization is baked into the model (via an adapted Normalization
    layer) so the exported TFLite model takes raw feature values directly —
    no separate scaler object for the device side to carry around."""
    normalizer = tf.keras.layers.Normalization(axis=-1)
    normalizer.adapt(X_train)
    reg = tf.keras.regularizers.l2(l2)

    model = tf.keras.Sequential([
        tf.keras.Input(shape=(X_train.shape[1],)),
        normalizer,
        tf.keras.layers.Dense(16, activation="relu", kernel_regularizer=reg),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(8, activation="relu", kernel_regularizer=reg),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def _confusion(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, int]:
    return {
        "tp": int(np.sum((y_true == 1) & (y_pred == 1))),
        "fp": int(np.sum((y_true == 0) & (y_pred == 1))),
        "tn": int(np.sum((y_true == 0) & (y_pred == 0))),
        "fn": int(np.sum((y_true == 1) & (y_pred == 0))),
    }


def run_lopo(df: pd.DataFrame, feature_columns: List[str] = FEATURE_COLUMNS,
             label_column: str = "fatigue_label", person_column: str = "person_id",
             epochs: int = 30, batch_size: int = 16, verbose: int = 0) -> List[dict]:
    person_ids = sorted(df[person_column].unique())
    if len(person_ids) < 2:
        raise ValueError(f"LOPO needs at least 2 people, got {len(person_ids)}: {person_ids}")

    results = []
    for held_out in person_ids:
        train_df = df[df[person_column] != held_out]
        val_df = df[df[person_column] == held_out]

        X_train = train_df[feature_columns].to_numpy(dtype="float32")
        y_train = train_df[label_column].to_numpy(dtype="float32")
        X_val = val_df[feature_columns].to_numpy(dtype="float32")
        y_val = val_df[label_column].to_numpy(dtype="float32")

        model = build_model(X_train)
        model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size, verbose=verbose)

        y_pred = (model.predict(X_val, verbose=0).ravel() > 0.5).astype(int)
        acc = float(np.mean(y_pred == y_val))

        results.append({
            "held_out_person": held_out,
            "val_accuracy": acc,
            "n_val": len(y_val),
            "confusion": _confusion(y_val.astype(int), y_pred),
        })

    return results


def quantize_and_export(model: tf.keras.Model, representative_X: np.ndarray, out_path: str) -> str:
    def representative_dataset():
        n = min(200, len(representative_X))
        for i in range(n):
            yield [representative_X[i:i + 1].astype(np.float32)]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_model = converter.convert()
    with open(out_path, "wb") as f:
        f.write(tflite_model)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-csv", required=True)
    parser.add_argument("--out-model", default="fatigue_model_v1_int8.tflite")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--skip-export", action="store_true",
                         help="only run LOPO evaluation, skip training the final model / TFLite export")
    args = parser.parse_args(argv)

    df = pd.read_csv(args.features_csv)
    print(f"loaded {len(df)} windows from {df['person_id'].nunique()} people: "
          f"{sorted(df['person_id'].unique())}")

    lopo_results = run_lopo(df, epochs=args.epochs)
    accs = [r["val_accuracy"] for r in lopo_results]
    print("\n=== LOPO results (this is the number for the Friday go/no-go, not a single split) ===")
    for r in lopo_results:
        print(f"  held out {r['held_out_person']}: acc={r['val_accuracy']:.2f} "
              f"n={r['n_val']} confusion={r['confusion']}")
    print(f"accuracy range: {min(accs):.2f} - {max(accs):.2f} (mean {np.mean(accs):.2f})")

    if args.skip_export:
        return

    print("\ntraining final model on all data for export...")
    X_all = df[FEATURE_COLUMNS].to_numpy(dtype="float32")
    y_all = df["fatigue_label"].to_numpy(dtype="float32")
    model = build_model(X_all)
    model.fit(X_all, y_all, epochs=args.epochs, batch_size=16, verbose=0)

    out_path = quantize_and_export(model, X_all, args.out_model)
    print(f"wrote quantized model to {out_path}")
    print("next: `python -m ethosu.vela --accelerator-config ethos-u65-256 "
          f"--optimise Performance {out_path}` to get the NPU-deployable *_vela.tflite")


if __name__ == "__main__":
    main()
