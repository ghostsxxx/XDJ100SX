#!/usr/bin/env bash
# Starts N virtual XDJ100SX players on this machine: separate Mixxx instances,
# each with its own settings folder, the XDJ100SX skin (symlinked, so skin edits
# apply on restart) and the click-track test library.
#
# Usage: sync/devenv/players.sh [N]        start N players (default 2)
#        sync/devenv/players.sh stop       stop all players started here
#        sync/devenv/players.sh reset      stop and delete all player settings
#
# First start of each player: Mixxx asks once for the music library folder.
# Any folder works; each player starts with a click track loaded.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
XDJ_DIR="$(cd "$HERE/../.." && pwd)"
MIXXX_DIR="${MIXXX_DIR:-$(cd "$XDJ_DIR/../mixxx" && pwd)}"
MIXXX_BIN="${MIXXX_BIN:-$MIXXX_DIR/build/mixxx}"
PLAYERS_DIR="${PLAYERS_DIR:-$XDJ_DIR/../.xdj-players}"
TRACKS_DIR="$XDJ_DIR/sync/testdata/tracks"

stop_players() {
    for pidfile in "$PLAYERS_DIR"/player*/mixxx.pid; do
        [[ -f "$pidfile" ]] || continue
        pid="$(cat "$pidfile")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && echo "stopped $(basename "$(dirname "$pidfile")") (pid $pid)"
        fi
        rm -f "$pidfile"
    done
}

case "${1:-2}" in
    stop) stop_players; exit 0 ;;
    reset) stop_players; rm -rf "$PLAYERS_DIR"; echo "removed $PLAYERS_DIR"; exit 0 ;;
esac

count="${1:-2}"
[[ -x "$MIXXX_BIN" ]] || { echo "Mixxx not built: $MIXXX_BIN (run build-mixxx.sh)"; exit 1; }
[[ -d "$TRACKS_DIR" ]] || python3 "$XDJ_DIR/sync/testdata/make_clicks.py"

for i in $(seq 1 "$count"); do
    dir="$PLAYERS_DIR/player$i"
    mkdir -p "$dir/skins"
    ln -sfn "$XDJ_DIR/mixxx/SKIN/XDJ100SX" "$dir/skins/XDJ100SX"
    python3 -I "$HERE/set_cfg.py" "$dir/mixxx.cfg" \
        Config ResizableSkin XDJ100SX \
        Library RescanOnStartup 1 \
        XdjSync Enabled 1 \
        XdjSync PlayerId "player$i"
    if [[ -f "$dir/mixxx.pid" ]] && kill -0 "$(cat "$dir/mixxx.pid")" 2>/dev/null; then
        echo "player$i already running"
        continue
    fi
    # Odd players get the high click, even the low one, so flams are audible.
    track="$TRACKS_DIR/click_128_00_$([[ $((i % 2)) == 1 ]] && echo high || echo low).wav"
    "$MIXXX_BIN" --settings-path "$dir" --resource-path "$MIXXX_DIR/res" --developer --log-level debug --log-flush-level debug \
        "$track" > "$dir/stdout.log" 2>&1 &
    echo $! > "$dir/mixxx.pid"
    echo "started player$i (pid $!), settings: $dir"
done
