# Vermögen – Architektur & API-Vertrag

Persönliche Vermögensverwaltung + Depot-Tracking (à la Parqet) + Haushaltsbuch, lokal betrieben.
Single-User, keine Auth (bindet nur auf localhost). Alle Integrationen kostenlos.

## Stack

| Schicht | Wahl | Begründung |
|---|---|---|
| DB | PostgreSQL 16 | gewünscht; JSONB für Rohdaten/Fundamentaldaten |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, APScheduler | ein Prozess, serviert auch das Frontend |
| Frontend | React + Vite + TypeScript, TanStack Query, React Router, ECharts | responsive SPA, wird nach `frontend/dist` gebaut |
| Lokales Modell | Ollama (`qwen2.5:7b-instruct` Text, `qwen2.5vl:7b` Vision) | kostenlos, lokal, strukturierte JSON-Ausgabe via `format` |
| Kursdaten | Yahoo Finance via `yfinance` (kostenlos, ~Echtzeit bis 15 min Verzögerung) | Kurse, Historie, Dividenden, Termine, Fundamentaldaten |
| ETF-Durchschau | Emittenten-Downloads (iShares CSV, Xtrackers XLSX, …) + Yahoo Top-10 als Fallback + manueller CSV-Import | vollständige, tagesaktuelle Gewichte nur beim Emittenten |

## Verzeichnisse & Ownership (Scrum-Team)

| Pfad | Owner |
|---|---|
| `backend/app/models/core.py`, `core/`, `main.py`, `tests/conftest.py` | Chief of Staff (Vertrag) |
| `backend/app/api/{accounts,transactions,categories,instruments,portfolio,analytics}.py`, `backend/app/services/`, `backend/app/seed.py`, `backend/alembic/` | Backend-Core |
| `backend/app/models/market.py`, `backend/app/market/`, `backend/app/api/market.py` | Market-Data |
| `backend/app/models/ingest.py`, `backend/app/ingest/`, `backend/app/api/documents.py` | Ingestion |
| `frontend/` | Frontend/Design |
| `backend/app/services/transfers.py`, `backend/app/api/export.py`, `backend/app/market/background.py`, `scripts/`, `Makefile`, `docker-compose.yml` | Backend-Integration (Sprint 2) |

Tests: `backend/tests/test_<domain>_*.py` (+ `test_integration_*.py` für die domänenübergreifenden Abläufe), Test-DB über
`TEST_DATABASE_URL=... pytest` bzw. `make test`. Keine Netzwerkzugriffe in Tests (Yahoo/Ollama mocken).

## Datenmodell (Kurzfassung – maßgeblich ist der Code in `backend/app/models/`)

* `accounts` – Konten (Giro, Tagesgeld, Depot). Depot hat `settlement_account_id` (Verrechnungskonto).
* `instruments` – Wertpapiere (ISIN, Yahoo-Symbol, Typ, Sektor/Land, ETF-Infos), `is_watchlist`.
* `transactions` – EIN Journal für alles. `amount` = vorzeichenbehaftete Cash-Wirkung inkl. Gebühren/Steuern.
  Wertpapier-Tx: `account_id` = Depot, `cash_account_id` = Verrechnungskonto.
* `categories` – Haushaltsbuch-Kategorien mit `match_rules` (Auto-Kategorisierung).
* `balance_snapshots` – bekannte Kontostände aus Auszügen (Abgleich).
* `settings` – Schlüssel/Wert (Demo-Marker, `transfer_link_exclusions` = vom Nutzer aufgelöste Umbuchungs-Paare,
  `tax` = `{sparerpauschbetrag}`).
* `quotes`, `price_history`, `instrument_events`, `etf_holdings`, `fundamentals` – Marktdaten.
* `documents`, `extraction_drafts` – Upload → Extraktion → Prüfen → Übernehmen.

### Saldo-Logik
* Cash-Konto-Saldo = `opening_balance` + Σ `amount` aller Tx mit (`account_id` = Konto und `cash_account_id` NULL) oder `cash_account_id` = Konto.
* Depot-Bestand je Instrument = Σ Stück (buy/delivery_in +, sell/delivery_out −, split multipliziert), Einstand FIFO.
* Depotwert = Σ Stück × aktueller Kurs (in EUR umgerechnet). **Cent-Rundung genau einmal, je Position (Depot ×
  Wertpapier)**: Wert einer Position, Depotwert eines Kontos, `invested_value` und `net_worth` sind Summen dieser
  Cent-Beträge – „Summe der Konten“ und Nettovermögen können nicht um einen Rundungscent abweichen.
* **Depotübertrag:** `delivery_in` ohne Kurs übernimmt die FIFO-Lose (Einstand + Kaufdaten) der passenden
  `delivery_out` (gleiches Wertpapier, gleiche Stückzahl, anderes Depot, ±10 Tage; Reihenfolge egal). Ohne Gegenstück
  und ohne Kurs: Einstand 0 + Warnung „Einstand fehlt“ (`missing_cost`), Kurs an der Buchung nachtragbar.
* **Splits:** Yahoo-Kurse sind rückwirkend bereinigt; ein Split-Termin nach dem Kauf ohne `split`-Buchung im Depot
  (±10 Tage) erscheint als `unbooked_splits` – `POST /portfolio/splits/book` bucht ihn.
* Verkauf über den Bestand hinaus: Bestand endet bei 0 (Valuation **und** Verlauf), Warnung in `summary.warnings`.

## Initiale Konten (Seed)

| Name | institution | type | settlement |
|---|---|---|---|
| Postbank Giro | postbank | checking | – |
| ING Girokonto | ing | checking | – |
| ING Depot | ing | brokerage | ING Girokonto |
| Scalable Depot | scalable | brokerage | Scalable Tagesgeld |
| Scalable Tagesgeld | scalable | savings | – |
| Trade Republic Depot | trade_republic | brokerage | Trade Republic Tagesgeld |
| Trade Republic Tagesgeld | trade_republic | savings | – |

## Betrieb

