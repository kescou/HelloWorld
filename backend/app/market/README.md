# Marktdaten-Modul (`app.market`)

Kurse, Historie, Termine, Fundamentaldaten, Wechselkurse und ETF-Durchschau – ausschließlich aus
**kostenlosen Quellen ohne API-Key**. Vertrag der REST-Endpunkte: `docs/ARCHITECTURE.md`, Abschnitt „Marktdaten“.

> **Wichtig:** Das Modul wurde in einer Sandbox ohne Internetzugang zu Yahoo, EZB, iShares usw. entwickelt.
> Alle Parser sind mit realistischen Fixture-Dateien getestet, die Live-Abrufe selbst aber nicht.
> Nach der Installation einmal ausführen:
>
> ```bash
> cd backend && python -m app.market.selfcheck
> ```
>
> Das prüft jede Quelle live und gibt eine ✅/❌-Tabelle aus (keine DB nötig, es wird nichts geschrieben).

## Quellen

| Daten | Quelle | Bemerkung |
|---|---|---|
| Kurse (≈ Echtzeit, bis 15 min verzögert) | Yahoo Finance via `yfinance` | 1 Request je 50 Symbole (`v7/finance/quote`), Fallback `fast_info` je Symbol |
| Tageshistorie, Dividenden, Splits | Yahoo (`Ticker.history(period="max", actions=True)`) | Erstabruf komplett, danach inkrementell (7 Tage Überlappung); nach Split Komplett-Reload |
| Termine (nächste Quartalszahlen, Ex-Tag, Zahltag) | Yahoo `calendar` / `info` | Vergangene Earnings-Termine best effort |
| Stammdaten (Name, Sektor, Land, Börse, Website, IR-Link) | Yahoo `info` | füllt nur leere Felder in `instruments` |
| Fundamentaldaten | Yahoo `income_stmt`, `cashflow`, `info` | nur ~4–5 Geschäftsjahre (siehe Grenzen) |
| Symbol-Suche (ISIN/Name → Yahoo-Symbol) | Yahoo-Suche, Fallback OpenFIGI (ohne Key, ~25 Req/min) | XETRA (`.DE`) wird bevorzugt, sonst Primärlisting |
| Wechselkurse | EZB-Referenzkurse (`eurofxref-hist-90d.xml`, einmalig `eurofxref-hist.xml`) | `fx_rates.rate` = Einheiten Fremdwährung je 1 EUR; Yahoo `EURXXX=X` für Währungen, die die EZB nicht führt |
| ETF-Bestandteile | Emittenten-Downloads, Yahoo Top-10, manueller Upload | siehe „ETF-Durchschau“ |

## Aufbau

```
providers/base.py     Provider-Abstraktion (Dataclasses + MarketDataProvider)
providers/yahoo.py    YahooProvider (yfinance 1.x; Throttle, Retry/Backoff, defensive Parser)
providers/openfigi.py ISIN → Ticker/Börse (Fallback)
service.py            Symbolauflösung, Quotes-Upsert, Historie, Stammdaten
fx.py                 EZB-Parser, fx_rates, FxConverter / get_rate / to_eur
events.py             Termine: Sync, Dividendenprognose, ICS-Import, Liste mit Positions-Anreicherung
fundamentals.py       aktienfinder-artige Kennzahlen (build_fundamentals ist rein funktional)
etf/                  Holdings-Provider (ishares, xtrackers, best_effort), Tabellen-Parser, Persistenz
lookthrough.py        compute_lookthrough(db, account_id=None)
positions.py          Bestände aus `transactions` (unabhängig von app.services)
jobs.py               Jobs (quotes/history/events/fundamentals/etf/fx/profile/all) + market_data_log
scheduler.py          APScheduler (quotes_tick, maintenance_tick)
selfcheck.py          Live-Prüfung aller Quellen (CLI)
```

Für andere Module nutzbar: `compute_lookthrough`, `fx.get_rate(db, currency, on=None)`, `fx.FxConverter(db).to_eur(...)`,
`positions.compute_quantities(db, account_id=None, as_of=None)`, `jobs.backfill_instrument(db, inst, provider)`,
`providers.get_provider()`.

