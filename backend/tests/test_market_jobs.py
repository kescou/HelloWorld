"""Jobs + scheduler: logging into market_data_log, backfill of new instruments, error isolation, offline safety."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.market import fx, jobs, scheduler, service
from app.market.providers.base import (
    AnnualStatement,
    CalendarData,
    DividendData,
    FundamentalsRaw,
    FundHolding,
    HistoryData,
    ProfileData,
    ProviderRateLimited,
    QuoteData,
    SearchResult,
)
from app.models.core import InstrumentType, TxType
from app.models.market import EtfHolding, Fundamentals, InstrumentEvent, MarketDataLog, PriceHistory, Quote
from fixtures.market.fakes import (  # noqa: F401
    bars,
    fixture_bytes,
    make_account,
    make_instrument,
    make_tx,
    no_network,
    provider,
)

TODAY = date.today()


def logs(db, job=None):
    stmt = select(MarketDataLog).order_by(MarketDataLog.id)
    if job:
        stmt = stmt.where(MarketDataLog.job == job)
    return list(db.scalars(stmt))


def _seed_provider(provider, symbol, price=100.0, currency="EUR"):
    provider.quotes[symbol] = QuoteData(symbol, price, currency, price - 1, datetime.now(timezone.utc))
    provider.history[symbol] = HistoryData(
        bars(TODAY - timedelta(days=40), [price] * 40),
        dividends=[DividendData(TODAY - timedelta(days=30 + 91 * i), 0.5, currency) for i in range(6)], currency=currency)
    provider.calendars[symbol] = CalendarData(earnings_dates=[TODAY + timedelta(days=20)])
    provider.profiles[symbol] = ProfileData(symbol=symbol, name=f"{symbol} Corp", type="stock", sector="Technology", country="Germany")
    provider.fundamentals[symbol] = FundamentalsRaw(
        annual=[AnnualStatement(TODAY.year - 1, date(TODAY.year - 1, 6, 30), eps=5.0, net_income=50.0)],
        eps_ttm=5.0, eps_forward=5.5, price=price, currency=currency)


def test_new_instrument_is_resolved_and_backfilled_by_quote_job(db, provider):
    acc = make_account(db)
    new = make_instrument(db, "Neu AG", isin="DE000NEU0001")                     # no symbol, no data yet
    etf = make_instrument(db, "iShares Core MSCI World", isin="IE00B4L5Y983", type_=InstrumentType.etf)
    sold = make_instrument(db, "Verkauft AG", symbol="OLD.DE")
    make_tx(db, acc, new, TxType.buy, 5, 100)
    make_tx(db, acc, etf, TxType.buy, 5, 100)
    make_tx(db, acc, sold, TxType.buy, 5, 100)
    make_tx(db, acc, sold, TxType.sell, 5, 110, on=TODAY - timedelta(days=5))
    provider.searches["DE000NEU0001"] = [SearchResult("NEU.DE", "Neu AG", "GER", "stock")]
    provider.searches["IE00B4L5Y983"] = [SearchResult("EUNL.DE", "iShares Core MSCI World", "GER", "etf")]
    for sym in ("NEU.DE", "EUNL.DE", "OLD.DE"):
        _seed_provider(provider, sym)
    provider.top_holdings["EUNL.DE"] = [FundHolding("NVIDIA Corp", 5.6, "NVDA")]

    res = jobs.run_job("quotes", db=db, provider=provider)
    assert res["status"] == "ok" and res["updated"] == 2 and res["requested"] == 2      # sold position gets no quote poll
    assert new.symbol == "NEU.DE" and etf.symbol == "EUNL.DE"
    assert set(res["backfilled"]) == {new.id, etf.id, sold.id}                          # …but its history is backfilled
    for inst in (new, etf, sold):
        assert db.scalar(select(func.count()).select_from(PriceHistory).where(PriceHistory.instrument_id == inst.id)) == 40
    assert db.get(Quote, new.id).price == 100
    assert db.scalar(select(func.count()).select_from(InstrumentEvent).where(InstrumentEvent.instrument_id == new.id)) > 6
    assert db.get(Fundamentals, new.id).data["fair_value"]["eps_forward"] == 5.5
    assert new.sector == "Technology"
    assert db.scalar(select(EtfHolding.source).where(EtfHolding.etf_instrument_id == etf.id)) == "yahoo_top10"
    entry = logs(db, "quotes")[-1]
    assert entry.status == "ok" and "backfilled=3" in entry.detail

    # second run: nothing left to backfill, log row is collapsed when asked to
    n_hist = sum(1 for c in provider.calls if c[0] == "history")
    res = jobs.run_job("quotes", db=db, provider=provider, collapse=True)
    assert res["backfilled"] == {} and sum(1 for c in provider.calls if c[0] == "history") == n_hist
    assert len(logs(db, "quotes")) == 1


def test_job_error_isolation_one_bad_instrument(db, provider):
    acc = make_account(db)
    good1 = make_instrument(db, "Gut 1", symbol="G1.DE")
    bad = make_instrument(db, "Kaputt", symbol="BAD.DE")
    good2 = make_instrument(db, "Gut 2", symbol="G2.DE")
    for inst in (good1, bad, good2):
        make_tx(db, acc, inst, TxType.buy, 1, 10)
    _seed_provider(provider, "G1.DE")
    _seed_provider(provider, "G2.DE")
    provider.fail.add("BAD.DE")

    res = jobs.run_job("history", db=db, provider=provider)
    assert res["status"] == "partial" and res["processed"] == 2 and res["total"] == 3 and res["rows"] == 80
    assert [e["symbol"] for e in res["errors"]] == ["BAD.DE"]
    assert db.scalar(select(func.count()).select_from(PriceHistory).where(PriceHistory.instrument_id == good2.id)) == 40
    entry = logs(db, "history")[-1]
    assert entry.status == "partial" and "[BAD.DE]" in entry.detail and "boom" in entry.detail

    for scope in ("events", "fundamentals", "profile"):
        res = jobs.run_job(scope, db=db, provider=provider)
        assert res["status"] == "partial" and res["processed"] == 2, scope
    quotes = jobs.run_job("quotes", db=db, provider=provider)
    assert quotes["updated"] == 2 and quotes["missing"] == ["BAD.DE"]


def test_rate_limit_aborts_batch_but_does_not_raise(db, provider):
    acc = make_account(db)
    insts = [make_instrument(db, f"I{i}", symbol=f"I{i}.DE") for i in range(4)]
    for inst in insts:
        make_tx(db, acc, inst, TxType.buy, 1, 10)
        _seed_provider(provider, inst.symbol)
    original = provider.get_history

    def limited(symbol, start=None):
        if symbol == "I1.DE":
            raise ProviderRateLimited("history I1.DE: rate limited by Yahoo")
        return original(symbol, start)

    provider.get_history = limited
    res = jobs.run_job("history", db=db, provider=provider)
    assert res["status"] == "partial" and res["processed"] == 1                 # I0 done, stopped at I1
    assert any("Rate-Limit" in e["error"] for e in res["errors"])
    assert not any(c[:2] == ("history", "I2.DE") for c in provider.calls)


def test_everything_offline_never_raises(db, provider):
    acc = make_account(db)
    stock = make_instrument(db, "A", symbol="A.DE")
    nosym = make_instrument(db, "B", isin="DE000B000001")
    etf = make_instrument(db, "Xtrackers MSCI World", isin="IE00BJ0KDQ92", symbol="XDWD.DE", type_=InstrumentType.etf)
    for inst in (stock, nosym, etf):
        make_tx(db, acc, inst, TxType.buy, 1, 10)
    db.commit()                                   # like real data: survives the rollback after a failed job
    provider.offline = True
    res = jobs.run_job("all", db=db, provider=provider)
    assert res["scope"] == "all" and set(res["jobs"]) == set(jobs.ALL_ORDER)
    assert res["jobs"]["fx"]["status"] == "error" and res["jobs"]["quotes"]["status"] == "error"
    assert res["jobs"]["etf"]["status"] == "error"
    assert res["status"] in ("error", "partial")
    by_job = {e.job: e for e in logs(db)}
    assert set(by_job) == set(jobs.ALL_ORDER)
    assert by_job["fx"].status == "error" and "network disabled" in by_job["fx"].detail
    assert "offline" in by_job["quotes"].detail
    assert nosym.symbol is None
    # instruments are still usable afterwards (session not left in a broken state)
    assert db.scalar(select(func.count()).select_from(Quote)) == 0
    assert jobs.run_job("nonsense", db=db)["status"] == "error"


def test_fx_job_logs_and_all_scope_ok(db, provider, monkeypatch):
    monkeypatch.setattr(fx, "fetch_ecb", lambda full=False: fx.parse_ecb_xml(fixture_bytes("ecb_hist_90d.xml")))
    res = jobs.run_job("fx", db=db, provider=provider)
    assert res["status"] == "ok" and res["rows"] == 15
    assert logs(db, "fx")[-1].detail.startswith("processed=15")
    res = jobs.run_job("all", db=db, provider=provider)                       # empty portfolio: everything trivially ok
    assert res["status"] == "ok"
    assert jobs.last_success(db, "fx") is not None and jobs.last_success(db, "etf") is not None


# ---------------------------------------------------------------- scheduler
class _SessionProxy:
    """Hands the test session to code that opens its own SessionLocal(); close() must not end the test tx."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass


