#!/usr/bin/env bash
# Laptop side, one command: MQTT tunnel to the board + dashboard.
#   tools/connect_board.sh                      # board over Tailscale
#   BOARD=10.199.29.167 tools/connect_board.sh  # board on the same Wi-Fi (much smoother video)
# The board's Mosquitto only listens on its own localhost, so we reach it through
# an SSH tunnel instead of changing the board's system config.
set -euo pipefail
BOARD="${BOARD:-100.71.95.53}"
cd "$(dirname "$0")/.."

pkill -f "^bash -c : mqtt-tunnel-supervisor" 2>/dev/null || true
pkill -f "^ssh -N -L 1883:127.0.0.1:1883" 2>/dev/null || true
pkill -f "^python3 -m server" 2>/dev/null || true
sleep 1

# Supervised: Wi-Fi hiccups and board reboots kill the SSH session; without this
# loop the dashboard silently sat on half-hour-old numbers. ServerAliveCountMax
# makes a dead link get noticed in ~30 s instead of hanging forever.
setsid bash -c ": mqtt-tunnel-supervisor; while true; do
    ssh -N -L 1883:127.0.0.1:1883 -o BatchMode=yes -o ConnectTimeout=15 \
        -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes root@${BOARD}
    echo \"\$(date +%T) tunnel to ${BOARD} closed, retrying in 3s\"; sleep 3
done" > /tmp/mqtt_tunnel.log 2>&1 < /dev/null &
sleep 5
if ! timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/1883' 2>/dev/null; then
    echo "MQTT tunnel to ${BOARD} failed:"; cat /tmp/mqtt_tunnel.log; exit 1
fi

setsid python3 -m server --mqtt-host 127.0.0.1 --board "rider-01=http://${BOARD}:8080" "$@" \
    > /tmp/dash.log 2>&1 < /dev/null &
sleep 2
echo "dashboard: http://$(hostname -I | awk '{print $1}'):8000/   (log: /tmp/dash.log)"