```bash
make setup            # venv, Abhängigkeiten, .env, DB-Schema, Basisdaten (make setup-demo: + Demo-Datensatz)
make start            # baut das Frontend bei Bedarf, API + UI auf http://127.0.0.1:8000
make dev              # uvicorn --reload (:8000) + Vite (:5173)
make test             # Backend-Tests (legt die Test-DB bei Bedarf an)
make e2e              # Browser-Smoke-Test (Playwright) aller Seiten gegen die laufende App, siehe „Frontend“
make seed-demo | reset-demo | selfcheck | backup | migrate
```

Die Skripte (`scripts/*.sh`, bash 3.2-kompatibel → macOS + Linux) akzeptieren Umgebungsvariablen:
`VENV`, `PYTHON`, `HOST`, `PORT`, `DATABASE_URL`, `UPLOAD_DIR`, `ENABLE_SCHEDULER`, `SKIP_DOCKER=1`, `SKIP_FRONTEND=1`,
`WITH_OCR=1` – z. B. `PORT=8080 DATABASE_URL=postgresql+psycopg://…/wealth2 scripts/start.sh`.
Datenbank: läuft schon ein PostgreSQL unter `DATABASE_URL`, wird es benutzt (fehlende Datenbank wird angelegt);
sonst wird `docker compose up -d db` gestartet; sonst erscheint eine Anleitung (Rolle/DB lokal anlegen).
Konfiguration: `.env` (Vorlage `.env.example`), Umgebungsvariablen haben Vorrang. Relative Pfade (`UPLOAD_DIR`) gelten
relativ zum Repo-Verzeichnis.

Abhängigkeiten: `backend/requirements.txt` (Laufzeit), `backend/requirements-dev.txt` (+ pytest, reportlab),
`backend/requirements-ocr.txt` (optional: pytesseract; braucht zusätzlich das `tesseract`-Binary).

**Startverhalten** (`app.main.lifespan`): Dokumente, die ein früherer Prozess in `uploaded`/`processing` hinterlassen
hat, werden neu eingereiht; der Marktdaten-Scheduler startet (wenn `ENABLE_SCHEDULER=true`) und stoppt beim Beenden.
Der Start scheitert nie an der Außenwelt: ohne Datenbank antwortet `/api/health` mit `status: "degraded"` und alle
DB-Endpunkte mit **503** `{detail}`; ohne Ollama laufen die festen Parser (`/api/documents/llm/status`); ohne
Yahoo/EZB stehen die Fehler in `/api/market/status` und die App rechnet mit den gespeicherten Kursen (Positionen ohne
Kurs: letzter Transaktionskurs, `price_source: "transaction"`).

**Performance** (Demo-Datensatz, ~1 400 Transaktionen, 3 Jahre Tageswerte): alle Dashboard-Endpunkte < 100 ms
(`portfolio/history|performance` ~50–70 ms) – kein Cache nötig. Ausnahmen mit Netzwerk: `market/search` und der erste
`market/fundamentals/{id}` eines Instruments ohne gespeicherte Daten (danach aus der DB; nach einem Fehlschlag 15 min Pause).

## REST-API (Prefix `/api`, JSON)

**Konventionen**
* Beträge sind JSON-Zahlen in **EUR**, sofern nicht anders angegeben; Datum `YYYY-MM-DD`, Zeitstempel ISO 8601 (UTC).
* **Prozentwerte sind Prozentpunkte**: `12.3` bedeutet 12,3 % (gilt für `*_pct`, `weight`, `share`, `twr`, `xirr`,
  `savings_rate`, `yield_on_cost`, `portfolio_yield`, `coverage_pct`, `weight_in_etf`, `interest_rate`, alle Quoten in
  `fundamentals`). Ausnahme: `earnings_stability`/`dividend_stability` (0…1) und `confidence` der Entwürfe (0…1).
* `amount` einer Transaktion = vorzeichenbehaftete Cash-Wirkung inkl. Gebühren/Steuern; `fees`/`taxes` positive Abzüge.
* Fehler: `{"detail": "<deutsche Meldung>"}` (bei Validierungsfehlern von FastAPI eine Liste) mit 404/409/422;
  unbekannte `/api/...`-Pfade → 404 JSON (nie die SPA); Datenbank nicht erreichbar → 503.
* `GET /health` → `{status: "ok"|"degraded", database: bool, version, error?}` (immer 200).
* Nie `NaN`/`Infinity` im JSON; fehlende Werte sind `null`.

### Konten – Backend-Core
* `GET /accounts?include_inactive=true` → `[Account]`,
  Account = `{id,name,institution,type,currency,iban,account_number,color,opening_balance,opening_date,interest_rate,settlement_account_id,is_active,sort_order,notes,balance,market_value,total_value}`
  (`type`: `checking|savings|brokerage|credit_card|other`; `balance` = Cash-Saldo, `market_value` = Depotwert bei Depots,
  `total_value` = Summe. Depots mit Verrechnungskonto haben `balance` 0 – das Geld liegt auf dem Verrechnungskonto.)
* `POST /accounts` (201), `PATCH /accounts/{id}`
* `DELETE /accounts/{id}` → `{deleted, deactivated, transaction_count}` – Konten mit Buchungen werden nur deaktiviert.
* `GET /accounts/{id}?days=730` → Account + `history: [{date, value}]` (Tageswerte Cash + Depotwert)
  + `snapshots: [{id, date, balance, computed_balance, difference, source_document_id}]` (Abgleich: Kontostand laut Auszug
  vs. Ledger-Saldo am Ende dieses Tages, `difference = balance − computed_balance`; neueste zuerst)

