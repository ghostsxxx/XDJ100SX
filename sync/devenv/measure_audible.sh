#!/usr/bin/env bash
# Compares the players' displayed offsets with the audible one.
#
# Both players must output to a loopback device (default "BlackHole 2ch"),
# player1 playing the high click track, player2 the low one, one of them
# following the other. Records SECONDS of the mix, measures the click offset
# (player1 vs player2) and prints it next to each player's displayed offset
# against its reference (player2's display has the opposite sign).
#
# Usage: sync/devenv/measure_audible.sh [SECONDS] [DEVICE]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
XDJ_DIR="$(cd "$HERE/../.." && pwd)"
PLAYERS_DIR="${PLAYERS_DIR:-$XDJ_DIR/../.xdj-players}"
seconds="${1:-10}"
device="${2:-BlackHole 2ch}"
wav="$(mktemp -t xdj-audible).wav"

swift "$XDJ_DIR/sync/testdata/record_device.swift" "$device" "$seconds" "$wav"
echo "audible:"
python3 -I "$XDJ_DIR/sync/testdata/measure_offset.py" "$wav"
for player in player1 player2; do
    grep "XdjSync - peers" "$PLAYERS_DIR/$player/mixxx.log" | tail -n "$seconds" |
        { grep -oE "offset_ms [-0-9.e]+" || true; } |
        awk -v p="$player" '{v[++n]=$2; s+=$2} END {
            if (!n) { printf "displayed by %s: no reference (master?)\n", p; exit }
            m=s/n; for (i=1;i<=n;i++) d+=(v[i]-m)^2;
            printf "displayed by %s vs its reference: mean %+.3f ms, std %.3f ms (%d samples)\n",
                p, m, sqrt(d/n), n}'
done
rm -f "$wav"
