# Vermögen

Lokale, private Vermögensverwaltung für einen Nutzer: Konten (Giro, Tagesgeld, Depots), Depot-Tracking mit
Kursen, Rendite und Dividenden, ETF-Durchschau bis auf die einzelnen Unternehmen, Haushaltsbuch mit
Auto-Kategorien, Termin-Kalender, Prognose – und ein Dokument-Import, der Kontoauszüge und
Wertpapierabrechnungen (PDF, CSV, Text, Screenshot) in prüfbare Buchungsentwürfe übersetzt.

Alles läuft auf dem eigenen Rechner: ein Python-Prozess (FastAPI) liefert API und Oberfläche, die Daten
liegen in PostgreSQL, das optionale Sprachmodell läuft über Ollama lokal. Es gibt keine Anmeldung; die App
bindet nur an `127.0.0.1`. Alle Datenquellen sind kostenlos.

> Was hier steht, wurde auf Linux (Ubuntu 24.04, Python 3.13, Node 22, PostgreSQL 16) tatsächlich
> ausgeführt – außer den mit **(ungeprüft)** markierten Punkten. In der Entwicklungsumgebung gab es weder
> Internetzugang zu Yahoo/EZB/Emittenten noch Ollama, Tesseract, Docker-Start oder macOS.

## Voraussetzungen

| Was | Version | Wofür |
|---|---|---|
| Python | ≥ 3.11 (mit `venv`) | Backend |
| Node.js + npm | ≥ 20 | Frontend bauen (einmalig bzw. nach Updates) |
| PostgreSQL | 16, lokal installiert **oder** `docker compose up -d db` (ungeprüft) | Datenbank |
| Ollama | aktuell, optional | Screenshots, Scans und unbekannte Belege lesen |
| Tesseract | optional (`tesseract-ocr`, `tesseract-ocr-deu`) (ungeprüft) | OCR-Vorstufe für Bilder |
| `make`, `bash` | – | Kurzbefehle (die Skripte unter `scripts/` gehen auch direkt) |

PostgreSQL lokal einrichten (einmalig), falls kein Docker benutzt wird:

```bash
# Linux
sudo -u postgres psql -c "CREATE ROLE wealth LOGIN PASSWORD 'wealth' CREATEDB"
sudo -u postgres createdb -O wealth wealth
# macOS (ungeprüft)
brew install postgresql@16 && brew services start postgresql@16
psql postgres -c "CREATE ROLE wealth LOGIN PASSWORD 'wealth' CREATEDB" && createdb -O wealth wealth
```

Ollama (optional, ungeprüft – siehe [docs/INGESTION.md](docs/INGESTION.md) für Modellgrößen):

```bash
ollama serve                       # oder die Ollama-App starten
ollama pull qwen2.5:7b-instruct    # Text  (OLLAMA_TEXT_MODEL in .env)
ollama pull qwen2.5vl:7b           # Bild  (OLLAMA_VISION_MODEL in .env)
```

## Schnellstart

```bash
make setup        # venv + Python-Pakete, .env, Datenbank-Schema, 7 Konten + Kategorien, npm-Pakete
make seed-demo    # optional: synthetischer Demo-Datensatz (~1 400 Buchungen, 8 Wertpapiere, 3 Jahre)
make start        # baut das Frontend bei Bedarf und startet → http://localhost:8000
```

* `make setup` findet eine laufende PostgreSQL-Instanz unter `DATABASE_URL` (Standard
  `postgresql+psycopg://wealth:wealth@localhost:5432/wealth`), legt die Datenbank bei Bedarf an und startet
  sonst – falls Docker vorhanden ist – `docker compose up -d db`. Mit `SKIP_DOCKER=1` wird Docker nie angefasst.
* Konfiguration: `.env` (wird aus `.env.example` erzeugt). Umgebungsvariablen haben Vorrang, z. B.
  `make start PORT=8080` oder `DATABASE_URL=… make start`.
* Demo-Daten wieder entfernen: `cd backend && ../.venv/bin/python -m app.seed --reset-demo` (eigene Daten bleiben).
* Alle Befehle: `make help`. Entwicklung mit Hot-Reload: `make dev` (Backend :8000, Vite :5173).
* API-Dokumentation: http://localhost:8000/docs

