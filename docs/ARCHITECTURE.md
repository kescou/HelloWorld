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

Tests: `backend/tests/test_<domain>_*.py`, jede Domain mit eigener Test-DB `wealth_test_<domain>`
(`TEST_DATABASE_URL=... pytest`). Keine Netzwerkzugriffe in Tests (Yahoo/Ollama mocken).

## Datenmodell (Kurzfassung – maßgeblich ist der Code in `backend/app/models/`)

* `accounts` – Konten (Giro, Tagesgeld, Depot). Depot hat `settlement_account_id` (Verrechnungskonto).
* `instruments` – Wertpapiere (ISIN, Yahoo-Symbol, Typ, Sektor/Land, ETF-Infos), `is_watchlist`.
* `transactions` – EIN Journal für alles. `amount` = vorzeichenbehaftete Cash-Wirkung inkl. Gebühren/Steuern.
  Wertpapier-Tx: `account_id` = Depot, `cash_account_id` = Verrechnungskonto.
* `categories` – Haushaltsbuch-Kategorien mit `match_rules` (Auto-Kategorisierung).
* `balance_snapshots` – bekannte Kontostände aus Auszügen (Abgleich).
* `quotes`, `price_history`, `instrument_events`, `etf_holdings`, `fundamentals` – Marktdaten.
* `documents`, `extraction_drafts` – Upload → Extraktion → Prüfen → Übernehmen.

### Saldo-Logik
* Cash-Konto-Saldo = `opening_balance` + Σ `amount` aller Tx mit (`account_id` = Konto und `cash_account_id` NULL) oder `cash_account_id` = Konto.
* Depot-Bestand je Instrument = Σ Stück (buy/delivery_in +, sell/delivery_out −, split multipliziert), Einstand FIFO.
* Depotwert = Σ Stück × aktueller Kurs (in EUR umgerechnet).

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

## REST-API (Prefix `/api`, JSON, Beträge als Zahl in EUR sofern nicht anders angegeben, Datum ISO)

### Konten – Backend-Core
* `GET /accounts` → `[{id,name,institution,type,currency,iban,color,balance,market_value,total_value,settlement_account_id,is_active,interest_rate,sort_order}]`
  (`balance` = Cash-Saldo, `market_value` = Depotwert bei Depots, `total_value` = Summe)
* `POST /accounts`, `PATCH /accounts/{id}`, `DELETE /accounts/{id}`
* `GET /accounts/{id}` → Konto + `history: [{date, value}]` (Tageswerte, 2 Jahre)

### Transaktionen – Backend-Core
* `GET /transactions?account_id=&instrument_id=&type=&category_id=&q=&date_from=&date_to=&limit=&offset=` → `{items:[Tx], total}`
  Tx = alle Spalten + `account_name`, `instrument_name`, `instrument_isin`, `category_name`
* `POST /transactions` (TransactionCreate; `amount` darf fehlen → wird aus quantity×price±fees/taxes berechnet;
  `isin` statt `instrument_id` erlaubt → Instrument wird angelegt/gefunden)
* `PATCH /transactions/{id}`, `DELETE /transactions/{id}`
* `POST /transactions/transfer` `{from_account_id,to_account_id,amount,date,description}` → zwei verknüpfte Tx

### Kategorien – Backend-Core
* `GET/POST/PATCH/DELETE /categories`, `POST /categories/auto-assign` (wendet `match_rules` auf unkategorisierte Tx an)

### Instrumente – Backend-Core (+ Market-Data für Daten)
* `GET /instruments?q=&watchlist=` , `POST /instruments` `{isin|symbol, ...}`, `PATCH /instruments/{id}`
* `GET /instruments/{id}` → Stammdaten + quote + Position (falls gehalten) + Tx-Liste

### Portfolio – Backend-Core
* `GET /portfolio/summary` → `{net_worth, cash_total, invested_value, cost_basis, unrealized_gain, unrealized_gain_pct, realized_gain_ytd, dividends_ytd, interest_ytd, day_change, day_change_pct, by_account:[{account_id,name,value}], updated_at}`
* `GET /portfolio/holdings?account_id=` → `[{instrument_id,isin,symbol,name,type,quantity,avg_cost,cost_basis,price,price_currency,market_value,day_change,day_change_pct,unrealized_gain,unrealized_gain_pct,weight,dividends_received,sector,country,accounts:[{account_id,quantity}]}]`
* `GET /portfolio/history?range=1M|3M|YTD|1Y|3Y|5Y|MAX&account_id=` → `[{date, net_worth, invested, cost_basis, cash}]`
* `GET /portfolio/performance?range=` → `{twr, xirr, absolute_gain, series:[{date, twr_index}]}`
* `GET /portfolio/allocation?by=type|sector|country|account|currency&lookthrough=true|false` → `[{key, value, weight}]`

### Analytics – Backend-Core
* `GET /analytics/dividends?year=` → `{received_by_month:[{month,amount}], received_by_year:[{year,amount}], forecast_12m:[{month,amount,items:[{instrument_id,name,amount,ex_date,pay_date}]}], yield_on_cost, portfolio_yield}`
* `GET /analytics/cashflow?months=12` → `[{month, income, expenses, savings, savings_rate}]`
* `GET /analytics/expenses-by-category?date_from=&date_to=` → `[{category_id,name,amount,share}]`
* `GET /analytics/forecast?years=10&monthly_savings=&expected_return=` → Vermögensprognose `{series:[{year,p10,p50,p90,contributions}]}`
* `GET /analytics/realized-gains?year=` → `[{instrument,date,quantity,proceeds,cost,gain,taxes}]`

