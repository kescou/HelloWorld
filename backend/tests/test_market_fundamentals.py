"""Fundamentals / fair-value maths (pure) + DB glue."""
import calendar
from datetime import date

import pytest

from app.market import events as ev
from app.market import fundamentals as fm
from app.market.providers.base import AnnualStatement, FundamentalsRaw
from app.models.market import EventType, Fundamentals, PriceHistory
from fixtures.market.fakes import make_fx, make_instrument, no_network, provider  # noqa: F401

TODAY = date(2025, 6, 30)


def month_ends(start_year: int, start_month: int, end: date, price: float = 100.0):
    out, y, m = [], start_year, start_month
    while date(y, m, 1) <= end:
        out.append((date(y, m, calendar.monthrange(y, m)[1]), price))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def raw_fixture() -> FundamentalsRaw:
    return FundamentalsRaw(
        annual=[
            AnnualStatement(2022, date(2022, 12, 31), revenue=1000.0, net_income=400.0, eps=4.0, shares=100.0, fcf=500.0, dividends_paid=214.36),
            AnnualStatement(2023, date(2023, 12, 31), revenue=1100.0, net_income=500.0, eps=5.0, shares=100.0, fcf=550.0, dividends_paid=235.79),
            AnnualStatement(2024, date(2024, 12, 31), revenue=1210.0, net_income=600.0, eps=6.0, shares=100.0, fcf=600.0, dividends_paid=259.37),
        ],
        currency="EUR", financial_currency="EUR", eps_ttm=6.5, eps_forward=7.0, pe=None, forward_pe=None, pb=3.0,
        dividend_rate=2.8, market_cap=10_000.0, shares_outstanding=100.0, price=100.0)


DIVIDENDS = [(date(y, 5, 15), round(1.1 ** (y - 2014), 6)) for y in range(2014, 2026)]    # +10 % every year