## Erste Schritte in der App

Auf der Übersicht steht nach der Installation eine Checkliste (ausblendbar):

1. **Kontostände eintragen** – Einstellungen → *Eröffnungssalden*: je Giro-/Tagesgeldkonto Saldo und Stichtag.
   Wer nur den heutigen Stand kennt, schaltet auf „Heutiger Kontostand“ um; der Eröffnungssaldo wird dann passend
   gesetzt. Konten anlegen, umbenennen, IBAN und Verrechnungskonto: Einstellungen → *Konten*.
   Angelegt sind Postbank Giro, ING Girokonto + Depot, Scalable Depot + Tagesgeld, Trade Republic Depot + Tagesgeld.
2. **Belege importieren** – Seite *Import* (siehe unten). Depotbestände entstehen aus Käufen/Einlieferungen;
   ohne Abrechnung: Transaktionen → *Transaktion erfassen* (Typ „Einlieferung“ oder „Kauf“, neues Wertpapier per ISIN).
3. **Marktdaten aktualisieren** – Einstellungen → *Marktdaten* → „Alles aktualisieren“. Läuft im Hintergrund
   (Fortschritt wird angezeigt) und danach automatisch: Kurse alle 2 Minuten (Mo–Fr 8–22 Uhr), Termine/Historie
   täglich, Fundamentaldaten wöchentlich, ETF-Zusammensetzung monatlich.
4. **Ollama** – Einstellungen → *Lokales Modell* zeigt, ob Ollama erreichbar ist und welche Modelle fehlen.

Danach: *Haushaltsbuch* → Kategorien und Regeln anpassen („Regeln auf offene Buchungen anwenden“),
*Watchlist* pflegen, im *Kalender* fehlende Termine (HV, Investorentage) von Hand eintragen.

## Dokument-Import

Ablauf: Datei hochladen, hineinziehen, Text einfügen oder Screenshot mit Strg+V einfügen → Verarbeitung im
Hintergrund → **Prüfansicht** (Original links, Entwürfe rechts) → korrigieren → übernehmen oder verwerfen.
Nichts wird ungeprüft gebucht.

* Feste Parser (ohne Modell): Trade Republic (Abrechnungen, Dividenden, Zinsen, Kontoauszug), Scalable Capital
  (Baader-Format, CSV), ING (Abrechnungen, Kontoauszug-PDF, CSV), Postbank (Kontoauszug-PDF, CSV), dazu
  eingefügte Umsatzlisten und generische Bank-CSVs. Alles andere geht an das lokale Modell.
* Jeder Entwurf wird nachgerechnet (Stück × Kurs ± Gebühren/Steuern = Betrag, Saldenkette bei Auszügen). Was
  nicht sicher passt, ist mit „Bitte prüfen“ markiert und wird von „Alle übernehmen“ ausgelassen
  („n übersprungen, bitte einzeln prüfen“ → einzeln bestätigen oder „Auch markierte übernehmen“).
* Duplikate (gleiche Buchung schon im Journal, z. B. Abrechnung **und** Kontoauszugszeile) werden erkannt, als
  „Duplikat“ gekennzeichnet und nicht gebucht; „Trotzdem übernehmen“ ist möglich.
* Ohne Ollama schlagen Bilder, Scans und unbekannte Layouts mit einer klaren Meldung fehl; nach dem Start von
  Ollama „Erneut verarbeiten“ wählen.

**Grenzen – bitte ernst nehmen:** Die Parser wurden **ohne echte Dokumente** geschrieben und nur gegen
synthetische Beispiele getestet (`backend/tests/fixtures/ingest/`). Banken ändern Layouts; deshalb gibt es den
Prüfschritt. Das Zusammenspiel mit einem echten Ollama und mit Tesseract ist ungeprüft. Wenn ein Beleg falsch
oder gar nicht gelesen wird:

```bash
cd backend
../.venv/bin/python -m app.ingest.cli ~/Downloads/Abrechnung.pdf --no-llm --text   # zeigt Text, Parser, Entwürfe
```