### Transaktionen – Backend-Core
* `GET /transactions?account_id=&instrument_id=&type=&category_id=&q=&date_from=&date_to=&uncategorized=&limit=100&offset=0`
  → `{items:[Tx], total}`; `type` darf kommagetrennt sein; `account_id` trifft Depot- UND Verrechnungskonto-Seite;
  `q` sucht in Gegenpartei, Text, Notiz, Instrument (Name/ISIN/Symbol), Kategorie; `limit` ≤ 5000; neueste zuerst.
  Tx = `{id,account_id,cash_account_id,instrument_id,type,booking_date,value_date,quantity,price,amount,fees,taxes,currency,fx_rate,category_id,counterparty,description,transfer_peer_id,source_document_id,dedupe_hash,notes,created_at}`
  + `account_name, cash_account_name, instrument_name, instrument_isin, instrument_symbol, category_name`.
  `type`: `buy|sell|dividend|interest|deposit|withdrawal|transfer_in|transfer_out|fee|tax|split|delivery_in|delivery_out`.
  `price` in `currency`, `fx_rate` = 1 EUR in `currency`; `amount` immer EUR.
* `GET /transactions/{id}` → Tx
* `POST /transactions` (201; TransactionCreate = Tx-Felder + `isin|wkn|symbol|name|instrument_type` statt `instrument_id`
  + `category_hint`, `allow_duplicate`). `amount` darf fehlen → aus quantity × price ± fees/taxes; Vorzeichen wird
  normalisiert (Kauf/Gebühr negativ; negative `deposit` wird `withdrawal` und umgekehrt); `cash_account_id` = Verrechnungskonto
  des Depots; unbekanntes Instrument wird angelegt; Kategorie automatisch (siehe unten). `keep_sign:true` lässt das
  Vorzeichen einer `fee` unangetastet (positive Gebühr = Erstattung). Duplikat (gleicher
  Konto/Typ/Datum/ISIN/Stück/Betrag-Hash) → **409** `{detail:{message, existing_id}}`.
* `PATCH /transactions/{id}` (nur gesendete Felder), `DELETE /transactions/{id}` → `{deleted:[ids]}`.
  PATCH rechnet wie POST: neuer Betrag aus quantity × price ÷ `fx_rate` ± fees/taxes (Fremdwährung!), bei neuem
  `account_id` folgt das Verrechnungskonto (außer `cash_account_id` kommt mit), bei neuem `type` wird das Vorzeichen
  normalisiert (buy→sell positiv); ein explizit positiver Betrag einer `fee` bleibt (Erstattung).
* `POST /transactions/transfer` `{from_account_id,to_account_id,amount,date,description?}` → `[Tx(out), Tx(in)]` (verknüpft)
* `POST /transactions/link-transfers` → `{linked, retyped, pairs:[{out_id,in_id,amount,from_account_id,to_account_id,date,reason}]}`
  + `suggestions:[{out_id,in_id,amount,from_account_id,to_account_id,date,in_date,out_text,in_text,ambiguous,reason:"amount"}]`
  – sucht Umbuchungen zwischen eigenen Konten unter allen unverknüpften Cash-Buchungen (siehe „Umbuchungen“).
* `GET /transactions/transfer-suggestions` → dieselben `suggestions` (ändert nichts),
  `POST /transactions/link-transfers/confirm` `{out_id,in_id}` → verknüpft ein bestätigtes Paar (422 wenn Beträge/Konten
  nicht passen oder schon verknüpft).

**Eine Stelle für neue Buchungen:** API, Demo-Seed und Dokument-Import buchen alle über
`app.services.ledger.create_transaction`.

**Auto-Kategorie** (nur wenn keine `category_id` mitkommt, nie für Wertpapierbuchungen): `match_rules` der Kategorien
(Teilstring ohne Groß/Klein, längste Regel gewinnt, Regeln ≤ 4 Zeichen nur als ganzes Wort; für
`deposit|withdrawal|fee|interest|tax`) > Kategorie mit dem Namen des `category_hint` > Namensähnlichkeit zum Hint.
Dividenden → „Dividenden“, Zinsen ohne Regel-/Hint-Treffer → „Zinsen“ (Kapitalerträge), Umbuchungen → „Umbuchung“;
`POST /categories/auto-assign` holt das für alte unkategorisierte Zins-/Dividendenbuchungen nach.

**Umbuchungen zwischen eigenen Konten** (`app.services.transfers`): Paar = entgegengesetzter Betrag auf zwei eigenen
Konten, Buchungstage höchstens 3 Tage auseinander. Belege (`reason`): `iban` (IBAN des anderen Kontos im Text bzw. vom
Import erkannt), `typed` (schon als Umbuchung typisiert), `name` (Kontoname oder Bank des anderen Kontos im Text),
`amount` (nur Betrag + Datum, keine Seite trägt eine Einnahme/Ausgabe-Kategorie) – **nie automatisch**: solche Paare
kommen nur als `suggestions` zurück und werden erst nach Bestätigung durch den Nutzer verknüpft. Ergebnis:
`transfer_out`/`transfer_in`, gegenseitige `transfer_peer_id`, Kategorie „Umbuchung“ (falls leer bzw. nur per Regel
gesetzt). Zeilen, die per IBAN auf ein eigenes Konto zeigen, deren Gegenbuchung aber noch fehlt, werden nur typisiert
und beim nächsten Lauf verknüpft. Läuft automatisch nach jedem übernommenen Import-Entwurf. Cashflow/Sparquote/Ausgaben
zählen `transfer_*` nie. Rückgängig: `PATCH type` auf `deposit|withdrawal` löst die Verknüpfung auf beiden Seiten und
merkt sich das Paar (wird nicht erneut verknüpft). `DELETE` löscht bei manuell angelegten Umbuchungen beide Seiten, bei
importierten nur die eine (die andere bleibt als unverknüpfte Umbuchung).

### Kategorien – Backend-Core
* `GET /categories` → `[{id,name,kind,parent_id,color,match_rules,transaction_count}]` (`kind`: `income|expense|transfer|investment`)
* `POST /categories` (201; 409 bei gleichem Namen unter derselben Oberkategorie), `PATCH /categories/{id}`
* `DELETE /categories/{id}` → `{deleted, uncategorized_transactions}` (Buchungen werden unkategorisiert, Unterkategorien rücken auf)
* `POST /categories/auto-assign` → `{assigned, updated, uncategorized}` (`assigned` = `updated` = Anzahl neu zugeordneter Buchungen) (wendet `match_rules` auf unkategorisierte Buchungen an)