@pytest.fixture()
def own_session(db, monkeypatch):
    import app.core.db as core_db

    monkeypatch.setattr(core_db, "SessionLocal", lambda: _SessionProxy(db))
    return db


def test_market_hours():
    berlin = ZoneInfo("Europe/Berlin")
    assert scheduler.is_market_hours(datetime(2026, 11, 12, 9, 0, tzinfo=berlin))          # Thursday
    assert scheduler.is_market_hours(datetime(2026, 11, 12, 21, 59, tzinfo=berlin))
    assert not scheduler.is_market_hours(datetime(2026, 11, 12, 7, 59, tzinfo=berlin))
    assert not scheduler.is_market_hours(datetime(2026, 11, 12, 22, 0, tzinfo=berlin))
    assert not scheduler.is_market_hours(datetime(2026, 11, 14, 12, 0, tzinfo=berlin))     # Saturday
    assert scheduler.is_market_hours(datetime(2026, 11, 12, 20, 30, tzinfo=timezone.utc))  # 21:30 Berlin


def test_quotes_tick_respects_market_hours(own_session, provider):
    db = own_session
    acc = make_account(db)
    inst = make_instrument(db, "A", symbol="A.DE")
    make_tx(db, acc, inst, TxType.buy, 1, 10)
    _seed_provider(provider, "A.DE")
    berlin = ZoneInfo("Europe/Berlin")
    closed = scheduler.quotes_tick(datetime(2026, 11, 14, 12, 0, tzinfo=berlin))
    assert closed["status"] == "ok" and closed["updated"] == 0
    assert not any(c[0] == "quotes" for c in provider.calls)                    # no polling at the weekend…
    assert set(closed["backfilled"]) == {inst.id}                               # …but the new instrument is backfilled
    opened = scheduler.quotes_tick(datetime(2026, 11, 12, 10, 0, tzinfo=berlin))
    assert opened["updated"] == 1 and any(c[0] == "quotes" for c in provider.calls)


