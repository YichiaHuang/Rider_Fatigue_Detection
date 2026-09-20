#!/usr/bin/env bash
# Laptop side, after the board lost power: bring EVERYTHING back with one command.
#   tools/revive.sh                 # board services + runner (demo mode), MQTT tunnel + dashboard, phone address
#   tools/revive.sh --ppg-new-baseline   # extra arguments go to the board's live runner
#
# A rebooted board starts neither Mosquitto's clients, the PPG supply (vexp-3v3)
# nor the runner; its LAN address changes with the Wi-Fi it lands on; and the
# laptop's MQTT tunnel sits on "Connection refused" until the platform is
# restarted. Order matters: services first, THEN the runner (started earlier it
# finds no heart-rate sensor), then the laptop side.
set -uo pipefail
cd "$(dirname "$0")/.."
TAILSCALE_IP="${BOARD_TAILSCALE:-100.71.95.53}"
PROJECT="${BOARD_PROJECT:-/home/Rider_Fatigue_Detection}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no"

echo "== board (via Tailscale $TAILSCALE_IP)"
n=0; until $SSH root@$TAILSCALE_IP true 2>/dev/null || [ $n -ge 20 ]; do n=$((n+1)); echo "   waiting for the board... ($n)"; sleep 5; done
$SSH root@$TAILSCALE_IP true || { echo "board unreachable over Tailscale; is it powered and on a network?"; exit 1; }

LAN=$($SSH root@$TAILSCALE_IP "
    systemctl start mosquitto vexp-3v3
    n=0; until (echo > /dev/tcp/127.0.0.1/1883) 2>/dev/null || [ \$n -ge 20 ]; do n=\$((n+1)); sleep 1; done
    echo \"   up: \$(uptime -p) | camera: \$(lsusb | grep -i -c logitech) | vexp/mosquitto: \$(systemctl is-active vexp-3v3 mosquitto | tr '\n' ' ')\" >&2
    sh $PROJECT/tools/start_board.sh --demo $* >&2
    n=0; until grep -a -q 'perception=' /tmp/live_runner.log || [ \$n -ge 45 ]; do n=\$((n+1)); sleep 1; done
    grep -a -E '^ppg:' /tmp/live_runner.log | head -1 >&2
    tail -1 /tmp/live_runner.log | cut -c1-150 >&2
    ip -4 -o addr show | grep -v -E ' lo |tailscale' | awk '{print \$4}' | cut -d/ -f1 | head -1
")
echo "   board LAN address: ${LAN:-unknown}"

# Same network = much smoother video; otherwise Tailscale always works.
BOARD_IP=$TAILSCALE_IP
if [ -n "$LAN" ] && timeout 6 $SSH root@$LAN true 2>/dev/null; then BOARD_IP=$LAN; fi
echo "== laptop: tunnel + dashboard -> $BOARD_IP"
BOARD=$BOARD_IP tools/connect_board.sh --tunnel-port 8008 | tail -1
n=0; until curl -s -m 3 "http://127.0.0.1:8000/api/state?history=1" | grep -q '"link": "online", "perception": "ok"' || [ $n -ge 15 ]; do n=$((n+1)); sleep 2; done
curl -s "http://127.0.0.1:8000/api/state?history=1" | python3 -c "
import sys, json
r = [r for r in json.load(sys.stdin)['riders'] if r['primary']][0]
print('   rider:', {k: r[k] for k in ('link', 'perception', 'score', 'status')})"

echo "== phone address"
pgrep -f "^bash -c : funnel-watchdog" > /dev/null || tools/keep_funnel.sh | tail -1
if ! tools/keep_funnel.sh check; then
    echo "   re-registering Funnel (the two public entrances come back 10-30 s apart)"
    TS="${TAILSCALE:-/mnt/c/Program Files/Tailscale/tailscale.exe}"
    "$TS" funnel --https=443 off > /tmp/keep_funnel_off.txt 2>&1
    "$TS" funnel --bg 8008 > /tmp/keep_funnel_on.txt 2>&1
    n=0; until tools/keep_funnel.sh check || [ $n -ge 12 ]; do n=$((n+1)); sleep 5; done
fi
