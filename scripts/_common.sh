#!/usr/bin/env bash
# Shared helpers for the scripts (sourced, not executed). Works with bash 3.2 (macOS) and newer.
#
# Environment overrides (all optional):
#   VENV=/path/to/venv        virtualenv to use/create            (default: <repo>/.venv)
#   PYTHON=python3.12         interpreter used to create the venv (default: newest python3.x >= 3.11)
#   HOST=127.0.0.1 PORT=8000  where the app listens
#   DATABASE_URL=postgresql+psycopg://user:pw@host:5432/db   overrides .env (also UPLOAD_DIR, ENABLE_SCHEDULER, ...)
#   SKIP_DOCKER=1             never call docker compose (use a locally installed PostgreSQL)
#   SKIP_FRONTEND=1           do not install/build/start the frontend
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
  [ -x "$VENV/bin/python" ] || die "Kein virtualenv unter $VENV – zuerst scripts/setup.sh (oder: make setup) ausführen."
}

have_docker_compose() {
  [ -z "${SKIP_DOCKER:-}" ] && command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

# Is the configured database reachable right now? (single attempt, quiet)
db_reachable() {
  wait_for_db 1 2>/dev/null
}

# Wait until PostgreSQL accepts connections (uses the app's own settings: env DATABASE_URL > .env > default).
# If the server answers but the DATABASE does not exist yet, it is created (needs CREATEDB, which the
# docker image's user and a typical local setup have).
wait_for_db() {
  local tries="${1:-30}"
  (
    cd "$BACKEND"
    "$VENV/bin/python" - "$tries" <<'PY'
import sys, time
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.core.config import get_settings

url = make_url(get_settings().database_url)
tries = int(sys.argv[1])
last = None
for attempt in range(tries):
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last = exc
        if "does not exist" in str(exc) and f'"{url.database}"' in str(exc):
            try:   # server is up, database missing -> create it via the maintenance database
                admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
                with admin.connect() as conn:
                    conn.execute(text(f'CREATE DATABASE "{url.database}"'))
                print(f'Datenbank "{url.database}" angelegt.')
                continue
            except Exception as exc2:  # noqa: BLE001
                last = exc2
        if attempt < tries - 1:
            time.sleep(1)
first_line = str(last).strip().splitlines()[0] if last else "unbekannter Fehler"
print(f"Datenbank nicht erreichbar ({url.render_as_string(hide_password=True)}): {first_line}", file=sys.stderr)
sys.exit(1)
PY
  )
}

db_help() {
  cat >&2 <<'TXT'

PostgreSQL ist nicht erreichbar. Zwei Möglichkeiten:

  A) Docker:           docker compose up -d db          (legt Benutzer/Datenbank "wealth" an)

  B) lokal installiert (einmalig Benutzer + Datenbank anlegen):
       Linux:   sudo -u postgres psql -c "CREATE ROLE wealth LOGIN PASSWORD 'wealth' CREATEDB"
                sudo -u postgres createdb -O wealth wealth
       macOS:   brew install postgresql@16 && brew services start postgresql@16
                psql postgres -c "CREATE ROLE wealth LOGIN PASSWORD 'wealth' CREATEDB"
                createdb -O wealth wealth

Andere Zugangsdaten: DATABASE_URL in .env anpassen (Vorlage: .env.example).
TXT
}

# Make sure the database is up: use what is there, else start the docker container, else explain.
ensure_db() {
  if db_reachable; then
    return 0
  fi
  if have_docker_compose; then
    log "Starte PostgreSQL (docker compose up -d db)"
    (cd "$ROOT" && docker compose up -d db) || warn "docker compose fehlgeschlagen"
    wait_for_db 60 && return 0
  else
    wait_for_db "${1:-5}" && return 0
  fi
  db_help
  return 1
}

# npm available and frontend wanted?
frontend_enabled() {
  [ -z "${SKIP_FRONTEND:-}" ] && [ -f "$FRONTEND/package.json" ]
}