Die Ausgabe (persönliche Daten ersetzen) reicht, um den Parser anzupassen – bitte solche anonymisierten
Beispiele schicken. Details, Modellwahl und alle Stellschrauben: [docs/INGESTION.md](docs/INGESTION.md).

## Marktdaten

| Daten | Quelle | Grenzen |
|---|---|---|
| Kurse, Historie, Dividenden, Splits, Quartalstermine, Stammdaten | Yahoo Finance über `yfinance` (inoffiziell) | je Börse bis ~15 min verzögert; kann drosseln oder sich ändern |
| Wechselkurse | EZB-Referenzkurse (Yahoo als Ersatz) | ein Fixing je Bankarbeitstag |
| Fundamentaldaten, fairer Wert (Ø-KGV × Gewinn je Aktie) | Yahoo | nur ~4–5 Geschäftsjahre |
| ETF-Zusammensetzung | Emittenten-Downloads, sonst Yahoo Top 10, sonst Upload | siehe unten |

`make selfcheck` prüft alle Quellen live gegen echte Wertpapiere und schreibt nichts in die Datenbank – nach
der Installation und immer dann sinnvoll, wenn Daten fehlen. **Ungeprüft:** In der Entwicklungsumgebung waren
Yahoo, EZB und Emittenten nicht erreichbar; alle Live-Abrufe sind nur gegen Aufzeichnungen/Fakes getestet (ohne
Internet läuft der Selfcheck minutenlang in Wiederholungen – dann abbrechen).

Ohne Internet startet die App normal und rechnet mit den gespeicherten Kursen; die Kopfzeile zeigt
„Yahoo nicht erreichbar“, Einstellungen → *Marktdaten* nennt je Schritt den Grund. Gespeicherte Daten werden
dabei nicht überschrieben. Die Wertpapiersuche (Watchlist) meldet den Ausfall nach wenigen Sekunden und bietet
„Manuell hinzufügen“ per ISIN/Symbol an. Positionen ohne Marktkurs werden mit dem letzten Handelskurs bewertet
und mit „Kein Kurs – Symbol prüfen“ gekennzeichnet (Depot, Watchlist, Wertpapier-Seite).

**Falsches oder fehlendes Symbol korrigieren:** Wertpapier öffnen → *Bearbeiten*. Dort lässt sich das Yahoo-Symbol
per Suche (ISIN oder Name) oder von Hand setzen (z. B. `SAP.DE` für Xetra), außerdem Name, Art, Währung,
Fondsanbieter, IR-Seite und – bei ETFs – eine eigene Download-Adresse der Positionsliste. Nach einer
Symboländerung verwirft die App Kurs, Historie, Termine und Kennzahlen des alten Symbols und lädt alles neu.

**Splits:** Yahoo liefert rückwirkend bereinigte Kurse. Meldet Yahoo einen Split nach dem Kaufdatum, der im
Journal fehlt, zeigt das Depot einen Hinweis mit „Split buchen“ – bis dahin stimmen Stückzahl und Wert nicht.

**Termine ergänzen:** Kalender bzw. Wertpapier-Seite → „Termin eintragen“ oder „Kalenderdatei importieren“
(Finanzkalender der IR-Seite als .ics: Datei, eingefügter Text oder Adresse). Eigene Termine lassen sich
bearbeiten und löschen.

Details und Datenlayouts: [backend/app/market/README.md](backend/app/market/README.md).

### ETF-Durchschau

Die Durchschau zerlegt ETFs in ihre Unternehmen und fasst sie mit direkt gehaltenen Aktien zusammen. Quellen je
ETF, in dieser Reihenfolge: eigene Download-URL → Emittent (iShares und Xtrackers implementiert; Vanguard,
Amundi, SPDR, Invesco „best effort“, alle live ungeprüft) → Yahoo Top 10 (nur ~10 Positionen).

Fehlt eine vollständige Liste, zeigt die Durchschau den Anteil „Nicht aufgeschlüsselt“. Unter *Durchschau →
ETFs im Depot* steht je ETF Quelle, Stand und Abdeckung, mit drei Aktionen (dieselben unter *Zusammensetzung* auf
der ETF-Seite):