### Instrumente – Backend-Core (+ Market-Data für Daten)
* `GET /instruments?q=&watchlist=&held=` → `[Instrument]`,
  Instrument = `{id,isin,wkn,symbol,name,type,currency,exchange,sector,industry,country,fund_provider,ter,distribution,logo_url,website,ir_url,meta,is_watchlist,created_at}`
  + `price, price_currency, day_change_pct, quantity, quote` (`quote` wie `/market/quotes` oder `null`; `ter` in Prozent, `0.20` = 0,20 %;
  `type`: `stock|etf|fund|bond|crypto|commodity|other`)
* `POST /instruments` `{isin|symbol, …}` – get-or-create: **201** neu, **200** wenn schon vorhanden (füllt leere Felder,
  setzt `is_watchlist`). Marktdaten kommen beim nächsten Kurs-Lauf bzw. `POST /market/refresh`.
* `PATCH /instruments/{id}` (Stammdaten: `symbol,name,type,currency,fund_provider,ir_url,is_watchlist,…`).
  * **`symbol` geändert** → Kurs, Kurshistorie, Yahoo-/Schätz-Termine und Fundamentaldaten des alten Symbols werden
    verworfen (eigene Termine `manual`/`ics` und ETF-Positionen bleiben). Danach
    `POST /market/refresh?wait=false {scope:"quotes", instrument_ids:[id]}` – lädt Kurs + volle Historie neu; bleibt
    der Kurs aus, meldet der Job `status:"error"` („kein Kurs für X“).
  * **`meta` wird gemerged**, nicht ersetzt: `{"meta":{"market":{"holdings_url":"https://…"}}}` setzt die eigene
    Download-Adresse der ETF-Positionsliste; `null`/`""` entfernt den Schlüssel. URLs müssen `http(s)://` sein (422).
* `GET /instruments/{id}` → Instrument + `quote` (wie `/market/quotes`) + `position` (wie ein Eintrag aus
  `/portfolio/holdings`, sonst `null`) + `transactions: [Tx]`

### Portfolio – Backend-Core
* `GET /portfolio/summary` → `{net_worth, cash_total, invested_value, cost_basis, unrealized_gain, unrealized_gain_pct, realized_gain_ytd, dividends_ytd, interest_ytd, day_change, day_change_pct, by_account:[{account_id,name,type,institution,color,value,balance,market_value}], updated_at, fx_warning, warnings:[str]}`
  + `unbooked_splits:[{event_id,instrument_id,name,date,ratio,title,account_ids}]`, `missing_cost:[{transaction_id,instrument_id,name,account_id,date,quantity}]`
  (`net_worth = cash_total + invested_value` = Σ `by_account.value` aller Konten; `dividends_ytd`/`interest_ytd` netto;
  `warnings` z. B. „Verkauf übersteigt Bestand“, „Split nicht gebucht“, „Einstand fehlt“)
* `POST /portfolio/splits/book` `{instrument_id,date,ratio}` (201) → `{booked, transaction_ids}` – bucht den Split in jedem
  Depot, das das Papier vor dem Datum hielt (409: nichts zu buchen / schon gebucht). Positionen tragen dieselben
  `unbooked_splits`/`missing_cost`.
* `GET /portfolio/holdings?account_id=` → `[{instrument_id,isin,symbol,name,type,quantity,avg_cost,cost_basis,price,price_currency,price_eur,price_source,market_value,day_change,day_change_pct,unrealized_gain,unrealized_gain_pct,weight,dividends_received,realized_gain,sector,country,currency,accounts:[{account_id,quantity,cost_basis,market_value}]}]`
  (`price` in `price_currency`, alles andere EUR; `price_source`: `quote|history|transaction`; `weight` = Anteil am
  Depotwert, Summe 100; Einstand FIFO; nach `market_value` absteigend)
* `GET /portfolio/history?range=1W|1M|3M|6M|YTD|1Y|2Y|3Y|5Y|10Y|MAX&account_id=` → `[{date, net_worth, invested, cost_basis, cash}]`
  (täglich; `invested` = Depotwert, `net_worth = invested + cash`)
* `GET /portfolio/performance?range=&account_id=&scope=all|investments` → `{twr, xirr, absolute_gain, start_value, end_value, net_flows, range, scope, series:[{date, twr_index, twr}]}`
  (`twr` kumuliert in %, `xirr` % p. a. oder `null`, `twr_index` startet bei 100; `scope=investments` = nur Depots)
* `GET /portfolio/allocation?by=type|sector|country|account|currency&lookthrough=false&account_id=` → `[{key, value, weight}]`
  (`type`/`account`/`currency` enthalten Cash; `sector`/`country` nur Depotwert, ETFs ohne Durchschau unter `key: "ETF"`,
  mit `lookthrough=true` aufgelöst, Rest `Unbekannt`)

### Analytics – Backend-Core
* `GET /analytics/dividends?year=` → `{year, received_by_month:[{month,amount,gross,taxes}], received_by_year:[{year,amount,gross,taxes}], forecast_12m:[{month,amount,items:[{instrument_id,name,symbol,amount,per_share,currency,quantity,ex_date,pay_date,is_estimate,source}]}], forecast_12m_total, yield_on_cost, portfolio_yield}`
  (`month` = `YYYY-MM`; `amount` netto EUR; `forecast_12m` = **genau 12 Einträge**: laufender Kalendermonat (ganzer Monat)
  + 11 Folgemonate nach Zahltag, **brutto**, aus den Terminen × aktuellem Bestand; `per_share` in `currency`;
  `source`: `events` (Termin bekannt/geschätzt) | `extrapolated` (Zahlung der letzten 11 Monate ein Jahr fortgeschrieben,
  für Titel ohne Termine) | `received` (im laufenden Monat schon erhalten); die Renditen beziehen sich auf `forecast_12m_total`)
