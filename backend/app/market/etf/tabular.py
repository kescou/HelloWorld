"""Flexible holdings-table reader shared by all ETF sources and the manual upload.

Accepts CSV (any of , ; tab |, UTF-8/UTF-16/Latin-1), XLSX, or a JSON list of objects. Finds the header
row by itself (issuer files start with a few title/date lines), maps German/English column names and
normalises weights to percent.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

from app.market.etf.base import HoldingRow, HoldingsUnavailable
from app.market.util import is_isin, normalize_country, normalize_sector, parse_date_loose, parse_number


def _norm(header: Any) -> str:
    return re.sub(r"[^a-z0-9%äöü]", "", str(header or "").lower())


_SYNONYMS: dict[str, list[str]] = {
    "name": ["name", "securityname", "holdingname", "constituentname", "bezeichnung", "wertpapier", "wertpapiername",
             "titel", "position", "emittent", "issuername", "instrument", "instrumentname", "holding", "security",
             "description", "securitydescription", "unternehmen", "company", "companyname", "holdings", "aktie",
             "securitylongdescription", "longname", "assetname", "komponente", "bestandteil"],
    "isin": ["isin", "isincode", "securityisin", "wertpapierisin", "holdingisin", "instrumentisin"],
    "symbol": ["ticker", "emittententicker", "issuerticker", "symbol", "tickersymbol", "kürzel", "bloombergticker",
               "bbg", "ric", "holdingticker", "securityticker"],
    "weight": ["weight", "weight%", "weighting", "weighting%", "gewichtung", "gewichtung%", "gewicht", "gewicht%",
               "anteil", "anteil%", "%ofnetassets", "percentoffund", "%netassets", "%offund", "holdingpercent",
               "portfolio%", "%", "marketvalue%", "percentageoftotalnetassets", "marketvaluepercentage", "weightin%",
               "%weight", "percent", "percentage", "fundweight", "nettoanteil", "ofnetassets", "%assets",
               "percentageoffund", "gewichtungin%", "anteilin%", "pctnetassets"],
    "sector": ["sector", "sektor", "branche", "industryclassification", "gicssector", "sectorclassification",
               "industry", "industriesektor", "gicssectordescription", "icbsectordescription", "sectorname",
               "icbindustry", "gicsindustrygroup"],
    "country": ["country", "land", "standort", "location", "countryofrisk", "tradecountryname", "domicile",
                "countryname", "locationofrisk", "region", "bloombergisocountry", "countrycode", "herkunftsland"],
    "asset_class": ["assetclass", "anlageklasse", "typeofsecurity", "assettype", "securitytype", "instrumenttype",
                    "type", "wertpapierart", "art"],
    "market_value": ["marketvalue", "marktwert", "basemarketvalue", "marketvaluebasecurrency", "kurswert",
                     "notionalvalue", "marketvalueeur", "marketvalueusd"],
}
_LOOKUP = {syn: field for field, syns in _SYNONYMS.items() for syn in syns}

_ASSET_CLASS = {
    "equity": "equity", "aktien": "equity", "aktie": "equity", "stock": "equity", "commonstock": "equity",
    "equities": "equity", "ordinaryshares": "equity", "reit": "equity", "shares": "equity",
    "cash": "cash", "barmittel": "cash", "geldmarkt": "cash", "moneymarket": "cash", "cashcollateralandmargins": "cash",
    "cashundoderderivate": "cash", "cashandorderivatives": "cash", "liquidität": "cash",
    "futures": "derivative", "future": "derivative", "fx": "derivative", "devisen": "derivative", "swap": "derivative",
    "swaps": "derivative", "derivative": "derivative", "derivatives": "derivative", "forwards": "derivative",
    "fixedincome": "bond", "bond": "bond", "bonds": "bond", "anleihen": "bond", "anleihe": "bond",
}


def normalize_asset_class(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _ASSET_CLASS.get(_norm(value), str(value).strip().lower()[:40])


# ---------------------------------------------------------------- raw readers
def _decode(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def _sniff_delimiter(text: str) -> str:
    best, best_score = ",", -1.0
    lines = [ln for ln in text.splitlines()[:60] if ln.strip()]
    for delim in (",", ";", "\t", "|"):
        counts = [ln.count(delim) for ln in lines]
        if not counts:
            continue
        # the table body has many columns on many lines
        score = sorted(counts)[len(counts) // 2] * 2 + max(counts)
        if score > best_score:
            best, best_score = delim, score
    return best


def read_rows(content: bytes, filename: str | None = None) -> list[list[Any]]:
    """Bytes -> list of rows (lists of cells). Detects XLSX by magic bytes, JSON by first char."""
    if not content:
        raise HoldingsUnavailable("empty file")
    if content[:2] == b"PK":
        return _read_xlsx(content)
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise HoldingsUnavailable("altes .xls-Format wird nicht unterstützt – bitte als .xlsx oder .csv speichern")
    head = content.lstrip()[:1]
    if head in (b"[", b"{"):
        try:
            return rows_from_json(json.loads(_decode(content)))
        except ValueError:
            pass
    if content.lstrip()[:5].lower() in (b"<!doc", b"<html"):
        raise HoldingsUnavailable("HTML statt Datei erhalten (Download-Seite / Consent-Seite?)")
    text = _decode(content)
    delim = _sniff_delimiter(text)
    return [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _read_xlsx(content: bytes) -> list[list[Any]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise HoldingsUnavailable("openpyxl fehlt (pip install openpyxl)") from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise HoldingsUnavailable(f"XLSX nicht lesbar: {exc}") from exc
    best: list[list[Any]] = []
    best_score = -1
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        _idx, score = _find_header(rows)
        if score > best_score or (score == best_score and len(rows) > len(best)):
            best, best_score = rows, score
    return best


def rows_from_json(payload: Any) -> list[list[Any]]:
    """Find the biggest list of flat-ish objects anywhere in a JSON document and turn it into header + rows."""
    best: list[dict] = []

    def walk(node: Any) -> None:
        nonlocal best
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) > len(best) and len(dicts) == len(node):
                best = dicts
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(payload)
    if not best:
        raise HoldingsUnavailable("keine Positionsliste im JSON gefunden")
    flat = [_flatten(d) for d in best]
    keys: list[str] = []
    for d in flat:
        for k in d:
            if k not in keys:
                keys.append(k)
    return [keys] + [[d.get(k) for k in keys] for d in flat]


def _flatten(d: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if not isinstance(v2, (dict, list)):
                    out.setdefault(k2, v2)
        elif not isinstance(v, list):
            out[k] = v
    return out


# ---------------------------------------------------------------- header detection + parsing
def _map_header(row: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = _norm(cell)
        if not key:
            continue
        field = _LOOKUP.get(key)
        if field is None:
            if "isin" in key and len(key) <= 16:
                field = "isin"
            elif key.startswith(("gewicht", "weight", "anteil")) or key.endswith("weight"):
                field = "weight"
            elif key.startswith(("sektor", "sector")):
                field = "sector"
            elif key.startswith(("land", "country")):
                field = "country"
        if field and field not in mapping:
            mapping[field] = idx
    return mapping


def _find_header(rows: list[list[Any]]) -> tuple[int, int]:
    best_idx, best_score = -1, 0
    for i, row in enumerate(rows[:60]):
        m = _map_header(row)
        if not ({"weight", "market_value"} & set(m)) or not ({"name", "isin", "symbol"} & set(m)):
            continue
        if len(m) > best_score:
            best_idx, best_score = i, len(m)
    return best_idx, best_score


_AS_OF_RE = re.compile(r"as of|as at|per\b|stand|datum|date|holdings as|stichtag|zum\b", re.I)


def _find_as_of(rows: list[list[Any]], header_idx: int) -> date | None:
    for row in rows[:header_idx]:
        cells = [c for c in row if c not in (None, "")]
        if not cells:
            continue
        text = " ".join(str(c) for c in cells)
        if _AS_OF_RE.search(text):
            for c in reversed(cells):
                d = parse_date_loose(c)
                if d and 2000 <= d.year <= date.today().year + 1:
                    return d
            d = parse_date_loose(text)
            if d:
                return d
    return None


def parse_holdings_rows(rows: list[list[Any]]) -> tuple[list[HoldingRow], date | None]:
    """Rows (with some header row somewhere) -> (holdings with weight in percent, as_of)."""
    header_idx, _ = _find_header(rows)
    if header_idx < 0:
        raise HoldingsUnavailable(
            "Keine Kopfzeile erkannt – benötigt werden mindestens eine Namens-/ISIN-Spalte und eine Gewichtsspalte "
            "(z.B. Name;ISIN;Gewichtung).")
    cols = _map_header(rows[header_idx])
    as_of = _find_as_of(rows, header_idx)

    def cell(row: list[Any], field: str) -> Any:
        idx = cols.get(field)
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        if isinstance(value, str):
            value = value.strip()
            if value in ("", "-", "--", "n/a", "N/A"):
                return None
        return value

    raw: list[tuple[HoldingRow, float | None, float | None]] = []
    for row in rows[header_idx + 1:]:
        if not row or all(c in (None, "") for c in row):
            continue
        name, isin, symbol = cell(row, "name"), cell(row, "isin"), cell(row, "symbol")
        isin = str(isin).strip().upper() if isin else None
        if isin and not is_isin(isin):
            isin = None
        weight = parse_number(cell(row, "weight")) if "weight" in cols else None
        mv = parse_number(cell(row, "market_value")) if "market_value" in cols else None
        label = name or symbol or isin
        if not label or (weight is None and mv is None):
            continue
        symbol = str(symbol).strip() if symbol else None
        if symbol and (len(symbol) > 32 or " " in symbol.strip() and len(symbol) > 12):
            symbol = None
        raw.append((HoldingRow(
            name=str(label).strip()[:200], weight=0.0, isin=isin, symbol=symbol,
            sector=normalize_sector(cell(row, "sector")), country=normalize_country(cell(row, "country")),
            asset_class=normalize_asset_class(cell(row, "asset_class")),
        ), weight, mv))
    if not raw:
        raise HoldingsUnavailable("Kopfzeile gefunden, aber keine Positionen mit Gewicht.")

    if any(w is not None for _h, w, _mv in raw):
        weights = [w or 0.0 for _h, w, _mv in raw]
        total = sum(w for w in weights if w > 0)
        # fractions (0.0451) instead of percent (4.51)? -> total around 1.0
        factor = 100.0 if 0 < total <= 1.5 else 1.0
        for (h, _w, _mv), w in zip(raw, weights):
            h.weight = w * factor
    else:
        total_mv = sum(mv for _h, _w, mv in raw if mv and mv > 0)
        if total_mv <= 0:
            raise HoldingsUnavailable("Weder Gewicht noch Marktwert auswertbar.")
        for h, _w, mv in raw:
            h.weight = (mv or 0.0) / total_mv * 100.0
    out = [h for h, _w, _mv in raw if abs(h.weight) >= 1e-6]
    out.sort(key=lambda h: -h.weight)
    return out, as_of


def parse_holdings_file(content: bytes, filename: str | None = None) -> tuple[list[HoldingRow], date | None]:
    return parse_holdings_rows(read_rows(content, filename))


def parse_holdings_json(payload: Any) -> tuple[list[HoldingRow], date | None]:
    return parse_holdings_rows(rows_from_json(payload))
