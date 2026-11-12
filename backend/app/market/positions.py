"""Positions derived from the `transactions` journal (independent of app.services).

Quantity rule (docs/ARCHITECTURE.md): buy/delivery_in +, sell/delivery_out −, split multiplies.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.core import Instrument, Transaction, TxType
from app.models.market import PriceHistory, Quote

_PLUS = {TxType.buy, TxType.delivery_in}
_MINUS = {TxType.sell, TxType.delivery_out}
EPS = Decimal("0.0000001")


def _security_txs(db: Session, account_id: int | None = None, instrument_id: int | None = None):
    stmt = (
        select(Transaction.account_id, Transaction.instrument_id, Transaction.type, Transaction.quantity,
               Transaction.booking_date)
        .where(Transaction.instrument_id.is_not(None),
               Transaction.type.in_(list(_PLUS | _MINUS | {TxType.split})))
        .order_by(Transaction.booking_date, Transaction.id)
    )
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if instrument_id is not None:
        stmt = stmt.where(Transaction.instrument_id == instrument_id)
    return db.execute(stmt).all()


def compute_quantities(db: Session, account_id: int | None = None, as_of: date | None = None) -> dict[int, Decimal]:
    """instrument_id -> net quantity (summed over accounts), optionally as of a date."""
    per: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal(0))
    for acc, inst, typ, qty, booked in _security_txs(db, account_id):
        if as_of is not None and booked > as_of:
            break
        if qty is None:
            continue
        key = (acc, inst)
        if typ in _PLUS:
            per[key] += abs(qty)
        elif typ in _MINUS:
            per[key] -= abs(qty)
        elif typ == TxType.split and qty > 0:
            per[key] *= qty
    out: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    for (_acc, inst), qty in per.items():
        out[inst] += qty
    return {k: v for k, v in out.items() if abs(v) > EPS}


def quantity_timeline(db: Session, instrument_id: int) -> list[tuple[date, Decimal]]:
    """[(date, total quantity after that day's transactions)] for one instrument, ascending."""
    per: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    out: list[tuple[date, Decimal]] = []
    for acc, _inst, typ, qty, booked in _security_txs(db, instrument_id=instrument_id):
        if qty is None:
            continue
        if typ in _PLUS:
            per[acc] += abs(qty)
        elif typ in _MINUS:
            per[acc] -= abs(qty)
        elif typ == TxType.split and qty > 0:
            per[acc] *= qty
        total = sum(per.values(), Decimal(0))
        if out and out[-1][0] == booked:
            out[-1] = (booked, total)
        else:
            out.append((booked, total))
    return out


def quantity_at(timeline: list[tuple[date, Decimal]], on: date) -> Decimal:
    qty = Decimal(0)
    for d, q in timeline:
        if d > on:
            break
        qty = q
    return qty


def held_instrument_ids(db: Session) -> set[int]:
    return {i for i, q in compute_quantities(db).items() if q > EPS}


def transacted_instrument_ids(db: Session) -> set[int]:
    rows = db.execute(select(Transaction.instrument_id).where(Transaction.instrument_id.is_not(None)).distinct())
    return {r[0] for r in rows}


def tracked_instruments(db: Session, include_past: bool = False) -> list[Instrument]:
    """Held (net quantity > 0) + watchlist. include_past: also everything that ever had a transaction
    (needed for price history of sold positions)."""
    ids = transacted_instrument_ids(db) if include_past else held_instrument_ids(db)
    stmt = select(Instrument).where(Instrument.is_watchlist.is_(True))
    if ids:
        stmt = select(Instrument).where((Instrument.id.in_(ids)) | (Instrument.is_watchlist.is_(True)))
    return list(db.scalars(stmt.order_by(Instrument.id)))


def latest_prices(db: Session, instrument_ids: list[int]) -> dict[int, tuple[float, str | None]]:
    """instrument_id -> (price, currency): quote, else last history close, else last transaction price."""
    out: dict[int, tuple[float, str | None]] = {}
    if not instrument_ids:
        return out
    for q in db.scalars(select(Quote).where(Quote.instrument_id.in_(instrument_ids))):
        out[q.instrument_id] = (float(q.price), q.currency)
    missing = [i for i in instrument_ids if i not in out]
    if missing:
        sub = (select(PriceHistory.instrument_id, func.max(PriceHistory.date).label("d"))
               .where(PriceHistory.instrument_id.in_(missing)).group_by(PriceHistory.instrument_id).subquery())
        rows = db.execute(
            select(PriceHistory.instrument_id, PriceHistory.close, Instrument.currency)
            .join(sub, (sub.c.instrument_id == PriceHistory.instrument_id) & (sub.c.d == PriceHistory.date))
            .join(Instrument, Instrument.id == PriceHistory.instrument_id))
        for inst, close, cur in rows:
            out[inst] = (float(close), cur)
    missing = [i for i in instrument_ids if i not in out]
    for inst in missing:
        row = db.execute(
            select(Transaction.price, Transaction.currency)
            .where(Transaction.instrument_id == inst, Transaction.price.is_not(None),
                   Transaction.type.in_([TxType.buy, TxType.sell]))
            .order_by(Transaction.booking_date.desc(), Transaction.id.desc()).limit(1)).first()
        if row and row[0]:
            out[inst] = (float(row[0]), row[1])
    return out
