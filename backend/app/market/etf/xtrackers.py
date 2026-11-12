"""Xtrackers (DWS): constituent export by ISIN (XLSX).  STATUS: implemented

    https://etf.dws.com/etfdata/export/GBR/ENG/excel/product/constituent/<ISIN>/

Columns (English export): Row, Name, ISIN, Country, Currency, Exchange, Type of Security, Rating,
Primary Listing, Industry Classification, Weighting. Swap-based Xtrackers ETFs list the substitute
basket, not the index – nothing we can do about that for free.
"""
from __future__ import annotations

from app.market import http
from app.market.etf.base import EtfHoldingsProvider, HoldingsResult, HoldingsUnavailable
from app.market.etf.tabular import parse_holdings_file
from app.models.core import Instrument

URLS = [
    "https://etf.dws.com/etfdata/export/GBR/ENG/excel/product/constituent/{isin}/",
    "https://etf.dws.com/etfdata/export/DEU/DEU/excel/product/constituent/{isin}/",
    "https://etf.dws.com/etfdata/export/DEU/DEU/csv/product/constituent/{isin}/",
]


def parse_export(content: bytes) -> HoldingsResult:
    rows, as_of = parse_holdings_file(content, "constituent.xlsx")
    return HoldingsResult(rows=rows, as_of=as_of, source="xtrackers")


class XtrackersProvider(EtfHoldingsProvider):
    issuer = "xtrackers"
    status = "implemented"

    def fetch(self, inst: Instrument) -> HoldingsResult:
        if not inst.isin:
            raise HoldingsUnavailable("ISIN fehlt")
        errors: list[str] = []
        for tpl in URLS:
            try:
                return parse_export(http.get_bytes(tpl.format(isin=inst.isin), timeout=60))
            except (http.RemoteError, HoldingsUnavailable) as exc:
                errors.append(str(exc))
        raise HoldingsUnavailable("Xtrackers-Export fehlgeschlagen: " + " | ".join(errors))
