from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.models.core import CategoryKind
from app.models.market import EventType, InstrumentEvent
from app.services import analytics, ledger
from test_core_helpers import add_fx, add_quote, make_accounts, make_category, make_instrument, tx

TODAY = date.today()


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def _event(db, inst, type_, when, amount, currency="EUR", estimate=False):
    db.add(InstrumentEvent(instrument_id=inst.id, type=type_, date=when, amount=D(str(amount)) if amount else None,
                           currency=currency, is_estimate=estimate, source="test"))
    db.flush()


def test_dividend_forecast_from_events_and_extrapolation(db):
    acc = make_accounts(db)
    sap = make_instrument(db)
    msft = make_instrument(db, isin="US5949181045", symbol="MSFT", name="Microsoft", currency="USD")
    alv = make_instrument(db, isin="DE0008404005", symbol="ALV.DE", name="Allianz")
    for inst, qty, price in ((sap, "10", "100"), (msft, "4", "250"), (alv, "5", "200")):
        tx(db, acc["depot"], "buy", days_ago(400), instrument_id=inst.id, quantity=D(qty), price=D(price))
        add_quote(db, inst, price)
    add_fx(db, "USD", 1.25, days_ago(1))
    # SAP: ex + payment pair (one item), plus an estimated pair later
    _event(db, sap, EventType.ex_dividend, TODAY + timedelta(days=20), 2.5)
    _event(db, sap, EventType.dividend_payment, TODAY + timedelta(days=24), 2.5)
    _event(db, sap, EventType.ex_dividend, TODAY + timedelta(days=200), 2.6, estimate=True)
    # MSFT: only an ex-date, in USD; one event beyond the 12 month horizon is ignored
    _event(db, msft, EventType.ex_dividend, TODAY + timedelta(days=40), 1.0, "USD")
    _event(db, msft, EventType.ex_dividend, TODAY + timedelta(days=500), 1.0, "USD")
    _event(db, msft, EventType.earnings, TODAY + timedelta(days=10), None)
    # Allianz: no events -> last 12 months of received dividends are extrapolated to the current quantity
    tx(db, acc["depot"], "dividend", days_ago(100), instrument_id=alv.id, quantity=D("5"), price=D("15.40"),
       taxes=D("20.31"))
    tx(db, acc["depot"], "buy", days_ago(50), instrument_id=alv.id, quantity=D("5"), price=D("210"))
    tx(db, acc["depot"], "dividend", days_ago(500), instrument_id=alv.id, quantity=D("5"), price=D("13.80"))  # too old

    result = analytics.dividends(db)
    items = [i for month in result["forecast_12m"] for i in month["items"]]
    by_inst = {}
    for i in items:
        by_inst.setdefault(i["instrument_id"], []).append(i)
    sap_items = sorted(by_inst[sap.id], key=lambda i: i["ex_date"])
    assert [(i["amount"], i["is_estimate"]) for i in sap_items] == [(25.0, False), (26.0, True)]
    assert sap_items[0]["pay_date"] == (TODAY + timedelta(days=24)).isoformat()
    assert sap_items[1]["pay_date"] is None
    (msft_item,) = by_inst[msft.id]
    assert msft_item["amount"] == 3.2 and msft_item["currency"] == "USD"      # 4 * 1.0 USD / 1.25
    (alv_item,) = by_inst[alv.id]
    assert alv_item["amount"] == 154.0 and alv_item["source"] == "extrapolated"   # 15.40 * 10 shares now
    total = 25.0 + 26.0 + 3.2 + 154.0
    assert result["forecast_12m_total"] == pytest.approx(total)
    assert sum(m["amount"] for m in result["forecast_12m"]) == pytest.approx(total)
    assert 12 <= len(result["forecast_12m"]) <= 13
    assert result["forecast_12m"][0]["month"] == f"{TODAY.year:04d}-{TODAY.month:02d}"
    cost = 1000 + 1000 + 1000 + 1050
    assert result["yield_on_cost"] == pytest.approx(total / cost * 100, abs=1e-3)
    value = 10 * 100 + 4 * 250 / 1.25 + 10 * 200
    assert result["portfolio_yield"] == pytest.approx(total / value * 100, abs=1e-3)
    # received
    years = {y["year"]: y for y in result["received_by_year"]}
    assert sum(y["amount"] for y in years.values()) == pytest.approx(56.69 + 69.0)
    assert len(result["received_by_month"]) == 12
    old_year = days_ago(500).year
    months = analytics.dividends(db, old_year)["received_by_month"]
    assert sum(m["amount"] for m in months) >= 69.0