* `GET /analytics/cashflow?months=12` → `[{month, income, expenses, savings, savings_rate}]`
  (nur Cash-Konten; ohne Umbuchungen, Wertpapierhandel und Kategorien der Art `transfer|investment`; `savings_rate` min. −100)
* `GET /analytics/expenses-by-category?date_from=&date_to=` → `[{category_id,name,amount,share,color,parent_id,parent_name,count}]`
* `GET /analytics/forecast?years=10&monthly_savings=&expected_return=&volatility=&start_value=` →
  `{series:[{year,p10,p50,p90,contributions}], start_value, monthly_savings, expected_return, volatility, years, start_year}`
  (Monte-Carlo; `expected_return`/`volatility` als Prozent `6` oder Bruchteil `0.06`, Antwort in Prozent; Standard: 6 % / 15 %,
  Startwert = Nettovermögen, Sparrate = Ø der letzten 12 Monate; `series[0]` = heute; `series[].year` ist der **Abstand in
  Jahren** (0…`years`), das Kalenderjahr ist `start_year + year`)
* `GET /analytics/taxes?year=` → Steuerübersicht eines Kalenderjahres (**Schätzung, keine Steuerberatung**):
  `{year, years:[…], is_estimate:true, realized:{gains,losses,net,taxes,count,by_class:[{key:"stock"|"fund"|"other",gains,losses,net,taxes,count}]}, dividends:{gross,taxes,net,count}, interest:{gross,taxes,net,count}, other_taxes:{amount,count}, capital_income, allowance, allowance_used, allowance_remaining, taxable_income, tax_rate, estimated_tax, taxes_withheld, difference}`
  (`capital_income` = realisierter Saldo + Dividenden brutto + Zinsen brutto; `allowance` = Sparerpauschbetrag aus den
  Einstellungen; `estimated_tax` = `taxable_income` × 26,375 %; nicht berücksichtigt: Teilfreistellung, Vorabpauschale,
  Aktien-Verlusttopf, Quellensteuer-Anrechnung, Kirchensteuer)
* `GET /analytics/recurring` → `{items:[{key,kind:"fixed"|"savings_plan",name,instrument_id,category_id,category_name,account_id,account_name,interval:"monthly"|"quarterly"|"yearly",amount,monthly_amount,last_amount,count,last_date,next_date}], fixed_monthly_total, fixed_count, savings_plan_monthly_total, savings_plan_count}`
  (erkannt: gleicher Empfänger bzw. gleiches Wertpapier, regelmäßiger Rhythmus – (fast) alle Abstände passen –, ähnlicher
  Betrag ±25 %, zuletzt noch aktiv; nur Ausgaben auf Cash-Konten ohne Umbuchungen; 800 Tage Rückblick)
* `GET /analytics/realized-gains?year=` → `[{instrument,instrument_id,isin,account_id,transaction_id,date,quantity,proceeds,cost,gain,taxes,fees,gain_after_tax}]`

### Einstellungen
* `GET /settings` → `{sparerpauschbetrag}` (Standard 1000), `PATCH /settings` `{sparerpauschbetrag}` (0 … 100 000)

### Marktdaten – Market-Data (Details: `backend/app/market/README.md`)
* `POST /market/refresh?wait=` `{scope:"quotes"|"events"|"history"|"etf"|"fundamentals"|"fx"|"profile"|"all", instrument_ids?, force?}` (Body optional, Standard `quotes`)
  * **synchron** (Standard für alle Scopes außer `all`, oder `?wait=true`): Job-Ergebnis
    `{scope, status:"ok"|"partial"|"error"|"busy", errors:[{instrument_id,symbol,error}], duration_ms, …}`;
    bei `all`: `{scope:"all", status, duration_ms, jobs:{fx:{…},quotes:{…},profile:{…},history:{…},events:{…},fundamentals:{…},etf:{…}}}`
  * **im Hintergrund** (Standard für `all`, oder `?wait=false`): antwortet sofort mit
    `{job_id, scope, instrument_ids, force, status:"running", background:true, started_at, finished_at:null, progress:{done,total,current}, result:null}`.
    Läuft schon ein Hintergrund-Job, kommt dieser zurück (`already_running:true`), es wird nichts Zweites gestartet.
  * Antwortet immer 200 – nicht erreichbare Quellen stehen in `status`/`errors`.
* `GET /market/refresh/{job_id}` → derselbe Job; nach Ende `status: ok|partial|error`, `finished_at`, `result` = Job-Ergebnis
  (nur im Speicher: nach einem Neustart 404).
* `GET /market/status` → `{source, provider, online, last_quote_update, errors:[str], error_log:[Log], jobs:[Log], recent:[Log], scheduler:{enabled,running,market_hours,jobs:[{id,next_run}]}, refresh:{running, current:Job|null, last:Job|null}, counts:{tracked_instruments,without_symbol,quotes,price_history_rows,events,etfs_with_holdings,fundamentals}, quotes_updated_at, fx_latest_date, sources}`
  (Log = `{id,job,status,detail,created_at}`; `jobs` = letzter Lauf je Job; `errors` = aktuelle Probleme als Text
  `"<job>: <detail>"` – Jobs, deren LETZTER Lauf nicht ok war, verschwindet nach dem nächsten Erfolg; `error_log` = die
  letzten 10 Nicht-OK-Läufe; `online` = letzter Kurs-/FX-Lauf hat die Quelle erreicht, `null` solange noch nie gelaufen;
  `source` = `provider` = `"yahoo"`; `last_quote_update` = `quotes_updated_at`)
