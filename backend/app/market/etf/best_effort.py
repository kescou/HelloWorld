"""BEST-EFFORT issuer providers: Vanguard, Amundi (incl. Lyxor), SPDR, Invesco.

!! These endpoints are undocumented, could NOT be tested from the build sandbox and are known to change.
!! They are written defensively (any failure -> HoldingsUnavailable -> next provider / Yahoo top-10).
!! `python -m app.market.selfcheck` shows which of them currently work. If one is broken, set a direct
!! download URL in instrument.meta["market"]["holdings_url"] or use the manual CSV/XLSX upload.
"""
from __future__ import annotations

from typing import Any

from app.market import http
from app.market.etf.base import EtfHoldingsProvider, HoldingsResult, HoldingsUnavailable
from app.market.etf.tabular import parse_holdings_file, parse_holdings_json, read_rows
from app.market.util import normalize_symbol, parse_date_loose
from app.models.core import Instrument


def _need_isin(inst: Instrument) -> str:
    if not inst.isin:
        raise HoldingsUnavailable("ISIN fehlt")
    return inst.isin


# ---------------------------------------------------------------- Vanguard (GraphQL of the German retail site)
VANGUARD_GQL = "https://www.de.vanguard/gpx/graphql"
_VG_HEADERS = {"Content-Type": "application/json", "x-consumer-id": "de0", "Origin": "https://www.de.vanguard",
               "Referer": "https://www.de.vanguard/"}
_VG_PRODUCTS = "query { funds { profile { portId identifiers { altId altIdCode altIdValue } } } }"
_VG_HOLDINGS = (
    "query Holdings($portIds: [String!]!, $lastItemKey: String) { borHoldings(portIds: $portIds) { "
    "holdings(limit: 1500, lastItemKey: $lastItemKey) { items { issuerName securityLongDescription "
    "gicsSectorDescription icbSectorDescription marketValuePercentage ticker isin securityType "
    "bloombergIsoCountry effectiveDate } totalHoldings lastItemKey } } }"
)


def vanguard_find_port_id(payload: Any, isin: str) -> str | None:
    try:
        funds = payload["data"]["funds"] or []
    except (KeyError, TypeError):
        return None
    for fund in funds:
        profile = (fund or {}).get("profile") or {}
        for ident in profile.get("identifiers") or []:
            if isinstance(ident, dict) and isin in (ident.get("altIdValue"), ident.get("altId")):
                return str(profile.get("portId")) if profile.get("portId") else None
    return None


def vanguard_parse_holdings(payload: Any) -> HoldingsResult:
    try:
        blocks = payload["data"]["borHoldings"] or []
        items = [it for b in blocks for it in ((b.get("holdings") or {}).get("items") or [])]
    except (KeyError, TypeError, AttributeError) as exc:
        raise HoldingsUnavailable(f"Vanguard-Antwort unerwartet: {exc}") from exc
    if not items:
        raise HoldingsUnavailable("Vanguard: keine Positionen")
    rows, _ = parse_holdings_json(items)
    as_of = parse_date_loose(items[0].get("effectiveDate"))
    return HoldingsResult(rows=rows, as_of=as_of, source="vanguard")


class VanguardProvider(EtfHoldingsProvider):
    issuer = "vanguard"
    status = "best_effort"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        from app.market.service import meta_get, meta_set

        isin = _need_isin(inst)
        try:
            port_id = meta_get(inst, "vanguard_port_id")
            if not port_id:
                port_id = vanguard_find_port_id(
                    http.post_json(VANGUARD_GQL, {"query": _VG_PRODUCTS}, headers=_VG_HEADERS, timeout=40), isin)
                if not port_id:
                    raise HoldingsUnavailable("Vanguard: portId zur ISIN nicht gefunden "
                                              "(meta.market.vanguard_port_id manuell setzen)")
            payload = http.post_json(
                VANGUARD_GQL, {"query": _VG_HOLDINGS, "variables": {"portIds": [str(port_id)], "lastItemKey": None}},
                headers=_VG_HEADERS, timeout=60)
            result = vanguard_parse_holdings(payload)
        except http.RemoteError as exc:
            raise HoldingsUnavailable(f"Vanguard: {exc}") from exc
        meta_set(inst, "vanguard_port_id", str(port_id))
        return result


