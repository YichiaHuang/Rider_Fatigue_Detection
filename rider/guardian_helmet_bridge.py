"""Bridges this project's RiderPipeline to the real NXP eIQ DMS pipeline in
/root/guardian_helmet/dms — H1's validated Layer A, running on the actual
i.MX93 camera and (optionally) the Ethos-U65 NPU delegate.

This is the real integration path; rider/layer_b_features.py's own
EAR/MAR/head-pose formulas are a fallback for when only raw landmarks are
available, not what feeds this pipeline.

Usage:
    from guardian_helmet_bridge import build_dms_frame_source
    frame_source = build_dms_frame_source(img_size=(640, 640))
    result = frame_source.analyze(image_rgb)   # None if no face detected
    if result is not None:
        pipeline.process_frame(timestamp, ear=result["ear"], mar=result["mar"],
                                head_pitch_deg=result["head_pitch_deg"])
"""
from __future__ import annotations

import os
import sys

GUARDIAN_HELMET_DMS_DIR = "/root/guardian_helmet/dms"


def _ensure_dms_on_path() -> None:
    if GUARDIAN_HELMET_DMS_DIR not in sys.path:
        if not os.path.isdir(GUARDIAN_HELMET_DMS_DIR):
            raise RuntimeError(
                f"{GUARDIAN_HELMET_DMS_DIR} not found — this bridge only works on a "
                "machine with the real guardian_helmet DMS pipeline checked out."
            )
        sys.path.insert(0, GUARDIAN_HELMET_DMS_DIR)


def build_dms_frame_source(img_size, delegate_path: str = ""):
    """delegate_path: pass the Ethos-U65 delegate (e.g. /usr/lib/libethosu_delegate.so)
    for NPU-accelerated inference; empty string runs on CPU (fine for offline
    processing of recorded video/calibration clips)."""
    _ensure_dms_on_path()
    from analyze import DMSFrameAnalyzer  # noqa: E402 (import must follow sys.path insert)
    return DMSFrameAnalyzer(img_size=img_size, delegate_path=delegate_path)