* `GET /market/quotes?instrument_ids=1,2` (ohne Parameter: gehaltene + Watchlist) → `[{instrument_id,price,previous_close,change,change_pct,currency,market_time,updated_at,source}]`
* `GET /market/history/{instrument_id}?range=1W|1M|3M|6M|YTD|1Y|2Y|3Y|5Y|10Y|MAX` → `[{date, close}]` (Kurswährung)
* `GET /market/events?from=&to=&types=&instrument_id=` → `[{id,instrument_id,instrument_name,symbol,isin,type,date,amount,currency,title,url,is_estimate,source,position_quantity,expected_payment}]`
  (nur gehaltene + Watchlist; Standardfenster −30 … +365 Tage; `types` kommagetrennt aus
  `ex_dividend|dividend_payment|earnings|agm|investor_day|split|other`; `amount` je Aktie in `currency`,
  `expected_payment` brutto in EUR; `source`: `yahoo|estimate|manual|ics|demo`)
  Mit `instrument_id` auch für Papiere, die weder gehalten noch beobachtet werden.
* `POST /market/events` `{instrument_id,type,date,amount?,currency?,title?,url?,is_estimate?}`, `PATCH|DELETE /market/events/{id}` (manuelle Termine;
  ein bearbeiteter Termin bekommt `source:"manual"` und wird von Yahoo-Abgleichen nicht mehr überschrieben)
* `POST /market/events/import-ics?instrument_id=` (multipart `file` | `text` | `url`, optional `only_future`) → `{parsed, imported}`
  (Termine bekommen `source:"ics"`; 422 ohne VEVENT, 502 wenn die URL nicht ladbar ist)
* `GET /market/fundamentals/{instrument_id}?refresh=false` → aktienfinder-artige Daten (auch für Demo-Daten dieses Layout):
  `{instrument_id, available, currency, financial_currency, fx_factor, years:[{year,period_end,revenue,net_income,eps,dps,fcf,payout_ratio,shares,fcf_per_share,fcf_payout_ratio,is_estimate}], dividends_by_year:[{year,dps,partial}], pe_history:[{date,pe}], fair_value_series:[{date,price,eps,fair_value}], fair_value:{method,value,pe_avg,pe_median,pe_months,eps_forward,eps_ttm,eps_basis,value_pe,value_dividend_yield,dividend_yield_avg,dividend_basis,price,upside_pct,verdict}, dividend_growth:{cagr_3y,cagr_5y,cagr_10y,years_raised,years_not_cut,years_paid,last_full_year}, valuation:{pe,forward_pe,pb,dividend_yield,dividend_rate,market_cap,payout_ratio,shares_outstanding,price}, earnings_quality:{earnings_stability,dividend_stability,eps_cagr,revenue_cagr,loss_years,payout_ratio_avg,fcf_payout_ratio_avg,score,years_of_data}, source, updated_at}`
  (`available:false` + leere Listen, solange nichts geladen ist – z. B. ETFs; liefert Yahoo beim Aktualisieren gar nichts
  (offline), bleiben gespeicherte Werte unverändert; `verdict`: `undervalued` (> +10 %) | `fair` | `overvalued`;
  Bilanzwerte in `financial_currency`, Kurse/Dividenden/Fair Value in `currency`)
* `GET /market/etf/{instrument_id}/holdings` → `{instrument_id,name,isin,as_of,source,issuer,coverage_pct,residual_pct,residual_label,count,holdings:[{name,isin,symbol,weight,sector,country,asset_class}],attempts}`
* `POST /market/etf/{instrument_id}/holdings/upload` (multipart `file` CSV/XLSX/JSON, optional `as_of`; 422 mit deutscher
  Meldung, wenn keine Namens-/ISIN- und Gewichtsspalte erkannt wird), `DELETE …/holdings`
* `POST …/holdings/refresh?force=` → `{status:"ok"|"kept"|"unavailable"|"error", source, rows?, coverage_pct?, as_of?, attempts:[{provider,ok,rows?,error?}]}`
  (Kette: `meta.market.holdings_url` → Emittent → Yahoo Top 10; `kept` = manueller Upload bleibt ohne `force`)
* `GET /market/lookthrough?account_id=` → **kombinierte Liste ETF-Anteile + Einzelaktien**:
  `{total_value, coverage_pct, items:[{key,name,isin,symbol,direct_value,etf_value,total_value,weight,sector,country,kind,sources:[{instrument_id,name,value,weight_in_etf,kind}]}], by_sector:[{key,label,value,weight}], by_country:[{key,value,weight}], etfs:[{instrument_id,name,isin,value,coverage_pct,source,as_of,count}], unpriced:[…]}`
  (`total_value` = Depotwert = Σ `items.total_value`; `kind`: `company|other|…`; nicht aufgelöster ETF-Rest unter `key:"_other"`
  – das UI zeigt ihn als „Nicht aufgeschlüsselt“, nicht als Unternehmen; `by_sector[].label` = deutscher Name;
  `etfs` = alle gehaltenen ETFs mit Durchschau-Status; summieren die Gewichte eines ETF auf > 100 %, werden sie auf 100 %
  skaliert; Namensabgleich ohne ISIN kennt beide Umlaut-Schreibweisen (ü→ue und ü→u) und abgeschnittene Namen ≥ 12 Zeichen)
* `GET /market/search?q=&limit=10` → `[{symbol,name,exchange,type,currency,isin,score,source,preferred}]` (live bei Yahoo, 4 s Timeout ohne Wiederholung;
  Yahoo nicht erreichbar → **503** `{detail}` nach < 1–4 s – das UI bietet dann das Anlegen per ISIN/Symbol an)
* `GET /market/fx` → `{date, base:"EUR", rates:{USD:1.124,…}}` (1 EUR = x Fremdwährung)

### Dokumente – Ingestion (Details: `docs/INGESTION.md`)
* `POST /documents` multipart (`files` | `files[]` | `file`, optional `account_id`) → `[Document]`; Verarbeitung im Hintergrund
  (Status pollen). Identische Datei (sha256) → vorhandenes Dokument mit `duplicate_upload:true`.
* `POST /documents/paste` `{text, account_id?}` → Document
* `GET /documents?status=&limit=200&offset=0` (`status` kommagetrennt) → `[Document]`,
  Document = `{id,filename,mime_type,source_kind,status,account_id,detected_institution,detected_doc_type,parser,draft_count,pending_count,duplicate_count,accepted_count,error,warnings,created_at,processed_at,processing_ms,used_llm,has_file,duplicate_upload}`
  (`status`: `uploaded → processing → review → imported` | `failed`; `source_kind`: `pdf|image|text|csv|paste`)