### Marktdaten – Market-Data
* `POST /market/refresh` `{scope:"quotes"|"events"|"history"|"etf"|"all", instrument_ids?}` → Job-Ergebnis
* `GET /market/quotes?instrument_ids=1,2` → `[{instrument_id,price,previous_close,change,change_pct,currency,market_time,updated_at}]`
* `GET /market/history/{instrument_id}?range=` → `[{date, close}]`
* `GET /market/events?from=&to=&types=` → `[{id,instrument_id,instrument_name,symbol,type,date,amount,currency,title,url,is_estimate,position_quantity,expected_payment}]` (nur gehaltene + Watchlist)
* `GET /market/fundamentals/{instrument_id}` → aktienfinder-artige Daten: `{years:[{year,revenue,net_income,eps,dps,fcf,payout_ratio,shares}], pe_history:[{date,pe}], fair_value:{method,value,pe_avg,eps_forward}, dividend_growth:{cagr_5y,cagr_10y,years_raised}, valuation:{pe,forward_pe,pb,dividend_yield,market_cap}, earnings_quality:{...}}`
* `GET /market/etf/{instrument_id}/holdings` → `{as_of, source, coverage_pct, holdings:[{name,isin,symbol,weight,sector,country}]}`
* `POST /market/etf/{instrument_id}/holdings/upload` (CSV manuell)
* `GET /market/lookthrough?account_id=` → **kombinierte Liste ETF-Anteile + Einzelaktien**:
  `{total_value, coverage_pct, items:[{key,name,isin,symbol,direct_value,etf_value,total_value,weight,sources:[{instrument_id,name,value,weight_in_etf}]}], by_sector:[...], by_country:[...]}`
* `GET /market/status` → letzte Jobs, Quelle, Fehler
* `GET /market/search?q=` → Yahoo-Suche (Name/ISIN → Symbol)

### Dokumente – Ingestion
* `POST /documents` multipart (`files[]`, optional `account_id`) → `[Document]`; Verarbeitung im Hintergrund
* `POST /documents/paste` `{text, account_id?}` → Document
* `GET /documents?status=` → `[{id,filename,source_kind,status,detected_institution,detected_doc_type,parser,draft_count,pending_count,error,created_at,processing_ms}]`
* `GET /documents/{id}` → Document + `drafts:[{id,kind,payload,confidence,status,transaction_id}]` + `raw_text`
* `GET /documents/{id}/file` → Originaldatei
* `PATCH /documents/drafts/{draft_id}` `{payload}` (Korrektur)
* `POST /documents/drafts/{draft_id}/accept` / `/reject`, `POST /documents/{id}/accept-all`
* `POST /documents/{id}/reprocess` `{force_llm?: bool}`
* `DELETE /documents/{id}`
* `GET /documents/llm/status` → `{available, models, text_model, vision_model}`

Draft-`payload` (Transaktion): `{account_id, type, booking_date, value_date, isin, wkn, name, symbol, quantity, price, amount, fees, taxes, currency, fx_rate, counterparty, description, category_hint}`
Draft-`payload` (Kontostand): `{account_id, date, balance}`

## Frontend-Seiten

1. **Übersicht** – Nettovermögen, Tagesänderung, Vermögensverlauf, Konten-Kacheln, Allokation, nächste Termine, offene Dokumente
2. **Konten** – Liste + Detail (Saldo-Verlauf, Transaktionen, Abgleich mit Auszug)
3. **Depot/Portfolio** – Positionen-Tabelle (sortierbar), Performance (TWR/XIRR), Allokation (Typ/Sektor/Land/Konto), Durchschau-Umschalter
4. **Durchschau (ETF-Look-through)** – kombinierte Liste ETF-Aktien + Einzelaktien mit % und €
5. **Wertpapier-Detail** – Chart, Kennzahlen, aktienfinder-artig: Fair-Value-Chart (KGV-Band), Dividendenhistorie/-wachstum, Umsatz/Gewinn, Termine, eigene Tx
6. **Dividenden** – erhalten je Monat/Jahr, Prognose 12 M, Kalender
7. **Kalender/Termine** – Ex-Tage, Zahltage, Quartalszahlen, HV, IR-Events
8. **Transaktionen** – Journal mit Filtern, manuelle Erfassung, Umbuchung
9. **Haushaltsbuch** – Einnahmen/Ausgaben je Monat, Kategorien, Sparquote, Regeln
10. **Import** – Drag&Drop/Einfügen/Screenshot, Status, Prüfansicht mit Original links, Entwürfe rechts
11. **Prognose** – Vermögensprognose (Monte-Carlo-Band), Dividendenprognose
12. **Watchlist** – Kurse, Termine, Fair-Value-Indikator
13. **Einstellungen** – Konten, Kategorien, Ollama-Status, Marktdaten-Status, Datenexport
