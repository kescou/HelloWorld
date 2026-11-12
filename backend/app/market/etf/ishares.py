"""iShares (BlackRock): full daily holdings CSV from the product page's ajax endpoint.  STATUS: implemented

    https://www.ishares.com/<site>/<products>/<productId>/<slug>/1478358465952.ajax?fileType=csv&fileName=holdings&dataType=fund

The product page is resolved by ISIN through the product-screener JSON of the same site. The resolved
CSV URL is cached in instrument.meta["market"]["holdings_url"]; it can also be set there by hand.
Note: equity-ETF CSVs carry ticker + name but NO ISIN per holding (bond ETFs do) – the look-through
therefore matches by normalised name/ticker.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.market import http
from app.market.etf.base import EtfHoldingsProvider, HoldingsResult, HoldingsUnavailable
from app.market.etf.tabular import parse_holdings_file
from app.models.core import Instrument

log = logging.getLogger("market.etf.ishares")

BASE = "https://www.ishares.com"
AJAX_ID = "1478358465952"
# (site path, screener config path) – English UK site first (English sector/country labels), then German.
SITES = [
    ("/uk/individual/en", "/templatedata/config/product-screener-v3/data/en/uk/product-screener/ishares-product-screener-backend-config"),
    ("/de/privatanleger/de", "/templatedata/config/product-screener-v3/data/de/de/product-screener/ishares-product-screener-backend-config"),
]


def screener_url(site: str, dcr_path: str) -> str:
    return f"{BASE}{site}/product-screener/product-screener-v3.1.jsn?dcrPath={dcr_path}&siteEntryPassthrough=true"


def _cell(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("r", value.get("d"))
    return value


def parse_screener(payload: Any) -> dict[str, str]:
    """ISIN -> product page path. Handles both known shapes of the screener JSON:
    v3  : {"251882": {"isin": "...", "productPageUrl": "/uk/..."}, ...}
    v3.1: {"data": {"tableData": {"columns": [{"name": "isin"}, ...], "data": [[...], ...]}}}
    """
    out: dict[str, str] = {}
    if not isinstance(payload, dict):
        return out
    table = (payload.get("data") or {}).get("tableData") if isinstance(payload.get("data"), dict) else None
    if isinstance(table, dict) and isinstance(table.get("columns"), list):
        names = [c.get("name") if isinstance(c, dict) else c for c in table["columns"]]
        try:
            i_isin, i_url = names.index("isin"), names.index("productPageUrl")
        except ValueError:
            return out
        for row in table.get("data") or []:
            try:
                isin, url = _cell(row[i_isin]), _cell(row[i_url])
            except (IndexError, TypeError):
                continue
            if isinstance(isin, str) and isinstance(url, str) and url:
                out[isin.strip().upper()] = url
        return out
    for value in payload.values():
        if isinstance(value, dict):
            isin, url = _cell(value.get("isin")), _cell(value.get("productPageUrl"))
            if isinstance(isin, str) and isinstance(url, str) and url:
                out[isin.strip().upper()] = url
    return out


def holdings_url(product_path: str) -> str:
    path = product_path.split("?")[0].rstrip("/")
    if not path.startswith("http"):
        path = BASE + path
    return f"{path}/{AJAX_ID}.ajax?fileType=csv&fileName=holdings&dataType=fund"


def resolve_holdings_url(isin: str) -> str:
    errors: list[str] = []
    for site, dcr in SITES:
        try:
            mapping = parse_screener(http.get_json(screener_url(site, dcr), timeout=40))
        except http.RemoteError as exc:
            errors.append(f"{site}: {exc}")
            continue
        if isin in mapping:
            return holdings_url(mapping[isin])
        errors.append(f"{site}: ISIN nicht im Screener ({len(mapping)} Produkte)")
    # last resort: site search page, look for a product link
    try:
        html = http.get_text(f"{BASE}/de/privatanleger/de/search/summary-search-results",
                             params={"searchText": isin, "siteEntryPassthrough": "true"}, timeout=30)
        m = re.search(r'href="(/de/privatanleger/de/produkte/\d+/[^"?#]*)', html)
        if m:
            return holdings_url(m[1])
        errors.append("Suche: kein Produktlink")
    except http.RemoteError as exc:
        errors.append(f"Suche: {exc}")
    raise HoldingsUnavailable("iShares-Produktseite nicht gefunden – " + "; ".join(errors))


def parse_csv(content: bytes) -> HoldingsResult:
    rows, as_of = parse_holdings_file(content, "holdings.csv")
    return HoldingsResult(rows=rows, as_of=as_of, source="ishares")


class ISharesProvider(EtfHoldingsProvider):
    issuer = "ishares"
    status = "implemented"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        from app.market.service import meta_get, meta_set

        if not inst.isin:
            raise HoldingsUnavailable("ISIN fehlt")
        url = meta_get(inst, "ishares_url")
        cached = bool(url)
        if not url:
            url = resolve_holdings_url(inst.isin)
        try:
            content = http.get_bytes(url, timeout=60)
            result = parse_csv(content)
        except (http.RemoteError, HoldingsUnavailable) as exc:
            if cached:      # stale cached URL -> resolve again next time
                meta_set(inst, "ishares_url", None)
            raise HoldingsUnavailable(f"iShares-Download fehlgeschlagen: {exc}") from exc
        meta_set(inst, "ishares_url", url)
        return result