* `GET /documents/{id}` → Document + `drafts:[Draft]` + `raw_text` + `timings` + `pages`,
  Draft = `{id,document_id,kind,payload,confidence,status,transaction_id,balance_snapshot_id}`
  (`kind`: `transaction|balance`; `status`: `pending|accepted|rejected|duplicate`)
* `GET /documents/{id}/file` → Originaldatei. Uploads sind nicht vertrauenswürdig: inline nur echte PDFs und
  Rasterbilder (am Dateianfang erkannt, nicht am Namen); CSV/TXT – und HTML/SVG – als `text/plain`, alles andere als
  Download; immer `X-Content-Type-Options: nosniff`.
* `PATCH /documents/drafts/{draft_id}` `{payload, status?}` – Payload wird gemerged und neu validiert; `status:"pending"`
  öffnet einen abgelehnten/Duplikat-Entwurf wieder; übernommene Entwürfe → 409
* `POST /documents/drafts/{draft_id}/accept?force=false` → Draft. Bucht über den Ledger-Service (Auto-Kategorie über
  `match_rules`, Umbuchungs-Verknüpfung). Duplikat → **409** `{detail:{message, transaction_id, draft}}` und der Entwurf
  bekommt Status `duplicate`; `force=true` bucht trotzdem. Fehlende Pflichtangaben → 422 (deutsche Meldung).
* `POST /documents/drafts/{draft_id}/reject` → Draft
* `POST /documents/{id}/accept-all?include_flagged=false` → `{accepted, skipped, duplicates, errors:[{draft_id,error}], document}`
  (Entwürfe mit `payload.review_required` werden ohne `include_flagged` übersprungen)
* `POST /documents/{id}/reprocess` `{force_llm?: bool}` → Document
* `DELETE /documents/{id}?delete_transactions=false` → `{deleted, id, transactions_deleted}`
* `GET /documents/llm/status` → `{available, models, text_model, vision_model, text_model_available, vision_model_available, ocr_available, url, error, hint}`

Draft-`payload` (Transaktion): `{account_id, type, booking_date, value_date, isin, wkn, name, symbol, quantity, price, amount, fees, taxes, currency, fx_rate, counterparty, description, category_hint}`
+ `warnings:[str], review_required, verified, dup_index, duplicate_of, gross_amount, reference, counterparty_iban, transfer_account_id`.
Draft-`payload` (Kontostand): `{account_id, date, balance}`.
Import-Besonderheiten beim Übernehmen: die n-te identische Zeile EINES Auszugs (`dup_index` n ≥ 1) bekommt den
Hash-Zusatz `|n` und wird als eigene Buchung gebucht; eine fremde Gegen-IBAN landet in `notes` („Gegenkonto-IBAN: …“).

### Datenexport & Backup
* `GET /export/transactions.csv?account_id=&date_from=&date_to=&format=de|en` → CSV-Download aller Buchungen (älteste zuerst).
  `de` (Standard): Semikolon, Dezimalkomma, UTF-8 mit BOM (Excel); `en`: Komma, Dezimalpunkt. Spalten:
  `id,booking_date,value_date,account,cash_account,type,isin,symbol,instrument,quantity,price,amount,fees,taxes,currency,fx_rate,category,counterparty,description,notes,transfer_peer_id,source_document_id`
* `GET /export/backup.json?include_market_history=false` → JSON-Download
  `{meta:{app,format,created_at,include_market_history,tables:{name:rows}}, tables:{accounts:[…],instruments:[…],categories:[…],documents:[…],transactions:[…],balance_snapshots:[…],extraction_drafts:[…],settings:[…],fx_rates:[…],quotes:[…],instrument_events:[…],etf_holdings:[…],fundamentals:[…]}}`
  – alle Tabellen zeilenweise (Dezimalzahlen als Strings, verlustfrei); `price_history` und `market_data_log` nur mit
  `include_market_history=true` (groß, wiederbeschaffbar). Hochgeladene Originaldateien liegen in `data/uploads/`.
* **Vollständiges, wiederherstellbares Backup** (empfohlen, `make backup` macht genau das nach `data/backups/`):
  ```bash
  pg_dump "postgresql://wealth:wealth@localhost:5432/wealth" -Fc -f vermoegen-$(date +%F).dump
  # Docker:  docker compose exec -T db pg_dump -U wealth -Fc wealth > vermoegen-$(date +%F).dump
  # Restore: pg_restore --clean --if-exists -d "postgresql://wealth:wealth@localhost:5432/wealth" vermoegen-2026-11-12.dump
  ```
  Dazu den Ordner `data/uploads/` sichern.

## Sicherheit (lokale App ohne Login)

Middleware `local_requests_only` (`app/main.py`): beantwortet werden nur Anfragen, deren `Host` `localhost`, `127.0.0.1`,
`[::1]` oder in `ALLOWED_HOSTS` (kommagetrennt in `.env`, `*` = alle) steht – sonst **400**. Schreibende Anfragen
(POST/PUT/PATCH/DELETE) mit `Origin`-Header müssen von einem dieser Hosts stammen – sonst **403**. Das verhindert
DNS-Rebinding und Cross-Site-Requests fremder Webseiten gegen die lokale API. Wer die App im Heimnetz unter einem anderen
Namen öffnet (`HOST=0.0.0.0`), trägt diesen Namen/die IP in `ALLOWED_HOSTS` ein.

## Frontend-Seiten

