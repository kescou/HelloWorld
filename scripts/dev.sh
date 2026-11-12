#!/usr/bin/env bash
# Development mode: uvicorn --reload on :8000 and the Vite dev server on :5173.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require_venv
wait_for_db 15 || die "PostgreSQL nicht erreichbar (docker compose up -d db)."

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "Backend:  http://$HOST:$PORT  (API-Doku: /docs)"
(cd "$BACKEND" && exec "$VENV/bin/uvicorn" app.main:app --reload --host "$HOST" --port "$PORT") &
PIDS+=($!)

if [ -f "$FRONTEND/package.json" ] && command -v npm >/dev/null 2>&1; then
  [ -d "$FRONTEND/node_modules" ] || (cd "$FRONTEND" && npm install --no-audit --no-fund)
  log "Frontend: http://localhost:5173"
  (cd "$FRONTEND" && exec npm run dev -- --port 5173) &
  PIDS+=($!)
else
  warn "Frontend nicht gefunden oder npm fehlt – starte nur das Backend."
fi

# exit as soon as one of the processes stops (portable: no `wait -n` on macOS bash 3.2)
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      exit 1
    fi
  done
  sleep 1
done
