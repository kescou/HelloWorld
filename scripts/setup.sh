#!/usr/bin/env bash
# One-time setup: virtualenv, Python deps, .env, database (schema + seed data), frontend deps.
#   scripts/setup.sh           # Basisdaten (Konten + Kategorien)
#   scripts/setup.sh --demo    # zusätzlich Demo-Datensatz
# Environment overrides: VENV, PYTHON, DATABASE_URL, SKIP_DOCKER=1, SKIP_FRONTEND=1, WITH_OCR=1 (see scripts/_common.sh)
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
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  done
fi
[ -n "$PYTHON" ] || die "Python >= 3.11 nicht gefunden (macOS: brew install python@3.12, Ubuntu: sudo apt install python3 python3-venv)."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 erforderlich (gefunden: $("$PYTHON" -V 2>&1))."

if [ ! -x "$VENV/bin/python" ]; then
  log "Erzeuge virtualenv in $VENV ($("$PYTHON" -V 2>&1))"
  "$PYTHON" -m venv "$VENV" || die "virtualenv konnte nicht erzeugt werden (Ubuntu/Debian: sudo apt install python3-venv)."
fi
log "Installiere Python-Abhängigkeiten"
"$VENV/bin/python" -m pip install --quiet --upgrade pip || warn "pip-Upgrade fehlgeschlagen – weiter mit vorhandener Version"
"$VENV/bin/python" -m pip install --quiet -r "$BACKEND/requirements-dev.txt"
if [ -n "${WITH_OCR:-}" ]; then
  "$VENV/bin/python" -m pip install --quiet -r "$BACKEND/requirements-ocr.txt"
fi

if [ ! -f "$ROOT/.env" ] && [ -f "$ROOT/.env.example" ]; then
  log "Lege .env aus .env.example an"
  cp "$ROOT/.env.example" "$ROOT/.env"
fi
mkdir -p "$ROOT/data/uploads"

log "Prüfe Datenbank"
ensure_db 5 || die "Ohne Datenbank geht es nicht weiter."

log "Datenbank-Migrationen (alembic upgrade head)"
(cd "$BACKEND" && "$VENV/bin/alembic" upgrade head)

log "Seed-Daten"
(cd "$BACKEND" && "$VENV/bin/python" -m app.seed ${SEED_ARGS[@]+"${SEED_ARGS[@]}"})

if frontend_enabled; then
  if command -v npm >/dev/null 2>&1; then
    log "Installiere Frontend-Abhängigkeiten"
    (cd "$FRONTEND" && npm install --no-audit --no-fund)
  else
    warn "npm nicht gefunden – Frontend-Abhängigkeiten nicht installiert (Node.js >= 18 installieren)."
  fi
fi

log "Fertig.  Starten: scripts/start.sh (make start)  ·  Entwicklung: scripts/dev.sh (make dev)"