## Endpunkte (`/api/market`)

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/refresh?wait=` `{scope, instrument_ids?, force?}` | Jobs sofort ausführen – synchron, außer `scope=all` (bzw. `?wait=false`): dann im Hintergrund, Antwort `{job_id, status:"running", …}`, Fortschritt unter `GET /status` → `refresh` bzw. `GET /refresh/{job_id}`; `?wait=true` erzwingt synchron. `scope`: `quotes`, `events`, `history`, `etf`, `fundamentals`, `fx`, `profile`, `all`. Antwortet immer 200; Ergebnis in `status` (`ok`/`partial`/`error`/`busy` = läuft bereits) + `errors`. `all` kann bei vielen neuen Instrumenten Minuten dauern (Yahoo wird bewusst gedrosselt) |
| GET | `/status` | `source`, `online`, `last_quote_update`, `errors` (aktuelle Probleme als Text), `error_log`, letzte Jobs, Hintergrund-Refresh, Scheduler, Zähler, Quellen |
| GET | `/quotes?instrument_ids=1,2` | ohne Parameter: gehaltene + Watchlist |
| GET | `/history/{id}?range=1W\|1M\|3M\|6M\|YTD\|1Y\|2Y\|3Y\|5Y\|10Y\|MAX` | `[{date, close}]` |
| GET | `/events?from=&to=&types=&instrument_id=` | Standardfenster −30 … +365 Tage; nur gehaltene + Watchlist |
| POST / PATCH / DELETE | `/events`, `/events/{id}` | manuelle Termine (HV, Capital Markets Day …), `source="manual"` |
| POST | `/events/import-ics` (multipart: `instrument_id` + `file` \| `text` \| `url`) | IR-Kalender importieren, `source="ics"` |
| GET | `/fundamentals/{id}?refresh=` | Layout siehe unten; `available=false`, solange nichts geladen ist |
| GET | `/etf/{id}/holdings` | `{as_of, source, coverage_pct, residual_pct, holdings[…], attempts[…]}` |
| POST | `/etf/{id}/holdings/upload` (multipart `file`, optional `as_of`) | manueller CSV/XLSX/JSON-Upload |
| POST | `/etf/{id}/holdings/refresh?force=` | Abrufkette für einen ETF jetzt starten |
| DELETE | `/etf/{id}/holdings` | Bestandteile löschen (z. B. um einen manuellen Upload zu entfernen) |
| GET | `/lookthrough?account_id=` | Durchschau ETF-Anteile + Einzelaktien |
| GET | `/search?q=` | Name/ISIN → Symbole, `preferred=true` markiert die automatische Wahl; 4 s Timeout ohne Wiederholung, Yahoo nicht erreichbar → 503 `{detail}` |
| GET | `/fx` | letzte Wechselkurse je Währung |

## Termine (`instrument_events`)

Priorität der Quellen: `manual` / `ics` > `yahoo` > `estimate`. Niedrigere überschreiben höhere nie; bearbeitete
Termine (PATCH) werden zu `manual`.

* **Ex-Dividende (historisch)** – Yahoo-Dividendenreihe, Betrag je Aktie in Kurswährung.
* **Ex-Tag / Zahltag (angekündigt)** – Yahoo-Kalender. Yahoo nennt den Betrag der nächsten Dividende nicht
  zuverlässig → es wird der zuletzt gezahlte Betrag eingetragen.
* **Prognose (`is_estimate=true`, `source="estimate"`)** – wenn die nächste Dividende nicht angekündigt ist:
  Frequenz aus dem Median-Abstand der letzten Zahlungen (monatlich/vierteljährlich/halbjährlich/jährlich),
  Termine = Termine des letzten Zyklus + 1 Jahr (Wochenende → Montag), Betrag = letzte Zahlung (bei ungleichen
  Halbjahreszahlungen der Betrag des gleichen Slots). Keine Prognose, wenn die letzte Zahlung länger als
  1,5 Perioden + 45 Tage zurückliegt. Zahltag-Prognose = Ex-Tag + gelernter Abstand (aus Yahoo-Kalender), sonst
  3 Tage (DE/AT/CH/NL/FR-Aktien), 14 Tage (IE/LU-ETFs) bzw. 21 Tage.
* **Quartalszahlen** – Yahoo; „voraussichtlich“, wenn Yahoo den Termin als geschätzt markiert.
* **Splits** – Yahoo; `amount` = Faktor.
* **HV, Investorentage, sonstige IR-Termine** – gibt es in keiner kostenlosen API → manuell oder per ICS-Import.

`GET /events` ergänzt `position_quantity` (bei vergangenen Ex-Tagen der Bestand am Vortag, sonst der aktuelle)
und `expected_payment` (brutto, in EUR umgerechnet).

## `fundamentals.data` (JSON-Layout, identisch mit `GET /market/fundamentals/{id}`)

Alle Quoten in **Prozent** (z. B. `payout_ratio: 43.2`, `dividend_yield: 2.1`, `cagr_5y: 8.4`), Stabilitäten 0…1.
Bilanzwerte in `financial_currency`, Dividenden/Kurse/Fair Value in `currency` (Kurswährung).

```jsonc
{
  "available": true,
  "currency": "EUR",                 // Kurswährung des Yahoo-Symbols
  "financial_currency": "USD",       // Währung der Abschlüsse
  "fx_factor": 0.92,                 // Abschlusswährung → Kurswährung (für EPS je Aktie)
  "years": [                         // je Geschäftsjahr (Yahoo: nur die letzten ~4–5)
    {"year": 2024, "period_end": "2024-06-30", "revenue": 2.45e11, "net_income": 8.8e10, "eps": 11.8,
     "dps": 3.0,                     // Summe der Dividenden im Kalenderjahr (Kurswährung)
     "fcf": 7.4e10, "payout_ratio": 25.4, "shares": 7.47e9, "fcf_per_share": 9.9, "fcf_payout_ratio": 30.1}
  ],
  "dividends_by_year": [{"year": 2010, "dps": 0.55, "partial": false}, ...],   // bis 25 Jahre, laufendes Jahr partial=true
  "pe_history": [{"date": "2024-01-31", "pe": 32.1}, ...],   // Monatsschlusskurs / zuletzt bekanntes Jahres-EPS
  "fair_value_series": [{"date": "2024-01-31", "price": 370.2, "eps": 9.7, "fair_value": 301.5}, ...], // KGV-Band-Chart
  "fair_value": {
    "method": "pe_avg_x_forward_eps",  // | pe_avg_x_trailing_eps | pe_avg_x_last_annual_eps | avg_dividend_yield
    "value": 412.3,                    // = value_pe, falls vorhanden, sonst value_dividend_yield
    "pe_avg": 29.8, "pe_median": 30.4, "pe_months": 48,
    "eps_forward": 13.8, "eps_ttm": 12.1, "eps_basis": 13.8,
    "value_pe": 412.3,                 // Ø-KGV × EPS-Basis
    "value_dividend_yield": 398.0,     // aktuelle Jahresdividende / Ø historische Dividendenrendite (bis 10 Jahre)
    "dividend_yield_avg": 0.85, "dividend_basis": 3.32,
    "price": 400.0, "upside_pct": 3.1, "verdict": "fair"     // undervalued (> +10 %) | fair | overvalued (< −10 %)
  },
  "dividend_growth": {"cagr_3y": 10.1, "cagr_5y": 10.2, "cagr_10y": 10.5,
                      "years_raised": 19,      // Jahre in Folge mit Erhöhung
                      "years_not_cut": 21, "years_paid": 21, "last_full_year": 2024},
  "valuation": {"pe": 33.0, "forward_pe": 29.0, "pb": 10.9, "dividend_yield": 0.83, "dividend_rate": 3.32,
                "market_cap": 3.0e12, "payout_ratio": 25.0, "shares_outstanding": 7.43e9, "price": 400.0},
  "earnings_quality": {"earnings_stability": 0.97,  // Korrelation des EPS mit einer steigenden Trendgeraden (0…1)
                       "dividend_stability": 0.99,  // dito für die Dividende je Kalenderjahr (bis 10 Jahre)
                       "eps_cagr": 14.2, "revenue_cagr": 11.0, "loss_years": 0,
                       "payout_ratio_avg": 27.0, "fcf_payout_ratio_avg": 31.0,
                       "score": 96,                // 0…100: 40 % Gewinnstabilität, 15 % keine Verlustjahre,
                                                   //         30 % Dividendenstabilität, 15 % Ausschüttungsquote ≤ 60 %
                       "years_of_data": 4},
  "source": "yahoo", "updated_at": "2026-11-12T08:00:00+00:00"
}
```

Rechenregeln: Das Jahres-EPS gilt ab 60 Tage nach Geschäftsjahresende (Veröffentlichungsverzug), im letzten Monat
wird das TTM-EPS verwendet. KGV-Werte ≤ 0 oder > 75 gehen nicht in den Durchschnitt ein. CAGR und Serien
(„Jahre in Folge“) basieren nur auf vollständigen Kalenderjahren.

## ETF-Durchschau

Abrufkette je ETF: **eigene URL** (`instruments.meta.market.holdings_url`) → **Emittent** → **Yahoo Top-10**.
Es wird immer komplett ersetzt (`as_of`, `source`). Ein manueller Upload wird nie automatisch überschrieben
(`force=true` erzwingt es), eine vollständige Emittentenliste wird nie durch die Top-10 ersetzt.
`coverage_pct` = Summe der bekannten Gewichte, der Rest erscheint als „Sonstige/Unbekannt“.

| Emittent | Status | Quelle |
|---|---|---|
| iShares | **implementiert** | Produkt-Screener-JSON (ISIN → Produktseite) + `…/1478358465952.ajax?fileType=csv&fileName=holdings&dataType=fund` (UK-Seite englisch, Fallback DE-Seite). Aktien-ETF-CSVs enthalten Ticker + Name, aber **keine ISIN** |
| Xtrackers (DWS) | **implementiert** | `https://etf.dws.com/etfdata/export/GBR/ENG/excel/product/constituent/<ISIN>/` (XLSX, mit ISIN) |
| Vanguard | Best Effort | GraphQL der deutschen Seite (`www.de.vanguard/gpx/graphql`); `meta.market.vanguard_port_id` kann manuell gesetzt werden |
| Amundi / Lyxor | Best Effort | `amundietf.de/mapi/ProductAPI/getProductsData` |
| SPDR | Best Effort | `ssga.com/…/holdings-daily-emea-en-<ticker>-<börse>.xlsx` (Ticker wird geraten, ISIN in der Datei geprüft) |
| Invesco | Best Effort | `dng-api.invesco.com/…/shareclasses/<ISIN>/holdings/index` |
| alle | Fallback | Yahoo `funds_data.top_holdings` → `source="yahoo_top10"` (nur ~10 Positionen) |
| alle | immer möglich | Upload CSV/XLSX/JSON: Spalten werden erkannt (Name/Bezeichnung, ISIN, Ticker, Gewicht/Gewichtung/Weight/%, alternativ Marktwert, Sektor/Branche, Land/Standort). Deutsche und englische Zahlenformate, Titelzeilen vor der Kopfzeile sind erlaubt |

