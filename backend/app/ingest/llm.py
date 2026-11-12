"""Tier 5 – local LLM fallback via the Ollama REST API (https://github.com/ollama/ollama/blob/main/docs/api.md).

Only two endpoints are used:
    GET  /api/tags  -> {"models": [{"name": "qwen2.5:7b-instruct", ...}]}
    POST /api/chat  -> {"message": {"role": "assistant", "content": "<json>"}, ...}
with ``stream: false``, ``format`` = JSON schema (structured outputs), ``options.temperature = 0``,
a bounded ``options.num_ctx`` and ``keep_alive`` so the model stays loaded between uploads.
Vision models get page images as base64 in ``messages[].images``.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from app.core.config import get_settings
from app.ingest.config import get_ingest_settings
from app.ingest.dto import TX_TYPES, Draft

log = logging.getLogger(__name__)

# Tests install an httpx.MockTransport here; production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None

LLM_CONFIDENCE = 0.7
VISION_CONFIDENCE = 0.62


class LLMUnavailable(RuntimeError):
    """Ollama not reachable or the model is not pulled. Message is German and user-facing."""


class LLMError(RuntimeError):
    """Ollama answered, but not with usable JSON."""


_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "institution": {"type": "string", "enum": ["postbank", "ing", "scalable", "trade_republic", "other"]},
        "doc_type": {"type": "string", "enum": ["trade_buy", "trade_sell", "savings_plan", "dividend", "interest",
                                                 "tax_statement", "account_statement", "depot_statement", "other"]},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(TX_TYPES)},
                    "booking_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "value_date": _NULLABLE_STR,
                    "isin": _NULLABLE_STR, "wkn": _NULLABLE_STR, "name": _NULLABLE_STR,
                    "quantity": _NULLABLE_NUM, "price": _NULLABLE_NUM,
                    "amount": {"type": "number"},
                    "fees": _NULLABLE_NUM, "taxes": _NULLABLE_NUM,
                    "currency": {"type": "string"}, "fx_rate": _NULLABLE_NUM,
                    "counterparty": _NULLABLE_STR, "description": _NULLABLE_STR,
                },
                "required": ["type", "booking_date", "amount"],
            },
        },
        "balances": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"date": {"type": "string"}, "balance": {"type": "number"}},
                      "required": ["date", "balance"]},
        },
    },
    "required": ["transactions", "balances"],
}

SYSTEM_PROMPT = """Du bist ein präziser Extraktor für deutsche Bank- und Depotdokumente (Postbank, ING, Scalable Capital/Baader Bank, Trade Republic).
Gib AUSSCHLIESSLICH JSON gemäß Schema zurück. Erfinde nichts: Was nicht im Dokument steht, ist null.

Regeln:
- Deutsche Zahlen umrechnen: "1.234,56" -> 1234.56; "85,00-" oder "85,00 S" -> -85.00; "H" = Haben = positiv.
- Datum als YYYY-MM-DD ("02.01.2024" -> "2024-01-02"). Fehlt das Jahr in einer Zeile, nimm es aus dem Kopf des Dokuments.
- amount = Wirkung auf das Geldkonto in EUR MIT Vorzeichen, inklusive Gebühren und Steuern:
  Kauf negativ; Verkauf, Dividende, Zinsen, Gutschrift positiv; Lastschrift/Kartenzahlung/Überweisung-Ausgang negativ.
- type: buy (Kauf, Sparplan), sell (Verkauf), dividend (Dividende/Ausschüttung/Ertrag), interest (Zinsen),
  deposit (Geldeingang), withdrawal (Geldausgang), fee (Entgelt/Kontoführung), tax (Steuerbuchung).
- Wertpapiere: isin (12 Zeichen), wkn, name, quantity (Stück, auch Bruchteile), price (Kurs je Stück in EUR),
  fees = Summe Provision/Fremdkosten/Entgelte (positiv), taxes = Summe Kapitalertragsteuer + Solidaritätszuschlag +
  Kirchensteuer + Quellensteuer (positiv). Bei Fremdwährung: fx_rate = Devisenkurs (1 EUR = x Fremdwährung), price in EUR.
