"""Portfolio: FIFO positions, valuation, daily history, performance, allocation.

Conventions
-----------
* `Transaction.amount`, `fees`, `taxes` are in the cash account's currency (EUR);
  `Transaction.price` is in `Transaction.currency` (fx_rate: 1 EUR = x currency).
* Market data is read from the tables quotes / price_history / fx_rates / etf_holdings only.
* fx_rates.rate = units of currency per 1 EUR -> value_eur = value / rate.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Float, cast, func, select, text
from sqlalchemy.orm import Session

from app.models.core import Account, AccountType, FxRate, Instrument, Transaction, TxType
from app.services import ledger
from app.services.performance import twr_series, xirr

ZERO = Decimal("0")
EPS = Decimal("0.000000005")
BASE = "EUR"
_MINOR = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}

RANGES = {"1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "2Y": 730, "3Y": 1095, "5Y": 1826, "10Y": 3652}


def range_start(range_: str | None, today: date | None = None) -> date | None:
    """Start date for a range token (None = MAX)."""
    today = today or date.today()
    token = (range_ or "1Y").upper()
    if token in ("MAX", "ALL"):
        return None
    if token == "YTD":
        return date(today.year, 1, 1)
    days = RANGES.get(token)
    if days is None:
        raise ValueError(f"unknown range {range_!r}")
    return today - timedelta(days=days)


def _f(value: Any) -> float:
    return 0.0 if value is None else float(value)


def _r(value: Any, digits: int = 2) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(v, digits) if np.isfinite(v) else 0.0


# --------------------------------------------------------------------------- FX
class Fx:
    """Latest FX rates (currency per 1 EUR) with fallback 1.0 + warning."""

    def __init__(self, db: Session):
        rows = db.execute(text(
            "SELECT DISTINCT ON (currency) currency, rate FROM fx_rates ORDER BY currency, date DESC"
        )).all()
        self.rates = {c: float(r) for c, r in rows if r}
        self.missing: set[str] = set()

    def normalize(self, currency: str | None) -> tuple[str, float]:
        if not currency:
            return BASE, 1.0
        if currency in _MINOR:
            return _MINOR[currency]
        return currency.upper(), 1.0

    def rate(self, currency: str | None) -> float:
        cur, _ = self.normalize(currency)
        if cur == BASE:
            return 1.0
        r = self.rates.get(cur)
        if not r:
            self.missing.add(cur)
            return 1.0
        return r

    def to_eur(self, value: float, currency: str | None) -> float:
        cur, div = self.normalize(currency)
        return value / div / self.rate(cur)


# --------------------------------------------------------------------------- FIFO
#: a delivery_out and the delivery_in of a depot transfer may be booked a few days apart
TRANSFER_MATCH_DAYS = 10
#: a ledger `split` booking within this many days of a provider split event counts as "booked"
SPLIT_MATCH_DAYS = 10

@dataclass
class Lot:
    quantity: Decimal
    unit_cost: Decimal  # EUR per unit incl. fees
    date: date


@dataclass
class Position:
    account_id: int
    instrument_id: int
    lots: deque = field(default_factory=deque)
    dividends: Decimal = ZERO        # net cash received (EUR)
    dividends_gross: Decimal = ZERO
    realized_gain: Decimal = ZERO
    last_price: Decimal | None = None
    last_price_currency: str = BASE
    last_price_date: date | None = None

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.lots), ZERO)

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.quantity * lot.unit_cost for lot in self.lots), ZERO)


@dataclass
class RealizedGain:
    instrument_id: int
    account_id: int
    date: date
    quantity: Decimal
    proceeds: Decimal
    cost: Decimal
    gain: Decimal
    taxes: Decimal
    fees: Decimal
    transaction_id: int


@dataclass
class FifoResult:
    positions: dict[tuple[int, int], Position]
    realized: list[RealizedGain]
    cost_events: list[tuple[date, int, int, float]]  # (date, account_id, instrument_id, delta cost basis)
    warnings: list[str]
    #: delivery_in bookings without price and without a matching delivery_out: cost basis unknown (0)
    missing_cost: list[dict] = field(default_factory=list)


def _held(entry: dict) -> bool:
    """Is the placeholder lot of a price-less delivery_in still in its position? (identity, not equality)"""
    return entry["lot"].quantity > EPS and any(lot is entry["lot"] for lot in entry["pos"].lots)


def _price_to_eur(price: Decimal, currency: str, fx_rate: Decimal | None, fx: Fx) -> Decimal:
    if (currency or BASE).upper() == BASE:
        return price
    if fx_rate:
        return price / fx_rate
    return Decimal(str(fx.to_eur(float(price), currency)))


def compute_fifo(db: Session, fx: Fx | None = None, until: date | None = None) -> FifoResult:
    """Replay all securities transactions and build FIFO lots per (account, instrument)."""
    fx = fx or Fx(db)
    stmt = (
        select(
            Transaction.id, Transaction.account_id, Transaction.instrument_id, Transaction.type,
            Transaction.booking_date, Transaction.quantity, Transaction.price, Transaction.amount,
            Transaction.fees, Transaction.taxes, Transaction.currency, Transaction.fx_rate,
        )
        .where(Transaction.instrument_id.is_not(None))
        .order_by(Transaction.booking_date, Transaction.id)
    )
    if until is not None:
        stmt = stmt.where(Transaction.booking_date <= until)
    positions: dict[tuple[int, int], Position] = {}
    realized: list[RealizedGain] = []
    cost_events: list[tuple[date, int, int, float]] = []
    warnings: list[str] = []
    # Depot transfers (delivery_out here, delivery_in there) keep their purchase lots. Both orders occur:
    # out booked first -> its consumed lots wait in `pending_out`; in booked first -> a zero-cost placeholder
    # lot waits in `pending_in` and is filled when the matching delivery_out shows up.
    pending_out: dict[int, list[dict]] = defaultdict(list)
    pending_in: dict[int, list[dict]] = defaultdict(list)
    missing_cost: list[dict] = []

    def near(a: date, b: date) -> bool:
        return abs((a - b).days) <= TRANSFER_MATCH_DAYS

    def consume(pos: Position, qty: Decimal, taken: list[Lot] | None = None) -> tuple[Decimal, Decimal]:
        """Remove qty from the oldest lots. Returns (cost removed, uncovered quantity)."""
        cost = ZERO
        remaining = qty
        while remaining > EPS and pos.lots:
            lot = pos.lots[0]
            take = lot.quantity if lot.quantity <= remaining + EPS else remaining
            cost += take * lot.unit_cost
            if taken is not None:
                taken.append(Lot(take, lot.unit_cost, lot.date))
            lot.quantity -= take
            remaining -= take
            if lot.quantity <= EPS:
                pos.lots.popleft()
        return cost, (remaining if remaining > EPS else ZERO)

    for row in db.execute(stmt):
        key = (row.account_id, row.instrument_id)
        pos = positions.get(key)
        if pos is None:
            pos = positions[key] = Position(row.account_id, row.instrument_id)
        qty = Decimal(row.quantity) if row.quantity is not None else ZERO
        amount = Decimal(row.amount or 0)
        fees = Decimal(row.fees or 0)
        taxes = Decimal(row.taxes or 0)
        t = row.type
        if t in (TxType.buy, TxType.delivery_in):
            if qty <= 0:
                continue
            if t == TxType.buy and amount != 0:
                cost = -amount
            elif row.price is not None:
                cost = qty * _price_to_eur(Decimal(row.price), row.currency, row.fx_rate, fx) + fees
            elif t == TxType.delivery_in:
                # no price given: take over the lots (cost + purchase dates) of the matching delivery_out
                match = next((m for m in pending_out[row.instrument_id]
                              if m["account_id"] != row.account_id and abs(m["qty"] - qty) <= EPS
                              and near(m["date"], row.booking_date)), None)
                if match is not None:
                    pending_out[row.instrument_id].remove(match)
                    cost = sum((lot.quantity * lot.unit_cost for lot in match["lots"]), ZERO) + fees
                    pos.lots.extend(Lot(lot.quantity, lot.unit_cost, lot.date) for lot in match["lots"])
                    if fees and match["lots"]:
                        pos.lots[-1].unit_cost += fees / pos.lots[-1].quantity
                    cost_events.append((row.booking_date, row.account_id, row.instrument_id, float(cost)))
                    continue
                placeholder = Lot(qty, ZERO, row.booking_date)
                pos.lots.append(placeholder)
                entry = {"lot": placeholder, "pos": pos, "qty": qty, "date": row.booking_date,
                         "account_id": row.account_id, "instrument_id": row.instrument_id, "transaction_id": row.id}
                pending_in[row.instrument_id].append(entry)
                missing_cost.append(entry)
                cost_events.append((row.booking_date, row.account_id, row.instrument_id, 0.0))
                continue
            else:
                cost = ZERO
            pos.lots.append(Lot(qty, cost / qty, row.booking_date))
            cost_events.append((row.booking_date, row.account_id, row.instrument_id, float(cost)))
        elif t in (TxType.sell, TxType.delivery_out):
            if qty <= 0:
                continue
            taken: list[Lot] = []
            cost, uncovered = consume(pos, qty, taken)
            if t == TxType.delivery_out and taken and uncovered == 0:
                waiting = next((m for m in pending_in[row.instrument_id]
                                if m["account_id"] != row.account_id and abs(m["qty"] - qty) <= EPS
                                and near(m["date"], row.booking_date)
                                and _held(m) and abs(m["lot"].quantity - qty) <= EPS), None)
                if waiting is not None:
                    # the delivery_in was booked first: replace its placeholder by the real lots
                    pending_in[row.instrument_id].remove(waiting)
                    missing_cost.remove(waiting)
                    lots = waiting["pos"].lots
                    at = next(n for n, lot in enumerate(lots) if lot is waiting["lot"])
                    del lots[at]
                    for offset, lot in enumerate(taken):
                        lots.insert(at + offset, Lot(lot.quantity, lot.unit_cost, lot.date))
                    cost_events.append((row.booking_date, waiting["account_id"], row.instrument_id, float(cost)))
                else:
                    pending_out[row.instrument_id].append(
                        {"account_id": row.account_id, "qty": qty, "date": row.booking_date, "lots": taken})
            if uncovered > 0:
                warnings.append(
                    f"Verkauf/Auslieferung übersteigt Bestand (Instrument {row.instrument_id}, Konto {row.account_id}, {row.booking_date})"
                )
            cost_events.append((row.booking_date, row.account_id, row.instrument_id, -float(cost)))
            if t == TxType.sell:
                proceeds = amount + taxes  # net of fees, before taxes
                gain = proceeds - cost
                pos.realized_gain += gain
                realized.append(RealizedGain(
                    row.instrument_id, row.account_id, row.booking_date, qty, proceeds, cost, gain, taxes, fees, row.id,
                ))
        elif t == TxType.split:
            if qty > 0:
                for lot in pos.lots:
                    lot.quantity *= qty
                    lot.unit_cost /= qty
        elif t == TxType.dividend:
            pos.dividends += amount
            pos.dividends_gross += amount + taxes
        if row.price is not None and t in (TxType.buy, TxType.sell, TxType.delivery_in):
            pos.last_price = Decimal(row.price)
            pos.last_price_currency = row.currency or BASE
            pos.last_price_date = row.booking_date
        elif t == TxType.split and qty > 0 and pos.last_price is not None:
            pos.last_price = pos.last_price / qty
    return FifoResult(positions, realized, cost_events, warnings,
                      [{"transaction_id": e["transaction_id"], "instrument_id": e["instrument_id"],
                        "account_id": e["account_id"], "date": e["date"].isoformat(), "quantity": float(e["qty"])}
                       for e in missing_cost if _held(e)])      # still (partly) held


# --------------------------------------------------------------------------- valuation
@dataclass
class PriceInfo:
    price: float
    currency: str
    previous_close: float | None = None
    source: str = "quote"   # quote | history | transaction
    as_of: datetime | date | None = None


def load_prices(db: Session, instrument_ids: list[int], fifo: FifoResult | None = None) -> dict[int, PriceInfo]:
    """Latest price per instrument: quotes -> last price_history close -> last transaction price."""
    out: dict[int, PriceInfo] = {}
    if not instrument_ids:
        return out
    ids = sorted(set(instrument_ids))
    currencies = dict(db.execute(select(Instrument.id, Instrument.currency).where(Instrument.id.in_(ids))).all())
    hist: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for iid, d, close in db.execute(text(
        "SELECT i.id, ph.date, ph.close FROM unnest(CAST(:ids AS integer[])) AS i(id) "
        "CROSS JOIN LATERAL (SELECT date, close FROM price_history WHERE instrument_id = i.id "
        "ORDER BY date DESC LIMIT 2) ph"
    ), {"ids": ids}):
        hist[iid].append((d, float(close)))
    for iid, price, prev, cur, mtime, updated in db.execute(text(
        "SELECT instrument_id, price, previous_close, currency, market_time, updated_at FROM quotes "
        "WHERE instrument_id = ANY(:ids)"
    ), {"ids": ids}):
        if price is None:
            continue
        prev_f = float(prev) if prev is not None else None
        if prev_f is None and hist.get(iid):
            qdate = (mtime or updated).date() if (mtime or updated) else date.today()
            older = [c for d, c in hist[iid] if d < qdate]
            prev_f = older[0] if older else None
        out[iid] = PriceInfo(float(price), cur or currencies.get(iid) or BASE, prev_f, "quote", mtime or updated)
    for iid in ids:
        if iid in out or not hist.get(iid):
            continue
        rows = hist[iid]
        out[iid] = PriceInfo(rows[0][1], currencies.get(iid) or BASE, rows[1][1] if len(rows) > 1 else None,
                             "history", rows[0][0])
    if fifo is not None:
        last: dict[int, Position] = {}
        for pos in fifo.positions.values():
            if pos.last_price is None:
                continue
            cur = last.get(pos.instrument_id)
            if cur is None or (pos.last_price_date and cur.last_price_date and pos.last_price_date > cur.last_price_date):
                last[pos.instrument_id] = pos
        for iid in ids:
            if iid not in out and iid in last:
                p = last[iid]
                out[iid] = PriceInfo(float(p.last_price), p.last_price_currency, None, "transaction", p.last_price_date)
    return out


@dataclass
class Valuation:
    holdings: list[dict]
    by_account_value: dict[int, float]
    warnings: list[str]
    fx_warning: bool
    fifo: FifoResult
    updated_at: datetime | None


def compute_valuation(db: Session, account_id: int | None = None) -> Valuation:
    """Current holdings (aggregated per instrument) valued in EUR."""
    fx = Fx(db)
    fifo = compute_fifo(db, fx)
    per_inst: dict[int, list[Position]] = defaultdict(list)
    for (acc, iid), pos in fifo.positions.items():
        if account_id is not None and acc != account_id:
            continue
        per_inst[iid].append(pos)
    held_ids = [iid for iid, plist in per_inst.items() if any(p.quantity > EPS for p in plist)]
    prices = load_prices(db, held_ids, fifo)
    instruments = {i.id: i for i in db.scalars(select(Instrument).where(Instrument.id.in_(list(per_inst) or [0])))}
    holdings: list[dict] = []
    by_account: dict[int, float] = defaultdict(float)
    warnings = list(fifo.warnings)
    updated_at: datetime | None = None
    for iid in held_ids:
        inst = instruments.get(iid)
        if inst is None:
            continue
        plist = per_inst[iid]
        qty = float(sum((p.quantity for p in plist), ZERO))
        cost = float(sum((p.cost_basis for p in plist), ZERO))
        dividends = float(sum((p.dividends for p in plist), ZERO))
        info = prices.get(iid)
        if info is None:
            warnings.append(f"Kein Kurs für {inst.name}")
            price, cur, price_eur, prev_eur, source = None, inst.currency, 0.0, None, None
        else:
            price, cur, source = info.price, info.currency, info.source
            price_eur = fx.to_eur(info.price, info.currency)
            prev_eur = fx.to_eur(info.previous_close, info.currency) if info.previous_close else None
            if isinstance(info.as_of, datetime) and (updated_at is None or info.as_of > updated_at):
                updated_at = info.as_of
        day_change = qty * (price_eur - prev_eur) if prev_eur else 0.0
        accounts = []
        # Cents are fixed ONCE, per position (depot x instrument). Every aggregate – value of the holding,
        # depot value of an account, invested value, net worth – is a sum of these cent amounts, so
        # "sum of the accounts" and "net worth" can never differ by a rounding cent.
        market_value = 0.0
        for p in plist:
            pq = float(p.quantity)
            if pq <= float(EPS):
                continue
            acc_value = _r(pq * price_eur if info is not None else float(p.cost_basis))
            market_value += acc_value
            by_account[p.account_id] += acc_value
            accounts.append({
                "account_id": p.account_id, "quantity": _r(pq, 8), "cost_basis": _r(p.cost_basis),
                "market_value": _r(acc_value),
            })
        holdings.append({
            "instrument_id": iid, "isin": inst.isin, "symbol": inst.symbol, "name": inst.name,
            "type": inst.type.value, "quantity": _r(qty, 8),
            "avg_cost": _r(cost / qty if qty else 0.0, 4), "cost_basis": _r(cost),
            "price": _r(price, 4) if price is not None else None, "price_currency": cur,
            "price_eur": _r(price_eur, 4), "price_source": source,
            "market_value": _r(market_value),
            "day_change": _r(day_change),
            "day_change_pct": _r((price_eur / prev_eur - 1) * 100 if prev_eur else 0.0, 4),
            "unrealized_gain": _r(market_value - cost),
            "unrealized_gain_pct": _r((market_value / cost - 1) * 100 if cost > 0 else 0.0, 4),
            "weight": 0.0,
            "dividends_received": _r(dividends),
            "realized_gain": _r(sum((p.realized_gain for p in plist), ZERO)),
            "sector": inst.sector, "country": inst.country, "currency": inst.currency,
            "accounts": accounts,
        })
    total = sum(h["market_value"] for h in holdings)
    by_id = {h["instrument_id"]: h for h in holdings}
    for h in holdings:
        h["weight"] = _r(h["market_value"] / total * 100 if total else 0.0, 4)
        h["unbooked_splits"] = []
        h["missing_cost"] = []
    account_names = dict(db.execute(select(Account.id, Account.name)).all())
    for m in fifo.missing_cost:
        h = by_id.get(m["instrument_id"])
        if h is None or (account_id is not None and m["account_id"] != account_id):
            continue
        h["missing_cost"].append(m)
        warnings.append(f"Einstand fehlt: {h['name']} wurde am {m['date']} ohne Kurs in "
                        f"{account_names.get(m['account_id'], 'ein Depot')} eingeliefert – Einstandskurs an der Buchung nachtragen")
    for split in unbooked_splits(db, per_inst):
        h = by_id.get(split["instrument_id"])
        if h is None:
            continue
        h["unbooked_splits"].append(split)
        warnings.append(f"Split nicht gebucht: {h['name']} {split['title']} vom {split['date']} – "
                        "Bestand und Wert stimmen erst, wenn der Split gebucht ist")
    holdings.sort(key=lambda h: h["market_value"], reverse=True)
    if fx.missing:
        warnings.append("Fehlende Wechselkurse (1.0 angenommen): " + ", ".join(sorted(fx.missing)))
    return Valuation(holdings, dict(by_account), warnings, bool(fx.missing), fifo, updated_at)


def unbooked_splits(db: Session, per_inst: dict[int, list[Position]]) -> list[dict]:
    """Split events of the data provider that the ledger does not know about.

    Yahoo prices are back-adjusted: after a 10:1 split every old price is a tenth, so a position bought before
    the split shows a tenth of its value until a `split` transaction multiplies the quantity. A split is
    "unbooked" when shares bought BEFORE the split date are still held in a depot and that depot has no `split`
    booking within ±SPLIT_MATCH_DAYS of the event.
    """
    open_lots: dict[int, list[tuple[int, date]]] = {}
    for iid, plist in per_inst.items():
        firsts = [(p.account_id, min(lot.date for lot in p.lots)) for p in plist if p.quantity > EPS and p.lots]
        if firsts:
            open_lots[iid] = firsts
    if not open_lots:
        return []
    today = date.today()
    rows = db.execute(text(
        "SELECT id, instrument_id, date, amount, title FROM instrument_events "
        "WHERE type::text = 'split' AND instrument_id = ANY(:ids) AND date <= :today AND amount IS NOT NULL "
        "AND amount > 0 AND amount <> 1 ORDER BY date"), {"ids": list(open_lots), "today": today}).all()
    if not rows:
        return []
    booked: dict[tuple[int, int], list[date]] = defaultdict(list)
    for acc, iid, when in db.execute(
            select(Transaction.account_id, Transaction.instrument_id, Transaction.booking_date)
            .where(Transaction.type == TxType.split, Transaction.instrument_id.in_(list(open_lots)))):
        booked[(acc, iid)].append(when)
    out = []
    for ev in rows:
        accounts = [acc for acc, first in open_lots[ev.instrument_id]
                    if first < ev.date
                    and not any(abs((d - ev.date).days) <= SPLIT_MATCH_DAYS for d in booked[(acc, ev.instrument_id)])]
        if accounts:
            ratio = float(ev.amount)
            out.append({"event_id": ev.id, "instrument_id": ev.instrument_id, "date": ev.date.isoformat(),
                        "ratio": ratio, "title": ev.title or f"Split {ratio:g}:1", "account_ids": accounts})
    return out


def book_split(db: Session, instrument_id: int, when: date, ratio: Decimal) -> list[Transaction]:
    """Book a split for every depot that held the instrument before `when` and has no split booking near that
    date (flushes, does NOT commit). Returns the created transactions."""
    fifo = compute_fifo(db, until=when - timedelta(days=1))
    booked = {acc: d for acc, d in db.execute(
        select(Transaction.account_id, Transaction.booking_date)
        .where(Transaction.type == TxType.split, Transaction.instrument_id == instrument_id))
        if abs((d - when).days) <= SPLIT_MATCH_DAYS}
    created = []
    for (acc, iid), pos in fifo.positions.items():
        if iid != instrument_id or pos.quantity <= EPS or acc in booked:
            continue
        created.append(ledger.create_transaction(db, {
            "account_id": acc, "type": TxType.split, "instrument_id": instrument_id, "booking_date": when,
            "quantity": ratio, "description": f"Split {ratio.normalize():f}:1" if ratio >= 1 else f"Reverse Split 1:{(1 / ratio).normalize():f}",
        }))
    return created


def account_values(db: Session, valuation: Valuation | None = None) -> dict[int, dict[str, float]]:
    """{account_id: {balance, market_value, total_value}} – balance in account currency, rest EUR."""
    valuation = valuation or compute_valuation(db)
    balances = ledger.cash_balances(db)
    fx = Fx(db)
    currencies = dict(db.execute(select(Account.id, Account.currency)).all())
    out = {}
    for acc_id, bal in balances.items():
        mv = valuation.by_account_value.get(acc_id, 0.0)
        bal_eur = fx.to_eur(float(bal), currencies.get(acc_id))
        out[acc_id] = {"balance": _r(bal), "balance_eur": _r(bal_eur), "market_value": _r(mv),
                       "total_value": _r(bal_eur + mv)}
    return out


def position_for_instrument(db: Session, instrument_id: int) -> dict | None:
    for h in compute_valuation(db).holdings:
        if h["instrument_id"] == instrument_id:
            return h
    return None


# --------------------------------------------------------------------------- summary
def compute_summary(db: Session) -> dict:
    today = date.today()
    val = compute_valuation(db)
    values = account_values(db, val)
    accounts = db.scalars(select(Account).order_by(Account.sort_order, Account.id)).all()
    cash_total = sum(v["balance_eur"] for v in values.values())
    invested = sum(h["market_value"] for h in val.holdings)
    cost = sum(h["cost_basis"] for h in val.holdings)
    net_worth = cash_total + invested
    day_change = sum(h["day_change"] for h in val.holdings)
    year_start = date(today.year, 1, 1)
    realized_ytd = sum((g.gain for g in val.fifo.realized if g.date >= year_start), ZERO)
    sums = dict(db.execute(
        select(Transaction.type, func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.booking_date >= year_start, Transaction.type.in_([TxType.dividend, TxType.interest]))
        .group_by(Transaction.type)
    ).all())
    prev = net_worth - day_change
    return {
        "net_worth": _r(net_worth), "cash_total": _r(cash_total), "invested_value": _r(invested),
        "cost_basis": _r(cost), "unrealized_gain": _r(invested - cost),
        "unrealized_gain_pct": _r((invested / cost - 1) * 100 if cost > 0 else 0.0, 4),
        "realized_gain_ytd": _r(realized_ytd),
        "dividends_ytd": _r(sums.get(TxType.dividend, 0)), "interest_ytd": _r(sums.get(TxType.interest, 0)),
        "day_change": _r(day_change), "day_change_pct": _r(day_change / prev * 100 if prev > 0 else 0.0, 4),
        "by_account": [
            {"account_id": a.id, "name": a.name, "type": a.type.value, "institution": a.institution,
             "color": a.color, "value": values[a.id]["total_value"], "balance": values[a.id]["balance_eur"],
             "market_value": values[a.id]["market_value"]}
            for a in accounts if a.is_active
        ],
        "updated_at": (val.updated_at or datetime.now(timezone.utc)).isoformat(),
        "fx_warning": val.fx_warning, "warnings": val.warnings,
        "unbooked_splits": [dict(sp, name=h["name"]) for h in val.holdings for sp in h["unbooked_splits"]],
        "missing_cost": [dict(m, name=h["name"]) for h in val.holdings for m in h["missing_cost"]],
    }


# --------------------------------------------------------------------------- daily series
_SEC_TYPES = {t.value for t in ledger.SECURITY_TYPES}
_EXTERNAL_CASH = {"deposit", "withdrawal", "transfer_in", "transfer_out"}
_QTY_SIGN = {"buy": 1.0, "delivery_in": 1.0, "sell": -1.0, "delivery_out": -1.0}


def _load_tx_frame(db: Session) -> pd.DataFrame:
    rows = db.execute(
        select(
            Transaction.id, Transaction.account_id, Transaction.cash_account_id, Transaction.instrument_id,
            Transaction.type, Transaction.booking_date,
            cast(Transaction.quantity, Float), cast(Transaction.price, Float), cast(Transaction.amount, Float),
            Transaction.currency, cast(Transaction.fx_rate, Float),
        ).order_by(Transaction.booking_date, Transaction.id)
    ).all()
    df = pd.DataFrame(
        [tuple(r) for r in rows],
        columns=["id", "account_id", "cash_account_id", "instrument_id", "type", "date", "quantity", "price",
                 "amount", "currency", "fx_rate"],
    )
    if df.empty:
        return df
    df["type"] = df["type"].map(lambda t: t.value if isinstance(t, TxType) else str(t))
    df["date"] = pd.to_datetime(df["date"])
    df["cash_acc"] = df["cash_account_id"].fillna(df["account_id"]).astype("int64")
    for col in ("quantity", "price", "amount", "fx_rate"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["amount"] = df["amount"].fillna(0.0)
    return df


def _apply_split_adjustment(sec: pd.DataFrame) -> pd.DataFrame:
    """Scale quantities/prices before a split so they match split-adjusted price history."""
    splits = sec[(sec["type"] == "split") & (sec["quantity"] > 0)]
    if splits.empty:
        return sec
    sec = sec.copy()
    factor = pd.Series(1.0, index=sec.index)
    for s in splits.itertuples():
        mask = (
            (sec["account_id"] == s.account_id) & (sec["instrument_id"] == s.instrument_id)
            & ((sec["date"] < s.date) | ((sec["date"] == s.date) & (sec["id"] < s.id)))
        )
        factor[mask] *= float(s.quantity)
    not_split = sec["type"] != "split"
    sec.loc[not_split, "quantity"] = sec.loc[not_split, "quantity"] * factor[not_split]
    sec.loc[not_split, "price"] = sec.loc[not_split, "price"] / factor[not_split]
    return sec


def _empty_series() -> pd.DataFrame:
    return pd.DataFrame(columns=["cash", "invested", "net_worth", "cost_basis", "flow"], dtype=float)


def daily_series(
    db: Session, start: date | None = None, end: date | None = None,
    account_ids: set[int] | None = None, valuation: Valuation | None = None,
) -> pd.DataFrame:
    """Daily (calendar days) cash / invested / net_worth / cost_basis / external flow in EUR.

    `account_ids` restricts the scope; flows crossing the scope boundary count as external.
    The frame starts one day before `start` (base value for performance) when data exists.
    """
    end = end or date.today()
    accounts = db.execute(
        select(Account.id, Account.type, cast(Account.opening_balance, Float), Account.opening_date)
    ).all()
    scope = {a.id for a in accounts} if account_ids is None else set(account_ids)
    tx = _load_tx_frame(db)
    end_ts = pd.Timestamp(end)
    if not tx.empty:
        tx = tx[tx["date"] <= end_ts]

    # opening balances as dated events
    first_tx_by_acc = tx.groupby("cash_acc")["date"].min().to_dict() if not tx.empty else {}
    global_first = tx["date"].min() if not tx.empty else None
    openings: list[tuple[pd.Timestamp, float]] = []
    for a in accounts:
        if a.id not in scope or not a[2]:
            continue
        od = pd.Timestamp(a.opening_date) if a.opening_date else first_tx_by_acc.get(a.id, global_first)
        if od is None:
            od = end_ts
        openings.append((min(od, end_ts), float(a[2])))
    candidates = [d for d, _ in openings]
    if not tx.empty:
        candidates.append(tx["date"].min())
    if not candidates:
        return _empty_series()
    first = min(candidates)
    idx = pd.date_range(first, end_ts, freq="D")

    # ---- cash + external cash flows
    cash_delta = pd.Series(0.0, index=idx)
    flow = pd.Series(0.0, index=idx)
    for d, amount in openings:
        cash_delta[d] += amount
        flow[d] += amount
    sec = pd.DataFrame()
    if not tx.empty:
        in_cash = tx["cash_acc"].isin(scope)
        in_sec = tx["account_id"].isin(scope)
        has_inst = tx["instrument_id"].notna()
        cash_delta = cash_delta.add(tx[in_cash].groupby("date")["amount"].sum(), fill_value=0.0)
        ext = tx[in_cash & tx["type"].isin(_EXTERNAL_CASH)].groupby("date")["amount"].sum()
        flow = flow.add(ext, fill_value=0.0)
        # security side inside scope, cash side outside: money flows in (buy) / out (sell, dividend)
        cross_in = tx[has_inst & in_sec & ~in_cash].groupby("date")["amount"].sum()
        flow = flow.sub(cross_in, fill_value=0.0)
        # cash side inside scope, security outside: trade cash is external for this scope
        cross_out = tx[has_inst & ~in_sec & in_cash].groupby("date")["amount"].sum()
        flow = flow.add(cross_out, fill_value=0.0)
        sec = tx[has_inst & in_sec & tx["type"].isin(_SEC_TYPES)]
    cash = cash_delta.reindex(idx, fill_value=0.0).cumsum()
    flow = flow.reindex(idx, fill_value=0.0)

    out_start = idx[0]
    if start is not None:
        out_start = max(idx[0], pd.Timestamp(start) - pd.Timedelta(days=1))
    out_idx = idx[idx >= out_start]

    invested = pd.Series(0.0, index=out_idx)
    cost_basis = pd.Series(0.0, index=out_idx)
    if not sec.empty:
        sec = _apply_split_adjustment(sec)
        sec = sec[sec["type"] != "split"].copy()
        sec["instrument_id"] = sec["instrument_id"].astype("int64")
        sec["signed"] = sec["quantity"].fillna(0.0) * sec["type"].map(_QTY_SIGN)
        # running quantity per depot x instrument. A sale beyond the holding ends at 0 (like the FIFO valuation and
        # the summary, which warn about it) – never at a negative quantity that would show a negative depot value.
        per_pos = (
            sec.pivot_table(index="date", columns=["account_id", "instrument_id"], values="signed", aggfunc="sum")
            .reindex(idx).fillna(0.0)
        )
        running = per_pos.cumsum()
        for col in running.columns[(running.min() < -1e-9).to_numpy()]:
            level, values = 0.0, []
            for delta in per_pos[col].to_numpy():
                level = max(0.0, level + delta)
                values.append(level)
            running[col] = values
        qty = running.T.groupby(level="instrument_id").sum().T
        qty = qty.where(qty.abs() > 1e-9, 0.0).loc[out_idx]
        inst_ids = [int(c) for c in qty.columns]
        inst_cur = dict(db.execute(select(Instrument.id, Instrument.currency).where(Instrument.id.in_(inst_ids))).all())

        # ---- fx matrix (currency per EUR), forward filled
        fx_rows = db.execute(select(FxRate.date, FxRate.currency, cast(FxRate.rate, Float))).all()
        if fx_rows:
            fxdf = pd.DataFrame([tuple(r) for r in fx_rows], columns=["date", "currency", "rate"])
            fxdf["date"] = pd.to_datetime(fxdf["date"])
            fxm = fxdf.pivot_table(index="date", columns="currency", values="rate", aggfunc="last")
            fxm = fxm.reindex(fxm.index.union(out_idx)).sort_index().ffill().bfill().reindex(out_idx)
        else:
            fxm = pd.DataFrame(index=out_idx)

        def fx_col(currency: str | None) -> pd.Series | float:
            cur = (currency or BASE).upper() if currency not in _MINOR else _MINOR[currency][0]
            if cur == BASE or cur not in fxm.columns:
                return 1.0
            return fxm[cur]

        # ---- price matrix in instrument currency
        p_start = (out_idx[0] - pd.Timedelta(days=14)).date()
        # raw DBAPI cursor: plain tuples are ~10x faster to load than SQLAlchemy rows for >100k prices
        cur = db.connection().connection.cursor()
        try:
            cur.execute(
                "SELECT instrument_id, date, close::float8 FROM price_history "
                "WHERE instrument_id = ANY(%(ids)s) AND date >= %(start)s AND date <= %(end)s",
                {"ids": inst_ids, "start": p_start, "end": end},
            )
            ph_rows = cur.fetchall()
        finally:
            cur.close()
        if ph_rows:
            col_ids, col_dates, col_close = zip(*ph_rows)
            ph = pd.DataFrame({
                "instrument_id": np.fromiter(col_ids, dtype="int64", count=len(col_ids)),
                "date": pd.to_datetime(np.array(col_dates, dtype="datetime64[D]")),
                "close": np.fromiter(col_close, dtype="float64", count=len(col_close)),
            })
            prices = ph.pivot(index="date", columns="instrument_id", values="close")
            prices = prices.reindex(prices.index.union(out_idx)).sort_index().ffill()
        else:
            prices = pd.DataFrame(index=out_idx)
        prices = prices.reindex(index=out_idx, columns=qty.columns)

        # fallback: transaction prices (converted into the instrument currency)
        txp = sec[sec["price"].notna() & (sec["price"] > 0)]
        if not txp.empty and prices.isna().any().any():
            txp = txp.copy()
            is_eur = txp["currency"].fillna(BASE).str.upper() == BASE
            txp["price_eur"] = np.where(is_eur, txp["price"], txp["price"] / txp["fx_rate"].where(txp["fx_rate"] > 0, 1.0))
            tpm = txp.pivot_table(index="date", columns="instrument_id", values="price_eur", aggfunc="last")
            tpm = tpm.reindex(idx).ffill().bfill().reindex(index=out_idx, columns=qty.columns)
            for col in qty.columns:
                if prices[col].isna().any() and col in tpm.columns:
                    prices[col] = prices[col].fillna(tpm[col] * fx_col(inst_cur.get(int(col))))
        prices = prices.bfill().fillna(0.0)

        prices_eur = prices.copy()
        for col in prices_eur.columns:
            rate = fx_col(inst_cur.get(int(col)))
            if not isinstance(rate, float):
                prices_eur[col] = prices_eur[col] / rate
            cur = inst_cur.get(int(col))
            if cur in _MINOR:
                prices_eur[col] = prices_eur[col] / _MINOR[cur][1]
        values = qty * prices_eur

        # today's row uses the live valuation (quotes) so history and summary agree
        if end >= date.today():
            valuation = valuation or compute_valuation(db)
            live = {h["instrument_id"]: h for h in valuation.holdings}
            for col in values.columns:
                h = live.get(int(col))
                if h is not None and h.get("price") is not None and qty.at[out_idx[-1], col] != 0:
                    values.at[out_idx[-1], col] = qty.at[out_idx[-1], col] * h["price_eur"]
        invested = values.sum(axis=1)

        # deliveries in/out are external flows valued at the day's price
        deliveries = sec[sec["type"].isin(["delivery_in", "delivery_out"]) & (sec["date"] >= out_idx[0])]
        for d in deliveries.itertuples():
            price = prices_eur.at[d.date, d.instrument_id] if d.date in prices_eur.index else 0.0
            flow[d.date] += d.signed * price

        fifo = valuation.fifo if valuation is not None else compute_fifo(db, until=end)
        events = [(pd.Timestamp(d), delta) for d, acc, _iid, delta in fifo.cost_events if acc in scope and d <= end]
        if events:
            ce = pd.DataFrame(events, columns=["date", "delta"]).groupby("date")["delta"].sum()
            cost_basis = ce.reindex(idx, fill_value=0.0).cumsum().loc[out_idx]

    out = pd.DataFrame({
        "cash": cash.loc[out_idx], "invested": invested, "cost_basis": cost_basis, "flow": flow.loc[out_idx],
    })
    out["net_worth"] = out["cash"] + out["invested"]
    return out


def _scope_for_account(db: Session, account_id: int | None) -> set[int] | None:
    return None if account_id is None else {account_id}


def investment_scope(db: Session) -> set[int]:
    return set(db.scalars(select(Account.id).where(Account.type == AccountType.brokerage)))


def compute_history(db: Session, range_: str | None = "1Y", account_id: int | None = None) -> list[dict]:
    start = range_start(range_)
    df = daily_series(db, start=start, account_ids=_scope_for_account(db, account_id))
    if df.empty:
        return []
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    df = df.round(2)
    dates = [d.date().isoformat() for d in df.index]
    return [
        {"date": d, "net_worth": nw, "invested": inv, "cost_basis": cb, "cash": c}
        for d, nw, inv, cb, c in zip(dates, df["net_worth"].tolist(), df["invested"].tolist(),
                                     df["cost_basis"].tolist(), df["cash"].tolist())
    ]


def account_history(db: Session, account_id: int, days: int = 730) -> list[dict]:
    start = date.today() - timedelta(days=days)
    df = daily_series(db, start=start, account_ids={account_id})
    if df.empty:
        return []
    df = df[df.index >= pd.Timestamp(start)]
    return [{"date": d.date().isoformat(), "value": v} for d, v in zip(df.index, df["net_worth"].round(2).tolist())]


def compute_performance(
    db: Session, range_: str | None = "1Y", account_id: int | None = None, scope: str = "all",
) -> dict:
    """TWR (daily, cash-flow adjusted) and XIRR. Percent values (12.3 = 12.3 %)."""
    start = range_start(range_)
    if account_id is not None:
        account_ids: set[int] | None = {account_id}
    elif scope == "investments":
        account_ids = investment_scope(db)
    else:
        account_ids = None
    df = daily_series(db, start=start, account_ids=account_ids)
    empty = {"twr": 0.0, "xirr": None, "absolute_gain": 0.0, "series": [], "start_value": 0.0, "end_value": 0.0,
             "net_flows": 0.0, "range": (range_ or "1Y").upper(), "scope": scope}
    if df.empty:
        return empty
    base_value = 0.0
    if start is not None and df.index[0] < pd.Timestamp(start):
        base_value = float(df["net_worth"].iloc[0])
        base_date = df.index[0].date()
        df = df.iloc[1:]
    else:
        base_date = (df.index[0] - pd.Timedelta(days=1)).date()
    if df.empty:
        return empty
    factor = twr_series(df["net_worth"], df["flow"], base_value)
    end_value = float(df["net_worth"].iloc[-1])
    net_flows = float(df["flow"].sum())
    flows: list[tuple[date, float]] = []
    if base_value:
        flows.append((base_date, -base_value))
    nz = df["flow"][df["flow"].abs() > 1e-9]
    flows.extend((d.date(), -float(v)) for d, v in nz.items())
    flows.append((df.index[-1].date(), end_value))
    rate = xirr(flows)
    return {
        "twr": _r((float(factor.iloc[-1]) - 1) * 100, 4),
        "xirr": _r(rate * 100, 4) if rate is not None and abs(rate) < 1e4 else None,
        "absolute_gain": _r(end_value - base_value - net_flows),
        "series": [
            {"date": d.date().isoformat(), "twr_index": i, "twr": t}
            for d, i, t in zip(factor.index, (factor * 100).round(4).tolist(), ((factor - 1) * 100).round(4).tolist())
        ],
        "start_value": _r(base_value), "end_value": _r(end_value), "net_flows": _r(net_flows),
        "range": (range_ or "1Y").upper(), "scope": scope if account_id is None else "account",
    }


# --------------------------------------------------------------------------- allocation
_UNKNOWN = "Sonstige"
_TYPE_LABEL_ETF = "ETF"


def _direct_dimension(h: dict, by: str) -> str:
    value = h.get(by)
    if value:
        return str(value)
    return _TYPE_LABEL_ETF if h["type"] in ("etf", "fund") else _UNKNOWN


def _table_lookthrough(db: Session, holdings: list[dict], by: str) -> dict[str, float]:
    """Sector/country split using the etf_holdings table directly (fallback implementation)."""
    etf_ids = [h["instrument_id"] for h in holdings if h["type"] in ("etf", "fund")]
    rows: dict[int, list[tuple[float, str | None]]] = defaultdict(list)
    if etf_ids:
        col = "sector" if by == "sector" else "country"
        for etf_id, weight, key in db.execute(text(
            f"SELECT etf_instrument_id, weight, {col} FROM etf_holdings WHERE etf_instrument_id = ANY(:ids)"
        ), {"ids": etf_ids}):
            rows[etf_id].append((float(weight), key))
    buckets: dict[str, float] = defaultdict(float)
    for h in holdings:
        value = h["market_value"]
        parts = rows.get(h["instrument_id"])
        if not parts:
            buckets[_direct_dimension(h, by)] += value
            continue
        total_w = sum(w for w, _ in parts)
        scale = 100.0 / total_w if total_w > 100.0 else 1.0
        for w, key in parts:
            buckets[key or _UNKNOWN] += value * w * scale / 100.0
        rest = value * max(0.0, 100.0 - total_w) / 100.0
        if rest > 0.005:
            buckets[f"{_TYPE_LABEL_ETF} ({_UNKNOWN.lower()})"] += rest
    return buckets


def _market_lookthrough(db: Session, by: str, account_id: int | None) -> dict[str, float] | None:
    """Use the market-data agent's look-through if available."""
    try:
        from app.market.lookthrough import compute_lookthrough  # type: ignore
    except ImportError:
        return None
    try:
        try:
            result = compute_lookthrough(db, account_id=account_id)
        except TypeError:
            result = compute_lookthrough(db)
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        items = result.get("by_sector" if by == "sector" else "by_country") if isinstance(result, dict) else None
        if not items:
            return None
        buckets: dict[str, float] = defaultdict(float)
        for item in items:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            key = item.get("key") or item.get("name") or item.get(by) or _UNKNOWN
            value = item.get("value")
            if value is None:
                value = item.get("total_value")
            if value is None:
                return None
            buckets[str(key)] += float(value)
        return buckets if buckets else None
    except Exception:  # noqa: BLE001 - foreign module must never break the allocation endpoint
        return None


