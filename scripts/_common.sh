#!/usr/bin/env bash
# Shared helpers for the dev scripts (sourced, not executed).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="${VENV:-$ROOT/.venv}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mFEHLER:\033[0m %s\n' "$*" >&2; exit 1; }

require_venv() {
  [ -x "$VENV/bin/python" ] || die "Kein virtualenv unter $VENV – zuerst scripts/setup.sh ausführen."
}

# Wait until PostgreSQL accepts connections (uses the app's own DATABASE_URL / .env).
wait_for_db() {
  local tries="${1:-30}"
  (
    cd "$BACKEND"
    "$VENV/bin/python" - "$tries" <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from app.core.config import get_settings

url = get_settings().database_url
tries = int(sys.argv[1])
for attempt in range(tries):
    try:
        with create_engine(url).connect() as conn:
            conn.execute(text("select 1"))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last = exc
        time.sleep(1)
print(f"Datenbank nicht erreichbar: {last}", file=sys.stderr)
sys.exit(1)
PY
  )
}
