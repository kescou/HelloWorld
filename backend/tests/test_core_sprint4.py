"""Sprint 4: tax overview, recurring bookings, settings, instrument editing, cent-exact sums, default categories."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.models.core import CategoryKind, InstrumentType, Transaction
from app.models.market import EventType, Fundamentals, InstrumentEvent, PriceHistory, Quote
from app.services import analytics, ledger, portfolio
from test_core_helpers import add_price, add_quote, make_accounts, make_category, make_instrument, tx

TODAY = date.today()
YEAR = TODAY.year
JAN = date(YEAR, 1, 2)


# --------------------------------------------------------------------------- taxes
def _tax_fixture(db):
    acc = make_accounts(db)
    sap = make_instrument(db)
    etf = make_instrument(db, isin="IE00B4L5Y983", symbol="EUNL.DE", name="World ETF", type_=InstrumentType.etf)
    last_year = date(YEAR - 1, 6, 1)
    tx(db, acc["depot"], "buy", last_year, instrument_id=sap.id, quantity=D("10"), price=D("100"))
    tx(db, acc["depot"], "buy", last_year, instrument_id=etf.id, quantity=D("10"), price=D("50"))
    # this year: +300 on SAP (26.38 tax withheld), -80 on the ETF
    tx(db, acc["depot"], "sell", JAN, instrument_id=sap.id, quantity=D("5"), price=D("160"), taxes=D("26.38"))
    tx(db, acc["depot"], "sell", JAN, instrument_id=etf.id, quantity=D("4"), price=D("30"))
    tx(db, acc["depot"], "dividend", JAN, instrument_id=sap.id, amount=D("73.63"), taxes=D("26.37"))
    tx(db, acc["cash"], "interest", JAN, amount=D("40.00"), taxes=D("10.00"))
    tx(db, acc["cash"], "tax", JAN, amount=D("-5.00"), description="Vorabpauschale")
    # last year's income must not leak into this year
    tx(db, acc["depot"], "dividend", date(YEAR - 1, 7, 1), instrument_id=sap.id, amount=D("20"), taxes=D("0"))
    return acc


def test_taxes_overview_of_a_year(db):
    _tax_fixture(db)
    t = analytics.taxes(db, YEAR)
    assert t["year"] == YEAR and t["is_estimate"] is True
    assert t["realized"]["gains"] == 300.0 and t["realized"]["losses"] == -80.0 and t["realized"]["net"] == 220.0
    assert t["realized"]["count"] == 2 and t["realized"]["taxes"] == 26.38
    assert [(c["key"], c["net"]) for c in t["realized"]["by_class"]] == [("stock", 300.0), ("fund", -80.0)]
    assert t["dividends"] == {"gross": 100.0, "taxes": 26.37, "net": 73.63, "count": 1}
    assert t["interest"] == {"gross": 50.0, "taxes": 10.0, "net": 40.0, "count": 1}
    assert t["other_taxes"] == {"amount": 5.0, "count": 1}
    assert t["capital_income"] == 370.0
    assert t["allowance"] == 1000.0 and t["allowance_used"] == 370.0 and t["allowance_remaining"] == 630.0
    assert t["taxable_income"] == 0.0 and t["estimated_tax"] == 0.0
    assert t["taxes_withheld"] == pytest.approx(26.38 + 26.37 + 10 + 5)
    assert t["difference"] == t["taxes_withheld"]
    assert YEAR in t["years"] and YEAR - 1 in t["years"] and t["years"] == sorted(t["years"], reverse=True)

    prev = analytics.taxes(db, YEAR - 1)
    assert prev["dividends"]["gross"] == 20.0 and prev["realized"]["count"] == 0 and prev["capital_income"] == 20.0


def test_taxes_allowance_setting_and_estimate(client, db):
    _tax_fixture(db)
    assert client.get("/api/settings").json() == {"sparerpauschbetrag": 1000.0}
    r = client.patch("/api/settings", json={"sparerpauschbetrag": 200})
    assert r.status_code == 200 and r.json() == {"sparerpauschbetrag": 200.0}
    t = client.get(f"/api/analytics/taxes?year={YEAR}").json()
    assert t["allowance"] == 200.0 and t["allowance_used"] == 200.0 and t["allowance_remaining"] == 0.0
    assert t["taxable_income"] == 170.0
    assert t["tax_rate"] == 26.375 and t["estimated_tax"] == pytest.approx(170 * 0.26375, abs=0.01)
    assert client.patch("/api/settings", json={"sparerpauschbetrag": -1}).status_code == 422
    client.patch("/api/settings", json={"sparerpauschbetrag": 2000})
    assert client.get("/api/analytics/taxes").json()["allowance"] == 2000.0


def test_taxes_empty_database_and_losses_only(client, db):
    t = client.get("/api/analytics/taxes").json()
    assert t["capital_income"] == 0.0 and t["allowance_used"] == 0.0 and t["years"] == [YEAR]
    acc = make_accounts(db)
    sap = make_instrument(db)
    tx(db, acc["depot"], "buy", JAN, instrument_id=sap.id, quantity=D("1"), price=D("100"))
    tx(db, acc["depot"], "sell", JAN + timedelta(days=1), instrument_id=sap.id, quantity=D("1"), price=D("60"))
    t = client.get("/api/analytics/taxes").json()
    assert t["capital_income"] == -40.0 and t["allowance_used"] == 0.0 and t["taxable_income"] == 0.0
    assert client.get("/api/analytics/taxes?year=1800").status_code == 422


# --------------------------------------------------------------------------- recurring
def _monthly(n: int, day_offset: int = 5) -> list[date]:
    return [TODAY - timedelta(days=day_offset + 30 * i) for i in range(n)]


def test_recurring_detects_fixed_costs_and_savings_plans(client, db):
    acc = make_accounts(db)
    rent = make_category(db, "Miete")
    salary = make_category(db, "Gehalt", CategoryKind.income)
    for when in _monthly(6):
        tx(db, acc["giro"], "withdrawal", when, amount=D("-950"), counterparty="Hausverwaltung Meier", category_id=rent.id)
        tx(db, acc["giro"], "withdrawal", when, amount=D("-12.99"), counterparty=f"SPOTIFY AB {when:%m%Y} REF")
        tx(db, acc["giro"], "deposit", when, amount=D("3000"), counterparty="Arbeitgeber GmbH", category_id=salary.id)
    # same company, second contract with a clearly different amount
    for when in _monthly(4, day_offset=12):
        tx(db, acc["giro"], "withdrawal", when, amount=D("-49.90"), counterparty="Hausverwaltung Meier")
    # irregular shopping: many bookings, no rhythm
    for i, days in enumerate((3, 5, 11, 12, 19, 30, 33, 41)):
        tx(db, acc["giro"], "withdrawal", TODAY - timedelta(days=days), amount=D(f"-{20 + i * 7}.10"), counterparty="REWE Markt")
    # yearly insurance and a cancelled subscription (last booking long ago)
    for when in (TODAY - timedelta(days=20), TODAY - timedelta(days=385)):
        tx(db, acc["giro"], "withdrawal", when, amount=D("-240"), counterparty="Haftpflicht Versicherung AG")
    for when in [TODAY - timedelta(days=200 + 30 * i) for i in range(5)]:
        tx(db, acc["giro"], "withdrawal", when, amount=D("-9.99"), counterparty="Altes Abo")
    etf = make_instrument(db, isin="IE00B4L5Y983", symbol="EUNL.DE", name="World ETF", type_=InstrumentType.etf)
    for when in _monthly(5, day_offset=2):
        tx(db, acc["depot"], "buy", when, instrument_id=etf.id, quantity=D("2"), amount=D("-200"))

    r = client.get("/api/analytics/recurring").json()
    fixed = {(i["name"].split(" ")[0], i["amount"]): i for i in r["items"] if i["kind"] == "fixed"}
    assert set(fixed) == {("Hausverwaltung", 950.0), ("Hausverwaltung", 49.9), ("SPOTIFY", 12.99), ("Haftpflicht", 240.0)}
    assert fixed[("Hausverwaltung", 950.0)]["interval"] == "monthly" and fixed[("Hausverwaltung", 950.0)]["count"] == 6
    assert fixed[("Hausverwaltung", 950.0)]["category_name"] == "Miete"
    yearly = fixed[("Haftpflicht", 240.0)]
    assert yearly["interval"] == "yearly" and yearly["monthly_amount"] == 20.0
    assert r["fixed_count"] == 4 and r["fixed_monthly_total"] == pytest.approx(950 + 49.9 + 12.99 + 20)
    (plan,) = [i for i in r["items"] if i["kind"] == "savings_plan"]
    assert plan["name"] == "World ETF" and plan["instrument_id"] == etf.id and plan["monthly_amount"] == 200.0
    assert r["savings_plan_monthly_total"] == 200.0 and r["savings_plan_count"] == 1
    last = date.fromisoformat(fixed[("SPOTIFY", 12.99)]["last_date"])
    assert date.fromisoformat(fixed[("SPOTIFY", 12.99)]["next_date"]) > last


def test_recurring_empty(client):
    assert client.get("/api/analytics/recurring").json() == {
        "items": [], "fixed_monthly_total": 0.0, "fixed_count": 0, "savings_plan_monthly_total": 0.0,
        "savings_plan_count": 0}


# --------------------------------------------------------------------------- default categories
def test_interest_and_dividends_get_their_category(client, db):
    acc = make_accounts(db)
    parent = make_category(db, "Kapitalerträge", CategoryKind.income)
    zinsen = make_category(db, "Zinsen", CategoryKind.income, parent=parent)
    dividenden = make_category(db, "Dividenden", CategoryKind.income, parent=parent)
    bonus = make_category(db, "Bonuszinsen", CategoryKind.income, rules=["Neukundenbonus"])
    sap = make_instrument(db)
    body = {"account_id": acc["cash"].id, "type": "interest", "booking_date": TODAY.isoformat(), "amount": 12.34}
    r = client.post("/api/transactions", json=body)
    assert r.status_code == 201 and r.json()["category_id"] == zinsen.id and r.json()["category_name"] == "Zinsen"
    # a matching rule still wins over the type default, an explicit category over everything
    r = client.post("/api/transactions", json=body | {"amount": 5, "description": "Neukundenbonus Q1"})
    assert r.json()["category_id"] == bonus.id
    r = client.post("/api/transactions", json=body | {"amount": 6, "category_id": parent.id})
    assert r.json()["category_id"] == parent.id
    r = client.post("/api/transactions", json={"account_id": acc["depot"].id, "type": "dividend", "instrument_id": sap.id,
                                               "booking_date": TODAY.isoformat(), "amount": 20})
    assert r.json()["category_id"] == dividenden.id
    # older uncategorised rows are picked up by "auto-assign"
    old = Transaction(account_id=acc["cash"].id, type="interest", booking_date=TODAY, amount=D("1"), currency="EUR")
    db.add(old)
    db.flush()
    assert client.post("/api/categories/auto-assign").json()["assigned"] == 1
    assert old.category_id == zinsen.id


def test_interest_without_categories_stays_uncategorised(db):
    acc = make_accounts(db)
    t = tx(db, acc["cash"], "interest", TODAY, amount=D("3"))
    assert t.category_id is None


# --------------------------------------------------------------------------- cent-exact sums
def test_net_worth_equals_sum_of_accounts_to_the_cent(db):
    acc = make_accounts(db)
    from app.models.core import Account, AccountType

    depot2 = Account(name="Depot 2", institution="ing", type=AccountType.brokerage, settlement_account_id=acc["cash"].id)
    db.add(depot2)
    db.flush()
    prices = ("33.335", "17.115", "101.005", "7.775")
    for n, price in enumerate(prices):
        inst = make_instrument(db, isin=f"DE000000000{n}", symbol=f"T{n}.DE", name=f"Titel {n}")
        # the same security in two depots with odd fractions
        tx(db, acc["depot"], "buy", JAN, instrument_id=inst.id, quantity=D("1.333"), price=D("10"))
        tx(db, depot2, "buy", JAN, instrument_id=inst.id, quantity=D("2.667"), price=D("10"))
        add_quote(db, inst, price)
    # three positions worth x.004 € each in ONE depot: rounded per security they add up to 60.00, the depot's
    # unrounded sum 60.012 used to be shown as 60.01 -> "sum of accounts" and net worth differed by a cent
    for n, price in enumerate(("10.004", "20.004", "30.004")):
        inst = make_instrument(db, isin=f"DE000000001{n}", symbol=f"U{n}.DE", name=f"Cent {n}")
        tx(db, acc["depot"], "buy", JAN, instrument_id=inst.id, quantity=D("1"), price=D("10"))
        add_quote(db, inst, price)
    val = portfolio.compute_valuation(db)
    summary = portfolio.compute_summary(db)
    values = portfolio.account_values(db, val)
    cents = lambda x: round(x * 100)  # noqa: E731
    assert cents(sum(a["value"] for a in summary["by_account"])) == cents(summary["net_worth"])
    assert cents(sum(v["total_value"] for v in values.values())) == cents(summary["net_worth"])
    assert cents(sum(v["market_value"] for v in values.values())) == cents(summary["invested_value"])
    assert cents(sum(h["market_value"] for h in val.holdings)) == cents(summary["invested_value"])
    for h in val.holdings:
        assert cents(sum(a["market_value"] for a in h["accounts"])) == cents(h["market_value"])
    assert cents(summary["cash_total"] + summary["invested_value"]) == cents(summary["net_worth"])
    assert values[acc["depot"].id]["market_value"] == pytest.approx(
        sum(round(1.333 * float(p), 2) for p in prices) + 60.00, abs=1e-9)


# --------------------------------------------------------------------------- instrument editing
def test_patch_instrument_symbol_change_drops_data_of_the_old_symbol(client, db):
    inst = make_instrument(db, isin="US5949181045", symbol="MSF.DE", name="Microsoft",
                           meta={"market": {"backfill_at": "2026-01-01T00:00:00+00:00", "profile_at": "x", "keep": 1}})
    other = make_instrument(db)
    for i in (inst, other):
        add_quote(db, i, "100")
        add_price(db, i, JAN, "99")
        db.add(Fundamentals(instrument_id=i.id, data={"available": True}))
        for source, type_ in (("yahoo", EventType.earnings), ("estimate", EventType.ex_dividend),
                              ("manual", EventType.agm), ("ics", EventType.investor_day)):
            db.add(InstrumentEvent(instrument_id=i.id, type=type_, date=TODAY, source=source))
    db.flush()

    # same symbol (different case) -> nothing is dropped
    r = client.patch(f"/api/instruments/{inst.id}", json={"symbol": "msf.de", "name": "Microsoft Corp."})
    assert r.status_code == 200 and r.json()["symbol"] == "MSF.DE" and r.json()["name"] == "Microsoft Corp."
    assert db.get(Quote, inst.id) is not None

    r = client.patch(f"/api/instruments/{inst.id}", json={"symbol": "msft"})
    assert r.status_code == 200 and r.json()["symbol"] == "MSFT"
    db.expire_all()
    assert db.get(Quote, inst.id) is None and db.get(Fundamentals, inst.id) is None
    assert db.query(PriceHistory).filter_by(instrument_id=inst.id).count() == 0
    assert {e.source for e in db.query(InstrumentEvent).filter_by(instrument_id=inst.id)} == {"manual", "ics"}
    assert r.json()["meta"]["market"] == {"keep": 1}
    # the other instrument is untouched
    assert db.get(Quote, other.id) is not None and db.get(Fundamentals, other.id) is not None
    assert db.query(PriceHistory).filter_by(instrument_id=other.id).count() == 1
    assert db.query(InstrumentEvent).filter_by(instrument_id=other.id).count() == 4
    detail = client.get(f"/api/instruments/{inst.id}").json()
    assert detail["quote"] is None and detail["symbol"] == "MSFT"

    # removing the symbol is allowed as well
    assert client.patch(f"/api/instruments/{inst.id}", json={"symbol": ""}).json()["symbol"] is None


def test_patch_instrument_master_data_and_meta_merge(client, db):
    inst = make_instrument(db, isin="IE00B4L5Y983", symbol="EUNL.DE", name="World", type_=InstrumentType.etf,
                           meta={"market": {"etf_checked_at": "2026-01-01"}, "other": True})
    r = client.patch(f"/api/instruments/{inst.id}", json={
        "name": "iShares Core MSCI World", "type": "fund", "currency": "usd", "fund_provider": "ishares",
        "ir_url": "https://example.com/ir", "meta": {"market": {"holdings_url": "https://example.com/h.csv"}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["name"], body["type"], body["currency"], body["fund_provider"]) == (
        "iShares Core MSCI World", "fund", "USD", "ishares")
    assert body["ir_url"] == "https://example.com/ir"
    assert body["meta"] == {"market": {"etf_checked_at": "2026-01-01", "holdings_url": "https://example.com/h.csv"},
                            "other": True}
    # null / empty removes one key, clears optional fields; required fields ignore empty values
    r = client.patch(f"/api/instruments/{inst.id}", json={
        "name": "", "ir_url": "", "fund_provider": None, "meta": {"market": {"holdings_url": ""}}})
    body = r.json()
    assert body["name"] == "iShares Core MSCI World" and body["ir_url"] is None and body["fund_provider"] is None
    assert body["meta"]["market"] == {"etf_checked_at": "2026-01-01"}
    assert client.patch(f"/api/instruments/{inst.id}", json={"ir_url": "javascript:alert(1)"}).status_code == 422
    assert client.patch(f"/api/instruments/{inst.id}",
                        json={"meta": {"market": {"holdings_url": "file:///etc/passwd"}}}).status_code == 422
    assert client.patch(f"/api/instruments/{inst.id}", json={"isin": "SHORT"}).status_code == 422
    assert client.patch("/api/instruments/999999", json={"name": "x"}).status_code == 404
