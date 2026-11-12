"""Performance maths: XIRR (own implementation) and daily time-weighted return."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd


def xirr(flows: Sequence[tuple[date, float]]) -> float | None:
    """Annualised internal rate of return for dated cash flows (investor view:
    contributions negative, withdrawals / final value positive). Returns a fraction (0.1 = 10 %).

    Newton-Raphson with a bisection fallback; None when there is no sign change.
    """
    flows = [(d, float(a)) for d, a in flows if a]
    if len(flows) < 2:
        return None
    amounts = np.array([a for _, a in flows], dtype=float)
    if not ((amounts > 0).any() and (amounts < 0).any()):
        return None
    t0 = min(d for d, _ in flows)
    years = np.array([(d - t0).days / 365.0 for d, _ in flows], dtype=float)
    if years.max() <= 0:
        return None

    def npv(rate: float) -> float:
        return float(np.sum(amounts / np.power(1.0 + rate, years)))

    def dnpv(rate: float) -> float:
        return float(np.sum(-years * amounts / np.power(1.0 + rate, years + 1.0)))

    rate = 0.1
    for _ in range(60):
        try:
            f = npv(rate)
            df = dnpv(rate)
        except (FloatingPointError, OverflowError, ZeroDivisionError):
            break
        if not np.isfinite(f) or not np.isfinite(df) or df == 0:
            break
        new_rate = rate - f / df
        if not np.isfinite(new_rate) or new_rate <= -0.999999:
            break
        if abs(new_rate - rate) < 1e-10:
            if abs(npv(new_rate)) < 1e-6 * max(1.0, float(np.abs(amounts).sum())):
                return float(new_rate)
            break
        rate = new_rate

    # bisection fallback: scan for a sign change
    grid = [-0.999999, -0.99, -0.9, -0.5, -0.2, 0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 100.0, 1e4, 1e6]
    with np.errstate(all="ignore"):
        values = [npv(r) for r in grid]
    lo = hi = None
    for (r1, v1), (r2, v2) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if np.isfinite(v1) and np.isfinite(v2) and v1 * v2 <= 0:
            lo, hi, flo = r1, r2, v1
            break
    if lo is None:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fmid = npv(mid)
        if abs(fmid) < 1e-9 or (hi - lo) < 1e-12:
            return float(mid)
        if flo * fmid <= 0:
            hi = mid
        else:
            lo, flo = mid, fmid
    return float((lo + hi) / 2.0)


def twr_series(values: pd.Series, flows: pd.Series, base_value: float = 0.0) -> pd.Series:
    """Cumulative time-weighted return factor per day (1.0 = start).

    values: end-of-day portfolio value; flows: external net inflow per day (assumed to arrive at
    the start of the day): r_t = V_t / (V_{t-1} + F_t) - 1.
    """
    if values.empty:
        return pd.Series(dtype=float)
    v = values.to_numpy(dtype=float)
    f = flows.reindex(values.index).fillna(0.0).to_numpy(dtype=float)
    prev = np.concatenate(([float(base_value)], v[:-1]))
    denom = prev + f
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(denom > 1e-6, v / denom - 1.0, 0.0)
    r = np.where(np.isfinite(r), r, 0.0)
    r = np.maximum(r, -1.0)
    return pd.Series(np.cumprod(1.0 + r), index=values.index)
