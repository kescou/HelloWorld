"""OpenFIGI mapping API (free, no key: ~25 requests/min) – ISIN -> ticker + exchange code.

Used as a fallback when the Yahoo search does not know an ISIN.
"""
from __future__ import annotations

import logging

from app.market import http
from app.market.providers.base import SearchResult

log = logging.getLogger("market.openfigi")
URL = "https://api.openfigi.com/v3/mapping"

# Bloomberg exchange code -> Yahoo suffix. Order = preference for a German EUR investor.
EXCH_TO_YAHOO: dict[str, str] = {
    "GY": ".DE", "GR": ".DE", "GF": ".F", "GS": ".SG", "GM": ".MU", "GD": ".DU", "GH": ".HM", "GB": ".BE",
    "US": "", "UN": "", "UW": "", "UQ": "", "UR": "", "UA": "", "UP": "",
    "NA": ".AS", "FP": ".PA", "BB": ".BR", "IM": ".MI", "SM": ".MC", "PL": ".LS", "AV": ".VI", "ID": ".IR",
    "LN": ".L", "SW": ".SW", "SE": ".SW", "VX": ".SW", "SS": ".ST", "DC": ".CO", "NO": ".OL", "FH": ".HE",
    "CN": ".TO", "CT": ".TO", "AU": ".AX", "AT": ".AX", "JP": ".T", "JT": ".T", "HK": ".HK", "SP": ".SI",
}
_PREFERENCE = list(EXCH_TO_YAHOO)
_TYPE_MAP = {"Common Stock": "stock", "ETP": "etf", "ETF": "etf", "REIT": "stock", "ADR": "stock",
             "Depositary Receipt": "stock", "Mutual Fund": "fund", "Open-End Fund": "fund", "Preference": "stock"}


def to_yahoo_symbol(ticker: str, exch_code: str) -> str | None:
    suffix = EXCH_TO_YAHOO.get(exch_code)
    if suffix is None or not ticker:
        return None
    base = ticker.strip().replace("/", "-").replace(" ", "-")
    return f"{base}{suffix}"


def parse_mapping(payload: object, isin: str) -> list[SearchResult]:
    out: list[SearchResult] = []
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return out
    seen: set[str] = set()
    for row in payload[0].get("data") or []:
        sym = to_yahoo_symbol(row.get("ticker") or "", row.get("exchCode") or "")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        rank = _PREFERENCE.index(row["exchCode"])
        out.append(SearchResult(
            symbol=sym, name=row.get("name"), exchange=row.get("exchCode"),
            type=_TYPE_MAP.get(row.get("securityType2") or "", _TYPE_MAP.get(row.get("securityType") or "", "other")),
            isin=isin, score=float(1000 - rank), source="openfigi",
        ))
    out.sort(key=lambda r: -(r.score or 0))
    return out


def lookup_isin(isin: str) -> list[SearchResult]:
    payload = http.post_json(URL, [{"idType": "ID_ISIN", "idValue": isin}],
                             headers={"Content-Type": "application/json"}, timeout=15)
    return parse_mapping(payload, isin)
