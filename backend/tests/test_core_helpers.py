"""Shared builders for the Backend-Core tests (no tests in here)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.core import Account, AccountType, Category, CategoryKind, FxRate, Instrument, InstrumentType
from app.models.market import PriceHistory, Quote
from app.services import ledger

D = Decimal


def make_accounts(db) -> dict[str, Account]:
    giro = Account(name="Giro", institution="postbank", type=AccountType.checking, opening_balance=D("1000"))
    cash = Account(name="Verrechnung", institution="scalable", type=AccountType.savings, opening_balance=D("0"))
    db.add_all([giro, cash])
    db.flush()
    depot = Account(name="Depot", institution="scalable", type=AccountType.brokerage, settlement_account_id=cash.id)
    db.add(depot)
    db.flush()
    return {"giro": giro, "cash": cash, "depot": depot}


def make_instrument(db, isin="DE0007164600", symbol="SAP.DE", name="SAP SE", currency="EUR",
                    type_=InstrumentType.stock, **kw) -> Instrument:
    inst = Instrument(isin=isin, symbol=symbol, name=name, currency=currency, type=type_, **kw)
    db.add(inst)
    db.flush()
    return inst


def make_category(db, name, kind=CategoryKind.expense, rules=None, parent=None) -> Category:
    cat = Category(name=name, kind=kind, match_rules=rules, parent_id=parent.id if parent else None)
    db.add(cat)
    db.flush()
    return cat


def tx(db, account, type_, when: date, **kw):
    return ledger.create_transaction(db, {"account_id": account.id, "type": type_, "booking_date": when, **kw})


def add_price(db, inst, when: date, close) -> None:
    db.add(PriceHistory(instrument_id=inst.id, date=when, close=D(str(close))))
    db.flush()


def add_quote(db, inst, price, previous_close=None, currency=None) -> None:
    db.add(Quote(instrument_id=inst.id, price=D(str(price)),
                 previous_close=D(str(previous_close)) if previous_close is not None else None,
                 currency=currency or inst.currency, market_time=datetime.now(timezone.utc), source="test"))
    db.flush()


def add_fx(db, currency, rate, when: date) -> None:
    db.add(FxRate(date=when, currency=currency, rate=D(str(rate))))
    db.flush()
