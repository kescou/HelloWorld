"""ETF holdings persistence + look-through merge (direct stock also inside an ETF)."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.market.etf import registry
from app.market.etf import service as etf
from app.market.etf.base import EtfHoldingsProvider, HoldingRow, HoldingsResult, HoldingsUnavailable
from app.market.etf.ishares import parse_csv
from app.market.etf.xtrackers import parse_export
from app.market.lookthrough import compute_lookthrough
from app.market.providers.base import FundHolding
from app.market.service import meta_get, meta_set
from app.models.core import InstrumentType, TxType
from app.models.market import EtfHolding, Quote
from fixtures.market.fakes import (  # noqa: F401
    fixture_bytes,
    make_account,
    make_fx,
    make_instrument,
    make_tx,
    no_network,
    provider,
)


def quote(db, inst, price, currency="EUR"):
    db.add(Quote(instrument_id=inst.id, price=Decimal(str(price)), currency=currency))
    db.flush()


def world_etf(db, name="iShares Core MSCI World UCITS ETF USD (Acc)", isin="IE00B4L5Y983", symbol="EUNL.DE"):
    return make_instrument(db, name, isin=isin, symbol=symbol, type_=InstrumentType.etf)


# ---------------------------------------------------------------- persistence
def test_store_holdings_replaces_all(db):
    inst = world_etf(db)
    n = etf.store_holdings(db, inst.id, parse_csv(fixture_bytes("ishares_EUNL_holdings_de.csv")))
    assert n == 9
    out = etf.get_holdings(db, inst)
    assert out["source"] == "ishares" and out["as_of"] == "2026-10-30" and out["count"] == 9
    assert out["coverage_pct"] == pytest.approx(22.28) and out["residual_pct"] == pytest.approx(77.72)
    assert out["residual_label"] == "Sonstige/Unbekannt" and out["issuer"] == "ishares"
    assert out["holdings"][0] == {"name": "NVIDIA CORP", "isin": None, "symbol": "NVDA", "weight": 5.62,
                                  "sector": "Information Technology", "country": "United States", "asset_class": "equity"}
    etf.store_holdings(db, inst.id, HoldingsResult([HoldingRow("Only One", 100.0, isin="US5949181045")], date(2026, 1, 1), "manual"))
    assert db.scalar(select(func.count()).select_from(EtfHolding).where(EtfHolding.etf_instrument_id == inst.id)) == 1
    assert etf.get_holdings(db, inst)["coverage_pct"] == 100.0
    assert etf.get_holdings(db, make_instrument(db, "Leer", type_=InstrumentType.etf))["holdings"] == []


class _StubIssuer(EtfHoldingsProvider):
    issuer = "ishares"
    status = "implemented"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def fetch(self, inst):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture()
def issuer_registry():
    saved = dict(registry.providers())
    yield registry
    registry.providers().clear()
    registry.providers().update(saved)


def test_refresh_chain_issuer_then_yahoo_fallback(db, provider, issuer_registry):
    inst = world_etf(db)
    provider.top_holdings["EUNL.DE"] = [FundHolding("NVIDIA Corp", 5.6, "NVDA"), FundHolding("Apple Inc", 4.9, "AAPL")]

    # 1) issuer download fails (blocked network) -> Yahoo top-10
    res = etf.refresh_etf_holdings(db, inst, provider)
    assert res["status"] == "ok" and res["source"] == "yahoo_top10" and res["coverage_pct"] == pytest.approx(10.5)
    assert [a["provider"] for a in res["attempts"]] == ["ishares", "yahoo_top10"]
    assert res["attempts"][0]["ok"] is False and meta_get(inst, "etf_attempts")

    # 2) issuer works -> full list replaces top-10
    stub = _StubIssuer(parse_csv(fixture_bytes("ishares_EUNL_holdings_de.csv")))
    issuer_registry.register(stub)
    res = etf.refresh_etf_holdings(db, inst, provider)
    assert res["source"] == "ishares" and res["rows"] == 9

    # 3) issuer breaks later -> full data is NOT downgraded to top-10
    issuer_registry.register(_StubIssuer(error=HoldingsUnavailable("HTTP 503")))
    res = etf.refresh_etf_holdings(db, inst, provider)
    assert res["status"] == "unavailable" and etf.current_source(db, inst.id) == "ishares"

    # 4) a crashing provider (parser bug) is contained as well
    issuer_registry.register(_StubIssuer(error=KeyError("unexpected")))
    assert etf.refresh_etf_holdings(db, inst, provider)["status"] == "unavailable"

    # 5) manual upload wins and is kept unless forced
    up = etf.upload_holdings(db, inst, fixture_bytes("manual_holdings_de.csv"), "x.csv")
    assert up["source"] == "manual" and up["rows"] == 4 and up["coverage_pct"] == 35.0 and up["as_of"] == "2026-09-30"
    issuer_registry.register(stub)
    assert etf.refresh_etf_holdings(db, inst, provider)["status"] == "kept"
    assert etf.refresh_etf_holdings(db, inst, provider, force=True)["source"] == "ishares"


def test_custom_url_and_issuer_detection(db, provider, monkeypatch):
    assert registry.detect_issuer("Xtrackers MSCI World UCITS ETF 1C") == "xtrackers"
    assert registry.detect_issuer("Lyxor Core STOXX Europe 600") == "amundi"
    assert registry.detect_issuer("BlackRock Asset Management Ireland") == "ishares"
    assert registry.detect_issuer("Vanguard FTSE All-World") == "vanguard"
    assert registry.detect_issuer("SPDR S&P 500") == "spdr" and registry.detect_issuer("HSBC MSCI World") is None
    assert set(registry.providers()) == {"ishares", "xtrackers", "vanguard", "amundi", "spdr", "invesco"}
    assert {k for k, p in registry.providers().items() if p.status == "implemented"} == {"ishares", "xtrackers"}

    inst = make_instrument(db, "HSBC MSCI World", isin="IE00B4X9L533", symbol="H4ZJ.DE", type_=InstrumentType.etf)
    meta_set(inst, "holdings_url", "https://example.org/holdings.xlsx")
    from app.market import http

    monkeypatch.setattr(http, "get_bytes", lambda url, **kw: fixture_bytes("xtrackers_constituent.xlsx"))
    res = etf.refresh_etf_holdings(db, inst, provider)
    assert res["source"] == "custom_url" and res["rows"] == 7 and res["as_of"] == "2026-10-30"


def test_all_issuer_providers_fail_cleanly_offline(db, provider):
    for n, (name, prov) in enumerate(registry.providers().items()):
        inst = make_instrument(db, f"{name} ETF", isin=f"IE00B4L5Y{n:03d}", symbol="XXXX.DE",
                               type_=InstrumentType.etf)
        with pytest.raises(HoldingsUnavailable):
            prov.fetch(inst)


# ---------------------------------------------------------------- look-through
def _portfolio(db):
    acc = make_account(db)
    msft = make_instrument(db, "Microsoft Corp. Registered Shares DL-,00000625", isin="US5949181045", symbol="MSF.DE",
                           sector="Technology", country="United States")
    aapl = make_instrument(db, "Apple Inc.", isin="US0378331005", symbol="AAPL", currency="USD",
                           sector="Technology", country="United States")
    world = world_etf(db)
    make_fx(db, "USD", 1.25)
    make_tx(db, acc, msft, TxType.buy, 10, 300)
    make_tx(db, acc, aapl, TxType.buy, 5, 150, currency="USD")
    make_tx(db, acc, world, TxType.buy, 120, 80)
    make_tx(db, acc, world, TxType.sell, 20, 90, on=date.today() - timedelta(days=10))
    quote(db, msft, 400)             # 10 × 400            = 4 000 EUR
    quote(db, aapl, 200, "USD")      # 5 × 200 / 1.25      =   800 EUR
    quote(db, world, 100)            # 100 × 100           = 10 000 EUR
    return acc, msft, aapl, world


def test_lookthrough_merges_direct_and_etf_by_name(db):
    acc, msft, aapl, world = _portfolio(db)
    etf.store_holdings(db, world.id, parse_csv(fixture_bytes("ishares_EUNL_holdings_de.csv")))   # no ISINs in file
    lt = compute_lookthrough(db)
    assert lt["total_value"] == 14800.0
    items = {i["name"]: i for i in lt["items"]}

    m = items[msft.name]
    assert m["key"] == "US5949181045" and m["isin"] == "US5949181045"
    assert (m["direct_value"], m["etf_value"], m["total_value"]) == (4000.0, 440.0, 4440.0)
    assert m["weight"] == pytest.approx(30.0)
    assert m["sources"] == [
        {"instrument_id": msft.id, "name": msft.name, "value": 4000.0, "weight_in_etf": None, "kind": "direct"},
        {"instrument_id": world.id, "name": world.name, "value": 440.0, "weight_in_etf": 4.4, "kind": "etf"}]
    a = items["Apple Inc."]
    assert (a["direct_value"], a["etf_value"]) == (800.0, 492.0)

    alphabet = [i for i in lt["items"] if "ALPHABET" in i["name"]]
    assert len(alphabet) == 1 and alphabet[0]["etf_value"] == 387.0            # class A + C = one company
    assert alphabet[0]["sources"][0]["weight_in_etf"] == pytest.approx(3.87)
    nvda = items["NVIDIA CORP"]
    assert nvda["direct_value"] == 0 and nvda["etf_value"] == 562.0 and nvda["symbol"] == "NVDA"

    other, cash = items["Sonstige/Unbekannt"], items["Cash/Derivate"]
    assert other["key"] == "_other" and other["etf_value"] == pytest.approx(7772.0) and other["kind"] == "other"
    assert cash["etf_value"] == 5.0
    assert sum(i["total_value"] for i in lt["items"]) == pytest.approx(14800.0, abs=0.05)
    assert sum(i["weight"] for i in lt["items"]) == pytest.approx(100.0, abs=0.01)
    assert lt["items"][0]["name"] == "Sonstige/Unbekannt"                       # sorted by value
    # resolved: 4000 + 800 + (22.28 − 0.05) % of 10 000
    assert lt["coverage_pct"] == pytest.approx((4800 + 2223) / 14800 * 100, abs=0.01)

    sectors = {s["key"]: s for s in lt["by_sector"]}
    assert sectors["Information Technology"]["value"] == pytest.approx(4000 + 800 + 562 + 492 + 440 + 38)
    assert sectors["Information Technology"]["label"] == "Informationstechnologie"
    assert sectors["Communication Services"]["value"] == 387.0
    assert sectors["Unbekannt"]["value"] == pytest.approx(7772.0)
    assert sum(s["weight"] for s in lt["by_sector"]) == pytest.approx(100.0, abs=0.01)
    countries = {c["key"]: c["value"] for c in lt["by_country"]}
    assert countries["Germany"] == 38.0 and countries["Switzerland"] == 30.0
    assert countries["United States"] == pytest.approx(4800 + 562 + 492 + 440 + 274 + 387)
    assert lt["etfs"] == [{"instrument_id": world.id, "name": world.name, "value": 10000.0, "coverage_pct": 22.28,
                           "source": "ishares", "as_of": "2026-10-30"}]
    assert {"total_value", "coverage_pct", "items", "by_sector", "by_country"} <= set(lt)
    assert {"key", "name", "isin", "symbol", "direct_value", "etf_value", "total_value", "weight", "sources"} <= set(lt["items"][0])


def test_lookthrough_matches_by_isin_and_filters_account(db):
    acc, msft, aapl, world = _portfolio(db)
    msft.name = "MSFT Namensaktie"                                 # name useless -> ISIN must match
    other_acc = make_account(db, "Zweitdepot")
    sap = make_instrument(db, "SAP SE", isin="DE0007164600", symbol="SAP.DE", sector="Technology", country="Germany")
    make_tx(db, other_acc, sap, TxType.buy, 10, 100)
    quote(db, sap, 200)
    etf.store_holdings(db, world.id, parse_export(fixture_bytes("xtrackers_constituent.xlsx")))
    lt = compute_lookthrough(db)
    items = {i["key"]: i for i in lt["items"]}
    assert items["US5949181045"]["name"] == "MSFT Namensaktie"
    assert items["US5949181045"]["direct_value"] == 4000.0 and items["US5949181045"]["etf_value"] == pytest.approx(441.0)
    assert items["DE0007164600"]["direct_value"] == 2000.0 and items["DE0007164600"]["etf_value"] == pytest.approx(38.0)
    alphabet = [i for i in lt["items"] if "ALPHABET" in i["name"].upper()]
    assert len(alphabet) == 1 and alphabet[0]["etf_value"] == pytest.approx(388.0)
    assert lt["total_value"] == 16800.0

    only_first = compute_lookthrough(db, account_id=acc.id)
    assert only_first["total_value"] == 14800.0
    assert {i["key"]: i for i in only_first["items"]}["DE0007164600"]["direct_value"] == 0
    only_second = compute_lookthrough(db, account_id=other_acc.id)
    assert only_second["total_value"] == 2000.0 and only_second["coverage_pct"] == 100.0


def test_lookthrough_etf_without_holdings_unpriced_and_empty(db):
    assert compute_lookthrough(db) == {"total_value": 0.0, "coverage_pct": 0.0, "items": [], "by_sector": [],
                                       "by_country": [], "etfs": [], "unpriced": []}
    acc = make_account(db)
    world = world_etf(db)
    ghost = make_instrument(db, "Ohne Kurs")
    gold = make_instrument(db, "Xetra-Gold", isin="DE000A0S9GB0", symbol="4GLD.DE", type_=InstrumentType.commodity)
    make_tx(db, acc, world, TxType.buy, 10, 100)
    make_tx(db, acc, gold, TxType.buy, 10, 50)
    db.add_all([])
    tx = make_tx(db, acc, ghost, TxType.delivery_in, 3, 0)
    assert tx.price is None
    quote(db, world, 100)
    lt = compute_lookthrough(db)                                    # gold priced via last transaction price
    assert lt["total_value"] == 1500.0 and lt["coverage_pct"] == pytest.approx(500 / 1500 * 100, abs=0.01)
    kinds = {i["kind"]: i for i in lt["items"]}
    assert kinds["fund"]["name"].endswith("(keine Durchschau)") and kinds["fund"]["etf_value"] == 1000.0
    assert kinds["asset"]["direct_value"] == 500.0
    assert lt["unpriced"] == [{"instrument_id": ghost.id, "name": "Ohne Kurs", "quantity": 3.0}]
    assert lt["etfs"][0]["coverage_pct"] == 0.0