def compute_allocation(
    db: Session, by: str = "type", lookthrough: bool = False, account_id: int | None = None,
) -> list[dict]:
    val = compute_valuation(db, account_id)
    buckets: dict[str, float] = defaultdict(float)
    if by in ("sector", "country"):
        looked = None
        if lookthrough:
            looked = _market_lookthrough(db, by, account_id) or _table_lookthrough(db, val.holdings, by)
        if looked:
            buckets.update(looked)
        else:
            for h in val.holdings:
                buckets[_direct_dimension(h, by)] += h["market_value"]
    else:
        values = account_values(db, val)
        accounts = {a.id: a for a in db.scalars(select(Account))}
        if account_id is not None:
            values = {k: v for k, v in values.items() if k == account_id}
        if by == "type":
            for h in val.holdings:
                buckets[h["type"]] += h["market_value"]
            buckets["cash"] += sum(v["balance_eur"] for v in values.values())
        elif by == "currency":
            for h in val.holdings:
                buckets[(h.get("currency") or BASE).upper()] += h["market_value"]
            for acc_id, v in values.items():
                buckets[(accounts[acc_id].currency or BASE).upper()] += v["balance_eur"]
        elif by == "account":
            for acc_id, v in values.items():
                buckets[accounts[acc_id].name] += v["total_value"]
        else:
            raise ValueError(f"unknown allocation dimension {by!r}")
    items = [(k, v) for k, v in buckets.items() if v > 0.005]
    total = sum(v for _, v in items)
    items.sort(key=lambda kv: kv[1], reverse=True)
    return [{"key": k, "value": _r(v), "weight": _r(v / total * 100 if total else 0.0, 4)} for k, v in items]
