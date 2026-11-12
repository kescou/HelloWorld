# Vermögen – häufige Aufgaben.  `make help` zeigt die Liste.
# Überschreibbar: VENV=…  PORT=…  HOST=…  DATABASE_URL=…  SKIP_DOCKER=1  SKIP_FRONTEND=1
# Beispiel: make start PORT=8080        make test TEST_DATABASE_URL=postgresql+psycopg://…/wealth_test

SHELL := /bin/bash
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV ?= $(ROOT).venv
PY := $(VENV)/bin/python
TEST_DATABASE_URL ?= postgresql+psycopg://wealth:wealth@localhost:5432/wealth_test_qa
PYTEST_ARGS ?= -q

export VENV
ifdef PORT
export PORT
endif
ifdef HOST
export HOST
endif
ifdef DATABASE_URL
export DATABASE_URL
endif
ifdef SKIP_DOCKER
export SKIP_DOCKER
endif
ifdef SKIP_FRONTEND
export SKIP_FRONTEND
endif

.DEFAULT_GOAL := help
.PHONY: help setup setup-demo dev start test migrate seed-demo reset-demo selfcheck backup

help: ## Diese Übersicht
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-12s %s\n", $$1, $$2}'

setup: ## Einmalig: virtualenv, Abhängigkeiten, .env, Datenbank-Schema, Basisdaten, Frontend-Pakete
	@$(ROOT)scripts/setup.sh

setup-demo: ## Wie setup, zusätzlich Demo-Datensatz
	@$(ROOT)scripts/setup.sh --demo

dev: ## Entwicklung: Backend mit --reload (:8000) + Vite-Dev-Server (:5173)
	@$(ROOT)scripts/dev.sh

start: ## App starten: baut das Frontend bei Bedarf, API + UI auf http://127.0.0.1:8000
	@$(ROOT)scripts/start.sh

test: ## Backend-Tests (eigene Test-Datenbank, wird bei Bedarf angelegt; kein Netzwerk nötig)
	@cd $(ROOT)backend && DATABASE_URL="$(TEST_DATABASE_URL)" $(PY) tests/_ensure_db.py
	@cd $(ROOT)backend && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" DATABASE_URL="$(TEST_DATABASE_URL)" $(PY) -m pytest $(PYTEST_ARGS)

migrate: ## Datenbank-Schema auf den neuesten Stand bringen
	@cd $(ROOT)backend && $(VENV)/bin/alembic upgrade head

seed-demo: migrate ## Demo-Datensatz einspielen (nichts passiert, wenn er schon da ist)
	@cd $(ROOT)backend && $(PY) -m app.seed --demo

reset-demo: migrate ## Demo-Datensatz entfernen und frisch erzeugen (eigene Daten bleiben)
	@cd $(ROOT)backend && $(PY) -m app.seed --reset-demo --demo

selfcheck: ## Marktdaten-Quellen live prüfen (Yahoo, EZB, ETF-Emittenten) – braucht Internet, schreibt nichts
	@cd $(ROOT)backend && $(PY) -m app.market.selfcheck

backup: ## Datenbank-Dump nach data/backups/ (pg_dump, Custom-Format; Wiederherstellen mit pg_restore)
	@mkdir -p $(ROOT)data/backups
	@cd $(ROOT)backend && URL=$$($(PY) -c "from app.core.config import get_settings; print(get_settings().database_url.replace('+psycopg', ''))") \
	  && OUT=$(ROOT)data/backups/vermoegen-$$(date +%Y-%m-%d_%H%M).dump \
	  && pg_dump "$$URL" -Fc -f "$$OUT" && echo "Backup: $$OUT"
