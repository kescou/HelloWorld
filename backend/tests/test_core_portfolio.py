import time
from datetime import date, timedelta
from decimal import Decimal as D

import pandas as pd
import pytest
from sqlalchemy import text

from app.models.core import InstrumentType
from app.services import ledger, portfolio
from app.services.performance import twr_series, xirr
from test_core_helpers import add_fx, add_price, add_quote, make_accounts, make_instrument, tx

TODAY = date.today()


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


# --------------------------------------------------------------------------- FIFO
def test_fifo_partial_sells_and_realized_gains(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    tx(db, acc["depot"], "buy", days_ago(300), instrument_id=inst.id, quantity=D("10"), price=D("100"), fees=D("10"))
    tx(db, acc["depot"], "buy", days_ago(200), instrument_id=inst.id, quantity=D("10"), price=D("120"))
    # sell 15: 10 from lot 1 (cost 1010) + 5 from lot 2 (cost 600)
    tx(db, acc["depot"], "sell", days_ago(100), instrument_id=inst.id, quantity=D("15"), price=D("150"), fees=D("5"),
       taxes=D("50"))
    fifo = portfolio.compute_fifo(db)
    pos = fifo.positions[(acc["depot"].id, inst.id)]
    assert pos.quantity == D("5")
    assert pos.cost_basis == D("600")
    (gain,) = fifo.realized
    assert gain.quantity == D("15")
    assert gain.proceeds == D("2245.00")        # 15*150 - 5 fees (before taxes)
    assert gain.cost == D("1610")
    assert gain.gain == D("635.00")
    assert gain.taxes == D("50.00")
    # second partial sell consumes the rest of lot 2
    tx(db, acc["depot"], "sell", days_ago(50), instrument_id=inst.id, quantity=D("2"), price=D("100"))
    fifo = portfolio.compute_fifo(db)
    assert fifo.positions[(acc["depot"].id, inst.id)].quantity == D("3")
    assert fifo.realized[-1].gain == D("-40.00")
    assert fifo.positions[(acc["depot"].id, inst.id)].realized_gain == D("595.00")


def test_fifo_split_and_deliveries(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    tx(db, acc["depot"], "buy", days_ago(300), instrument_id=inst.id, quantity=D("10"), price=D("400"))
    tx(db, acc["depot"], "split", days_ago(200), instrument_id=inst.id, quantity=D("4"))
    fifo = portfolio.compute_fifo(db)
    pos = fifo.positions[(acc["depot"].id, inst.id)]
    assert pos.quantity == D("40") and pos.cost_basis == D("4000")
    assert pos.lots[0].unit_cost == D("100")
    tx(db, acc["depot"], "sell", days_ago(150), instrument_id=inst.id, quantity=D("10"), price=D("120"))
    tx(db, acc["depot"], "delivery_in", days_ago(140), instrument_id=inst.id, quantity=D("5"), price=D("110"))
    tx(db, acc["depot"], "delivery_out", days_ago(130), instrument_id=inst.id, quantity=D("30"))
    fifo = portfolio.compute_fifo(db)
    pos = fifo.positions[(acc["depot"].id, inst.id)]
    assert pos.quantity == D("5")
    assert pos.cost_basis == D("550")            # only the delivered-in lot is left
    assert [g.gain for g in fifo.realized] == [D("200.00")]   # delivery_out realises nothing
    assert not fifo.warnings
    tx(db, acc["depot"], "sell", days_ago(10), instrument_id=inst.id, quantity=D("9"), price=D("100"))
    assert portfolio.compute_fifo(db).warnings     # oversold is reported, not fatal


def test_split_adjusted_history_has_no_jump(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    tx(db, acc["cash"], "deposit", days_ago(40), amount=D("4000"))
    tx(db, acc["depot"], "buy", days_ago(40), instrument_id=inst.id, quantity=D("10"), price=D("400"))
    tx(db, acc["depot"], "split", days_ago(20), instrument_id=inst.id, quantity=D("4"))
    # split-adjusted closes (as delivered by Yahoo)
    add_price(db, inst, days_ago(40), 100)
    add_price(db, inst, days_ago(20), 100)
    add_price(db, inst, days_ago(1), 100)
    hist = portfolio.compute_history(db, "3M")
    invested = {h["invested"] for h in hist if h["date"] >= days_ago(40).isoformat()}
    assert invested == {4000.0}


# --------------------------------------------------------------------------- valuation
def test_valuation_price_sources_fx_and_dividends(db):
    acc = make_accounts(db)
    sap = make_instrument(db)
    msft = make_instrument(db, isin="US5949181045", symbol="MSFT", name="Microsoft", currency="USD")
    etf = make_instrument(db, isin="IE00B4L5Y983", symbol="EUNL.DE", name="MSCI World", type_=InstrumentType.etf)
    tx(db, acc["depot"], "buy", days_ago(100), instrument_id=sap.id, quantity=D("10"), price=D("100"))
    tx(db, acc["depot"], "buy", days_ago(100), instrument_id=msft.id, quantity=D("2"), amount=D("-700"))
    tx(db, acc["depot"], "buy", days_ago(100), instrument_id=etf.id, quantity=D("5"), price=D("80"))
    tx(db, acc["depot"], "dividend", days_ago(50), instrument_id=sap.id, quantity=D("10"), price=D("2"), taxes=D("5"))
    add_quote(db, sap, 120, previous_close=118)                 # quote
    add_price(db, msft, days_ago(3), 440)                       # history fallback (USD)
    add_price(db, msft, days_ago(2), 450)
    val = portfolio.compute_valuation(db)                        # etf: last transaction price
    by_id = {h["instrument_id"]: h for h in val.holdings}
    assert by_id[sap.id]["market_value"] == 1200.0 and by_id[sap.id]["price_source"] == "quote"
    assert by_id[sap.id]["day_change"] == 20.0
    assert by_id[sap.id]["dividends_received"] == 15.0
    assert by_id[sap.id]["unrealized_gain_pct"] == 20.0
    assert by_id[etf.id]["market_value"] == 400.0 and by_id[etf.id]["price_source"] == "transaction"
    # no fx rate yet: fallback 1.0 + warning flag
    assert val.fx_warning and by_id[msft.id]["market_value"] == 900.0
    add_fx(db, "USD", 1.25, days_ago(2))
    val = portfolio.compute_valuation(db)
    by_id = {h["instrument_id"]: h for h in val.holdings}
    assert not val.fx_warning
    assert by_id[msft.id]["market_value"] == 720.0               # 2 * 450 / 1.25
    assert by_id[msft.id]["price_currency"] == "USD" and by_id[msft.id]["price_source"] == "history"
    assert by_id[msft.id]["day_change"] == 16.0
    assert round(sum(h["weight"] for h in val.holdings), 2) == 100.0
    assert by_id[sap.id]["accounts"] == [
        {"account_id": acc["depot"].id, "quantity": 10.0, "cost_basis": 1000.0, "market_value": 1200.0}]
    values = portfolio.account_values(db, val)
    assert values[acc["depot"].id]["market_value"] == 2320.0
    assert values[acc["cash"].id]["balance"] == -2085.0          # buys minus net dividend
    summary = portfolio.compute_summary(db)
    assert summary["invested_value"] == 2320.0
    assert summary["net_worth"] == 2320.0 - 2085.0 + 1000.0
    assert summary["dividends_ytd"] in (15.0, 0.0)               # 0 only in the first weeks of a year


# --------------------------------------------------------------------------- XIRR / TWR
def test_xirr_known_value():
    flows = [
        (date(2008, 1, 1), -10000), (date(2008, 3, 1), 2750), (date(2008, 10, 30), 4250),
        (date(2009, 2, 15), 3250), (date(2009, 4, 1), 2750),
    ]
    assert xirr(flows) == pytest.approx(0.373362535, abs=1e-7)   # Excel XIRR reference
    assert xirr([(date(2020, 1, 1), -1000), (date(2021, 1, 1), 1100)]) == pytest.approx(0.0997, abs=1e-3)
    assert xirr([(date(2020, 1, 1), -1000), (date(2021, 1, 1), 500)]) == pytest.approx(-0.4993, abs=1e-3)
    assert xirr([(date(2020, 1, 1), -1000), (date(2020, 1, 11), 2000)]) is not None   # extreme: bisection range
    assert xirr([(date(2020, 1, 1), 1000), (date(2021, 1, 1), 500)]) is None           # no sign change
    assert xirr([]) is None


def test_twr_series_ignores_external_flows():
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    values = pd.Series([1000.0, 1100.0, 2100.0, 2310.0], index=idx)
    flows = pd.Series([1000.0, 0.0, 1000.0, 0.0], index=idx)
    factor = twr_series(values, flows, 0.0)
    assert factor.round(6).tolist() == [1.0, 1.1, 1.1, 1.21]


def test_performance_twr_and_xirr_end_to_end(db):
    acc = make_accounts(db)
    acc["giro"].opening_balance = D("0")
    inst = make_instrument(db)
    tx(db, acc["cash"], "deposit", days_ago(30), amount=D("1000"))
    tx(db, acc["depot"], "buy", days_ago(30), instrument_id=inst.id, quantity=D("10"), price=D("100"))
    add_price(db, inst, days_ago(30), 100)
    add_price(db, inst, days_ago(21), 110)
    tx(db, acc["giro"], "deposit", days_ago(20), amount=D("1000"))           # external inflow
    ledger.create_transfer(db, {"from_account_id": acc["giro"].id, "to_account_id": acc["cash"].id,
                                "amount": D("500"), "date": days_ago(15)})   # internal: no effect
    add_price(db, inst, days_ago(10), 121)
    perf = portfolio.compute_performance(db, "MAX")
    # day -21: 1000 -> 1100 (+10 %); day -10: 2100 -> 2210 (+5.238 %)
    expected = (1.10 * (2210 / 2100) - 1) * 100
    assert perf["twr"] == pytest.approx(expected, abs=1e-3)
    assert perf["absolute_gain"] == 210.0
    assert perf["end_value"] == 2210.0 and perf["net_flows"] == 2000.0
    assert perf["series"][0]["twr_index"] == 100.0
    assert perf["series"][-1]["twr_index"] == pytest.approx(100 + expected, abs=1e-3)
    assert perf["series"][-1]["date"] == TODAY.isoformat()
    expected_xirr = xirr([(days_ago(30), -1000), (days_ago(20), -1000), (TODAY, 2210)]) * 100
    assert perf["xirr"] == pytest.approx(expected_xirr, rel=1e-6)
    # depot only: the buy is an external inflow, cash stays outside -> pure price return 21 %
    depot = portfolio.compute_performance(db, "MAX", account_id=acc["depot"].id)
    assert depot["twr"] == pytest.approx(21.0, abs=1e-3)
    assert depot["absolute_gain"] == 210.0
    # sub-range starting after the first price move
    short = portfolio.compute_performance(db, "1W")
    assert short["twr"] == pytest.approx(0.0, abs=1e-9) and short["start_value"] == 2210.0


def test_history_series_and_account_history(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    tx(db, acc["cash"], "deposit", days_ago(10), amount=D("1000"))
    tx(db, acc["depot"], "buy", days_ago(8), instrument_id=inst.id, quantity=D("5"), price=D("100"), fees=D("1"))
    add_price(db, inst, days_ago(8), 100)
    add_price(db, inst, days_ago(4), 104)       # forward filled over the following days
    hist = portfolio.compute_history(db, "1M")
    by_date = {h["date"]: h for h in hist}
    assert set(hist[0]) == {"date", "net_worth", "invested", "cost_basis", "cash"}
    assert hist[-1]["date"] == TODAY.isoformat()
    d9, d8, d3 = (by_date[days_ago(n).isoformat()] for n in (9, 8, 3))
    assert (d9["cash"], d9["invested"], d9["net_worth"]) == (2000.0, 0.0, 2000.0)   # incl. giro opening 1000
    assert (d8["cash"], d8["invested"], d8["cost_basis"]) == (1499.0, 500.0, 501.0)
    assert (d3["invested"], d3["net_worth"]) == (520.0, 2019.0)
    depot_hist = portfolio.account_history(db, acc["depot"].id)
    assert depot_hist[-1] == {"date": TODAY.isoformat(), "value": 520.0}
    cash_hist = portfolio.compute_history(db, "1M", account_id=acc["cash"].id)
    assert cash_hist[-1]["net_worth"] == 499.0 and cash_hist[-1]["invested"] == 0.0
    assert portfolio.compute_history(db, "MAX")[0]["date"] <= days_ago(10).isoformat()


def test_allocation_dimensions_and_lookthrough(db, monkeypatch):
    from app.models.market import EtfHolding

    # force the table based fallback (the market-data module is tested by its owner)
    monkeypatch.setattr(portfolio, "_market_lookthrough", lambda *a, **k: None)

    acc = make_accounts(db)
    sap = make_instrument(db, sector="Technology", country="Germany")
    etf = make_instrument(db, isin="IE00B4L5Y983", symbol="EUNL.DE", name="MSCI World", type_=InstrumentType.etf)
    tx(db, acc["depot"], "buy", days_ago(5), instrument_id=sap.id, quantity=D("10"), price=D("100"))
    tx(db, acc["depot"], "buy", days_ago(5), instrument_id=etf.id, quantity=D("10"), price=D("100"))
    db.add_all([
        EtfHolding(etf_instrument_id=etf.id, holding_name="Apple", weight=D("30"), sector="Technology",
                   country="United States", source="test"),
        EtfHolding(etf_instrument_id=etf.id, holding_name="Nestle", weight=D("20"), sector="Consumer Defensive",
                   country="Switzerland", source="test"),
    ])
    db.flush()
    direct = {a["key"]: a for a in portfolio.compute_allocation(db, "sector")}
    assert direct["Technology"]["value"] == 1000.0 and direct["ETF"]["weight"] == 50.0
    looked = {a["key"]: a["value"] for a in portfolio.compute_allocation(db, "sector", lookthrough=True)}
    assert looked["Technology"] == pytest.approx(1300.0)
    assert looked["Consumer Defensive"] == pytest.approx(200.0)
    assert sum(looked.values()) == pytest.approx(2000.0)
    countries = {a["key"]: a["value"] for a in portfolio.compute_allocation(db, "country", lookthrough=True)}
    assert countries["Germany"] == pytest.approx(1000.0) and countries["United States"] == pytest.approx(300.0)
    by_type = {a["key"]: a["value"] for a in portfolio.compute_allocation(db, "type")}
    assert by_type["stock"] == 1000.0 and by_type["etf"] == 1000.0
    by_account = portfolio.compute_allocation(db, "account")
    assert {a["key"] for a in by_account} == {"Depot", "Giro"}       # negative settlement balance is left out
    assert round(sum(a["weight"] for a in by_account), 2) == 100.0
    assert {a["key"] for a in portfolio.compute_allocation(db, "currency")} == {"EUR"}


def test_allocation_uses_market_lookthrough_when_available(db, monkeypatch):
    import sys
    import types

    acc = make_accounts(db)
    sap = make_instrument(db, sector="Technology", country="Germany")
    tx(db, acc["depot"], "buy", days_ago(5), instrument_id=sap.id, quantity=D("10"), price=D("100"))
    calls = []

    def fake(db_, account_id=None):
        calls.append(account_id)
        return {"by_sector": [{"key": "Technologie", "value": 750.0, "weight": 75.0},
                              {"key": "Finanzen", "value": 250.0, "weight": 25.0}], "by_country": []}

    module = types.ModuleType("app.market.lookthrough")
    module.compute_lookthrough = fake
    monkeypatch.setitem(sys.modules, "app.market.lookthrough", module)
    result = portfolio.compute_allocation(db, "sector", lookthrough=True)
    assert result == [{"key": "Technologie", "value": 750.0, "weight": 75.0},
                      {"key": "Finanzen", "value": 250.0, "weight": 25.0}]
    assert calls == [None]
    # empty market result -> fallback to own computation
    assert portfolio.compute_allocation(db, "country", lookthrough=True)[0]["key"] == "Germany"
    module.compute_lookthrough = lambda *a, **k: 1 / 0          # broken foreign module must not break us
    assert portfolio.compute_allocation(db, "sector", lookthrough=True)[0]["key"] == "Technology"


# --------------------------------------------------------------------------- speed
def test_history_and_performance_are_fast_for_10y_50_instruments(db):
    acc = make_accounts(db)
    db.execute(text(
        "INSERT INTO instruments (isin, symbol, name, type, currency, is_watchlist) "
        "SELECT 'XX' || lpad(g::text, 10, '0'), 'SYM' || g, 'Instrument ' || g, 'stock', "
        "CASE WHEN g % 5 = 0 THEN 'USD' ELSE 'EUR' END, false FROM generate_series(1, 50) g"))
    ids = [r[0] for r in db.execute(text("SELECT id FROM instruments WHERE isin LIKE 'XX%' ORDER BY id"))]
    params = {"ids": ids, "depot": acc["depot"].id, "cash": acc["cash"].id, "giro": acc["giro"].id}
    db.execute(text(
        "INSERT INTO price_history (instrument_id, date, close) "
        "SELECT i, d::date, 50 + (i % 7) * 10 + extract(epoch from d - (current_date - 3650)) / 86400 / 40.0 "
        "FROM unnest(CAST(:ids AS integer[])) i, "
        "generate_series(current_date - 3650, current_date - 1, interval '1 day') d "
        "WHERE extract(isodow from d) < 6"), params)
    db.execute(text(
        "INSERT INTO fx_rates (date, currency, rate) SELECT d::date, 'USD', 1.1 "
        "FROM generate_series(current_date - 3650, current_date, interval '1 day') d"))
    db.execute(text(
        "INSERT INTO transactions (account_id, cash_account_id, instrument_id, type, booking_date, quantity, price, "
        "amount, fees, taxes, currency) "
        "SELECT :depot, :cash, i, 'buy', (current_date - 3640 + m * 30), 1, 100, -100, 0, 0, 'EUR' "
        "FROM unnest(CAST(:ids AS integer[])) i, generate_series(0, 119) m"), params)
    db.execute(text(
        "INSERT INTO transactions (account_id, type, booking_date, amount, fees, taxes, currency) "
        "SELECT :giro, CAST(CASE WHEN n % 20 = 0 THEN 'deposit' ELSE 'withdrawal' END AS tx_type), (current_date - (n % 3650)), "
        "CASE WHEN n % 20 = 0 THEN 3000 ELSE -40 END, 0, 0, 'EUR' FROM generate_series(1, 10000) n"), params)
    db.execute(text(
        "INSERT INTO transactions (account_id, type, booking_date, amount, fees, taxes, currency) "
        "VALUES (:cash, 'deposit', current_date - 3650, 700000, 0, 0, 'EUR')"), params)
    rows = db.execute(text("SELECT count(*) FROM price_history")).scalar()
    assert rows > 120_000
    portfolio.compute_history(db, "1M")            # warm up (imports, plan cache)
    t0 = time.perf_counter()
    hist = portfolio.compute_history(db, "MAX")
    t_hist = time.perf_counter() - t0
    t0 = time.perf_counter()
    perf = portfolio.compute_performance(db, "MAX")
    t_perf = time.perf_counter() - t0
    print(f"\nhistory MAX: {t_hist * 1000:.0f} ms, performance MAX: {t_perf * 1000:.0f} ms, days: {len(hist)}")
    assert len(hist) >= 3650
    assert hist[-1]["invested"] > 0 and perf["twr"] != 0
    assert len(portfolio.compute_valuation(db).holdings) == 50
    # target is < 500 ms on a developer machine; generous bound for slow CI boxes
    assert t_hist < 2.0 and t_perf < 2.0
