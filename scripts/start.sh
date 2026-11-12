#!/usr/bin/env bash
# Run the app locally: builds the frontend if needed and serves API + UI on http://127.0.0.1:8000
#   scripts/start.sh            # baut frontend/dist nur, wenn es fehlt oder veraltet ist
#   scripts/start.sh --rebuild  # Frontend immer neu bauen
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_venv

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

needs_build() {
  [ -f "$FRONTEND/package.json" ] || return 1
  [ "$REBUILD" = "1" ] && return 0
  [ -f "$FRONTEND/dist/index.html" ] || return 0
  # rebuild when any source file is newer than the last build
  [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" "$FRONTEND/package.json" -newer "$FRONTEND/dist/index.html" -type f 2>/dev/null | head -n 1)" ]
}

if needs_build; then
  if command -v npm >/dev/null 2>&1; then
    log "Baue Frontend"
    [ -d "$FRONTEND/node_modules" ] || (cd "$FRONTEND" && npm install --no-audit --no-fund)
    (cd "$FRONTEND" && npm run build)
  else
    warn "npm nicht gefunden – Frontend wird nicht gebaut (nur API verfügbar)."
  fi
fi

wait_for_db 20 || die "PostgreSQL nicht erreichbar (docker compose up -d db)."
log "Datenbank-Migrationen"
(cd "$BACKEND" && "$VENV/bin/alembic" upgrade head)
(cd "$BACKEND" && "$VENV/bin/python" -m app.seed >/dev/null)

log "Vermögen läuft auf http://$HOST:$PORT"
cd "$BACKEND"
exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