# ---------------------------------------------------------------- Amundi / Lyxor (product API of amundietf.de)
AMUNDI_API = "https://www.amundietf.de/mapi/ProductAPI/getProductsData"


def amundi_payload(isin: str) -> dict:
    return {
        "context": {"countryCode": "DEU", "countryName": "Germany", "languageCode": "de", "languageName": "German",
                    "bcp47Code": "de-DE", "userProfileName": "RETAIL"},
        "productIds": [isin], "productType": "PRODUCT",
        "composition": {"compositionFields": ["date", "type", "bbg", "isin", "name", "weight", "quantity",
                                              "currency", "sector", "country", "countryOfRisk"]},
    }


def amundi_parse(payload: Any) -> HoldingsResult:
    try:
        comp = payload["products"][0]["composition"]
        data = comp.get("compositionData") or []
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise HoldingsUnavailable(f"Amundi-Antwort unerwartet: {exc}") from exc
    items = []
    for entry in data:
        chars = dict((entry or {}).get("compositionCharacteristics") or {})
        if "weight" not in chars and "weight" in (entry or {}):
            chars["weight"] = entry["weight"]
        if chars:
            items.append(chars)
    if not items:
        raise HoldingsUnavailable("Amundi: keine Positionen")
    rows, _ = parse_holdings_json(items)
    return HoldingsResult(rows=rows, as_of=parse_date_loose(items[0].get("date")), source="amundi")


class AmundiProvider(EtfHoldingsProvider):
    issuer = "amundi"
    status = "best_effort"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        isin = _need_isin(inst)
        try:
            return amundi_parse(http.post_json(AMUNDI_API, amundi_payload(isin),
                                               headers={"Content-Type": "application/json"}, timeout=60))
        except http.RemoteError as exc:
            raise HoldingsUnavailable(f"Amundi: {exc}") from exc


# ---------------------------------------------------------------- SPDR (State Street) daily holdings XLSX
SPDR_URL = "https://www.ssga.com/library-content/products/fund-data/etfs/emea/holdings-daily-emea-en-{ticker}-{exch}.xlsx"
SPDR_EXCHANGES = ("gy", "ln", "na", "im", "fp", "sw")


def spdr_parse(content: bytes, expected_isin: str | None = None) -> HoldingsResult:
    rows_raw = read_rows(content, "holdings.xlsx")
    if expected_isin:
        head = " ".join(str(c) for row in rows_raw[:12] for c in row if c)
        if "ISIN" in head.upper() and expected_isin not in head:
            raise HoldingsUnavailable("SPDR: Datei gehört zu einem anderen Fonds")
    rows, as_of = parse_holdings_file(content, "holdings.xlsx")
    return HoldingsResult(rows=rows, as_of=as_of, source="spdr")


class SpdrProvider(EtfHoldingsProvider):
    issuer = "spdr"
    status = "best_effort"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        isin = _need_isin(inst)
        ticker = normalize_symbol(inst.symbol).lower()
        if not ticker:
            raise HoldingsUnavailable("SPDR: Börsenkürzel (Symbol) fehlt")
        errors: list[str] = []
        for exch in SPDR_EXCHANGES:
            try:
                return spdr_parse(http.get_bytes(SPDR_URL.format(ticker=ticker, exch=exch), timeout=40, retries=0), isin)
            except (http.RemoteError, HoldingsUnavailable) as exc:
                errors.append(f"{exch}: {exc}")
        raise HoldingsUnavailable("SPDR: " + " | ".join(errors)[:500])


# ---------------------------------------------------------------- Invesco (JSON API behind etf.invesco.com)
INVESCO_URL = "https://dng-api.invesco.com/cache/v1/accounts/de_DE/shareclasses/{isin}/holdings/index?idType=isin"


def invesco_parse(payload: Any) -> HoldingsResult:
    rows, _ = parse_holdings_json(payload)
    as_of = parse_date_loose(payload.get("effectiveDate")) if isinstance(payload, dict) else None
    return HoldingsResult(rows=rows, as_of=as_of, source="invesco")


class InvescoProvider(EtfHoldingsProvider):
    issuer = "invesco"
    status = "best_effort"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        isin = _need_isin(inst)
        try:
            return invesco_parse(http.get_json(INVESCO_URL.format(isin=isin), timeout=40))
        except http.RemoteError as exc:
            raise HoldingsUnavailable(f"Invesco: {exc}") from exc
