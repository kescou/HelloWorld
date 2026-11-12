# Dokumenten-Import (Ingestion)

Upload (PDF, Screenshot/Foto, Text, CSV) oder eingefügter Text → Entwürfe („Drafts“) → Prüfen →
Übernehmen in `transactions` / `balance_snapshots`. Alles läuft lokal; das Sprachmodell kommt von
[Ollama](https://ollama.com) auf demselben Rechner.

Code: `backend/app/ingest/`, API: `backend/app/api/documents.py`, Schemas: `backend/app/schemas/ingest.py`,
Tests: `backend/tests/test_ingest_*.py`, Beispiel-Dokumente: `backend/tests/fixtures/ingest/`.

## Pipeline (billigste Stufe zuerst)

| # | Stufe | Modul | Dauer (typisch) |
|---|---|---|---|
| 1 | **Intake** – Datei nach `upload_dir/<sha[:2]>/<sha256>.<ext>`, Dedupe über sha256 (gleiche Datei → vorhandenes Dokument), Art erkennen (pdf/image/text/csv/paste) | `storage.py`, `extract.detect_kind` | ms |
| 2 | **Text** – PDF: pdfplumber (Zeilen bleiben Zeilen), Fallback pypdf. PDF ohne Textebene: Seiten mit pypdfium2 rendern. Bilder: optional Tesseract-OCR als Vorstufe | `extract.py` | 10–300 ms/Seite |
| 3 | **Klassifikation** – Bank + Dokumenttyp über Schlagworte | `classify.py` | < 1 ms |
| 4 | **Deterministische Parser** (Regex/Template, Konfidenz ≈ 0,95) | `parsers/` | 1–20 ms |
| 5 | **LLM-Fallback** über Ollama – nur wenn kein Parser passt, die Selbstprüfung scheitert oder `force_llm` | `llm.py` | 3–60 s |
| 6 | **Validierung/Normalisierung** – für Parser- UND LLM-Ergebnis | `validate.py` | ms |
| 7 | **Prüfen & Übernehmen** – gebucht wird über `app.services.ledger.create_transaction` (Auto-Kategorie über `match_rules`, Dedupe-Hash) | `commit.py`, API | ms |

Die Verarbeitung läuft im Hintergrund (Thread-Pool, `INGEST_WORKERS`, Standard 2). Status:
`uploaded → processing → review` (Entwürfe offen) `→ imported` (alle entschieden) bzw. `failed`
(mit deutscher Fehlermeldung in `error`). `processing_ms` und Einzelzeiten (`timings`) werden gespeichert.
Nach einem Neustart hängen gebliebene Dokumente werden beim Start der App neu eingereiht (Fallback: beim ersten `GET /documents`).

### Woher die Qualität kommt (Stufe 6)

* Pydantic-/Typ-Normalisierung: deutsche Zahlen (`1.234,56`, `85,00-`, `85,00 S`), Datumsformate, Typ-Aliase.
* **ISIN-Prüfziffer** (Luhn) – falsche ISIN → Warnung + `review_required`.
* **Rechenprobe**: Stück × Kurs ± Gebühren ± Steuern ≈ Betrag (Toleranz 2 Cent bzw. 0,1 %; Dividenden/Zinsen:
  Brutto − Steuern ≈ Gutschrift). Passt → `payload.verified = true`. Passt nicht → Warnung, Konfidenz −0,3, `review_required`.
* Kontoauszüge: alter Saldo + Σ Buchungen = neuer Saldo (ING/Postbank), Saldo-Kette je Zeile (Trade Republic).
* **Vorzeichen** nach ARCHITECTURE.md: `amount` = Cash-Wirkung inkl. Gebühren/Steuern (Kauf −, Verkauf/Dividende/Zins +).
  `fees`/`taxes` sind positive Abzüge (negativ = Erstattung). Fremdwährung: `price` und `amount` in EUR, `fx_rate` = 1 EUR in Fremdwährung.
* **Kontozuordnung**: Nutzer-Hinweis `account_id` > IBAN/Depotnummer im Dokument > erkannte Bank + Rolle.
  Wertpapierbuchungen gehen immer aufs Depot (bei Hinweis auf ein Girokonto: das Depot, das darüber abrechnet),
  Cash-Zeilen aufs Verrechnungs-/Girokonto. Gegen-IBAN = eigenes Konto → `transfer_in/out`.
* **Duplikate**: gleiche(s) Konto/Betrag/ISIN, Datum ±4 Tage bei Wertpapieren (Handelstag vs. Valuta), exakt bei Cash-Zeilen
  → Draft-Status `duplicate`. Giro-Zeile = Cash-Seite einer schon gebuchten Depot-Transaktion → ebenfalls `duplicate`.
  Zwei identische Zeilen in EINEM Auszug bleiben zwei Buchungen (`payload.dup_index`).
* `category_hint` für Girozeilen (Schlagwortliste in `categorize.py`). Beim Übernehmen gewinnen die `match_rules` der
  Kategorien (längste Regel), sonst der Hint (Kategoriename, dann Namensähnlichkeit).
* Wenn die Selbstprüfung eines Parsers scheitert, wird das LLM gefragt; es gewinnt nur, wenn SEIN Ergebnis die
  Rechenprobe besteht. Ist Ollama aus, bleibt das Parser-Ergebnis mit Warnung und `review_required`.

Zusatzfelder im Draft-`payload` (über den Vertrag hinaus): `warnings: [str]`, `review_required: bool`,
`verified: bool`, `dup_index`, `duplicate_of` (Tx-ID), `gross_amount`, `reference`, `counterparty_iban`, `transfer_account_id`.

## Unterstützte Formate (deterministische Parser)

| Bank | Dokument | Parser |
|---|---|---|
| Trade Republic | Wertpapierabrechnung Kauf / Verkauf / Sparplan / Saveback / Round up | `trade_republic.trade` |
| | Dividende / Ausschüttung (inkl. Fremdwährung, Quellensteuer) | `trade_republic.dividend` |
| | Abrechnung Zinsen | `trade_republic.interest` |
| | Kontoauszug (Umsatzübersicht; Handel/Erträge mit ISIN → Depot-Buchung, Endsaldo → Kontostand) | `trade_republic.statement` |
| Scalable Capital | Baader-Bank-Format: Wertpapierabrechnung Kauf/Verkauf, Dividendenabrechnung/Ertragsmitteilung/Fondsausschüttung | `scalable.baader` |
| | Scalable-Capital-Bank-Format (Label-Parser, best effort) | `scalable.bank` |
| | Transaktions-CSV aus der Web-App | `scalable.csv` |
| ING | Wertpapierabrechnung Kauf/Verkauf, Dividendengutschrift/Ertragsgutschrift | `ing.trade`, `ing.dividend` |
| | Girokonto-Kontoauszug (PDF) | `ing.statement` |
| | Umsatzanzeige-CSV (Semikolon, latin-1/cp1252, Kopfzeilen) | `ing.csv` |
| Postbank | Kontoauszug PDF (neues und klassisches Layout, Jahr aus dem Kopf, Dez/Jan-Wechsel) | `postbank.statement` |
| | Umsatz-CSV (neues und altes Format) | `postbank.csv` |
| beliebig | eingefügte Umsatztabellen (Datum – Text – Betrag, ein- oder mehrzeilig, `+/-`, `S/H`, Tabs) | `generic.statement` |
| | Bank-CSV mit deutschen Spaltennamen | `generic.csv` |
| | Wertpapierabrechnung unbekannter Broker (Label-Parser, Konfidenz 0,8) | `generic.security` |

Alles andere (Screenshots ohne OCR, Scans, Steuerbescheinigungen, Depotauszüge, Freitext, unbekannte Layouts) → LLM.

## Lokales Modell (Ollama)

```bash
# Installation: https://ollama.com/download   (Linux: curl -fsSL https://ollama.com/install.sh | sh)
ollama serve                      # falls nicht ohnehin als Dienst/App aktiv
ollama pull qwen2.5:7b-instruct   # Text (Standard)
ollama pull qwen2.5vl:7b          # Vision: Screenshots, Fotos, gescannte PDFs (Standard)
```

Konfiguration in `.env` (Repo-Wurzel): `OLLAMA_URL`, `OLLAMA_TEXT_MODEL`, `OLLAMA_VISION_MODEL`.
`GET /api/documents/llm/status` zeigt Erreichbarkeit, vorhandene Modelle und den passenden `ollama pull`-Befehl.

Größenordnungen (Q4-Quantisierung; Werte sind Richtwerte, bitte auf der eigenen Hardware prüfen):

| Zweck | Modell | Download / RAM bzw. VRAM | Tempo | Einsatz |
|---|---|---|---|---|
| Text, schwache Hardware | `qwen2.5:3b-instruct` | ~2 GB / ~4 GB | schnell auch auf CPU | einfache Abrechnungen; bei langen Auszügen eher Fehler |
| **Text, Standard** | `qwen2.5:7b-instruct` | ~4,7 GB / ~6–7 GB | GPU/Apple Silicon: wenige Sekunden je Seite; reine CPU: ~0,5–1 min | guter Kompromiss, sauberes JSON |
| Text, Qualität | `qwen2.5:14b-instruct` | ~9 GB / ~11–12 GB | ~2–3× langsamer | lange/unübliche Auszüge |
| Vision, schwache Hardware | `qwen2.5vl:3b` oder `gemma3:4b` | ~3 GB / ~5–6 GB | mittel | Screenshots mit großer Schrift |
| **Vision, Standard** | `qwen2.5vl:7b` | ~6 GB / ~8–10 GB | GPU: ~5–20 s je Bild; CPU: Minuten | Screenshots, Fotos, Scans |
| Vision, Qualität | `gemma3:12b` (oder `qwen2.5vl:32b` mit ≥ 24 GB VRAM) | ~8 GB / ~11 GB | langsam | kleingedruckte Scans |

So wird das Modell benutzt (`llm.py`): `POST /api/chat`, `stream:false`, `format` = JSON-Schema (strukturierte Ausgabe),
`temperature: 0`, fester `seed`, begrenztes `num_ctx` (8192) und `num_predict`, `keep_alive: 30m` (Modell bleibt geladen –
der erste Aufruf nach dem Start ist langsam, danach schnell), deutscher System-Prompt + 3 Few-Shot-Beispiele.
Lange Auszüge werden an Seiten-/Zeilengrenzen (vor einer Datumszeile) in Stücke von ~6000 Zeichen geteilt und parallel
verarbeitet. Kaputtes JSON wird repariert (Code-Zäune, Text drumherum, hängende Kommas, abgeschnittenes Ende) und sonst
einmal mit Korrekturhinweis neu angefragt. Bilder gehen als base64 an das Vision-Modell, ein vorhandener OCR-Text als Kontext.

Stellschrauben (`.env`, Präfix `INGEST_`, Defaults in `app/ingest/config.py`): `INGEST_LLM_TIMEOUT_SECONDS=180`,
`INGEST_LLM_NUM_CTX=8192`, `INGEST_LLM_KEEP_ALIVE=30m`, `INGEST_LLM_CONCURRENCY=2`, `INGEST_LLM_CHUNK_CHARS=6000`,
`INGEST_LLM_RETRIES=1`, `INGEST_OCR_ENABLED=true`, `INGEST_OCR_LANG=deu+eng`, `INGEST_WORKERS=2`, `INGEST_MAX_UPLOAD_MB=40`,
`INGEST_DUPLICATE_WINDOW_DAYS=4`.
Parallele Stücke bringen nur etwas, wenn Ollama mit `OLLAMA_NUM_PARALLEL>=2` läuft (kostet mehr Speicher); sonst
werden sie von Ollama nacheinander abgearbeitet. Bei wenig RAM: `INGEST_LLM_CONCURRENCY=1` und `INGEST_LLM_NUM_CTX=4096`
(dann auch `INGEST_LLM_CHUNK_CHARS=3000`).

**Ohne Ollama** funktionieren alle deterministischen Parser weiter. Dokumente, die das Modell brauchen, landen auf
`failed` mit dem Hinweis, Ollama zu starten; danach „Erneut verarbeiten“ (`POST /documents/{id}/reprocess`) oder
dieselbe Datei noch einmal hochladen.

**Optionales OCR:** Ist Tesseract installiert (`apt install tesseract-ocr tesseract-ocr-deu` + `pip install -r backend/requirements-ocr.txt`),
wird bei Bildern/Scans erst OCR versucht. Erkennt ein Parser den OCR-Text und besteht die Rechenprobe, wird das
Vision-Modell gar nicht gebraucht (Konfidenz auf 0,85 gedeckelt); sonst bekommt das Modell Bild + OCR-Text.

## API (Präfix `/api/documents`) – Ergänzungen zum Vertrag

* `POST ""` – Feldname `files` (auch `files[]` oder `file`), optional `account_id`. Antwort zusätzlich:
  `mime_type, account_id, duplicate_count, accepted_count, warnings, processed_at, used_llm, has_file, duplicate_upload`.
* `GET ""` – `status` darf kommagetrennt sein; `limit`, `offset`.
* `GET /{id}` – zusätzlich `timings`, `pages`; Drafts mit `document_id`, `balance_snapshot_id`.
* `PATCH /drafts/{id}` `{payload, status?}` – Payload wird gemerged, Validierung + Duplikatprüfung laufen neu;
  `status: "pending"` öffnet einen abgelehnten/Duplikat-Entwurf wieder. Übernommene Entwürfe: 409.
* `POST /drafts/{id}/accept?force=true` – bucht auch ein erkanntes Duplikat. Ohne `force`: 409 mit `transaction_id`.
  Fehlende Pflichtangaben (z. B. Konto): 422 mit deutscher Meldung.
* `POST /{id}/accept-all?include_flagged=true` → `{accepted, skipped, duplicates, errors, document}`.
  Standard: Entwürfe mit `review_required` werden NICHT automatisch übernommen.
* `DELETE /{id}?delete_transactions=true` – Standard: importierte Buchungen bleiben (Quelle wird gelöst).
* `GET /llm/status` – zusätzlich `text_model_available, vision_model_available, ocr_available, url, error, hint`.

DB-Ergänzungen (`models/ingest.py`): `documents.meta` (JSON), `documents.processed_at`,
`extraction_drafts.balance_snapshot_id`.

## Parser debuggen, ohne die Datenbank anzufassen

```bash
cd backend
../.venv/bin/python -m app.ingest.cli ~/Downloads/Abrechnung.pdf            # Parser, LLM nur bei Bedarf
../.venv/bin/python -m app.ingest.cli auszug.pdf --no-llm --text            # nie LLM, Rohtext mit ausgeben
../.venv/bin/python -m app.ingest.cli foto.jpg --llm                        # Modell erzwingen
pbpaste | ../.venv/bin/python -m app.ingest.cli -                           # eingefügter Text von stdin
```

Ausgabe: JSON mit `institution`, `doc_type`, `parser`, `timings`, `warnings`, `drafts[]`. `--text` zeigt genau den
Text, den die Parser sehen – das ist die Grundlage für jede Regex-Anpassung.

## Einen Parser hinzufügen / anpassen

1. Dokument mit `cli --no-llm --text` ausgeben, persönliche Daten ersetzen, als Fixture nach
   `backend/tests/fixtures/ingest/<bank>_<typ>.txt` legen (Zeilenstruktur exakt lassen).
2. Modul `app/ingest/parsers/<bank>.py` mit `matches(ctx) -> bool` und `parse(ctx) -> ParseResult | None`
   (Bausteine: `parsers/common.py` – `tx_draft`, `cash_line_draft`, `balance_draft`, `sum_costs`;
   `parsers/generic_security.py` – Label-Parser; `parsers/bank_csv.py` – CSV mit Spalten-Synonymen; `util.py` – Zahlen/Datum/ISIN).
3. In `parsers/__init__.py` unter `INSTITUTION_PARSERS` eintragen, Schlagworte in `classify.py` ergänzen.
4. Test in `tests/test_ingest_parsers.py`. Der Parser muss nichts validieren – das macht `validate.py`; er soll nur
   lesen, was dasteht, und im Zweifel `None` zurückgeben (dann übernimmt das LLM).

## Ehrliche Grenzen

* Die Parser sind **ohne Zugriff auf echte Dokumente des Nutzers** geschrieben – nach bestem Wissen über die Layouts,
  getestet nur gegen selbst geschriebene, synthetische Beispiele. Banken ändern Layouts; pdfplumber liefert je nach PDF
  leicht andere Zeilenumbrüche. Dagegen stehen drei Netze: Rechenprobe/Saldoprobe, LLM-Fallback, Prüfansicht.
  **Wo ein Parser danebenliegt: bitte ein anonymisiertes Beispiel schicken** (`cli --text`-Ausgabe genügt) – die Anpassung
  ist meist eine Zeile Regex plus Fixture.
* Am unsichersten: das neue Scalable-Capital-Bank-Format (Label-Parser), Bedeutung von `amount/fee/tax` im Scalable-CSV
  (angenommen: `amount` ohne Gebühr/Steuer; Cash-Wirkung = amount − fee − tax), Trade-Republic-Kontoauszug (Zeilen werden
  von pdfplumber teils über drei Zeilen verteilt; Richtung kommt aus der Saldo-Kette) und Postbank-PDF (Gegenpartei/Verwendungszweck-Trennung).
* Trade-Republic-Kontoauszug liefert für Käufe keinen Kurs und keine Gebühren → Kurs = Betrag ÷ Stück; die
  Wertpapierabrechnung ist genauer und wird als Duplikat erkannt, egal in welcher Reihenfolge importiert wird.
* Giro-Zeilen wie „Wertpapierkauf“ (ING) duplizieren die Cash-Seite der Depot-Buchung. Sie werden markiert
  (`review_required`, bei schon gebuchter Abrechnung `duplicate`) und von „Alle übernehmen“ ausgelassen.
* Umbuchungen zwischen eigenen Konten werden als `transfer_in/out` typisiert und nach jedem Übernehmen mit der
  Gegenbuchung verknüpft (`transfer_peer_id`, `app.services.transfers`; Regeln siehe ARCHITECTURE.md → „Umbuchungen“).
  Fehlt die Gegenbuchung noch (anderer Auszug nicht importiert), bleibt die Zeile typisiert und wird später verknüpft;
  `POST /api/transactions/link-transfers` stößt den Abgleich für alle Buchungen an.
* Nicht als Buchung umgesetzt: Steuerbescheinigungen, Depotauszüge (Bestände), Thesaurierung/Vorabpauschale ohne Cash,
  Splits/Kapitalmaßnahmen – diese gehen ans LLM bzw. müssen manuell erfasst werden.
* Live-Aufrufe gegen Ollama konnten in der Entwicklungsumgebung nicht getestet werden (kein Ollama, kein Netz); der
  Client hält sich an die dokumentierte REST-API und ist gegen einen Fake getestet. Tesseract-OCR ebenso nur über einen Stub.
* LLM-Ergebnisse haben Konfidenz 0,7 (Vision 0,62), +0,15 bei bestandener Rechenprobe. Kontoauszüge über das LLM
  können Zeilen auslassen – deshalb immer den Kontostand-Entwurf mit übernehmen und im Konto abgleichen.