def test_cashflow_excludes_transfers_and_trades(db):
    acc = make_accounts(db)
    invest = make_category(db, "Sparplan", CategoryKind.investment)
    food = make_category(db, "Lebensmittel", rules=["REWE"])
    rent = make_category(db, "Miete", rules=["Miete"])
    make_category(db, "Gehalt", CategoryKind.income, rules=["Gehalt"])
    day = TODAY.replace(day=1)
    tx(db, acc["giro"], "deposit", day, amount=D("3000"), description="Gehalt")
    tx(db, acc["giro"], "withdrawal", day, amount=D("-1000"), description="Miete")
    tx(db, acc["giro"], "withdrawal", day, amount=D("-200"), counterparty="REWE")
    tx(db, acc["giro"], "withdrawal", day, amount=D("-50"), counterparty="Kiosk")                 # uncategorised
    tx(db, acc["giro"], "deposit", day, amount=D("20"), counterparty="REWE", description="Pfand")  # refund
    tx(db, acc["giro"], "withdrawal", day, amount=D("-500"), counterparty="Broker", category_id=invest.id)
    ledger.create_transfer(db, {"from_account_id": acc["giro"].id, "to_account_id": acc["cash"].id,
                                "amount": D("700"), "date": day})
    tx(db, acc["depot"], "buy", day, isin="DE0007164600", quantity=D("2"), price=D("100"))
    tx(db, acc["cash"], "interest", day, amount=D("5"))
    rows = analytics.cashflow(db, 3)
    assert [r["month"] for r in rows][-1] == f"{TODAY.year:04d}-{TODAY.month:02d}" and len(rows) == 3
    last = rows[-1]
    assert last["income"] == 3025.0          # salary + refund + interest
    assert last["expenses"] == 1250.0
    assert last["savings"] == 1775.0
    assert last["savings_rate"] == pytest.approx(58.68, abs=0.01)
    assert rows[0] == {"month": rows[0]["month"], "income": 0.0, "expenses": 0.0, "savings": 0.0, "savings_rate": 0.0}

    cats = analytics.expenses_by_category(db)
    by_name = {c["name"]: c for c in cats}
    assert by_name["Miete"]["amount"] == 1000.0 and by_name["Miete"]["category_id"] == rent.id
    assert by_name["Lebensmittel"]["amount"] == 180.0 and by_name["Lebensmittel"]["category_id"] == food.id
    assert by_name["Ohne Kategorie"]["amount"] == 50.0 and by_name["Ohne Kategorie"]["category_id"] is None
    assert set(by_name) == {"Miete", "Lebensmittel", "Ohne Kategorie"}
    assert sum(c["share"] for c in cats) == pytest.approx(100.0, abs=0.05)
    assert analytics.expenses_by_category(db, date_to=day - timedelta(days=1)) == []


def test_wealth_forecast_monte_carlo(db):
    acc = make_accounts(db)
    result = analytics.wealth_forecast(db, years=10, monthly_savings=500, expected_return=6, start_value=10000)
    series = result["series"]
    assert [p["year"] for p in series] == list(range(11))
    assert series[0] == {"year": 0, "p10": 10000.0, "p50": 10000.0, "p90": 10000.0, "contributions": 10000.0}
    last = series[-1]
    assert last["contributions"] == 70000.0
    assert last["p10"] < last["p50"] < last["p90"]
    # median close to the deterministic 6 % path (~ 99.3k), percent and fraction input are equivalent
    assert 85_000 < last["p50"] < 112_000
    assert analytics.wealth_forecast(db, 10, 500, 0.06, start_value=10000)["series"] == series
    flat = analytics.wealth_forecast(db, 2, 100, 0.0, volatility=0.0, start_value=1000)["series"]
    assert flat[-1]["p10"] == flat[-1]["p90"] == 3400.0
    # defaults: start = net worth, savings = average of the last 12 months
    prev = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=15)
    tx(db, acc["giro"], "deposit", prev, amount=D("3000"))
    tx(db, acc["giro"], "withdrawal", prev, amount=D("-2000"))
    auto = analytics.wealth_forecast(db, years=1)
    assert auto["start_value"] == 2000.0 and auto["monthly_savings"] == 1000.0
    assert auto["expected_return"] == 6.0 and auto["volatility"] == 15.0


def test_realized_gains_per_year(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    tx(db, acc["depot"], "buy", date(2023, 3, 1), instrument_id=inst.id, quantity=D("10"), price=D("100"))
    tx(db, acc["depot"], "sell", date(2024, 5, 2), instrument_id=inst.id, quantity=D("4"), price=D("150"), taxes=D("52.75"))
    tx(db, acc["depot"], "sell", date(2025, 6, 3), instrument_id=inst.id, quantity=D("6"), price=D("90"), fees=D("5"))
    rows = analytics.realized_gains(db)
    assert [r["date"] for r in rows] == ["2025-06-03", "2024-05-02"]
    (g24,) = analytics.realized_gains(db, 2024)
    assert g24 == {**g24, "instrument": "SAP SE", "quantity": 4.0, "proceeds": 600.0, "cost": 400.0, "gain": 200.0,
                   "taxes": 52.75}
    (g25,) = analytics.realized_gains(db, 2025)
    assert (g25["proceeds"], g25["cost"], g25["gain"]) == (535.0, 600.0, -65.0)
    assert analytics.realized_gains(db, 2020) == []
