"""Pure-logic smoke test for record_calibration_session.py: drives a fake
pipeline and a fake clock (no real 30-second waits, no camera) through a
full session and checks the CSV rows land with the right labels and land
in the right segment order.
"""
import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from record_calibration_session import (  # noqa: E402
    FrameSource, SEGMENT_PLAN, SEGMENT_SECONDS, run_session,
)


def run():
    fake_time = {"t": 0.0}

    def fake_clock():
        return fake_time["t"]

    def fake_sleep(seconds):
        fake_time["t"] += seconds

    frame_counter = {"n": 0}

    def get_frame():
        frame_counter["n"] += 1
        t = fake_clock()
        return (t, 0.3, 0.2, 5.0, 0.05)

    frame_source = FrameSource(get_frame=get_frame)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "calib.csv")
        run_session("test-person", "s1", out_path, frame_source, dry_run=False,
                    clock=fake_clock, sleep=fake_sleep)

        with open(out_path) as f:
            rows = list(csv.DictReader(f))

        expected_rows = len(SEGMENT_PLAN) * SEGMENT_SECONDS
        print(f"共寫入 {len(rows)} 行（預期 {expected_rows} 行：{len(SEGMENT_PLAN)} 段 x {SEGMENT_SECONDS} 秒）")
        assert len(rows) == expected_rows, f"expected {expected_rows} rows, got {len(rows)}"

        # each segment is SEGMENT_SECONDS consecutive rows in SEGMENT_PLAN order —
        # labels repeat back-to-back by design (2 segments per state), so we
        # chunk by fixed segment length rather than de-duping adjacent labels.
        for i, expected_label in enumerate(SEGMENT_PLAN):
            chunk = rows[i * SEGMENT_SECONDS:(i + 1) * SEGMENT_SECONDS]
            labels_in_chunk = {row["label"] for row in chunk}
            assert labels_in_chunk == {expected_label}, (
                f"segment {i} expected all '{expected_label}', got {labels_in_chunk}"
            )
        print("段落順序核對 OK：", SEGMENT_PLAN)

        for row in rows:
            assert row["person_id"] == "test-person"
            assert row["session_id"] == "s1"
            assert row["label"] in SEGMENT_PLAN

    print("\nAll record-session smoke tests passed.")


if __name__ == "__main__":
    run()