* **Vom Anbieter laden** – fragt die Kette oben sofort ab,
* **Hochladen** – Datei von der Produktseite des Emittenten: CSV, XLSX oder JSON mit Spalten wie Name, ISIN,
  Gewichtung (%), optional Sektor und Land; deutsche und englische Zahlenformate werden erkannt, Kopfzeilen über
  der Tabelle stören nicht. Ein Upload wird vom automatischen Abruf nicht überschrieben,
* **Löschen** – entfernt die Positionsdaten des ETF.

## Steuern, Fixkosten

* **Steuern** (eigene Seite): je Jahr realisierte Gewinne/Verluste (FIFO), Dividenden und Zinsen brutto / Steuer /
  netto, genutzter Sparerpauschbetrag und eine grobe Steuerschätzung (25 % + Soli auf den Betrag darüber). Den
  Pauschbetrag (1.000 € / 2.000 € / eigener Wert) stellt man unter *Einstellungen → Steuern* ein. **Nur ein
  Überblick, keine Steuerberatung:** Teilfreistellung, Vorabpauschale, Aktien-Verlusttopf, Quellensteuer und
  Kirchensteuer bleiben außen vor – maßgeblich ist die Jahressteuerbescheinigung der Bank.
* **Fixkosten** (Haushaltsbuch): automatisch erkannte wiederkehrende Ausgaben (gleicher Empfänger, regelmäßiger
  Rhythmus, ähnlicher Betrag) mit Monatssumme, darunter erkannte Sparpläne.
* Manuell erfasste Zinsen landen automatisch in „Kapitalerträge → Zinsen“, Dividenden in „Dividenden“.

## Sicherheit

Die App hat keinen Login und ist für den eigenen Rechner gedacht. Sie beantwortet nur Anfragen an
`localhost`/`127.0.0.1` und weist schreibende Anfragen fremder Webseiten ab. Soll sie im Heimnetz unter einem
anderen Namen erreichbar sein (`HOST=0.0.0.0`), diesen Namen bzw. die IP in `.env` freigeben:
`ALLOWED_HOSTS=nas.local,192.168.1.20` – jeder im Netz kann dann alles lesen und ändern. Hochgeladene Belege
werden nie als Webseite ausgeliefert (nur PDF und Bilder inline, alles andere als Text oder Download).

## Backup

```bash
make backup       # pg_dump (Custom-Format) nach data/backups/vermoegen-<Datum>.dump
pg_restore --clean --if-exists -d "postgresql://wealth:wealth@localhost:5432/wealth" data/backups/vermoegen-….dump
```

Dazu den Ordner `data/uploads/` (Originalbelege) sichern. Mit Docker (ungeprüft):
`docker compose exec -T db pg_dump -U wealth -Fc wealth > vermoegen.dump`.

Zusätzlich in der App unter Einstellungen → *Datenexport und Backup*: alle Buchungen als CSV (Excel-tauglich)
und ein JSON mit allen Tabellen. Das JSON ist zum Nachschlagen und Weiterverarbeiten gedacht; einen
Wiederherstellen-Knopf dafür gibt es nicht – das verlässliche Backup ist der `pg_dump`.

## Was kostenlos nicht geht

| Wunsch | Stand in der App | Kostenpflichtige Alternative (optional) |
|---|---|---|
| Hauptversammlungen, Investorentage, IR-Termine | in keiner kostenlosen API → im Kalender von Hand eintragen (per API auch ICS-Import) | Wall Street Horizon, EQS-Feeds |
| Fundamentaldaten über mehr als 4–5 Jahre (wie aktienfinder) | KGV-Historie und fairer Wert beruhen auf 4–5 Jahren; Dividenden reichen weiter zurück | Financial Modeling Prep, EODHD Fundamentals, aktienfinder-Abo |
| Betrag der nächsten, angekündigten Dividende | Yahoo liefert ihn meist nicht → letzter Betrag bzw. „Schätzung“ | EODHD, Polygon |
| Echtzeitkurse, garantierte Verfügbarkeit | Yahoo inoffiziell, verzögert | Twelve Data, Finnhub, Polygon |
| Vollständige ETF-Bestandteile für jeden Emittenten | Emittenten-Downloads soweit vorhanden, sonst Upload | Morningstar, justETF-Daten |

