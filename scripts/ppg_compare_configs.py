"""Record the same finger under several MAX30102 configurations and compare
signal quality. All configurations output 50 Hz; they differ in how many raw
ADC samples are averaged into each output sample, and in LED current.

Stop the live runner first (only one process may drain the FIFO), keep still:
    sh tools/start_board.sh stop && python3 scripts/ppg_compare_configs.py; sh tools/start_board.sh --demo
Raw traces are left in /tmp/ppg_<A|B|C>.csv (t,red,ir — readable by hr_analyze.py).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rider"))
import ppg_dsp  # noqa: E402
from ppg_reader import Max30102  # noqa: E402

SECONDS = 22
# name, SPO2_CONFIG, FIFO_CONFIG, LED_PA
CONFIGS = [("A 100sps avg2 led0x24 (H2 default)", 0x27, 0x3F, 0x24),
           ("B 400sps avg8 led0x24", 0x2F, 0x7F, 0x24),
           ("C 400sps avg8 led0x3C", 0x2F, 0x7F, 0x3C)]

for name, spo2, fifo, led in CONFIGS:
    try:
        sensor = Max30102(0, spo2, fifo, led)
    except Exception as exc:
        print(f"{name:36s} FAILED to start: {exc}")
        continue
    time.sleep(1.0)
    sensor.read_samples()
    rows, t0, lost = [], time.time(), 0
    while time.time() - t0 < SECONDS:
        batch, overflowed = sensor.read_samples()
        now = time.time() - t0
        lost += overflowed
        rows += [(now - (len(batch) - 1 - i) / 50.0, v) for i, v in enumerate(batch)]
        time.sleep(0.04)
    alive = sensor.alive()
    with open(f"/tmp/ppg_{name.split()[0]}.csv", "w") as f:
        f.write("t,red,ir\n" + "".join(f"{t:.4f},0,{v}\n" for t, v in rows))
    seg = [(t, v) for t, v in rows if t >= 6]
    result = ppg_dsp.analyze([t for t, _ in seg], [v for _, v in seg])
    irs = [v for _, v in seg]
    print(f"{name:36s} {len(rows) / SECONDS:4.1f}Hz ovf={lost} alive={alive} ir={min(irs)}..{max(irs)} -> "
          f"{result['quality']} hr={result['heart_rate_bpm']} PI={result['perfusion_index']} r={result['autocorr']}")