„Best Effort“ heißt: undokumentierte Endpunkte, aus der Erinnerung implementiert, live nicht verifiziert – ein
Fehler führt nur zum nächsten Glied der Kette. Der Selfcheck zeigt, welche davon aktuell funktionieren.
Wenn ein Emittent eine stabile Download-URL anbietet, genügt es, sie in `meta.market.holdings_url` einzutragen
(CSV/XLSX/JSON wird automatisch erkannt). Weitere Emittenten: Klasse von `EtfHoldingsProvider` ableiten und
`registry.register(...)` aufrufen.

**`compute_lookthrough(db, account_id=None) -> dict`** – Bestände aus `transactions` (Kauf/Einlieferung +,
Verkauf/Auslieferung −, Split multipliziert) × letzter Kurs × FX. Zuordnung eines ETF-Bestandteils zu einem
Unternehmen: ISIN → normalisierter Name (Rechtsform-/Gattungszusätze entfernt, daher werden z. B. Alphabet A + C
zusammengefasst) → Ticker. Zusätzliche Felder gegenüber dem Vertrag: `items[].sector/country/kind`,
`etfs[]` (Abdeckung je ETF), `unpriced[]` (Positionen ohne Kurs), `by_sector[].label` (deutsch).
`coverage_pct` = Anteil des Depotwerts, der bis auf Einzeltitel aufgelöst ist.

