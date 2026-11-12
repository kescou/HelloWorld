#!/usr/bin/env bash
# One-time setup: virtualenv, Python deps, database schema (alembic), seed data, frontend deps.
#   scripts/setup.sh           # Basisdaten (Konten + Kategorien)
#   scripts/setup.sh --demo    # zusätzlich Demo-Datensatz
#   SKIP_DOCKER=1 scripts/setup.sh   # vorhandenes PostgreSQL verwenden, kein docker compose
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

SEED_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --demo) SEED_ARGS+=("--demo") ;;
    -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Unbekannte Option: $arg" ;;
  esac
done

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || die "Python >= 3.11 nicht gefunden."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 erforderlich (gefunden: $("$PYTHON" -V 2>&1))."

if [ ! -x "$VENV/bin/python" ]; then
  log "Erzeuge virtualenv in $VENV"
  "$PYTHON" -m venv "$VENV"
fi
log "Installiere Python-Abhängigkeiten"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$BACKEND/requirements-dev.txt"
if [ -d "$BACKEND/requirements" ]; then
  for req in "$BACKEND"/requirements/*.txt; do
    [ -e "$req" ] && "$VENV/bin/python" -m pip install --quiet -r "$req"
  done
fi

mkdir -p "$ROOT/data/uploads"

if [ -z "${SKIP_DOCKER:-}" ] && command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Starte PostgreSQL (docker compose)"
  (cd "$ROOT" && docker compose up -d db) || warn "docker compose fehlgeschlagen – verwende vorhandene Datenbank"
else
  log "Überspringe docker compose (verwende vorhandenes PostgreSQL)"
fi

log "Warte auf die Datenbank"
wait_for_db 40 || die "PostgreSQL nicht erreichbar. Läuft die Datenbank (docker compose up -d db)? DATABASE_URL in .env korrekt?"

log "Datenbank-Migrationen (alembic upgrade head)"
(cd "$BACKEND" && "$VENV/bin/alembic" upgrade head)

log "Seed-Daten"
(cd "$BACKEND" && "$VENV/bin/python" -m app.seed ${SEED_ARGS[@]+"${SEED_ARGS[@]}"})

if [ -f "$FRONTEND/package.json" ]; then
  if command -v npm >/dev/null 2>&1; then
    log "Installiere Frontend-Abhängigkeiten"
    (cd "$FRONTEND" && npm install --no-audit --no-fund)
  else
    warn "npm nicht gefunden – Frontend-Abhängigkeiten nicht installiert."
  fi
fi

log "Fertig. Entwicklung: scripts/dev.sh   |   Produktion lokal: scripts/start.sh"
