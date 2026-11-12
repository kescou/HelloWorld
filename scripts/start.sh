#!/usr/bin/env bash
# Run the app locally: builds the frontend if needed and serves API + UI on http://127.0.0.1:8000
#   scripts/start.sh            # baut frontend/dist nur, wenn es fehlt oder veraltet ist
#   scripts/start.sh --rebuild  # Frontend immer neu bauen
# Environment overrides: HOST, PORT, VENV, DATABASE_URL, SKIP_DOCKER=1, SKIP_FRONTEND=1 (see scripts/_common.sh)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_venv

REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=1 ;;
    -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Unbekannte Option: $arg" ;;
  esac
done

needs_build() {
  frontend_enabled || return 1
  [ "$REBUILD" = "1" ] && return 0
  [ -f "$FRONTEND/dist/index.html" ] || return 0
  # rebuild when any source file is newer than the last build
  [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" "$FRONTEND/package.json" -type f -newer "$FRONTEND/dist/index.html" 2>/dev/null | head -n 1)" ]
}

if needs_build; then
  if command -v npm >/dev/null 2>&1; then
    log "Baue Frontend"
    [ -d "$FRONTEND/node_modules" ] || (cd "$FRONTEND" && npm install --no-audit --no-fund)
    (cd "$FRONTEND" && npm run build) || warn "Frontend-Build fehlgeschlagen – starte mit dem vorhandenen Stand."
  else
    warn "npm nicht gefunden – Frontend wird nicht gebaut (nur API verfügbar)."
  fi
fi

ensure_db 10 || die "Ohne Datenbank geht es nicht weiter."
log "Datenbank-Migrationen"
(cd "$BACKEND" && "$VENV/bin/alembic" upgrade head)
(cd "$BACKEND" && "$VENV/bin/python" -m app.seed >/dev/null)

log "Vermögen läuft auf http://$HOST:$PORT   (API-Doku: http://$HOST:$PORT/docs, beenden mit Strg+C)"
cd "$BACKEND"
exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