## Scheduler

Aktiv, wenn `settings.enable_scheduler` (Start/Stop in `main.lifespan`). Zwei Jobs, damit es auch auf einem
Rechner funktioniert, der nicht durchläuft:

* `quotes_tick` alle `quote_refresh_seconds`: Kurse für gehaltene + Watchlist-Instrumente, nur Mo–Fr 08–22 Uhr
  (Europe/Berlin). Unabhängig von der Uhrzeit: neue Instrumente bekommen ein Yahoo-Symbol und werden komplett
  nachgeladen (Stammdaten, Historie, Termine, Fundamentaldaten, ETF-Bestandteile; max. 5 je Lauf).
* `maintenance_tick` alle 30 min: führt aus, was laut `market_data_log` überfällig ist – FX, Stammdaten, Historie,
  Termine täglich; Fundamentaldaten wöchentlich; ETF-Bestandteile monatlich. Fehlgeschlagene Jobs werden
  frühestens nach 2 h wiederholt.

Jeder Lauf schreibt eine Zeile in `market_data_log` (der 2-Minuten-Kursjob überschreibt seine letzte OK-Zeile,
Einträge älter als 30 Tage werden gelöscht). Jobs werfen nie Exceptions in die App: offline ⇒ `status="error"`
im Log, die App läuft mit den gespeicherten Daten weiter. Bei Yahoo-Rate-Limit (HTTP 429) oder wenn die ersten
fünf Abrufe eines Laufs alle scheitern (offline), wird der Stapel abgebrochen und beim nächsten Lauf fortgesetzt.
Eine leere Kurshistorie gilt als Fehler (yfinance verschluckt HTTP-Fehler) – delistete Symbole tauchen deshalb
in `/status` unter `errors` auf.

