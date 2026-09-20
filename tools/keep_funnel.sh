#!/usr/bin/env bash
# Laptop side: keep the phones' FIXED address alive (docs/APP.md 3B).
#   tools/keep_funnel.sh            # start (or restart) the watchdog in the background
#   tools/keep_funnel.sh check      # one probe, print the verdict
#   tools/keep_funnel.sh stop
#
# Why: `tailscale funnel status` happily says "Funnel on" while the public
# ingress has forgotten this node (seen after the laptop changed networks): a
# phone then gets a TLS handshake that just closes — a black screen on an iPhone.
# Turning Funnel off and on re-registers it and fixes that within seconds.
#
# Why --resolve: MagicDNS resolves our own hostname to the tailnet IP, so a plain
# curl from this machine only ever tests the LOCAL serve, never the internet
# path. The probe resolves the name through a public DNS server instead.
set -uo pipefail
TS="${TAILSCALE:-/mnt/c/Program Files/Tailscale/tailscale.exe}"
HOST="${FUNNEL_HOST:-yichia.tail76e092.ts.net}"
PORT="${TUNNEL_PORT:-8008}"
LOG=/tmp/keep_funnel.log
INTERVAL=30
FAILS_BEFORE_RESET=2

probe() {  # 0 = the public internet path answers
    local ip
    ip=$(dig +short @8.8.8.8 "$HOST" A 2>/dev/null | grep -E '^[0-9.]+$' | head -1)
    [ -n "$ip" ] || return 2
    local code
    code=$(curl -s -m 15 --resolve "$HOST:443:$ip" -o /dev/null -w '%{http_code}' "https://$HOST/app/")
    [ "$code" = "200" ]
}

reregister() {  # tailscale.exe output is buffered under WSL: send it to files, never a pipe
    "$TS" funnel --https=443 off > /tmp/keep_funnel_off.txt 2>&1
    sleep 2
    "$TS" funnel --bg "$PORT" > /tmp/keep_funnel_on.txt 2>&1
}

case "${1:-start}" in
    check) if probe; then echo "ok: https://$HOST/app/ answers from the internet"; else echo "DEAD: public ingress does not reach this node (rc $?)"; exit 1; fi; exit 0 ;;
    stop) pkill -f "^bash -c : funnel-watchdog" 2>/dev/null; echo "stopped"; exit 0 ;;
esac

pkill -f "^bash -c : funnel-watchdog" 2>/dev/null
setsid bash -c ": funnel-watchdog
source_probe() { $(declare -f probe); probe; }
source_rereg() { $(declare -f reregister); reregister; }
TS='$TS'; HOST='$HOST'; PORT='$PORT'
fails=0
while true; do
    if source_probe; then
        [ \$fails -gt 0 ] && echo \"\$(date +%T) public path back\"; fails=0
    else
        fails=\$((fails + 1)); echo \"\$(date +%T) public path failed (\$fails)\"
        if [ \$fails -ge $FAILS_BEFORE_RESET ]; then
            echo \"\$(date +%T) re-registering Funnel\"; source_rereg; fails=0; sleep 10
        fi
    fi
    sleep $INTERVAL
done" > "$LOG" 2>&1 < /dev/null &
sleep 1
echo "funnel watchdog running (log: $LOG); fixed address: https://$HOST/app/"
