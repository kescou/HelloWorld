"""Analytics: dividends, cashflow (Haushaltsbuch), expenses by category, wealth forecast, realized gains.

Percent convention: savings_rate, share, yields are percent values (12.3 = 12.3 %).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.core import Account, AccountType, Category, CategoryKind, Instrument, Transaction, TxType
from app.services import portfolio
from app.services.portfolio import _r

#: transaction types that count as household income/expense on cash accounts
HOUSEHOLD_TYPES = [TxType.deposit, TxType.withdrawal, TxType.interest, TxType.dividend, TxType.fee, TxType.tax]


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, 1)


def _month_range(first: date, count: int) -> list[str]:
    return [_month_key(_add_months(first, i)) for i in range(count)]


# --------------------------------------------------------------------------- dividends
def _forecast_from_events(db: Session, holdings: dict[int, dict], fx: portfolio.Fx, today: date, horizon: date):
    """Forecast items from instrument_events (ex_dividend / dividend_payment incl. estimates)."""
    if not holdings:
        return [], set()
    rows = db.execute(text(
        "SELECT instrument_id, type::text, date, amount, currency, is_estimate FROM instrument_events "
        "WHERE instrument_id = ANY(:ids) AND type::text IN ('ex_dividend', 'dividend_payment') "
        "AND date >= :start AND date <= :end ORDER BY instrument_id, date"
    ), {"ids": list(holdings), "start": today - timedelta(days=60), "end": horizon + timedelta(days=60)}).all()
    by_inst: dict[int, dict[str, list]] = defaultdict(lambda: {"ex_dividend": [], "dividend_payment": []})
    for r in rows:
        by_inst[r.instrument_id][r[1]].append(r)
    items: list[dict] = []
    covered: set[int] = set()
    for iid, groups in by_inst.items():
        h = holdings[iid]
        pays = list(groups["dividend_payment"])
        used: set[int] = set()
        pairs: list[tuple] = []  # (ex_row | None, pay_row | None)
        for ex in groups["ex_dividend"]:
            match = None
            for i, pay in enumerate(pays):
                if i not in used and timedelta(0) <= pay.date - ex.date <= timedelta(days=75):
                    match = i
                    break
            if match is not None:
                used.add(match)
                pairs.append((ex, pays[match]))
            else:
                pairs.append((ex, None))
        pairs.extend((None, pay) for i, pay in enumerate(pays) if i not in used)
        for ex, pay in pairs:
            ref = pay.date if pay is not None else ex.date
            if not (today < ref <= horizon):
                # an ex-date in the past with an outstanding payment still counts via `ref`
                continue
            src = next((e for e in (pay, ex) if e is not None and e.amount is not None), None)
            if src is None:
                continue
            currency = src.currency or h.get("currency") or "EUR"
            amount = fx.to_eur(float(src.amount) * h["quantity"], currency)
            covered.add(iid)
            items.append({
                "instrument_id": iid, "name": h["name"], "symbol": h.get("symbol"),
                "amount": _r(amount), "per_share": _r(src.amount, 6), "currency": currency,
                "quantity": h["quantity"],
                "ex_date": ex.date.isoformat() if ex is not None else None,
                "pay_date": pay.date.isoformat() if pay is not None else None,
                "is_estimate": bool((ex is not None and ex.is_estimate) or (pay is not None and pay.is_estimate)),
                "source": "events", "_month": _month_key(ref),
            })
    return items, covered


def _forecast_from_history(db: Session, holdings: dict[int, dict], skip: set[int], today: date) -> list[dict]:
    """Extrapolate the dividends received in the last 12 months one year into the future."""
    ids = [i for i in holdings if i not in skip]
    if not ids:
        return []
    rows = db.execute(
        select(Transaction.instrument_id, Transaction.booking_date, Transaction.quantity, Transaction.amount,
               Transaction.taxes)
        .where(Transaction.type == TxType.dividend, Transaction.instrument_id.in_(ids),
               Transaction.booking_date > today - timedelta(days=365), Transaction.booking_date <= today)
        .order_by(Transaction.booking_date)
    ).all()
    items = []
    for iid, booked, qty, amount, taxes in rows:
        h = holdings[iid]
        gross = float(amount or 0) + float(taxes or 0)
        if qty and float(qty) > 0:
            gross = gross / float(qty) * h["quantity"]
        try:
            pay = booked.replace(year=booked.year + 1)
        except ValueError:  # 29 Feb
            pay = booked + timedelta(days=365)
        items.append({
            "instrument_id": iid, "name": h["name"], "symbol": h.get("symbol"), "amount": _r(gross),
            "per_share": _r(gross / h["quantity"], 6) if h["quantity"] else None, "currency": "EUR",
            "quantity": h["quantity"], "ex_date": None, "pay_date": pay.isoformat(), "is_estimate": True,
            "source": "extrapolated", "_month": _month_key(pay),
        })
    return items


def dividends(db: Session, year: int | None = None) -> dict:
    today = date.today()
    year = year or today.year
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount, Transaction.taxes)
        .where(Transaction.type == TxType.dividend)
    ).all()
    by_month: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    by_year: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for booked, amount, taxes in rows:
        net, tax = float(amount or 0), float(taxes or 0)
        for bucket in (by_month[_month_key(booked)], by_year[booked.year]):
            bucket[0] += net
            bucket[1] += tax
    received_by_month = [
        {"month": m, "amount": _r(by_month[m][0]), "gross": _r(by_month[m][0] + by_month[m][1]),
         "taxes": _r(by_month[m][1])}
        for m in _month_range(date(year, 1, 1), 12)
    ]
    received_by_year = [
        {"year": y, "amount": _r(v[0]), "gross": _r(v[0] + v[1]), "taxes": _r(v[1])}
        for y, v in sorted(by_year.items())
    ]

    val = portfolio.compute_valuation(db)
    holdings = {h["instrument_id"]: h for h in val.holdings}
    fx = portfolio.Fx(db)
    horizon = _add_months(today, 12) + timedelta(days=today.day - 1)
    items, covered = _forecast_from_events(db, holdings, fx, today, horizon)
    items += _forecast_from_history(db, holdings, covered, today)
    months = _month_range(date(today.year, today.month, 1), 13)
    grouped: dict[str, list[dict]] = {m: [] for m in months}
    for item in items:
        month = item.pop("_month")
        if month in grouped:
            grouped[month].append(item)
    forecast = []
    for m in months:
        entries = sorted(grouped[m], key=lambda i: (i["pay_date"] or i["ex_date"] or "", i["name"]))
        if m == months[-1] and not entries:
            continue
        forecast.append({"month": m, "amount": _r(sum(i["amount"] for i in entries)), "items": entries})
    total = sum(f["amount"] for f in forecast)
    cost = sum(h["cost_basis"] for h in val.holdings)
    value = sum(h["market_value"] for h in val.holdings)
    return {
        "year": year,
        "received_by_month": received_by_month,
        "received_by_year": received_by_year,
        "forecast_12m": forecast,
        "forecast_12m_total": _r(total),
        "yield_on_cost": _r(total / cost * 100 if cost > 0 else 0.0, 4),
        "portfolio_yield": _r(total / value * 100 if value > 0 else 0.0, 4),
    }


# --------------------------------------------------------------------------- household cashflow
def _household_rows(db: Session, date_from: date | None, date_to: date | None):
    """Income/expense rows on cash accounts: no own-account transfers, no securities trades."""
    cash_acc = func.coalesce(Transaction.cash_account_id, Transaction.account_id)
    stmt = (
        select(Transaction.booking_date, Transaction.amount, Transaction.category_id, Transaction.type)
        .join(Account, Account.id == cash_acc)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(
            Transaction.type.in_(HOUSEHOLD_TYPES),
            Account.type != AccountType.brokerage,
            (Category.id.is_(None)) | (Category.kind.in_([CategoryKind.income, CategoryKind.expense])),
        )
    )
    if date_from is not None:
        stmt = stmt.where(Transaction.booking_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.booking_date <= date_to)
    return db.execute(stmt).all()


def cashflow(db: Session, months: int = 12) -> list[dict]:
    months = max(1, min(int(months), 240))
    today = date.today()
    first = _add_months(date(today.year, today.month, 1), -(months - 1))
    income: dict[str, float] = defaultdict(float)
    expenses: dict[str, float] = defaultdict(float)
    for booked, amount, _cat, _type in _household_rows(db, first, None):
        value = float(amount or 0)
        if value >= 0:
            income[_month_key(booked)] += value
        else:
            expenses[_month_key(booked)] -= value
    out = []
    for m in _month_range(first, months):
        inc, exp = income.get(m, 0.0), expenses.get(m, 0.0)
        out.append({
            "month": m, "income": _r(inc), "expenses": _r(exp), "savings": _r(inc - exp),
            "savings_rate": _r(max(-100.0, (inc - exp) / inc * 100) if inc > 0 else 0.0, 2),
        })
    return out


def average_monthly_savings(db: Session, months: int = 12) -> float:
    """Average savings of the last `months` complete months (falls back to months with data)."""
    rows = cashflow(db, months + 1)[:-1]  # skip the running month
    rows = [r for r in rows if r["income"] or r["expenses"]]
    if not rows:
        return 0.0
    return sum(r["savings"] for r in rows) / len(rows)


def expenses_by_category(db: Session, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
    today = date.today()
    if date_from is None and date_to is None:
        date_from = _add_months(date(today.year, today.month, 1), -11)
    categories = {c.id: c for c in db.scalars(select(Category))}
    sums: dict[int | None, float] = defaultdict(float)
    counts: dict[int | None, int] = defaultdict(int)
    for _booked, amount, cat_id, _type in _household_rows(db, date_from, date_to):
        value = float(amount or 0)
        cat = categories.get(cat_id)
        if cat is not None and cat.kind == CategoryKind.income:
            continue
        if cat is None and value >= 0:
            continue  # uncategorised income
        sums[cat_id] -= value  # refunds inside an expense category reduce it
        counts[cat_id] += 1
    items = [(k, v) for k, v in sums.items() if v > 0.005]
    total = sum(v for _, v in items)
    items.sort(key=lambda kv: kv[1], reverse=True)
    out = []
    for cat_id, value in items:
        cat = categories.get(cat_id)
        parent = categories.get(cat.parent_id) if cat is not None and cat.parent_id else None
        out.append({
            "category_id": cat_id, "name": cat.name if cat else "Ohne Kategorie",
            "amount": _r(value), "share": _r(value / total * 100 if total else 0.0, 2),
            "color": cat.color if cat else None,
            "parent_id": parent.id if parent else None, "parent_name": parent.name if parent else None,
            "count": counts[cat_id],
        })
    return out


# --------------------------------------------------------------------------- forecast
def _as_fraction(value: float) -> float:
    """Accept 6 (percent) as well as 0.06 (fraction)."""
    return value / 100.0 if abs(value) > 1.0 else value


def wealth_forecast(
    db: Session, years: int = 10, monthly_savings: float | None = None, expected_return: float | None = None,
    volatility: float | None = None, start_value: float | None = None, simulations: int = 5000, seed: int = 42,
) -> dict:
    """Monte-Carlo wealth projection (log-normal monthly returns), percentiles per year."""
    years = max(1, min(int(years), 60))
    mu = _as_fraction(expected_return) if expected_return is not None else 0.06
    sigma = abs(_as_fraction(volatility)) if volatility is not None else 0.15
    if start_value is None:
        start_value = portfolio.compute_summary(db)["net_worth"]
    if monthly_savings is None:
        monthly_savings = average_monthly_savings(db, 12)
    start_value = float(start_value)
    monthly_savings = float(monthly_savings)
    months = years * 12
    rng = np.random.default_rng(seed)
    sigma_m = sigma / np.sqrt(12.0)
    mu_m = np.log1p(mu) / 12.0 - 0.5 * sigma_m**2
    growth = np.exp(rng.normal(mu_m, sigma_m, size=(simulations, months))) if sigma_m > 0 else np.full(
        (1, months), np.exp(mu_m))
    values = np.full(growth.shape[0], start_value)
    series = [{
        "year": 0, "p10": _r(start_value), "p50": _r(start_value), "p90": _r(start_value),
        "contributions": _r(start_value),
    }]
    for m in range(months):
        values = values * growth[:, m] + monthly_savings
        if (m + 1) % 12 == 0:
            p10, p50, p90 = np.percentile(values, [10, 50, 90])
            y = (m + 1) // 12
            series.append({
                "year": y, "p10": _r(p10), "p50": _r(p50), "p90": _r(p90),
                "contributions": _r(start_value + monthly_savings * (m + 1)),
            })
    return {
        "series": series,
        "start_value": _r(start_value), "monthly_savings": _r(monthly_savings),
        "expected_return": _r(mu * 100, 4), "volatility": _r(sigma * 100, 4), "years": years,
        "start_year": date.today().year,
    }


# --------------------------------------------------------------------------- realized gains
def realized_gains(db: Session, year: int | None = None) -> list[dict]:
    fifo = portfolio.compute_fifo(db)
    gains = [g for g in fifo.realized if year is None or g.date.year == year]
    names = {
        i.id: i for i in db.scalars(select(Instrument).where(Instrument.id.in_({g.instrument_id for g in gains} or {0})))
    }
    out = []
    for g in sorted(gains, key=lambda g: (g.date, g.transaction_id), reverse=True):
        inst = names.get(g.instrument_id)
        out.append({
            "instrument": inst.name if inst else str(g.instrument_id),
            "instrument_id": g.instrument_id, "isin": inst.isin if inst else None,
            "account_id": g.account_id, "transaction_id": g.transaction_id,
            "date": g.date.isoformat(), "quantity": _r(g.quantity, 8),
            "proceeds": _r(g.proceeds), "cost": _r(g.cost), "gain": _r(g.gain), "taxes": _r(g.taxes),
            "fees": _r(g.fees), "gain_after_tax": _r(g.gain - g.taxes),
        })
    return out
