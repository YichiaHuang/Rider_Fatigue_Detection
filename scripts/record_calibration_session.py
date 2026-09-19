"""Guides one calibration recording session (plan.md section 4.1 self-built
data) and, once rider/pipeline.py is wired to real Layer A output, logs
per-frame features straight into the Stage B training schema — no manual
video labeling afterward.

Recording priority is headcount over per-person duration (see the
"資料量" discussion in the plan): sliding-window features are highly
autocorrelated within one person, so the number of distinct people is
what actually buys generalization, not extra minutes per person.

Segment plan: 4 states x 2 segments x 30s each, with a buffer gap between
segments. The on-screen guide text matches what the operator (H3) should
read aloud before each segment starts.

Usage:
  # dry run before Layer A is wired in - just rehearses timing/prompts
  python record_calibration_session.py --person-id alice --dry-run

  # real session, once `pipeline` can be constructed with a live frame source
  python record_calibration_session.py --person-id alice --session-id s1 \
      --out calibration_alice_s1.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

SEGMENT_SECONDS = 30
GAP_SECONDS = 7

GUIDANCE = {
    "normal": "維持平常騎車看前方的姿勢，自然眨眼就好，不用刻意做什麼。",
    "eyes_closed": "想像已經騎了三個小時，眼皮很重。慢慢閉上眼睛撐住兩三秒，再慢慢張開，重複幾次，不要從頭到尾閉著不動。",
    "yawn": "打一個自然的哈欠，嘴巴張到最大停一下再閉上。這段時間內打 3-4 次，中間正常休息。",
    "head_drop": "想像快撐不住要睡著，頭慢慢往下垂，停兩三秒，再抬起來，重複幾次。",
}

SEGMENT_PLAN: List[str] = [
    "normal", "eyes_closed", "eyes_closed", "yawn", "yawn",
    "head_drop", "head_drop", "normal",
]

CSV_FIELDS = ["person_id", "session_id", "timestamp", "label", "ear", "mar", "head_pitch_deg", "perclos"]


@dataclass
class FrameSource:
    """Adapter the real pipeline plugs into: get_frame() should return
    (timestamp, ear, mar, head_pitch_deg, perclos) for the current instant.
    In dry-run mode this is unused — only the countdown/prompts run.
    """
    get_frame: Callable[[], tuple]


def run_segment(label: str, seconds: int, person_id: str, session_id: str,
                 writer: Optional[csv.DictWriter], frame_source: Optional[FrameSource],
                 clock: Callable[[], float], sleep: Callable[[float], None],
                 tick_seconds: float = 1.0) -> None:
    print(f"\n>>> 狀態：{label}（{seconds} 秒）")
    print(f"    引導稿：{GUIDANCE[label]}")
    start = clock()
    while clock() - start < seconds:
        remaining = seconds - (clock() - start)
        print(f"    剩餘 {remaining:4.1f}s", end="\r")
        if frame_source is not None and writer is not None:
            timestamp, ear, mar, head_pitch_deg, perclos = frame_source.get_frame()
            writer.writerow({
                "person_id": person_id, "session_id": session_id, "timestamp": timestamp,
                "label": label, "ear": ear, "mar": mar,
                "head_pitch_deg": head_pitch_deg, "perclos": perclos,
            })
        sleep(tick_seconds)
    print()


def run_session(person_id: str, session_id: str, out_path: Optional[str],
                 frame_source: Optional[FrameSource], dry_run: bool,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
    csv_file = None
    writer = None
    if not dry_run:
        if out_path is None:
            raise ValueError("--out is required when not using --dry-run")
        csv_file = open(out_path, "w", newline="")
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

    try:
        print(f"=== 校準錄製：{person_id} / {session_id} ===")
        for i, label in enumerate(SEGMENT_PLAN):
            run_segment(label, SEGMENT_SECONDS, person_id, session_id, writer, frame_source, clock, sleep)
            if i < len(SEGMENT_PLAN) - 1:
                print(f"    --- 緩衝 {GAP_SECONDS} 秒，準備下一段 ---")
                sleep(GAP_SECONDS)
        print("\n錄製完成。" + ("" if dry_run else f" 特徵已寫入 {out_path}"))
    finally:
        if csv_file is not None:
            csv_file.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--person-id", required=True)
    parser.add_argument("--session-id", default="s1")
    parser.add_argument("--out", default=None, help="CSV output path (required unless --dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                         help="just rehearse timing/prompts, no pipeline/CSV needed")
    args = parser.parse_args(argv)

    frame_source = None
    if not args.dry_run:
        print("提示：目前還沒有接 Layer A 真實 frame source，", file=sys.stderr)
        print("這裡只是佔位——等 rider/pipeline.py 能拿到即時特徵後，", file=sys.stderr)
        print("在這裡建一個 FrameSource(get_frame=...) 傳進來即可。", file=sys.stderr)
        sys.exit(1)

    run_session(args.person_id, args.session_id, args.out, frame_source, args.dry_run)


if __name__ == "__main__":
    main()