1. **Übersicht** – Nettovermögen, Tagesänderung, Vermögensverlauf, Konten-Kacheln, Allokation, nächste Termine, offene Dokumente
2. **Konten** – Liste + Detail (Saldo-Verlauf, Transaktionen, Abgleich mit Auszug)
3. **Depot/Portfolio** – Positionen-Tabelle (sortierbar), Performance (TWR/XIRR), Allokation (Typ/Sektor/Land/Konto), Durchschau-Umschalter
4. **Durchschau (ETF-Look-through)** – kombinierte Liste ETF-Aktien + Einzelaktien mit % und €
5. **Wertpapier-Detail** – Chart, Kennzahlen, aktienfinder-artig: Fair-Value-Chart (KGV-Band), Dividendenhistorie/-wachstum, Umsatz/Gewinn, Termine, eigene Tx
6. **Dividenden** – erhalten je Monat/Jahr, Prognose 12 M, Kalender
6a. **Steuern** (`/steuern`) – Kapitalerträge je Jahr, Sparerpauschbetrag (genutzt/frei), einbehaltene vs. geschätzte Steuer, Verkäufe
7. **Kalender/Termine** – Ex-Tage, Zahltage, Quartalszahlen, HV, IR-Events
8. **Transaktionen** – Journal mit Filtern, manuelle Erfassung, Umbuchung
9. **Haushaltsbuch** – Einnahmen/Ausgaben je Monat, Kategorien, Sparquote, Regeln
10. **Import** – Drag&Drop/Einfügen/Screenshot, Status, Prüfansicht mit Original links, Entwürfe rechts
11. **Prognose** – Vermögensprognose (Monte-Carlo-Band), Dividendenprognose
12. **Watchlist** – Kurse, Termine, Fair-Value-Indikator
13. **Einstellungen** – Konten, Kategorien, Ollama-Status, Marktdaten-Status, Datenexport

### Frontend: Sprint 4

* **Wertpapier bearbeiten** (Wertpapier-Detail → „Bearbeiten“, Deep-Link `?bearbeiten=1`): Yahoo-Symbol mit Suche
  (`/market/search`, offline: Hinweis + Handeingabe), Name, Art, Währung, Fondsanbieter, IR-Seite, Adresse der
  ETF-Positionsliste. Nach einer Symboländerung startet das UI den Abruf für dieses Wertpapier. Ohne Kurs zeigen
  Detail, Depot und Watchlist „Kein Kurs – Symbol prüfen“ (führt in den Editor).
* **Termine:** Kalender und Wertpapier-Detail importieren Kalenderdateien (Datei / Text / Adresse →
  `import-ics`); eigene Termine (`source` `manual`/`ics`) haben einen Stift zum Bearbeiten/Löschen.
* **ETF-Durchschau verwalten:** Durchschau → „ETFs im Depot“ (Quelle, Stand, Abdeckung je ETF) und Wertpapier-Detail →
  Zusammensetzung: „Vom Anbieter laden“, „Positionsliste hochladen“ (mit Formathinweis), Löschen.
* **Depot-Hinweise** (`PortfolioWarnings` auf Depot und Wertpapier-Detail): „Split buchen“ (ein Klick), „Einstandskurs
  nachtragen“ (öffnet die Buchung über `/transaktionen?buchung=<id>`), Verkauf über Bestand.
* **Umbuchungsvorschläge:** Transaktionen zeigt `transfer-suggestions` mit „Ist eine Umbuchung“ / „Nein“.
* **Buchungsformular:** Kategorie „Automatisch“ beim Erfassen; ein getipptes Vorzeichen gilt für Dividende/Zinsen/Gebühr
  (`-12,50` = Storno, `+4,90` Gebühr = Erstattung, `keep_sign`).
* **Steuern / Fixkosten:** Seite Steuern; Einstellungen → Steuern (Sparerpauschbetrag 1.000 €/2.000 €/eigener Wert);
  Haushaltsbuch → „Fixkosten“ und „Sparpläne“ aus `/analytics/recurring`.

### Frontend: Verhalten gegenüber dem Backend (Sprint 3)

* **Typen/Mock:** `frontend/src/api/types.ts` spiegelt diesen Vertrag; `frontend/src/mock/` (nur `npm run dev:mock`) bildet
  dieselben Formen nach und muss bei Vertragsänderungen mitgezogen werden.
* **Fehlertexte:** `detail` als String, als FastAPI-Validierungsliste oder als Objekt `{message, …}` (409) – angezeigt wird
  immer die deutsche Meldung. 409 beim Buchen bietet „Trotzdem speichern/übernehmen“ (`allow_duplicate` bzw. `?force=true`).
* **Marktdaten aktualisieren:** alle Schaltflächen starten `POST /market/refresh?wait=false` (Hintergrund-Job), zeigen
  „läuft …“ und pollen `GET /market/status` (1,5 s) bis `refresh.running=false`; danach wird `refresh.last.result`
  zusammengefasst (ok / teilweise / Quelle nicht erreichbar) und die abhängigen Daten werden neu geladen.
* **Import:** `accept-all` überspringt `review_required`-Entwürfe – das UI meldet „n übersprungen, bitte einzeln prüfen“ und
  bietet „Auch markierte übernehmen“ (`include_flagged=true`). Duplikate sind gekennzeichnet und lassen sich mit
  `?force=true` trotzdem buchen oder über `PATCH … {status:"pending"}` wieder öffnen. PDFs werden direkt über
  `GET /documents/{id}/file` im iframe gezeigt, CSV/Text als erkannter Text.
* **Export:** Einstellungen verlinken `GET /export/transactions.csv` und `GET /export/backup.json` als Downloads.
* **Erste Schritte:** Checkliste auf der Übersicht (Eröffnungssalden → Belege → Marktdaten → Ollama), ausblendbar
  (`localStorage`); Eröffnungssaldo + Stichtag je Konto unter Einstellungen → Eröffnungssalden (`PATCH /accounts/{id}`).
* **Smoke-Test:** `frontend/scripts/e2e-smoke.mjs` (`make e2e`, Optionen über `E2E_ARGS="--dark --shots <ordner>"`) öffnet
  jede Route als Deep-Link in Desktop- und Handybreite gegen die laufende App und scheitert bei Konsolenfehlern,
  fehlgeschlagenen Anfragen, HTTP ≥ 400 auf `/api`, `NaN`/`undefined` im Text oder horizontalem Überlauf.