Die Dividendenprognose ist **brutto** (vor Steuern) und für den aktuellen Bestand gerechnet; geschätzte Werte
sind als „Schätzung“ markiert. Die Vermögensprognose ist eine Monte-Carlo-Spanne ohne Inflation und Steuern.

## Tests

```bash
make test                         # Backend (pytest, eigene Test-Datenbank, kein Netzwerk)
cd frontend && npm run build && npm test
make e2e                          # Browser-Smoke-Test aller Seiten gegen die laufende App (make start)
```

`make e2e` braucht Playwright mit Chromium, global installiert (`npm i -g playwright && npx playwright install chromium` – Installation hier ungeprüft, die Umgebung brachte beides mit);
es ist bewusst keine Projektabhängigkeit. Optionen: `make e2e E2E_ARGS="--dark --shots /tmp/shots"`.

## Fehlersuche

| Symptom | Ursache / Abhilfe |
|---|---|
| `make setup`: „PostgreSQL ist nicht erreichbar“ | Datenbank starten bzw. Rolle/Datenbank anlegen (siehe oben) oder `DATABASE_URL` in `.env` anpassen |
| Seite lädt, aber überall „konnten nicht geladen werden“ | http://localhost:8000/api/health zeigt `degraded` → PostgreSQL läuft nicht; nach dem Start genügt Neuladen |
| Oberfläche zeigt alten Stand nach einem Update | `scripts/start.sh --rebuild` |
| Port 8000 belegt | `make start PORT=8080` |
| „Yahoo nicht erreichbar“, keine Kurse | Internet/Firewall prüfen, `make selfcheck`; Details unter Einstellungen → Marktdaten. Die App rechnet solange mit gespeicherten Kursen |
| Wertpapier ohne Kurs („Kein Kurs – Symbol prüfen“) | Yahoo-Symbol fehlt oder ist falsch → Hinweis anklicken bzw. Wertpapier → *Bearbeiten*, Symbol suchen/eintragen; der Abruf startet danach von selbst |
| Position zeigt nach einem Aktiensplit einen Bruchteil des Werts | Depot → Hinweis „Split nicht gebucht“ → „Split buchen“ |
| Buchgewinn zu hoch nach Depotübertrag | Hinweis „Einstand fehlt“ → Einstandskurs an der Einlieferung nachtragen (oder Auslieferung im alten Depot buchen – die Kaufkurse wandern dann mit) |
| „Unbekannter Host“ (HTTP 400) beim Aufruf über einen anderen Namen/eine IP | `ALLOWED_HOSTS=…` in `.env` setzen (siehe Sicherheit) |
| Import: „Ollama ist nicht erreichbar“ | `ollama serve` starten, Modelle mit `ollama pull …` laden, im Beleg „Erneut verarbeiten“ |
| Import liest einen Beleg falsch | Entwurf in der Prüfansicht korrigieren; `python -m app.ingest.cli <Datei> --no-llm --text` und anonymisiertes Beispiel schicken |
| Kontostand weicht vom Auszug ab | Konto → *Abgleich mit Auszug* zeigt die Differenz je importiertem Auszug; Eröffnungssaldo/Stichtag prüfen, fehlende Auszüge importieren |
| Buchung doppelt / Umbuchung nicht erkannt | Transaktionen: Duplikat löschen; Umbuchungen werden nach jedem Import automatisch verknüpft, wenn IBAN oder Kontoname im Text stehen – reine Betragstreffer erscheinen oben als „Mögliche Umbuchungen“ zum Bestätigen |
| Der automatische Abruf stört (z. B. unterwegs) | `ENABLE_SCHEDULER=false` in `.env` |

Weiterführend: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (Datenmodell, vollständiger API-Vertrag),
[docs/DESIGN.md](docs/DESIGN.md) (Gestaltung), [docs/INGESTION.md](docs/INGESTION.md),
[backend/app/market/README.md](backend/app/market/README.md).
