#!/usr/bin/env bash
# Laptop side: give phones an HTTPS address for the rider app (docs/APP.md 3A),
# and KEEP it alive.
#   tools/share_app.sh            # start (or restart) in the background, print the address
#   tools/share_app.sh url        # print the current address
#   tools/share_app.sh stop
#
# Why a supervisor and not just `cloudflared tunnel --url ...`: a Cloudflare quick
# tunnel does not survive losing its connection for more than a few minutes
# (laptop on a phone hotspot that naps, Wi-Fi change, lid closed). cloudflared
# then retries forever against "Unauthorized: Tunnel not found" and the phones
# stay dead until somebody notices. This loop notices: no healthy connection for
# DEAD_AFTER seconds -> start a fresh tunnel. The address changes when that
# happens (quick tunnels cannot keep theirs) — it is printed to the log and kept
# in $URL_FILE. An installed iPhone icon points at the old address and has to be
# re-added; a fixed address needs Tailscale Serve or a named tunnel (docs/APP.md).
set -uo pipefail
CLOUDFLARED="${CLOUDFLARED:-$HOME/cloudflared}"
PORT="${DASH_PORT:-8000}"
METRICS=127.0.0.1:20241
DEAD_AFTER=45
LOG=/tmp/share_app.log
URL_FILE=/tmp/rider_app_url.txt

current_url() { cat "$URL_FILE" 2>/dev/null; }

stop_all() {
    pkill -f "^bash -c : share-app-supervisor" 2>/dev/null
    pkill -f "^$CLOUDFLARED tunnel" 2>/dev/null
    # cloudflared shuts down gracefully (a few seconds) and keeps the metrics port
    # until it is gone; a new one started too early cannot bind it and exits.
    for i in $(seq 1 15); do pgrep -f "^$CLOUDFLARED tunnel" > /dev/null || break; sleep 1; done
}

case "${1:-start}" in
    url) current_url; exit 0 ;;
    stop) stop_all; rm -f "$URL_FILE"; echo "stopped"; exit 0 ;;
esac

[ -x "$CLOUDFLARED" ] || { echo "cloudflared not found at $CLOUDFLARED (see docs/APP.md 3A)"; exit 1; }
stop_all; sleep 1; rm -f "$URL_FILE"

# http2 (TCP): phone hotspots and venue Wi-Fi often drop the default QUIC/UDP.
setsid bash -c ": share-app-supervisor
while true; do
    : > /tmp/cloudflared.log
    '$CLOUDFLARED' tunnel --protocol http2 --metrics $METRICS --url http://localhost:$PORT >> /tmp/cloudflared.log 2>&1 &
    pid=\$!
    # The address only counts once the edge has accepted the connection.
    for i in \$(seq 1 40); do
        grep -aq 'Registered tunnel connection' /tmp/cloudflared.log && break
        kill -0 \$pid 2>/dev/null || break; sleep 1
    done
    url=\$(grep -aoE 'https://[a-z0-9]+(-[a-z0-9]+)+\.trycloudflare\.com' /tmp/cloudflared.log | head -1)
    if [ -z \"\$url\" ] || ! grep -aq 'Registered tunnel connection' /tmp/cloudflared.log; then
        echo \"\$(date +%T) tunnel did not come up (no internet?), retrying in 10s\"
        kill \$pid 2>/dev/null; wait \$pid 2>/dev/null; sleep 10; continue
    fi
    echo \"\$url/app/\" > $URL_FILE
    echo \"\$(date +%T) rider app: \$url/app/\"
    dead=0
    while kill -0 \$pid 2>/dev/null; do
        sleep 5
        if curl -s -m 3 http://$METRICS/ready | grep -q '\"readyConnections\":[1-9]'; then dead=0; else dead=\$((dead + 5)); fi
        if [ \$dead -ge $DEAD_AFTER ]; then echo \"\$(date +%T) tunnel dead for \${dead}s, replacing it\"; break; fi
    done
    kill \$pid 2>/dev/null; wait \$pid 2>/dev/null
    rm -f $URL_FILE
    sleep 3
done" > "$LOG" 2>&1 < /dev/null &

for i in $(seq 1 60); do [ -s "$URL_FILE" ] && break; sleep 1; done
echo "rider app: $(current_url)   (log: $LOG; address again: tools/share_app.sh url)"