def test_helpers():
    assert fm.cagr(1.0, 1.1 ** 5, 5) == pytest.approx(10.0)
    assert fm.cagr(0, 5, 5) is None and fm.cagr(1, -1, 5) is None
    assert fm.stability([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert fm.stability([5, 4, 3, 2, 1]) == 0.0
    assert fm.stability([2, 2, 2]) == 1.0 and fm.stability([1, 2]) is None
    assert 0 < fm.stability([1, 3, 2, 4, 3.5]) < 1
    closes = fm.monthly_closes([(date(2025, 1, 2), 1.0), (date(2025, 1, 31), 2.0), (date(2025, 2, 3), 3.0)])
    assert closes == [(date(2025, 1, 31), 2.0), (date(2025, 2, 3), 3.0)]
    assert fm.dps_by_year([(date(2024, 3, 1), 0.5), (date(2024, 9, 1), 0.5), (date(2025, 3, 1), 0.6)]) == {2024: 1.0, 2025: 0.6}


def test_dividend_growth_streaks():
    dps = fm.dps_by_year(DIVIDENDS)
    g = fm.dividend_growth(dps, TODAY)
    assert g["cagr_5y"] == pytest.approx(10.0) and g["cagr_10y"] == pytest.approx(10.0)
    assert g["years_raised"] == 10 and g["years_paid"] == 11 and g["last_full_year"] == 2024
    cut = dict(dps) | {2022: 0.5}                              # cut in 2022 -> streak restarts
    g2 = fm.dividend_growth(cut, TODAY)
    assert g2["years_raised"] == 2 and g2["years_not_cut"] == 2 and g2["cagr_10y"] == pytest.approx(10.0)
    flat = {2021: 1.0, 2022: 1.0, 2023: 1.0, 2024: 1.0}
    g3 = fm.dividend_growth(flat, TODAY)
    assert g3["years_raised"] == 0 and g3["years_not_cut"] == 3 and g3["cagr_3y"] == pytest.approx(0.0)
    assert fm.dividend_growth({2020: 1.0}, TODAY)["years_paid"] == 0          # nothing paid in the last full year
    assert fm.dividend_growth({}, TODAY)["cagr_5y"] is None


def test_build_fundamentals_pe_history_and_fair_value():
    data = fm.build_fundamentals(raw_fixture(), month_ends(2020, 1, TODAY), DIVIDENDS, today=TODAY)

    # --- per-year table
    assert [y["year"] for y in data["years"]] == [2022, 2023, 2024]
    y24 = data["years"][-1]
    assert y24["revenue"] == 1210 and y24["eps"] == 6.0 and y24["fcf"] == 600 and y24["shares"] == 100
    assert y24["dps"] == pytest.approx(1.1 ** 10, abs=1e-4)
    assert y24["payout_ratio"] == pytest.approx(259.37 / 600 * 100, abs=0.01)
    assert y24["fcf_per_share"] == 6.0 and y24["fcf_payout_ratio"] == pytest.approx(43.23, abs=0.01)

    # --- P/E history: annual EPS becomes effective 60 days after fiscal year end; last month uses TTM EPS
    pe = {p["date"]: p["pe"] for p in data["pe_history"]}
    assert min(pe) == "2022-12-31" and max(pe) == "2025-06-30" and len(pe) == 31
    assert pe["2023-02-28"] == 25.0 and pe["2024-01-31"] == 25.0          # EPS 4
    assert pe["2024-02-29"] == 20.0 and pe["2025-02-28"] == 20.0          # EPS 5
    assert pe["2025-03-31"] == pytest.approx(16.67) and pe["2025-06-30"] == pytest.approx(15.38)
    expected_avg = (14 * 25 + 13 * 20 + 3 * (100 / 6) + 100 / 6.5) / 31
    fv = data["fair_value"]
    assert fv["pe_avg"] == pytest.approx(expected_avg, abs=0.01)
    assert fv["method"] == "pe_avg_x_forward_eps" and fv["eps_forward"] == 7.0
    assert fv["value"] == pytest.approx(expected_avg * 7.0, abs=0.01) == fv["value_pe"]
    assert fv["upside_pct"] == pytest.approx((expected_avg * 7.0 / 100 - 1) * 100, abs=0.01)
    assert fv["verdict"] == "undervalued"

    # --- dividend-yield fair value: 2.8 / avg(yield 2015..2024) with price 100 only from 2020 -> years 2020-2024
    avg_yield = sum(1.1 ** (y - 2014) for y in range(2020, 2025)) / 5 / 100
    assert fv["dividend_yield_avg"] == pytest.approx(avg_yield * 100, abs=0.01)
    assert fv["value_dividend_yield"] == pytest.approx(2.8 / avg_yield, abs=0.01)

    # --- fair value series for the KGV-band chart
    last = data["fair_value_series"][-1]
    assert last["date"] == "2025-06-30" and last["price"] == 100.0
    assert last["fair_value"] == pytest.approx(expected_avg * 6.5, abs=0.01)

    # --- valuation / growth / quality
    v = data["valuation"]
    assert v["pe"] == pytest.approx(100 / 6.5, abs=0.01) and v["forward_pe"] == pytest.approx(100 / 7, abs=0.01)
    assert v["pb"] == 3.0 and v["dividend_yield"] == pytest.approx(2.8) and v["market_cap"] == 10_000
    assert data["dividend_growth"]["cagr_5y"] == pytest.approx(10.0)
    assert data["dividend_growth"]["years_raised"] == 10
    q = data["earnings_quality"]
    assert q["earnings_stability"] == pytest.approx(1.0) and q["dividend_stability"] > 0.95
    assert q["loss_years"] == 0 and q["eps_cagr"] == pytest.approx(22.47, abs=0.01) and q["score"] >= 95
    assert data["available"] and data["currency"] == "EUR"
    assert {"years", "pe_history", "fair_value", "dividend_growth", "valuation", "earnings_quality"} <= set(data)
    assert {"method", "value", "pe_avg", "eps_forward"} <= set(fv)
    partial = data["dividends_by_year"][-1]
    assert partial["year"] == 2025 and partial["partial"] is True


def test_build_fundamentals_edge_cases():
    # loss maker without dividends: no P/E, no fair value, nothing crashes
    raw = FundamentalsRaw(annual=[AnnualStatement(2023, date(2023, 12, 31), net_income=-5.0, eps=-1.0),
                                  AnnualStatement(2024, date(2024, 12, 31), net_income=-2.0, eps=-0.5)],
                          currency="USD", eps_ttm=-0.2, price=10.0)
    data = fm.build_fundamentals(raw, month_ends(2024, 1, TODAY, 10.0), [], today=TODAY)
    assert all(p["pe"] is None for p in data["pe_history"])
    assert data["fair_value"]["value"] is None and data["fair_value"]["method"] is None
    assert data["earnings_quality"]["loss_years"] == 2 and data["dividend_growth"]["years_raised"] == 0
    # ETF-like: dividends only -> dividend-yield method
    etf = fm.build_fundamentals(FundamentalsRaw(currency="EUR", price=100.0), month_ends(2018, 1, TODAY),
                                [(date(y, 6, 1), 2.0) for y in range(2018, 2026)], today=TODAY)
    assert etf["years"] == [] and etf["fair_value"]["method"] == "avg_dividend_yield"
    assert etf["fair_value"]["value"] == pytest.approx(100.0)
    # completely empty
    empty = fm.build_fundamentals(FundamentalsRaw(), [], [], today=TODAY)
    assert empty["available"] is False and empty["pe_history"] == []
    assert set(fm.empty_fundamentals()) <= set(empty) | {"instrument_id"}
    # outliers above the cap are ignored in the average
    raw = FundamentalsRaw(annual=[AnnualStatement(2023, date(2023, 6, 30), eps=0.01), AnnualStatement(2024, date(2024, 6, 30), eps=5.0)],
                          price=100.0)
    out = fm.build_fundamentals(raw, month_ends(2023, 7, TODAY), [], today=TODAY)
    assert out["fair_value"]["pe_avg"] == pytest.approx(20.0)


def test_statement_currency_differs_from_quote_currency():
    # statements in DKK, listing in EUR, 1 EUR = 7.5 DKK -> fx_factor 1/7.5; Yahoo's trailing EPS is already EUR
    raw = FundamentalsRaw(annual=[AnnualStatement(2023, date(2023, 12, 31), eps=30.0), AnnualStatement(2024, date(2024, 12, 31), eps=37.5)],
                          currency="EUR", financial_currency="DKK", eps_ttm=5.0, pe=20.0, price=100.0)
    data = fm.build_fundamentals(raw, month_ends(2024, 1, TODAY), [], today=TODAY, fx_factor=1 / 7.5)
    pe = {p["date"]: p["pe"] for p in data["pe_history"]}
    assert pe["2024-12-31"] == 25.0                       # 100 / (30 / 7.5)
    assert pe["2025-05-31"] == 20.0                       # 100 / (37.5 / 7.5)
    assert data["fair_value"]["eps_ttm"] == 5.0           # not converted twice
    assert data["years"][0]["eps"] == 30.0                # table stays in statement currency
    assert data["financial_currency"] == "DKK"


def test_refresh_fundamentals_stores_json(db, provider):
    inst = make_instrument(db, "Novo", symbol="NOV.DE", currency="EUR")
    make_fx(db, "DKK", 7.5)
    today = date.today()
    raw = raw_fixture()
    raw.financial_currency = "DKK"
    raw.annual = [AnnualStatement(today.year - 1, date(today.year - 1, 6, 30), net_income=100.0, eps=37.5, shares=10.0)]
    raw.eps_ttm, raw.eps_forward, raw.pe = 5.0, 6.0, 20.0
    provider.fundamentals["NOV.DE"] = raw
    db.add_all([PriceHistory(instrument_id=inst.id, date=date(today.year - 1, 12, 30), close=100),
                PriceHistory(instrument_id=inst.id, date=today, close=100)])
    ev.upsert_event(db, inst.id, EventType.ex_dividend, date(today.year - 1, 3, 20), amount=1.5, currency="EUR")
    ev.upsert_event(db, inst.id, EventType.ex_dividend, date(today.year + 1, 3, 20), amount=9.9, is_estimate=True, source="estimate")
    db.flush()
    res = fm.refresh_fundamentals(db, inst, provider)
    assert res["stored"] and res["years"] == 1
    data = db.get(Fundamentals, inst.id).data
    assert data["fx_factor"] == pytest.approx(1 / 7.5, abs=1e-5)
    assert data["years"][0]["dps"] == 1.5                 # estimates are not counted as paid dividends
    assert data["pe_history"][0] == {"date": date(today.year - 1, 12, 30).isoformat(), "pe": 20.0}
    fm.refresh_fundamentals(db, inst, provider)           # upsert, no duplicate key
    assert db.query(Fundamentals).filter_by(instrument_id=inst.id).count() == 1
