#!/usr/bin/env bash
# ============================================================================
#  AI Workbench — one-click start (Linux / macOS / Git Bash on Windows)
#
#  Native runtimes (no Docker):
#    * PostgreSQL and Redis must be available (system packages, brew, or
#      binaries on PATH) — this script does not download Linux/macOS builds
#      because the project's verified environment is Windows.
#    * Qdrant is downloaded on first run if `qdrant` is not on PATH.
#
#  For Docker, use:  docker compose up -d
#
#  Usage:
#      ./start.sh              start everything
#      ./start.sh --no-web     backend only
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
START_WEB=1
[[ "${1:-}" == "--no-web" ]] && START_WEB=0

say()  { printf '\033[36m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[32m[+]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$1"; }
die()  { printf '\033[31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

echo
echo "=========================================================="
echo "  AI Workbench — starting"
echo "=========================================================="
echo

# ------------------------------------------------------------ prerequisites --
[[ -x "$PY" ]] || die "Virtual environment missing. Create it with:
      python3 -m venv .venv
      .venv/bin/python -m pip install -r backend/requirements-dev.txt"

command -v pg_isready >/dev/null 2>&1 || warn "pg_isready not found — assuming PostgreSQL is managed elsewhere"
command -v redis-cli  >/dev/null 2>&1 || warn "redis-cli not found — assuming Redis is managed elsewhere"

port_open() {
    "$PY" - "$1" <<'PYEOF'
import socket, sys
s = socket.socket()
s.settimeout(0.7)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PYEOF
}

# ------------------------------------------------------------------ Qdrant ---
if ! port_open 6333; then
    if command -v qdrant >/dev/null 2>&1; then
        say "Starting Qdrant"
        mkdir -p .runtime/qdrant .runtime/logs
        (cd .runtime/qdrant && nohup qdrant > "$ROOT/.runtime/logs/qdrant.log" 2>&1 &)
    else
        warn "Qdrant is not running and not on PATH."
        warn "Install it (https://qdrant.tech/documentation/guides/installation/) or use Docker."
    fi
fi

# -------------------------------------------------------------- dependency ---
say "Checking dependencies"
"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, "backend")
from app.core.config import settings
print(f"    AI provider       : {settings.ai_provider}")
print(f"    Embedding provider: {settings.embedding_provider}")
print(f"    Postgres          : {settings.database_url.split('@')[-1]}")
print(f"    Redis             : {settings.redis_url}")
print(f"    Qdrant            : {settings.qdrant_url}")
PYEOF

"$PY" - <<'PYEOF' || warn "Some dependencies are unreachable — see the report above"
import asyncio, sys
sys.path.insert(0, "backend")
from app.cli import _check
sys.exit(asyncio.run(_check()))
PYEOF

# ---------------------------------------------------------------- processes --
mkdir -p .runtime/logs .runtime/pids

start_bg() {
    local name="$1"; shift
    local pidfile="$ROOT/.runtime/pids/$name.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        ok "$name already running (pid $(cat "$pidfile"))"
        return
    fi
    say "Starting $name"
    nohup "$@" > "$ROOT/.runtime/logs/$name.out.log" 2>&1 &
    echo $! > "$pidfile"
    ok "$name started (pid $!)"
}

if port_open 8000; then
    warn "Port 8000 is already in use — skipping the API"
else
    ( cd backend && PYTHONPATH=. start_bg api "$PY" -m app.cli serve --host 127.0.0.1 --port 8000 )
fi

if [[ $START_WEB -eq 1 ]]; then
    if [[ -d frontend/node_modules ]]; then
        if port_open 5173; then
            warn "Port 5173 is already in use — skipping the frontend"
        else
            ( cd frontend && start_bg web npm run dev -- --host 127.0.0.1 --port 5173 )
        fi
    else
        warn "frontend/node_modules missing — run: cd frontend && npm install"
    fi
fi

echo
echo "=========================================================="
echo "  AI Workbench is up"
echo "=========================================================="
echo
echo "  Web UI        http://127.0.0.1:5173"
echo "  API           http://127.0.0.1:8000"
echo "  API docs      http://127.0.0.1:8000/docs"
echo "  Qdrant UI     http://127.0.0.1:6333/dashboard"
echo
echo "  Demo account  demo / demo1234"
echo
echo "  Stop with:    ./stop.sh"
echo "=========================================================="
echo
