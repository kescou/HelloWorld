"""DB-facing services with the FakeProvider: symbols, quotes, history, profile, FX, events."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.market import events as ev
from app.market import fx, positions, service
from app.market.providers import openfigi
from app.market.providers.base import (
    CalendarData,
    DividendData,
    HistoryData,
    ProfileData,
    QuoteData,
    SearchResult,
    SplitData,
)
from app.models.core import FxRate, InstrumentType, TxType
from app.models.market import EventType, InstrumentEvent, PriceHistory, Quote
from fixtures.market.fakes import (  # noqa: F401
    bars,
    fixture_bytes,
    make_account,
    make_fx,
    make_instrument,
    make_tx,
    no_network,
    provider,
)

TODAY = date.today()


# ---------------------------------------------------------------- positions
def test_positions_buy_sell_split_delivery(db):
    acc, acc2 = make_account(db), make_account(db, "Depot 2")
    a = make_instrument(db, "A", symbol="A.DE")
    b = make_instrument(db, "B", symbol="B.DE")
    make_tx(db, acc, a, TxType.buy, 10, on=TODAY - timedelta(days=50))
    make_tx(db, acc, a, TxType.split, 4, on=TODAY - timedelta(days=40))        # 10 -> 40
    make_tx(db, acc, a, TxType.sell, 15, on=TODAY - timedelta(days=30))        # 25
    make_tx(db, acc2, a, TxType.delivery_in, 5, on=TODAY - timedelta(days=20))  # +5 in other depot (after split)
    make_tx(db, acc, b, TxType.buy, 3, on=TODAY - timedelta(days=10))
    make_tx(db, acc, b, TxType.delivery_out, 3, on=TODAY - timedelta(days=5))   # closed
    qty = positions.compute_quantities(db)
    assert qty == {a.id: Decimal(30)}
    assert positions.compute_quantities(db, account_id=acc2.id) == {a.id: Decimal(5)}
    assert positions.compute_quantities(db, as_of=TODAY - timedelta(days=45)) == {a.id: Decimal(10)}
    assert positions.held_instrument_ids(db) == {a.id}
    timeline = positions.quantity_timeline(db, a.id)
    assert positions.quantity_at(timeline, TODAY - timedelta(days=35)) == 40
    assert positions.quantity_at(timeline, TODAY) == 30
    w = make_instrument(db, "Watch", symbol="W.DE", is_watchlist=True)
    assert {i.id for i in positions.tracked_instruments(db)} == {a.id, w.id}
    assert {i.id for i in positions.tracked_instruments(db, include_past=True)} == {a.id, b.id, w.id}


# ---------------------------------------------------------------- symbol resolution
def test_resolve_symbol_prefers_xetra_listing(db, provider):
    inst = make_instrument(db, "Microsoft", isin="US5949181045", currency="EUR")
    provider.searches["US5949181045"] = [
        SearchResult("MSFT", "Microsoft Corporation", "NMS", "stock"),
        SearchResult("MSF.DE", "Microsoft Corporation", "GER", "stock"),
    ]
    assert service.resolve_symbol(db, inst, provider) == "MSF.DE"
    assert inst.symbol == "MSF.DE" and service.meta_get(inst, "symbol_source") == "yahoo"


def test_resolve_symbol_finds_xetra_sibling_by_name(db, provider):
    inst = make_instrument(db, "Microsoft", isin="US5949181045")
    provider.searches["US5949181045"] = [SearchResult("MSFT", "Microsoft Corporation", "NMS", "stock")]
    provider.searches["Microsoft Corporation"] = [
        SearchResult("MSFT", "Microsoft Corporation", "NMS", "stock"),
        SearchResult("MSFT.NE", "Microsoft CDR", "NEO", "stock"),
        SearchResult("MSF.DE", "Microsoft Corporation", "GER", "stock"),
    ]
    assert service.resolve_symbol(db, inst, provider) == "MSF.DE"


def test_resolve_symbol_falls_back_to_primary_listing(db, provider):
    inst = make_instrument(db, "Costco", isin="US22160K1051")
    provider.searches["US22160K1051"] = [SearchResult("COST", "Costco Wholesale Corporation", "NMS", "stock")]
    assert service.resolve_symbol(db, inst, provider) == "COST"


def test_resolve_symbol_openfigi_fallback_and_failure(db, provider, monkeypatch):
    import json

    inst = make_instrument(db, "Microsoft", isin="US5949181045")
    monkeypatch.setattr(openfigi, "lookup_isin", lambda isin: openfigi.parse_mapping(
        json.loads(fixture_bytes("openfigi_mapping.json")), isin))
    provider.quotes["MSF.DE"] = QuoteData("MSF.DE", 441.0, "EUR", 440.0)
    assert service.resolve_symbol(db, inst, provider) == "MSF.DE"
    assert inst.currency == "EUR" and service.meta_get(inst, "symbol_source") == "openfigi"

    unknown = make_instrument(db, "Unbekannt", isin="XS0000000009")
    monkeypatch.setattr(openfigi, "lookup_isin", lambda isin: [])
    assert service.resolve_symbol(db, unknown, provider) is None
    assert unknown.symbol is None and service.meta_get(unknown, "symbol_lookup_failed_at")


def test_search_survives_offline_openfigi(db, provider):
    # Yahoo knows nothing, OpenFIGI is unreachable (network blocked by the autouse fixture) -> empty, no exception
    assert service.search_symbols(provider, "US0000000001") == []


# ---------------------------------------------------------------- quotes
def test_quotes_upsert(db, provider):
    a = make_instrument(db, "A", symbol="A.DE")
    b = make_instrument(db, "B", symbol="B", currency="EUR")
    c = make_instrument(db, "C", symbol="C.DE")
    nosym = make_instrument(db, "D")
    t0 = datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)
    provider.quotes = {"A.DE": QuoteData("A.DE", 10.5, "EUR", 10.0, t0), "B": QuoteData("B", 200.0, "USD", 198.0, t0)}
    res = service.refresh_quotes(db, [a, b, c, nosym], provider)
    assert res == {"requested": 3, "updated": 2, "missing": ["C.DE"]}
    qa = db.get(Quote, a.id)
    assert (qa.price, qa.previous_close, qa.currency, qa.market_time) == (Decimal("10.5"), Decimal("10"), "EUR", t0)
    assert b.currency == "USD"                                   # quote currency wins over the guessed one
    provider.quotes["A.DE"] = QuoteData("A.DE", 11.0, "EUR", 10.5, t0 + timedelta(days=1))
    service.refresh_quotes(db, [a], provider)
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Quote)) == 2      # updated in place, not duplicated
    assert db.get(Quote, a.id).price == Decimal("11")


# ---------------------------------------------------------------- history
def test_history_backfill_then_incremental(db, provider):
    inst = make_instrument(db, "A", symbol="A.DE")
    start = TODAY - timedelta(days=30)
    provider.history["A.DE"] = HistoryData(bars(start, [100 + i for i in range(20)]), currency="EUR")
    written, hist = service.refresh_history(db, inst, provider)
    assert written == 20 and hist.full
    assert provider.calls[-1] == ("history", "A.DE", None)               # max range on first load
    assert db.get(Quote, inst.id).price == Decimal("119")                # quote seeded from last bar
    assert db.get(Quote, inst.id).previous_close == Decimal("118")

    # 5 new days + a correction of the last stored close
    series = [100 + i for i in range(19)] + [500] + [200 + i for i in range(5)]
    provider.history["A.DE"] = HistoryData(bars(start, series), currency="EUR")
    written, hist = service.refresh_history(db, inst, provider)
    last_before = start + timedelta(days=19)
    assert provider.calls[-1] == ("history", "A.DE", last_before - timedelta(days=7))   # incremental with overlap
    assert written == 6 and not hist.full                                # 5 inserted + 1 corrected
    assert db.scalar(select(func.count()).select_from(PriceHistory).where(PriceHistory.instrument_id == inst.id)) == 25
    assert db.scalar(select(PriceHistory.close).where(
        PriceHistory.instrument_id == inst.id, PriceHistory.date == last_before)) == Decimal("500")
    assert service.refresh_history(db, inst, provider)[0] == 0           # idempotent


def test_history_full_reload_after_split(db, provider):
    inst = make_instrument(db, "A", symbol="A.DE")
    start = TODAY - timedelta(days=30)
    provider.history["A.DE"] = HistoryData(bars(start, [400.0] * 10), currency="EUR")
    service.refresh_history(db, inst, provider)
    # 4:1 split two days after the last stored bar: Yahoo back-adjusts ALL closes
    provider.history["A.DE"] = HistoryData(bars(start, [100.0] * 14), splits=[SplitData(start + timedelta(days=11), 4.0)],
                                           currency="EUR")
    written, hist = service.refresh_history(db, inst, provider)
    assert hist.full and written == 14
    closes = set(db.scalars(select(PriceHistory.close).where(PriceHistory.instrument_id == inst.id)))
    assert closes == {Decimal("100")}


# ---------------------------------------------------------------- profile
def test_profile_fills_empty_fields_only(db, provider):
    inst = make_instrument(db, "Meine SAP", isin="DE0007164600", symbol="SAP.DE", type_=InstrumentType.other,
                           sector="Eigene Branche")
    provider.profiles["SAP.DE"] = ProfileData(
        symbol="SAP.DE", name="SAP SE", type="stock", currency="EUR", exchange="XETRA", sector="Technology",
        industry="Software", country="Germany", website="https://www.sap.com", ir_url="https://www.sap.com/investors")
    res = service.refresh_profile(db, inst, provider)
    assert inst.name == "Meine SAP" and inst.sector == "Eigene Branche"   # user data kept
    assert inst.type == InstrumentType.stock and inst.country == "Germany" and inst.ir_url.endswith("/investors")
    assert "country" in res["updated"] and "sector" not in res["updated"]
    assert service.meta_get(inst, "yahoo_name") == "SAP SE"

    etf = make_instrument(db, "IE00B4L5Y983", isin="IE00B4L5Y983", symbol="EUNL.DE")
    provider.profiles["EUNL.DE"] = ProfileData(symbol="EUNL.DE", name="iShares Core MSCI World UCITS ETF USD (Acc)",
                                               type="etf", fund_provider="BlackRock Asset Management Ireland",
                                               ter=0.2, distribution="accumulating")
    service.refresh_profile(db, etf, provider)
    assert etf.type == InstrumentType.etf and etf.fund_provider == "ishares" and etf.ter == Decimal("0.2")
    assert etf.name.startswith("iShares Core MSCI World")                # placeholder name (== ISIN) replaced


# ---------------------------------------------------------------- FX
def test_fx_store_and_lookup(db, provider, monkeypatch):
    monkeypatch.setattr(fx, "fetch_ecb", lambda full=False: fx.parse_ecb_xml(fixture_bytes("ecb_hist_90d.xml")))
    make_instrument(db, "TSMC", symbol="2330.TW", currency="TWD")       # not published by the ECB -> Yahoo fallback
    provider.fx["TWD"] = [(date(2026, 11, 10), 35.1), (date(2026, 11, 11), 35.25)]
    res = fx.update_fx(db, provider)
    assert res["rows"] == 15 and res["latest"] == "2026-11-11" and res["yahoo_fallback"] == {"TWD": 2}
    assert fx.update_fx(db, provider)["rows"] == 0                        # idempotent
    assert db.scalar(select(func.count()).select_from(FxRate)) == 17
    assert fx.get_rate(db, "USD") == Decimal("1.0850")
    assert fx.get_rate(db, "USD", date(2026, 11, 10)) == Decimal("1.08")
    assert fx.get_rate(db, "USD", date(2020, 1, 1)) == Decimal("1.079")   # before first row -> earliest known
    assert fx.get_rate(db, "EUR") == 1 and fx.get_rate(db, "ZZZ") is None
    conv = fx.FxConverter(db)
    assert conv.to_eur(108.5, "USD") == pytest.approx(100.0)
    assert conv.to_eur(8345.0, "GBp") == pytest.approx(100.0)            # pence
    assert conv.to_eur(1.0, "ZZZ") is None and conv.to_eur(5, "EUR") == 5


def test_fx_offline_raises_remote_error_only(db, provider):
    from app.market.http import RemoteError

    with pytest.raises(RemoteError):
        fx.update_fx(db, provider)


# ---------------------------------------------------------------- dividend estimation (pure)
def _quarterly(last: date, n: int, amount: float = 0.5):
    return [(last - timedelta(days=91 * i), amount) for i in range(n)][::-1]


def test_estimate_quarterly_payer():
    today = date(2026, 11, 12)
    past = _quarterly(date(2026, 10, 15), 8, 0.50)
    past[-1] = (past[-1][0], 0.55)                                        # latest raise
    est = ev.estimate_dividends(past, today)
    assert len(est) == 4
    assert all(a == 0.55 for _d, a in est)                                # last amount carried forward
    assert all(today < d <= today + timedelta(days=365) for d, _a in est)
    assert all(d.weekday() < 5 for d, _a in est)
    gaps = [(b[0] - a[0]).days for a, b in zip(est, est[1:])]
    assert all(80 <= g <= 100 for g in gaps)
    assert ev.detect_frequency([d for d, _ in past]) == 4


def test_estimate_annual_and_semiannual_uneven():
    today = date(2026, 11, 12)
    annual = [(date(y, 5, 14), 2.0 + 0.1 * (y - 2020)) for y in range(2020, 2027)]
    est = ev.estimate_dividends(annual, today)
    assert est == [(date(2027, 5, 14), 2.6)]
    semi = [(date(2025, 3, 20), 1.0), (date(2025, 8, 14), 0.4), (date(2026, 3, 19), 1.1), (date(2026, 8, 13), 0.45)]
    est = ev.estimate_dividends(semi, today)
    assert est == [(date(2027, 3, 19), 1.1), (date(2027, 8, 13), 0.45)]   # interim/final keep their own amounts


def test_estimate_skips_announced_and_stopped_payers():
    today = date(2026, 11, 12)
    past = _quarterly(date(2026, 10, 15), 8)
    est = ev.estimate_dividends(past, today, known_future=[date(2027, 1, 12)])
    assert len(est) == 3 and all(abs((d - date(2027, 1, 12)).days) > 45 for d, _ in est)
    assert ev.estimate_dividends(_quarterly(date(2025, 6, 1), 8), today) == []      # stopped > 1.5 periods ago
    assert ev.estimate_dividends([], today) == []
    monthly = [(date(2026, 10, 1) - timedelta(days=30 * i), 0.1) for i in range(12)]
    assert ev.detect_frequency([d for d, _ in monthly]) == 12
    assert 11 <= len(ev.estimate_dividends(monthly, today)) <= 12


# ---------------------------------------------------------------- events sync
def test_refresh_events_full_flow(db, provider):
    inst = make_instrument(db, "Quarterly Inc", isin="US0000000001", symbol="QTR", currency="USD")
    last = TODAY - timedelta(days=20)
    provider.history["QTR"] = HistoryData(
        dividends=[DividendData(d, a, "USD") for d, a in _quarterly(last, 8, 0.5)],
        splits=[SplitData(TODAY - timedelta(days=300), 2.0)], currency="USD")
    announced_ex, announced_pay = TODAY + timedelta(days=71), TODAY + timedelta(days=85)
    earnings = TODAY + timedelta(days=30)
    provider.calendars["QTR"] = CalendarData(
        earnings_dates=[earnings], earnings_is_estimate=True, past_earnings_dates=[TODAY - timedelta(days=60)],
        ex_dividend_date=announced_ex, dividend_pay_date=announced_pay, dividend_amount=0.5, currency="USD")
    res = ev.refresh_events(db, inst, provider)
    assert res["actions"] == 9 and res["calendar"] == 4
    rows = list(db.scalars(select(InstrumentEvent).where(InstrumentEvent.instrument_id == inst.id)))
    by_type = lambda t, est=None: sorted(r.date for r in rows if r.type == t and (est is None or r.is_estimate == est))  # noqa: E731
    assert len(by_type(EventType.ex_dividend, False)) == 9                # 8 historical + 1 announced
    assert announced_ex in by_type(EventType.ex_dividend, False)
    assert by_type(EventType.dividend_payment, False) == [announced_pay]
    assert by_type(EventType.split) == [TODAY - timedelta(days=300)]
    assert by_type(EventType.earnings) == [TODAY - timedelta(days=60), earnings]
    est_ex = by_type(EventType.ex_dividend, True)
    assert len(est_ex) == 3                                               # 4 per year minus the announced one
    assert all(abs((d - announced_ex).days) > 45 for d in est_ex)
    est_pay = by_type(EventType.dividend_payment, True)
    assert len(est_pay) == 3
    assert service.meta_get(inst, "pay_lag_days") == 14                   # learnt from the calendar
    assert all(0 <= (p - e).days - 14 <= 2 for e, p in zip(est_ex, est_pay))
    est_row = next(r for r in rows if r.is_estimate and r.type == EventType.ex_dividend)
    assert est_row.amount == Decimal("0.5") and est_row.currency == "USD" and est_row.source == "estimate"

    # second sync: earnings date moved, estimates rebuilt, no duplicates, history not downloaded again
    n_calls = sum(1 for c in provider.calls if c[0] == "history")
    provider.calendars["QTR"].earnings_dates = [earnings + timedelta(days=2)]
    ev.refresh_events(db, inst, provider)
    assert sum(1 for c in provider.calls if c[0] == "history") == n_calls
    rows = list(db.scalars(select(InstrumentEvent).where(InstrumentEvent.instrument_id == inst.id)))
    assert sorted(r.date for r in rows if r.type == EventType.earnings) == [TODAY - timedelta(days=60), earnings + timedelta(days=2)]
    assert len([r for r in rows if r.is_estimate and r.type == EventType.ex_dividend]) == 3
    assert len(rows) == 9 + 1 + 1 + 2 + 3 + 3


def test_manual_events_are_never_overwritten(db, provider):
    inst = make_instrument(db, "A", symbol="A.DE")
    day = TODAY + timedelta(days=30)
    manual = ev.upsert_event(db, inst.id, EventType.earnings, day, title="Q3 laut IR-Seite", source="manual")
    provider.calendars["A.DE"] = CalendarData(earnings_dates=[day])
    ev.refresh_events(db, inst, provider)
    db.refresh(manual)
    assert manual.title == "Q3 laut IR-Seite" and manual.source == "manual"
    # a real provider row replaces an estimate, but not the other way round
    d2 = TODAY + timedelta(days=90)
    ev.upsert_event(db, inst.id, EventType.ex_dividend, d2, amount=1.0, is_estimate=True, source="estimate")
    real = ev.upsert_event(db, inst.id, EventType.ex_dividend, d2, amount=1.2, source="yahoo")
    assert real.amount == Decimal("1.2") and not real.is_estimate
    assert ev.upsert_event(db, inst.id, EventType.ex_dividend, d2, amount=9, is_estimate=True, source="estimate") is None


def test_etf_distribution_detected_from_dividends(db, provider):
    acc_etf = make_instrument(db, "Thesaurierer", symbol="ACC.DE", type_=InstrumentType.etf)
    dist_etf = make_instrument(db, "Ausschütter", symbol="DIST.DE", type_=InstrumentType.etf)
    provider.history["DIST.DE"] = HistoryData(dividends=[DividendData(d, a) for d, a in _quarterly(TODAY - timedelta(days=30), 6, 0.3)])
    ev.refresh_events(db, acc_etf, provider)
    ev.refresh_events(db, dist_etf, provider)
    assert acc_etf.distribution == "accumulating" and dist_etf.distribution == "distributing"


def test_ics_import_and_list_events_enrichment(db, provider):
    acc = make_account(db)
    held = make_instrument(db, "US Payer", symbol="PAY", currency="USD")
    watch = make_instrument(db, "Watch AG", symbol="W.DE", is_watchlist=True)
    other = make_instrument(db, "Nicht gehalten", symbol="X.DE")
    make_fx(db, "USD", 1.25)
    make_tx(db, acc, held, TxType.buy, 10, on=TODAY - timedelta(days=100))
    make_tx(db, acc, held, TxType.buy, 30, on=TODAY - timedelta(days=10))     # after the past ex-date
    past_ex, future_ex = TODAY - timedelta(days=20), TODAY + timedelta(days=40)
    ev.upsert_event(db, held.id, EventType.ex_dividend, past_ex, amount=0.5, currency="USD")
    ev.upsert_event(db, held.id, EventType.ex_dividend, future_ex, amount=0.5, currency="USD", is_estimate=True, source="estimate")
    ev.upsert_event(db, other.id, EventType.earnings, future_ex)
    res = ev.import_ics(db, watch, fixture_bytes("ir_calendar.ics"))
    assert res == {"parsed": 3, "imported": 3}
    assert ev.import_ics(db, watch, fixture_bytes("ir_calendar.ics"))["imported"] == 3     # idempotent upsert

    items = ev.list_events(db, date_from=TODAY - timedelta(days=30), date_to=date(2027, 12, 31))
    assert {i["instrument_id"] for i in items} == {held.id, watch.id}     # not-held instrument excluded
    past = next(i for i in items if i["date"] == past_ex.isoformat())
    assert past["position_quantity"] == 10 and past["expected_payment"] == pytest.approx(4.0)       # 10 × 0.5 USD / 1.25
    future = next(i for i in items if i["date"] == future_ex.isoformat())
    assert future["position_quantity"] == 40 and future["expected_payment"] == pytest.approx(16.0) and future["is_estimate"]
    agm = next(i for i in items if i["type"] == "agm")
    assert agm["source"] == "ics" and agm["instrument_name"] == "Watch AG" and agm["expected_payment"] is None
    only = ev.list_events(db, date_to=date(2027, 12, 31), types=[EventType.agm, EventType.investor_day])
    assert {i["type"] for i in only} == {"agm", "investor_day"}


def test_fx_full_history_falls_back_to_90_days(db, provider, monkeypatch):
    from app.market.http import RemoteError

    calls = []

    def fetch(full=False):
        calls.append(full)
        if full:
            raise RemoteError("timeout")
        return fx.parse_ecb_xml(fixture_bytes("ecb_hist_90d.xml"))

    monkeypatch.setattr(fx, "fetch_ecb", fetch)
    res = fx.update_fx(db, provider)                     # empty table -> wants the full file first
    assert calls == [True, False] and res["rows"] == 15 and res["full"] is False


def test_empty_full_history_does_not_mark_actions_complete(db, provider):
    inst = make_instrument(db, "A", symbol="A.DE")
    ev.refresh_events(db, inst, provider)                # provider knows nothing about A.DE
    assert service.meta_get(inst, "actions_full_at") is None
    provider.history["A.DE"] = HistoryData(bars(TODAY - timedelta(days=5), [1.0] * 5),
                                           dividends=[DividendData(TODAY - timedelta(days=3), 0.4, "EUR")])
    ev.refresh_events(db, inst, provider)                # retried in full on the next sync
    assert service.meta_get(inst, "actions_full_at")
    assert db.scalar(select(func.count()).select_from(InstrumentEvent).where(
        InstrumentEvent.instrument_id == inst.id, InstrumentEvent.source == "yahoo")) == 1


def test_special_dividend_does_not_inflate_estimates():
    """Reviewer M6: a one-off special dividend was projected forward as the regular amount."""
    today = date(2026, 11, 12)
    # quarterly 0.50, the latest regular payment came together with a 5.00 special dividend
    past = [(date(2024, 12, 10), 0.46), (date(2025, 3, 10), 0.48), (date(2025, 6, 10), 0.48), (date(2025, 9, 10), 0.48),
            (date(2025, 12, 10), 0.48), (date(2026, 3, 10), 0.50), (date(2026, 6, 10), 0.50), (date(2026, 9, 10), 0.50),
            (date(2026, 9, 24), 5.00)]
    assert (date(2026, 9, 24), 5.00) not in ev.regular_payments(past)
    est = ev.estimate_dividends(past, today)
    assert len(est) == 4 and {a for _, a in est} == {0.5}
    # special dividend in the middle of the history, alone in its month: recognised because it does not recur
    mid = [(date(2023, 5, 10), 2.0), (date(2024, 5, 10), 2.1), (date(2024, 11, 20), 6.0), (date(2025, 5, 12), 2.2),
           (date(2026, 5, 11), 2.3)]
    assert [a for _, a in ev.regular_payments(mid)] == [2.0, 2.1, 2.2, 2.3]
    assert [a for _, a in ev.estimate_dividends(mid, today)] == [2.3]
    # an uneven but RECURRING pattern (small interim, big final) is not special
    semi = [(date(2024, 5, 10), 2.4), (date(2024, 11, 10), 0.6), (date(2025, 5, 10), 2.5), (date(2025, 11, 10), 0.6),
            (date(2026, 5, 10), 2.6), (date(2026, 11, 10), 0.6)]
    assert ev.regular_payments(semi) == semi
    assert sorted(a for _, a in ev.estimate_dividends(semi, today)) == [0.6, 2.6]
    # a dividend that was really raised a lot and stays there is not special either
    raised = [(date(2023, 5, 10), 1.0), (date(2024, 5, 10), 1.0), (date(2025, 5, 10), 2.0), (date(2026, 5, 10), 2.1)]
    assert ev.regular_payments(raised) == raised


def test_xetra_sibling_respects_share_class(provider):
    """Reviewer M10: the ISIN of Berkshire B could resolve to the German line of the A share."""
    from app.market.providers.base import SearchResult

    isin_b = "US0846707026"
    provider.searches[isin_b] = [SearchResult("BRK-B", "Berkshire Hathaway Inc. Class B", "NYQ", "stock", "USD")]
    provider.searches["Berkshire Hathaway Inc. Class B"] = [
        SearchResult("BRH.DE", "Berkshire Hathaway Inc. Class A", "GER", "stock", "EUR"),
        SearchResult("BRYN.DE", "Berkshire Hathaway Inc. Class B", "GER", "stock", "EUR"),
        SearchResult("BRK-A", "Berkshire Hathaway Inc. Class A", "NYQ", "stock", "USD"),
    ]
    results = service.search_symbols(provider, isin_b)
    assert [r.symbol for r in results] == ["BRYN.DE", "BRK-B"] and results[0].isin == isin_b
    assert service.pick_listing(results).symbol == "BRYN.DE"

    # only the A line is listed in Germany -> keep the primary listing instead of the wrong share
    provider.searches["Berkshire Hathaway Inc. Class B"] = [
        SearchResult("BRH.DE", "Berkshire Hathaway Inc. Class A", "GER", "stock", "EUR")]
    assert [r.symbol for r in service.search_symbols(provider, isin_b)] == ["BRK-B"]
    # class unknown on the German lines and two of them -> ambiguous, keep the primary
    provider.searches["Berkshire Hathaway Inc. Class B"] = [
        SearchResult("BRH.DE", "Berkshire Hathaway Inc.", "GER", "stock", "EUR"),
        SearchResult("BRYN.DE", "Berkshire Hathaway Inc.", "GER", "stock", "EUR")]
    assert [r.symbol for r in service.search_symbols(provider, isin_b)] == ["BRK-B"]
    # a candidate that exposes the matching ISIN wins, one with another ISIN is never taken
    provider.searches["Berkshire Hathaway Inc. Class B"] = [
        SearchResult("BRH.DE", "Berkshire Hathaway Inc. Class B", "GER", "stock", "EUR", isin="US0846701086"),
        SearchResult("BRYN.DE", "Berkshire Hathaway Inc.", "GER", "stock", "EUR", isin=isin_b)]
    assert [r.symbol for r in service.search_symbols(provider, isin_b)] == ["BRYN.DE", "BRK-B"]
    # ordinary single-class company: unchanged behaviour
    provider.searches["US5949181045"] = [SearchResult("MSFT", "Microsoft Corporation", "NMS", "stock", "USD")]
    provider.searches["Microsoft Corporation"] = [SearchResult("MSF.DE", "Microsoft Corp", "GER", "stock", "EUR")]
    assert [r.symbol for r in service.search_symbols(provider, "US5949181045")] == ["MSF.DE", "MSFT"]
    assert service.share_class("Volkswagen AG Vz") == "VZ" and service.share_class("Alphabet Inc.", "GOOG") is None
