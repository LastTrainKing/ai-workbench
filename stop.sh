#!/usr/bin/env bash
# ============================================================================
#  AI Workbench — stop everything (Linux / macOS / Git Bash)
#
#  Stops the application processes started by start.sh.
#  Infrastructure processes you started yourself (system PostgreSQL/Redis) are
#  left running on purpose — this script never kills services it did not start.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"

ok()   { printf '\033[32m[+]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$1"; }

PID_DIR=".runtime/pids"

for name in web api; do
    pidfile="$PID_DIR/$name.pid"
    if [[ ! -f "$pidfile" ]]; then
        warn "$name is not tracked"
        continue
    fi
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        ok "$name stopped (pid $pid)"
    else
        warn "$name was not running"
    fi
    rm -f "$pidfile"
done

echo
ok "AI Workbench application processes stopped."
echo "   Infrastructure (PostgreSQL / Redis / Qdrant) was left running."
echo "   On Windows use stop.bat to stop those too."
