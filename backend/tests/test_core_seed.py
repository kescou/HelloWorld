from datetime import date

from sqlalchemy import func, select

from app import seed
from app.models.core import Account, AccountType, Category, FxRate, Instrument, Setting, Transaction, TxType
from app.models.market import EtfHolding, Fundamentals, InstrumentEvent, PriceHistory, Quote
from app.services import analytics, ledger, portfolio


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_seed_base_is_idempotent(db):
    seed.seed_base(db)
    accounts = {a.name: a for a in db.scalars(select(Account))}
    assert len(accounts) == 7
    assert accounts["ING Depot"].settlement_account_id == accounts["ING Girokonto"].id
    assert accounts["Scalable Depot"].settlement_account_id == accounts["Scalable Tagesgeld"].id
    assert accounts["Trade Republic Depot"].settlement_account_id == accounts["Trade Republic Tagesgeld"].id
    assert accounts["Postbank Giro"].type == AccountType.checking
    assert accounts["Trade Republic Tagesgeld"].type == AccountType.savings
    n_cat = _count(db, Category)
    names = set(db.scalars(select(Category.name)))
    assert {"Wohnen", "Lebensmittel", "Mobilität", "Versicherungen", "Freizeit", "Abos", "Gesundheit", "Gehalt",
            "Kapitalerträge", "Umbuchung"} <= names
    seed.seed_base(db)
    seed.seed_base(db)
    assert _count(db, Account) == 7 and _count(db, Category) == n_cat
    # match rules work for typical German counterparties
    cats = db.scalars(select(Category)).all()
    assert ledger.match_category(cats, "REWE Markt GmbH").name == "Lebensmittel"
    assert ledger.match_category(cats, "LIDL Dienstleistung").name == "Lebensmittel"
    assert ledger.match_category(cats, "NETFLIX.COM").name == "Abos"
    assert ledger.match_category(cats, None, "Lohn/Gehalt 03/2025").name == "Gehalt"
    # user changes survive re-seeding
    accounts["Postbank Giro"].color = "#000000"
    db.flush()
    seed.seed_base(db)
    assert accounts["Postbank Giro"].color == "#000000"


def test_seed_demo_and_reset(db):
    first = seed.seed_demo(db)
    assert first["skipped"] is False and first["transactions"] > 800
    n_tx = _count(db, Transaction)
    assert seed.seed_demo(db) == {"skipped": True}          # idempotent
    assert _count(db, Transaction) == n_tx
    assert _count(db, Instrument) == 8 and _count(db, Quote) == 8
    assert _count(db, PriceHistory) > 8 * 600
    assert _count(db, FxRate) > 600
    assert _count(db, Fundamentals) == 6
    assert _count(db, EtfHolding) >= 30
    assert _count(db, InstrumentEvent) > 30
    fundamentals = db.scalars(select(Fundamentals)).first().data
    assert {"years", "pe_history", "fair_value", "dividend_growth", "valuation", "earnings_quality"} <= set(fundamentals)
    assert {"year", "revenue", "net_income", "eps", "dps", "fcf", "payout_ratio", "shares"} <= set(fundamentals["years"][0])

    # the dataset is consistent: no negative balances, every transfer is linked, plausible analytics
    balances = ledger.cash_balances(db)
    assert all(b >= 0 for b in balances.values()), balances
    transfers = db.scalars(select(Transaction).where(Transaction.type.in_([TxType.transfer_in, TxType.transfer_out]))).all()
    assert transfers and all(t.transfer_peer_id for t in transfers)
    summary = portfolio.compute_summary(db)
    assert summary["net_worth"] > 30_000 and summary["invested_value"] > summary["cost_basis"] * 0.7
    assert not summary["fx_warning"] and not summary["warnings"]
    holdings = portfolio.compute_valuation(db).holdings
    assert {h["isin"] for h in holdings} == {i["isin"] for i in seed.DEMO_INSTRUMENTS}
    assert len(portfolio.compute_history(db, "MAX")) > 900
    perf = portfolio.compute_performance(db, "1Y")
    assert -60 < perf["twr"] < 150 and perf["xirr"] is not None
    div = analytics.dividends(db)
    assert div["forecast_12m_total"] > 100 and len(div["received_by_year"]) >= 2
    flow = analytics.cashflow(db, 12)
    assert all(m["income"] > 0 for m in flow[:-1])
    assert 5 < sum(m["savings_rate"] for m in flow[:-1]) / 11 < 70
    assert len(analytics.expenses_by_category(db)) > 8
    assert len(analytics.realized_gains(db)) >= 2
    looked = portfolio._table_lookthrough(db, holdings, "sector")
    assert looked["Technology"] > sum(h["market_value"] for h in holdings if h["sector"] == "Technology")

    result = seed.reset_demo(db)
    assert result["transactions"] == n_tx
    for model in (Transaction, Instrument, Quote, PriceHistory, FxRate, Fundamentals, EtfHolding, InstrumentEvent):
        assert _count(db, model) == 0, model.__name__
    assert db.get(Setting, seed.DEMO_SETTING) is None
    assert _count(db, Account) == 7                       # base data stays
    assert seed.seed_demo(db, today=date.today())["skipped"] is False   # can be seeded again


def test_reset_demo_keeps_user_data(db):
    base = seed.seed_base(db)
    giro = base["accounts"]["Postbank Giro"]
    depot = base["accounts"]["ING Depot"]
    own = ledger.create_transaction(db, {"account_id": giro.id, "type": "deposit", "booking_date": date(2020, 1, 2),
                                         "amount": "123.45", "description": "eigene Buchung"})
    seed.seed_demo(db)
    sap = db.scalar(select(Instrument).where(Instrument.isin == "DE0007164600"))
    own_buy = ledger.create_transaction(db, {"account_id": depot.id, "type": "buy", "booking_date": date(2020, 1, 3),
                                             "instrument_id": sap.id, "quantity": "1", "price": "100"})
    seed.reset_demo(db)
    remaining = db.scalars(select(Transaction.id)).all()
    assert sorted(remaining) == sorted([own.id, own_buy.id])
    assert db.scalar(select(Instrument.isin)) == "DE0007164600"   # still referenced by the user's transaction
    assert _count(db, Instrument) == 1