## Schlüssel in `instruments.meta["market"]`

`symbol_source`, `symbol_lookup_failed_at`, `yahoo_name`, `profile_at`, `history_at`, `actions_full_at`,
`events_at`, `fundamentals_at`, `backfill_at`, `pay_lag_days`, `etf_attempts`, `etf_checked_at`, `ishares_url`
(Cache) sowie manuell setzbar: `holdings_url`, `vanguard_port_id`.

## Tests

```bash
cd backend && TEST_DATABASE_URL=postgresql+psycopg://wealth:wealth@localhost:5432/wealth_test_market \
  ../.venv/bin/pytest -q tests/test_market_*.py
```

Kein Netzwerk: `FakeProvider` (`tests/fixtures/market/fakes.py`) + Fixture-Dateien; ein Autouse-Fixture lässt
jeden echten HTTP-Aufruf fehlschlagen.

## Grenzen / kostenpflichtige Alternativen

| Thema | Was kostenlos nicht geht | Umgang im Modul | Kostenpflichtige Alternative |
|---|---|---|---|
| Yahoo allgemein | Inoffizielle API ohne Garantie; kann sich ändern oder drosseln (429). Kurse je nach Börse bis 15 min verzögert | Throttle, Retry/Backoff, Abbruch bei 429, Selfcheck | EOD Historical Data, Twelve Data, Finnhub, Polygon |
| Fundamentaldaten-Historie | Yahoo liefert nur die letzten **~4–5 Geschäftsjahre**. aktienfinder zeigt 10–20 Jahre | KGV-Historie, Ø-KGV und Stabilitäten beruhen auf 4–5 Jahren; Dividenden reichen dagegen Jahrzehnte zurück | Financial Modeling Prep, EODHD Fundamentals, SimFin+, aktienfinder-Abo |
| EPS nach Splits | Unklar, ob Yahoo ältere Jahres-EPS immer split-bereinigt; Kurse und Dividenden sind bereinigt | nach einem Split die KGV-Historie plausibilisieren | wie oben |
| Analystenschätzungen | nur Forward-EPS für das nächste Jahr, keine Mehrjahresschätzungen | Fair Value = Ø-KGV × Forward-EPS | FactSet/Refinitiv-basierte Dienste |
| Historische Zahltage | Yahoo kennt nur Ex-Tage und den nächsten Zahltag | Zahltag-Prognose aus gelerntem Abstand | EODHD, Polygon |
| Betrag der nächsten Dividende | wird vor dem Ex-Tag meist nicht geliefert | letzter Betrag bzw. Prognose (`is_estimate`) | wie oben |
| HV, Investorentage, IR-Events | in keiner kostenlosen API | manuelle Termine + ICS-Import | Wall Street Horizon, EQS-Feeds |
| Vollständige ETF-Bestandteile | keine einheitliche kostenlose API; je Emittent eigene, undokumentierte Downloads. iShares ohne ISIN je Position; Swap-ETFs zeigen den Trägerkorb | Emittenten-Provider, eigene URL, Yahoo Top-10, manueller Upload | Morningstar, ETFdb Pro, justETF-Daten |
| Symbolauflösung | ISIN → XETRA-Listing ist bei Yahoo nicht immer eindeutig; OpenFIGI-Ticker passen nicht immer zu Yahoo | Kandidaten werden per Kursabfrage geprüft; Symbol jederzeit am Instrument korrigierbar | OpenFIGI mit Key, EODHD-ID-Mapping |
| Wechselkurse | EZB nur ein Fixing je Bankarbeitstag (~16 Uhr), keine Intraday-Kurse | reicht für Bewertung in EUR | kommerzielle FX-Feeds |
| Logos | kein offizieller kostenloser Logo-Dienst | Favicon der Unternehmens-Website (Google-Favicon-Dienst) | Logo-APIs |