- Kontoauszug: JEDE Buchungszeile ist eine eigene Transaktion, in Dokumentreihenfolge, keine auslassen, keine doppelt.
  counterparty = Auftraggeber/Empfänger, description = Buchungstext + Verwendungszweck.
- balances: nur der ausgewiesene End-/Neue Kontostand mit Datum (nicht der alte Saldo, keine Zwischensummen).
- Keine Summenzeilen, Überträge oder Saldozeilen als Transaktion ausgeben."""

_FEWSHOT: list[tuple[str, dict]] = [
    (
        "WERTPAPIERABRECHNUNG\nMarket-Order Kauf am 02.01.2024, um 10:23 Uhr an der Lang & Schwarz Exchange.\n"
        "POSITION ANZAHL KURS BETRAG\nTesla Inc. 3 Stk. 108,10 EUR 324,30 EUR\nISIN: US88160R1014\n"
        "Fremdkostenzuschlag -1,00 EUR\nGESAMT -325,30 EUR\nVERRECHNUNGSKONTO VALUTA BETRAG\nDE00123 04.01.2024 -325,30 EUR",
        {"institution": "trade_republic", "doc_type": "trade_buy", "transactions": [
            {"type": "buy", "booking_date": "2024-01-02", "value_date": "2024-01-04", "isin": "US88160R1014", "wkn": None,
             "name": "Tesla Inc.", "quantity": 3, "price": 108.10, "amount": -325.30, "fees": 1.00, "taxes": 0,
             "currency": "EUR", "fx_rate": None, "counterparty": "Trade Republic", "description": "Kauf Tesla Inc."}],
         "balances": []},
    ),
    (
        "Kontoauszug März 2023\nAlter Saldo 1.000,00 Euro\nNeuer Saldo 3.391,55 Euro\nDatum 31.03.2023\n"
        "01.03.2023 Gehalt/Rente Muster GmbH 2.500,00\n01.03.2023 Lohn 03/2023\n"
        "02.03.2023 Lastschrift Stadtwerke Musterstadt -85,00\n02.03.2023 Abschlag Strom\n"
        "05.03. Kartenzahlung REWE SAGT DANKE 23,45-",
        {"institution": "ing", "doc_type": "account_statement", "transactions": [
            {"type": "deposit", "booking_date": "2023-03-01", "value_date": "2023-03-01", "isin": None, "wkn": None, "name": None,
             "quantity": None, "price": None, "amount": 2500.00, "fees": 0, "taxes": 0, "currency": "EUR", "fx_rate": None,
             "counterparty": "Muster GmbH", "description": "Gehalt/Rente Lohn 03/2023"},
            {"type": "withdrawal", "booking_date": "2023-03-02", "value_date": "2023-03-02", "isin": None, "wkn": None, "name": None,
             "quantity": None, "price": None, "amount": -85.00, "fees": 0, "taxes": 0, "currency": "EUR", "fx_rate": None,
             "counterparty": "Stadtwerke Musterstadt", "description": "Lastschrift Abschlag Strom"},
            {"type": "withdrawal", "booking_date": "2023-03-05", "value_date": None, "isin": None, "wkn": None, "name": None,
             "quantity": None, "price": None, "amount": -23.45, "fees": 0, "taxes": 0, "currency": "EUR", "fx_rate": None,
             "counterparty": "REWE SAGT DANKE", "description": "Kartenzahlung"}],
         "balances": [{"date": "2023-03-31", "balance": 3391.55}]},
    ),
    (
        "Dividendengutschrift\nISIN (WKN) US0378331005 (865985)\nWertpapierbezeichnung Apple Inc.\nNominale 10 Stück\n"
        "Zahltag 16.02.2023\nBrutto USD 2,30\nQuSt 15,00 % USD 0,35\nUmg. z. Dev.-Kurs (1,071500) EUR 1,82\n"
        "Kapitalertragsteuer 25,00 % EUR 0,21\nSolidaritätszuschlag 5,50 % EUR 0,01\nGesamtbetrag zu Ihren Gunsten EUR 1,60",
        {"institution": "ing", "doc_type": "dividend", "transactions": [
            {"type": "dividend", "booking_date": "2023-02-16", "value_date": "2023-02-16", "isin": "US0378331005", "wkn": "865985",
             "name": "Apple Inc.", "quantity": 10, "price": 0.2147, "amount": 1.60, "fees": 0, "taxes": 0.55,
             "currency": "EUR", "fx_rate": 1.0715, "counterparty": "ING", "description": "Dividende Apple Inc."}],
         "balances": []},
    ),
]


def _fewshot_messages() -> list[dict]:
    msgs: list[dict] = []
    for doc, answer in _FEWSHOT:
        msgs.append({"role": "user", "content": f"Dokument:\n{doc}"})
        msgs.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    return msgs


# ----------------------------------------------------------------------------- JSON repair

def repair_json(raw: str) -> dict:
    """Best-effort parse of slightly broken model output (code fences, prose around the object,
    trailing commas, truncated tail). Raises LLMError if nothing usable is found."""
    if raw is None:
        raise LLMError("leere Antwort")
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I)
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()
    for candidate in (s, _outer_object(s)):
        if not candidate:
            continue
        for fix in (lambda x: x, _strip_trailing_commas, lambda x: _close_truncated(_strip_trailing_commas(x))):
            try:
                obj = json.loads(fix(candidate))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list):
                return {"transactions": obj, "balances": []}
    raise LLMError("Antwort des Modells ist kein gültiges JSON")


def _outer_object(s: str) -> str | None:
    start = s.find("{")
    if start == -1:
        return None
    end = s.rfind("}")
    return s[start:end + 1] if end > start else s[start:]


def _strip_trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def _close_truncated(s: str) -> str:
    """Close open strings/brackets of a cut-off answer (num_predict reached)."""
    stack: list[str] = []
    in_str = esc = False
    last_complete = 0
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            if len(stack) == 2:      # an element of root.transactions[] just closed
                last_complete = i + 1
    if not stack and not in_str:
        return s
    if last_complete and len(stack) >= 2:
        s = s[:last_complete]
        return s + "]}"
    if in_str:
        s += '"'
    return s + "".join(reversed(stack))


# ----------------------------------------------------------------------------- client

class OllamaClient:
    def __init__(self, base_url: str | None = None, text_model: str | None = None, vision_model: str | None = None,
                 transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.cfg = get_ingest_settings()
        self.base_url = (base_url or s.ollama_url).rstrip("/")
        self.text_model = text_model or s.ollama_text_model
        self.vision_model = vision_model or s.ollama_vision_model
        self._transport = transport or TRANSPORT

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=3.0),
                            transport=self._transport, trust_env=False)

    # -- status
    def list_models(self) -> list[str]:
        try:
            with self._client(self.cfg.llm_status_timeout_seconds) as c:
                r = c.get("/api/tags")
                r.raise_for_status()
                return [m.get("name") or m.get("model") for m in r.json().get("models", []) if m.get("name") or m.get("model")]
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMUnavailable(self._down_message()) from exc

    @staticmethod
    def has_model(models: list[str], wanted: str) -> bool:
        w = wanted if ":" in wanted else f"{wanted}:latest"
        return any(m == wanted or m == w or (":" not in m and f"{m}:latest" == w) for m in models)

    def status(self) -> dict:
        out = {"available": False, "models": [], "text_model": self.text_model, "vision_model": self.vision_model,
               "text_model_available": False, "vision_model_available": False, "url": self.base_url, "error": None, "hint": None}
        try:
            models = self.list_models()
        except LLMUnavailable as exc:
            out["error"] = str(exc)
            out["hint"] = "Ollama starten: `ollama serve` (bzw. die Ollama-App öffnen)."
            return out
        out["available"] = True
        out["models"] = models
        out["text_model_available"] = self.has_model(models, self.text_model)
        out["vision_model_available"] = self.has_model(models, self.vision_model)
        missing = [m for m, ok in ((self.text_model, out["text_model_available"]), (self.vision_model, out["vision_model_available"])) if not ok]
        if missing:
            out["hint"] = "Fehlende Modelle laden: " + " && ".join(f"ollama pull {m}" for m in missing)
        return out

    def _down_message(self) -> str:
        return (f"Das lokale Sprachmodell (Ollama) ist unter {self.base_url} nicht erreichbar. Bitte Ollama starten "
                f"(`ollama serve`) und das Dokument erneut verarbeiten.")

    # -- chat
    def chat_json(self, model: str, messages: list[dict], schema: dict | None = None) -> dict:
        """One structured-output call incl. repair + retry on malformed JSON."""
        cfg = self.cfg
        msgs = list(messages)
        last_err: Exception | None = None
        for attempt in range(cfg.llm_retries + 1):
            body = {
                "model": model, "messages": msgs, "stream": False, "format": schema or "json",
                "keep_alive": cfg.llm_keep_alive,
                "options": {"temperature": 0, "num_ctx": cfg.llm_num_ctx, "num_predict": cfg.llm_num_predict, "seed": 7},
            }
            try:
                with self._client(cfg.llm_timeout_seconds) as c:
                    r = c.post("/api/chat", json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(
                    f"Das lokale Sprachmodell hat nicht innerhalb von {int(cfg.llm_timeout_seconds)} s geantwortet. "
                    "Kleineres Modell wählen oder INGEST_LLM_TIMEOUT_SECONDS erhöhen.") from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable(self._down_message()) from exc
            if r.status_code == 404:
                raise LLMUnavailable(f"Das Modell „{model}“ ist in Ollama nicht installiert. Bitte ausführen: `ollama pull {model}`")
            if r.status_code >= 400:
                detail = ""
                try:
                    detail = r.json().get("error", "")
                except ValueError:
                    detail = r.text[:200]
                if "not found" in detail.lower():
                    raise LLMUnavailable(f"Das Modell „{model}“ ist in Ollama nicht installiert. Bitte ausführen: `ollama pull {model}`")
                raise LLMError(f"Ollama-Fehler {r.status_code}: {detail}")
            try:
                content = (r.json().get("message") or {}).get("content", "")
            except ValueError as exc:
                last_err = LLMError(f"Ollama lieferte kein JSON: {exc}")
                continue
            try:
                return repair_json(content)
            except LLMError as exc:
                last_err = exc
                log.warning("LLM returned malformed JSON (attempt %d): %.200s", attempt + 1, content)
                msgs = list(messages) + [
                    {"role": "assistant", "content": content[:2000]},
                    {"role": "user", "content": "Das war kein gültiges JSON. Antworte erneut, NUR mit dem vollständigen JSON-Objekt gemäß Schema."},
                ]
        raise last_err or LLMError("unbekannter Fehler")


def get_client() -> OllamaClient:
    return OllamaClient()


# ----------------------------------------------------------------------------- chunking

_DATE_LINE = re.compile(r"^\s*\d{1,2}[. ]")


def split_chunks(text: str, max_chars: int | None = None) -> list[str]:
    """Split by page, then by lines (preferring breaks before a line that starts with a date so a
    booking is never cut in half). Short documents stay in one piece."""
    max_chars = max_chars or get_ingest_settings().llm_chunk_chars
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    buf: list[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        if buf:
            chunks.append("\n".join(buf).strip())
        buf, size = [], 0

    for page in text.split("\f"):
        page = page.strip("\n")
        if not page.strip():
            continue
        if size and size + len(page) > max_chars:
            flush()
        if len(page) <= max_chars:
            buf.append(page)
            size += len(page) + 1
            continue
        for line in page.split("\n"):
            if size + len(line) > max_chars and (_DATE_LINE.match(line) or size > max_chars * 1.3):
                flush()
            buf.append(line)
            size += len(line) + 1
        flush()
    flush()
    return [c for c in chunks if c]


# ----------------------------------------------------------------------------- extraction

def _to_drafts(obj: dict, confidence: float) -> list[Draft]:
    drafts: list[Draft] = []
    for t in obj.get("transactions") or []:
        if not isinstance(t, dict):
            continue
        payload = {k: t.get(k) for k in ("type", "booking_date", "value_date", "isin", "wkn", "name", "quantity", "price",
                                         "amount", "fees", "taxes", "currency", "fx_rate", "counterparty", "description")}
        payload["account_id"] = None
        drafts.append(Draft(kind="transaction", payload=payload, confidence=confidence))
    for b in obj.get("balances") or []:
        if isinstance(b, dict) and b.get("balance") is not None:
            drafts.append(Draft(kind="balance", payload={"account_id": None, "date": b.get("date"), "balance": b.get("balance")},
                                confidence=confidence, role="cash"))
    return drafts


def _map_concurrent(fn, items: list) -> list:
    workers = max(1, min(get_ingest_settings().llm_concurrency, len(items)))
    if workers == 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest-llm") as pool:
        return list(pool.map(fn, items))


def _merge(results: list[dict], confidence: float) -> tuple[list[Draft], str | None, str | None]:
    drafts: list[Draft] = []
    institution = doc_type = None
    balances: list[Draft] = []
    for obj in results:
        institution = institution or (obj.get("institution") if obj.get("institution") not in (None, "other") else None)
        doc_type = doc_type or (obj.get("doc_type") if obj.get("doc_type") not in (None, "other") else None)
        for d in _to_drafts(obj, confidence):
            (balances if d.kind == "balance" else drafts).append(d)
    # several chunks may each report a balance: keep the latest one only
    if balances:
        balances.sort(key=lambda d: str(d.payload.get("date") or ""))
        drafts.append(balances[-1])
    return drafts, institution, doc_type


def extract_text(text: str, client: OllamaClient | None = None, *, hint: str | None = None) -> tuple[list[Draft], str | None, str | None]:
    """Text path. Returns (drafts, institution, doc_type). Raises LLMUnavailable / LLMError."""
    client = client or get_client()
    cfg = get_ingest_settings()
    chunks = split_chunks(text)[: cfg.llm_max_chunks]
    if not chunks:
        return [], None, None
    header = chunks[0][:600]
    base = [{"role": "system", "content": SYSTEM_PROMPT}, *_fewshot_messages()]

    def run(item: tuple[int, str]) -> dict:
        i, chunk = item
        parts = []
        if hint:
            parts.append(f"Hinweis: {hint}")
        if i > 0:
            parts.append(f"Kopf des Dokuments (nur Kontext für Jahr/Bank, NICHT erneut extrahieren):\n{header}")
            parts.append(f"Dokument (Teil {i + 1} von {len(chunks)}):\n{chunk}")
        else:
            parts.append(f"Dokument{f' (Teil 1 von {len(chunks)})' if len(chunks) > 1 else ''}:\n{chunk}")
        return client.chat_json(client.text_model, [*base, {"role": "user", "content": "\n\n".join(parts)}], EXTRACTION_SCHEMA)

    results = _map_concurrent(run, list(enumerate(chunks)))
    return _merge(results, LLM_CONFIDENCE)


def extract_images(images: list[bytes], client: OllamaClient | None = None, *, ocr_text: str | None = None,
                   hint: str | None = None) -> tuple[list[Draft], str | None, str | None]:
    """Vision path: one request per page image, OCR text (if any) as extra context."""
    client = client or get_client()
    base = [{"role": "system", "content": SYSTEM_PROMPT}, *_fewshot_messages()]
    ocr_pages = (ocr_text or "").split("\f") if ocr_text else []

    def run(item: tuple[int, bytes]) -> dict:
        i, img = item
        parts = ["Extrahiere alle Buchungen aus dem Bild (Scan/Screenshot eines Bank- oder Depotdokuments)."]
        if hint:
            parts.append(f"Hinweis: {hint}")
        if len(images) > 1:
            parts.append(f"Seite {i + 1} von {len(images)}.")
        ocr = ocr_pages[i].strip() if i < len(ocr_pages) else ""
        if ocr:
            parts.append("OCR-Text derselben Seite (kann Lesefehler enthalten; im Zweifel gilt das Bild):\n" + ocr[:6000])
        msg = {"role": "user", "content": "\n\n".join(parts), "images": [base64.b64encode(img).decode("ascii")]}
        return client.chat_json(client.vision_model, [*base, msg], EXTRACTION_SCHEMA)

    results = _map_concurrent(run, list(enumerate(images)))
    return _merge(results, VISION_CONFIDENCE)
