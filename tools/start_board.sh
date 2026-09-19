#!/bin/sh
# Board side: camera -> DMS -> Stage A -> MQTT, plus the demo video stream.
#   sh tools/start_board.sh                 # normal mode (video off until the operator turns it on)
#   sh tools/start_board.sh --demo          # start with the stream on
#   RIDER_ID=rider-06 sh tools/start_board.sh   # a second board needs its own id
cd /home/fatigue-detection || exit 1
pkill -f "^python3 rider/stream_server" 2>/dev/null
pkill -f "^python3 rider/live_runner" 2>/dev/null
sleep 1
setsid nohup python3 rider/live_runner.py --rider-id "${RIDER_ID:-rider-01}" "$@" \
    > /tmp/live_runner.log 2>&1 < /dev/null &
echo "started; log: /tmp/live_runner.log"
