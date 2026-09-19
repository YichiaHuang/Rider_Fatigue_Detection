#!/bin/sh
# Board side: camera -> DMS -> Stage A -> MQTT, plus the demo video stream.
#   sh tools/start_board.sh                 # normal mode (video off until the operator turns it on)
#   sh tools/start_board.sh --demo          # start with the stream on
#   RIDER_ID=rider-06 sh tools/start_board.sh   # a second board needs its own id
#   sh tools/start_board.sh stop            # stop everything
cd /home/fatigue-detection || exit 1

# Stop the supervisor first, otherwise it would just restart the runner we kill next.
pkill -f "^sh -c : rider-supervisor" 2>/dev/null   # anchored: an unanchored pattern also matches the SSH shell running this script
pkill -f "^python3 rider/stream_server" 2>/dev/null
pkill -f "^python3 rider/live_runner" 2>/dev/null
sleep 1
[ "$1" = "stop" ] && { echo "stopped"; exit 0; }

# Supervised: if the runner ever dies it is restarted after 3 s, so a crash costs
# a few seconds of "unknown" on the dashboard instead of the whole demo.
RIDER="${RIDER_ID:-rider-01}"
setsid nohup sh -c ": rider-supervisor; while true; do python3 rider/live_runner.py --rider-id $RIDER $*; echo \"[supervisor] runner exited, restarting in 3s\"; sleep 3; done" \
    > /tmp/live_runner.log 2>&1 < /dev/null &
echo "started (auto-restart on); log: /tmp/live_runner.log"
