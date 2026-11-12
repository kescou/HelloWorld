"""aktienfinder-style fundamentals: builds the JSON stored in `fundamentals.data`.

Layout is documented in app/market/README.md ("fundamentals.data"). `build_fundamentals` is pure
(no DB / network) so the maths is unit-tested with hand-made inputs.
All ratio-like outputs are PERCENT (payout_ratio, dividend_yield, cagr_*, upside_pct); stabilities are 0..1.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market.fx import get_rate
from app.market.providers.base import FundamentalsRaw, MarketDataProvider
from app.market.service import meta_set
from app.models.core import Instrument
from app.models.market import EventType, Fundamentals, InstrumentEvent, PriceHistory, Quote

PE_CAP = 75.0                 # P/E values above are treated as outliers for the historical average
REPORTING_LAG_DAYS = 60       # annual EPS becomes "known" ~2 months after fiscal year end
MAX_DIVIDEND_YEARS = 25


def _r(value: float | None, places: int = 4) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, places)


def empty_fundamentals() -> dict:
    return {
        "available": False, "currency": None, "financial_currency": None, "years": [], "dividends_by_year": [],
        "pe_history": [], "fair_value_series": [],
        "fair_value": {"method": None, "value": None, "pe_avg": None, "eps_forward": None},
        "dividend_growth": {"cagr_5y": None, "cagr_10y": None, "years_raised": 0},
        "valuation": {"pe": None, "forward_pe": None, "pb": None, "dividend_yield": None, "market_cap": None},
        "earnings_quality": {}, "source": None, "updated_at": None,
    }


# ---------------------------------------------------------------- small pure helpers
def monthly_closes(prices: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Last close of each calendar month, ascending."""
    by_month: dict[tuple[int, int], tuple[date, float]] = {}
    for d, c in sorted(prices):
        by_month[(d.year, d.month)] = (d, c)
    return [by_month[k] for k in sorted(by_month)]


