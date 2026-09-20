# Keep the laptop's local MQTT port connected to the board's loopback broker.
# Run hidden in the background; SSH exits on a dead connection and this loop retries.
param([string]$Board = '100.71.95.53')

while ($true) {
    $listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
    if (-not $listener) {
        & ssh.exe -N -L '1883:127.0.0.1:1883' -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes "root@$Board"
    }
    Start-Sleep -Seconds 3
}