def test_scheduler_ticks_survive_exploding_jobs(own_session, provider, monkeypatch):
    db = own_session
    scheduler._last_attempt.clear()

    def explode(*a, **kw):
        raise RuntimeError("kaboom")

    # 1) a job function that raises unexpectedly is caught inside run_job and logged
    monkeypatch.setitem(jobs._JOBS, "history", explode)
    monkeypatch.setitem(jobs._JOBS, "fx", explode)
    ran = scheduler.maintenance_tick()
    assert ran["fx"] == "error" and ran["history"] == "error"
    assert ran["events"] == "ok" and ran["fundamentals"] == "ok" and ran["profile"] == "ok"     # others still ran
    assert {e.job: e.status for e in logs(db)}["history"] == "error"
    assert "kaboom" in logs(db, "history")[-1].detail

    # 2) not retried immediately, successful jobs not repeated before their interval
    assert scheduler.maintenance_tick() == {}
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    assert set(scheduler.maintenance_tick(later)) == {"fx", "history"}
    assert set(scheduler.maintenance_tick(later + timedelta(days=8))) >= {"fx", "history", "events", "profile", "fundamentals"}

    # 3) even run_job itself blowing up / no DB does not escape the ticks
    monkeypatch.setattr(jobs, "run_job", explode)
    assert scheduler.quotes_tick()["status"] == "error"
    scheduler._last_attempt.clear()
    assert all(v == "error" for v in scheduler.maintenance_tick(later + timedelta(days=60)).values())
    import app.core.db as core_db

    monkeypatch.setattr(core_db, "SessionLocal", explode)
    assert "_error" in scheduler.maintenance_tick()
    scheduler._last_attempt.clear()