def dps_by_year(dividends: list[tuple[date, float]]) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for d, a in dividends:
        if a and a > 0:
            out[d.year] += a
    return dict(out)


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    """Compound annual growth in percent."""
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def stability(values: list[float]) -> float | None:
    """0..1: how steadily a series grows = max(0, Pearson correlation with a straight upward trend line)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    xs = list(range(len(vals)))
    mx, my = statistics.fmean(xs), statistics.fmean(vals)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in vals)
    if syy <= 1e-18:
        return 1.0 if my > 0 else 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, vals))
    return max(0.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def dividend_growth(dps: dict[int, float], today: date) -> dict:
    """CAGR 5y/10y + streaks, based on complete calendar years only (current year is partial)."""
    last_full = today.year - 1
    full = {y: v for y, v in dps.items() if y <= last_full}
    out = {"cagr_3y": None, "cagr_5y": None, "cagr_10y": None, "years_raised": 0, "years_not_cut": 0,
           "years_paid": 0, "last_full_year": last_full if full else None}
    if not full or full.get(last_full, 0) <= 0:
        return out
    for n in (3, 5, 10):
        out[f"cagr_{n}y"] = _r(cagr(full.get(last_full - n), full.get(last_full), n), 2)
    y = last_full
    while full.get(y, 0) > 0:
        out["years_paid"] += 1
        y -= 1
    y = last_full
    while full.get(y - 1, 0) > 0 and full[y] > full[y - 1] * 1.001:
        out["years_raised"] += 1
        y -= 1
    y = last_full
    while full.get(y - 1, 0) > 0 and full[y] >= full[y - 1] * 0.999:
        out["years_not_cut"] += 1
        y -= 1
    return out


# ---------------------------------------------------------------- main builder
def build_fundamentals(
    raw: FundamentalsRaw,
    prices: list[tuple[date, float]],
    dividends: list[tuple[date, float]],
    today: date | None = None,
    price: float | None = None,
    fx_factor: float = 1.0,
) -> dict:
    """Assemble the contract's `/market/fundamentals/{id}` payload.

    prices     – daily or monthly closes in quote currency (split-adjusted)
    dividends  – (ex-date, amount per share) in quote currency
    fx_factor  – multiplies statement-currency per-share values into quote currency (1.0 if identical)
    """
    today = today or date.today()
    monthly = monthly_closes(prices)
    price = price or raw.price or (monthly[-1][1] if monthly else None)
    dps = dps_by_year(dividends)
    annual = sorted(raw.annual, key=lambda a: a.year)

    # ---- per-year table (statement values in financial currency, dps in quote currency)
    years: list[dict] = []
    stmt_years = {a.year for a in annual}
    for a in annual:
        year_dps = dps.get(a.year)
        payout = None
        if a.dividends_paid and a.net_income and a.net_income > 0:
            payout = a.dividends_paid / a.net_income * 100
        elif year_dps and a.eps and a.eps > 0:
            payout = year_dps / (a.eps * fx_factor) * 100
        fcf_ps = a.fcf / a.shares if a.fcf is not None and a.shares else None
        years.append({
            "year": a.year, "period_end": a.period_end.isoformat() if a.period_end else None,
            "revenue": _r(a.revenue, 0), "net_income": _r(a.net_income, 0), "eps": _r(a.eps),
            "dps": _r(year_dps), "fcf": _r(a.fcf, 0), "payout_ratio": _r(payout, 2), "shares": _r(a.shares, 0),
            "fcf_per_share": _r(fcf_ps),
            "fcf_payout_ratio": _r(a.dividends_paid / a.fcf * 100, 2) if a.dividends_paid and a.fcf and a.fcf > 0 else None,
        })
    dividends_by_year = [
        {"year": y, "dps": _r(dps[y]), "partial": y >= today.year}
        for y in sorted(dps) if y >= today.year - MAX_DIVIDEND_YEARS
    ]

    # ---- trailing EPS per month (step function of annual EPS, TTM for the latest month) -> P/E history
    eps_steps = [(a.period_end or date(a.year, 12, 31), a.eps * fx_factor) for a in annual if a.eps is not None]
    eps_ttm_q = _info_eps_in_quote(raw.eps_ttm, raw, fx_factor)
    eps_fwd_q = _info_eps_in_quote(raw.eps_forward, raw, fx_factor, forward=True)

    def eps_at(d: date) -> float | None:
        current = None
        for end, eps in eps_steps:
            if end + timedelta(days=REPORTING_LAG_DAYS) <= d:
                current = eps
        if current is None and eps_steps and eps_steps[0][0] <= d:
            current = eps_steps[0][1]      # between first FY end and its publication
        return current

    pe_history: list[dict] = []
    series: list[dict] = []
    for i, (d, close) in enumerate(monthly):
        eps = eps_at(d)
        if i == len(monthly) - 1 and eps_ttm_q is not None and (today - d).days <= 45:
            eps = eps_ttm_q
        if eps is None:
            continue
        pe = close / eps if eps > 0 else None
        pe_history.append({"date": d.isoformat(), "pe": _r(pe, 2)})
        series.append((d, close, eps))
    valid = [p["pe"] for p in pe_history if p["pe"] is not None and 0 < p["pe"] <= PE_CAP]
    pe_avg = statistics.fmean(valid) if valid else None
    pe_median = statistics.median(valid) if valid else None

    # ---- fair value
    last_annual_eps = eps_steps[-1][1] if eps_steps else None
    if eps_fwd_q and eps_fwd_q > 0:
        eps_basis, method = eps_fwd_q, "pe_avg_x_forward_eps"
    elif eps_ttm_q and eps_ttm_q > 0:
        eps_basis, method = eps_ttm_q, "pe_avg_x_trailing_eps"
    elif last_annual_eps and last_annual_eps > 0:
        eps_basis, method = last_annual_eps, "pe_avg_x_last_annual_eps"
    else:
        eps_basis, method = None, None
    value_pe = pe_avg * eps_basis if pe_avg and eps_basis else None

    # dividend-yield based: current annual dividend / average historical yield (up to 10 full years)
    year_prices: dict[int, list[float]] = defaultdict(list)
    for d, close in monthly:
        year_prices[d.year].append(close)
    yields = []
    for y in range(today.year - 10, today.year):
        if dps.get(y, 0) > 0 and year_prices.get(y):
            yields.append(dps[y] / statistics.fmean(year_prices[y]))
    avg_yield = statistics.fmean(yields) if len(yields) >= 3 else None
    ttm_div = sum(a for d, a in dividends if a and today - timedelta(days=365) < d <= today)
    div_basis = raw.dividend_rate if raw.dividend_rate and raw.dividend_rate > 0 else (ttm_div or None)
    value_div = div_basis / avg_yield if div_basis and avg_yield else None

    if value_pe is not None:
        fv_value = value_pe
    elif value_div is not None:
        fv_value, method = value_div, "avg_dividend_yield"
    else:
        fv_value = None
    upside = (fv_value / price - 1) * 100 if fv_value and price else None
    if upside is None:
        verdict = None
    elif upside > 10:
        verdict = "undervalued"
    elif upside < -10:
        verdict = "overvalued"
    else:
        verdict = "fair"
    fair_value = {
        "method": method, "value": _r(fv_value, 2), "pe_avg": _r(pe_avg, 2), "pe_median": _r(pe_median, 2),
        "eps_forward": _r(eps_fwd_q), "eps_ttm": _r(eps_ttm_q), "eps_basis": _r(eps_basis),
        "value_pe": _r(value_pe, 2), "value_dividend_yield": _r(value_div, 2),
        "dividend_yield_avg": _r(avg_yield * 100, 2) if avg_yield else None, "dividend_basis": _r(div_basis),
        "price": _r(price, 4), "upside_pct": _r(upside, 2), "verdict": verdict,
        "pe_months": len(valid),
    }
    fair_value_series = [
        {"date": d.isoformat(), "price": _r(close, 4), "eps": _r(eps),
         "fair_value": _r(pe_avg * eps, 2) if pe_avg and eps and eps > 0 else None}
        for d, close, eps in series
    ]

    # ---- valuation snapshot
    pe_now = raw.pe if raw.pe and raw.pe > 0 else (price / eps_ttm_q if price and eps_ttm_q and eps_ttm_q > 0 else None)
    fwd_pe = raw.forward_pe if raw.forward_pe and raw.forward_pe > 0 else (
        price / eps_fwd_q if price and eps_fwd_q and eps_fwd_q > 0 else None)
    dy = raw.dividend_yield
    if dy is None and div_basis and price:
        dy = div_basis / price
    valuation = {
        "pe": _r(pe_now, 2), "forward_pe": _r(fwd_pe, 2), "pb": _r(raw.pb, 2),
        "dividend_yield": _r(dy * 100, 2) if dy is not None else None, "dividend_rate": _r(div_basis),
        "market_cap": _r(raw.market_cap, 0), "payout_ratio": _r(raw.payout_ratio * 100, 2) if raw.payout_ratio is not None else None,
        "shares_outstanding": _r(raw.shares_outstanding, 0), "price": _r(price, 4),
    }

    # ---- growth / quality
    growth = dividend_growth(dps, today)
    eps_values = [a.eps for a in annual if a.eps is not None]
    earn_stab = stability(eps_values)
    full_dps = [dps.get(y, 0.0) for y in range(max(min(dps, default=today.year), today.year - 10), today.year)] if dps else []
    div_stab = stability(full_dps) if any(full_dps) else None
    loss_years = sum(1 for a in annual if (a.net_income is not None and a.net_income < 0) or (a.eps is not None and a.eps < 0))
    eps_cagr = cagr(eps_values[0], eps_values[-1], len(eps_values) - 1) if len(eps_values) >= 2 else None
    rev_values = [a.revenue for a in annual if a.revenue is not None]
    rev_cagr = cagr(rev_values[0], rev_values[-1], len(rev_values) - 1) if len(rev_values) >= 2 else None
    payouts = [y["payout_ratio"] for y in years if y["payout_ratio"] is not None]
    fcf_payouts = [y["fcf_payout_ratio"] for y in years if y["fcf_payout_ratio"] is not None]
    payout_avg = statistics.fmean(payouts) if payouts else None
    parts: list[tuple[float, float]] = []      # (weight, 0..1)
    if earn_stab is not None:
        parts.append((0.40, earn_stab))
    if annual:
        parts.append((0.15, 1.0 - loss_years / len(annual)))
    if div_stab is not None:
        parts.append((0.30, div_stab))
        if payout_avg is not None:
            parts.append((0.15, max(0.0, min(1.0, (100.0 - payout_avg) / 40.0))))   # <=60 % -> 1, >=100 % -> 0
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts) * 100 if parts else None
    earnings_quality = {
        "earnings_stability": _r(earn_stab, 3), "dividend_stability": _r(div_stab, 3),
        "eps_cagr": _r(eps_cagr, 2), "revenue_cagr": _r(rev_cagr, 2), "loss_years": loss_years,
        "payout_ratio_avg": _r(payout_avg, 2),
        "fcf_payout_ratio_avg": _r(statistics.fmean(fcf_payouts), 2) if fcf_payouts else None,
        "score": _r(score, 0), "years_of_data": len(annual),
    }

    return {
        "available": bool(years or pe_history or dividends_by_year or valuation["pe"]),
        "currency": raw.currency, "financial_currency": raw.financial_currency or raw.currency,
        "fx_factor": _r(fx_factor, 6),
        "years": years, "dividends_by_year": dividends_by_year,
        "pe_history": pe_history, "fair_value_series": fair_value_series, "fair_value": fair_value,
        "dividend_growth": growth, "valuation": valuation, "earnings_quality": earnings_quality,
        "source": "yahoo", "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _info_eps_in_quote(eps: float | None, raw: FundamentalsRaw, fx_factor: float, forward: bool = False) -> float | None:
    """Yahoo's trailing/forward EPS are usually already in quote currency; verify via price / PE if possible."""
    if eps is None:
        return None
    if abs(fx_factor - 1.0) < 1e-9:
        return eps
    pe = raw.forward_pe if forward else raw.pe
    if raw.price and pe and pe > 0:
        implied = raw.price / pe
        if abs(implied - eps) > abs(implied - eps * fx_factor):
            return eps * fx_factor
    return eps