## Sprint 4: Bearbeiten, Splits, Schätzungen

* **Symbol ändern** (`PATCH /instruments/{id}`): `service.reset_market_data` löscht Kurs, Historie, Fundamentaldaten und
  alle Termine außer `manual`/`ics` sowie die symbolbezogenen `meta.market`-Schlüssel (`backfill_at`, `profile_at`,
  `actions_full_at`, `symbol_lookup_failed_at`, `yahoo_name`). Der nächste `quotes`-Lauf mit `instrument_ids` füllt per
  `backfill_instrument` alles neu; liefert Yahoo keinen Kurs, enthält das Ergebnis einen Fehler je Symbol.
* **XETRA-Schwester einer ISIN** (`service.pick_xetra_sibling`): gleiche ISIN gewinnt; sonst muss die Aktiengattung
  übereinstimmen (`share_class`: „Class B“, „Vz“ …) und der Kandidat eindeutig sein – im Zweifel bleibt die Heimatbörse.
* **Sonderdividenden** (`events.regular_payments`): Zahlungen > 1,8 × Median der Nachbarn, die sich im Folge-/Vorjahr
  nicht wiederholen, fließen nicht in Frequenz und Betrag der Schätzung ein.
* **Nicht gebuchte Splits**: `services.portfolio.unbooked_splits` vergleicht Split-Termine mit den `split`-Buchungen.
* **Durchschau**: Gewichtssummen > 100 % werden skaliert; `util.name_keys` liefert beide Umlaut-Schlüssel; Namen ohne
  ISIN dürfen ab 12 Zeichen per Präfix auf genau ein bekanntes Unternehmen passen.