def test_run_job_without_database_returns_error(monkeypatch, provider):
    import app.core.db as core_db

    def no_db():
        raise ConnectionError("could not connect to server")

    monkeypatch.setattr(core_db, "SessionLocal", no_db)
    res = jobs.run_job("quotes")
    assert res["status"] == "error" and "could not connect" in res["errors"][0]["error"]


def test_scheduler_start_stop(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    assert scheduler.start() is False                       # disabled in tests (ENABLE_SCHEDULER=false)
    assert scheduler.status()["running"] is False
    monkeypatch.setattr(settings, "enable_scheduler", True)
    monkeypatch.setattr(scheduler, "quotes_tick", lambda: {})
    monkeypatch.setattr(scheduler, "maintenance_tick", lambda: {})
    try:
        assert scheduler.start() is True and scheduler.start() is True      # idempotent
        st = scheduler.status()
        assert st["running"] and {j["id"] for j in st["jobs"]} == {"market_quotes", "market_maintenance"}
    finally:
        scheduler.stop()
    assert scheduler.status()["running"] is False
    scheduler.stop()                                         # stopping twice is fine


def test_etf_holdings_stale(db, provider):
    acc = make_account(db)
    etf = make_instrument(db, "ETF", symbol="E.DE", type_=InstrumentType.etf)
    assert jobs.etf_holdings_stale(db) is False             # not held yet
    make_tx(db, acc, etf, TxType.buy, 1, 10)
    assert jobs.etf_holdings_stale(db) is True
    service.meta_set(etf, "etf_checked_at", datetime.now(timezone.utc).isoformat())
    db.flush()
    assert jobs.etf_holdings_stale(db) is False
    service.meta_set(etf, "etf_checked_at", (datetime.now(timezone.utc) - timedelta(days=40)).isoformat())
    db.flush()
    assert jobs.etf_holdings_stale(db) is True


def test_quotes_job_for_explicit_instrument_reports_missing_quote(db, provider):
    """After a symbol change the UI refreshes ONE instrument: an unknown symbol must not come back as "ok"."""
    inst = make_instrument(db, "Tippfehler AG", isin="DE000TIPP001", symbol="TIPPX.DE")
    db.commit()
    result = jobs.run_job("quotes", [inst.id], db=db, provider=provider)
    assert result["status"] == "error" and result["missing"] == ["TIPPX.DE"]
    assert any(e["symbol"] == "TIPPX.DE" and "kein Kurs" in e["error"] for e in result["errors"])