# ---------------------------------------------------------------- DB glue
def refresh_fundamentals(db: Session, inst: Instrument, provider: MarketDataProvider, today: date | None = None) -> dict:
    if not inst.symbol:
        return {"stored": False}
    raw = provider.get_fundamentals(inst.symbol)
    prices = [(d, float(c)) for d, c in db.execute(
        select(PriceHistory.date, PriceHistory.close).where(PriceHistory.instrument_id == inst.id)
        .order_by(PriceHistory.date))]
    divs = [(d, float(a)) for d, a in db.execute(
        select(InstrumentEvent.date, InstrumentEvent.amount).where(
            InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.ex_dividend,
            InstrumentEvent.is_estimate.is_(False), InstrumentEvent.amount.is_not(None))
        .order_by(InstrumentEvent.date))]
    quote = db.get(Quote, inst.id)
    price = float(quote.price) if quote else None
    raw.currency = raw.currency or inst.currency
    fx_factor = 1.0
    fin = raw.financial_currency
    if fin and raw.currency and fin != raw.currency:
        r_fin, r_quote = get_rate(db, fin), get_rate(db, raw.currency)
        if r_fin and r_quote:
            fx_factor = float(r_quote) / float(r_fin)
    data = build_fundamentals(raw, prices, divs, today=today, price=price, fx_factor=fx_factor)
    row = db.get(Fundamentals, inst.id)
    if row is None:
        row = Fundamentals(instrument_id=inst.id, data=data)
        db.add(row)
    else:
        row.data = data
    row.updated_at = datetime.now(timezone.utc)
    meta_set(inst, "fundamentals_at", row.updated_at.isoformat())
    db.flush()
    return {"stored": True, "years": len(data["years"]), "pe_points": len(data["pe_history"])}
