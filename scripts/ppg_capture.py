"""Record raw MAX30102 samples to CSV and compare this project's online DSP with
H2's offline analyzer on the SAME data. Stop the live runner first — only one
process can drain the sensor FIFO:

    sh tools/start_board.sh stop
    python3 scripts/ppg_capture.py --seconds 30 --out /tmp/ppg_capture.csv
    python3 /root/sensors/hr_analyze.py /tmp/ppg_capture.csv 5
    sh tools/start_board.sh --demo
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rider"))
import ppg_dsp  # noqa: E402
from ppg_reader import SAMPLE_HZ, Max30102  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--bus", type=int, default=0)
    parser.add_argument("--out", default="/tmp/ppg_capture.csv")
    args = parser.parse_args()

    sensor, rows, t0 = Max30102(args.bus), [], time.time()
    while time.time() - t0 < args.seconds:
        (batch, overflowed), now = sensor.read_samples(), time.time() - t0
        if overflowed:
            print(f"  FIFO overflow at {now:.1f}s — samples lost")
        rows += [(now - (len(batch) - 1 - i) / SAMPLE_HZ, ir) for i, ir in enumerate(batch)]
        time.sleep(0.05)
    with open(args.out, "w") as f:  # same columns hr_analyze.py reads
        f.write("t,red,ir\n" + "".join(f"{t:.4f},0,{v}\n" for t, v in rows))
    print(f"{len(rows)} samples -> {args.out}  (effective {len(rows) / args.seconds:.1f} Hz)")
    for start in range(4, int(args.seconds) - 11, 4):
        seg = [(t, v) for t, v in rows if start <= t < start + 12]
        r = ppg_dsp.analyze([t for t, _ in seg], [v for _, v in seg])
        print(f"  {start:2d}-{start + 12:2d}s", {k: r[k] for k in ("quality", "heart_rate_bpm", "perfusion_index", "autocorr")})


if __name__ == "__main__":
    main()
