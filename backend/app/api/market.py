"""Market-data API (prefix /api/market). Contract: docs/ARCHITECTURE.md, section "Marktdaten"."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.market import events as events_mod
from app.market import fundamentals as fundamentals_mod
from app.market import http as market_http
from app.market import background, jobs, positions, scheduler, service
from app.market.etf import registry as etf_registry
from app.market.etf import service as etf_service
from app.market.etf.base import HoldingsUnavailable
from app.market.lookthrough import compute_lookthrough
from app.market.providers import get_provider
from app.market.providers.base import ProviderError
from app.market.util import to_decimal
from app.models.core import FxRate, Instrument
from app.models.market import (
    EtfHolding,
    EventType,
    Fundamentals,
    InstrumentEvent,
    MarketDataLog,
    PriceHistory,
    Quote,
)
from app.schemas.market import (
    EventCreate,
    EventOut,
    EventUpdate,
    HistoryPoint,
    QuoteOut,
    RefreshRequest,
    SearchResultOut,
)

router = APIRouter()


def _instrument(db: Session, instrument_id: int) -> Instrument:
    inst = db.get(Instrument, instrument_id)
    if inst is None:
        raise HTTPException(404, "Instrument nicht gefunden")
    return inst


def _parse_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    try:
        return [int(x) for x in raw.replace(";", ",").split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(422, "instrument_ids muss eine kommagetrennte Liste von IDs sein") from exc


# ---------------------------------------------------------------- refresh / status
@router.post("/refresh")
def refresh(
    body: RefreshRequest | None = None,
    wait: bool | None = Query(None, description="true: synchron ausführen; false: im Hintergrund. "
                                                "Standard: scope=all im Hintergrund, alles andere synchron"),
    db: Session = Depends(get_db),
) -> dict:
    """Run the scheduler jobs on demand. Never fails with 5xx because of an unreachable source –
    look at `status` / `errors` in the result.

    * synchronous (default for every scope except `all`, or `?wait=true`): the job result
      `{scope, status: ok|partial|error|busy, errors, duration_ms, ...}` (`all`: `jobs: {scope: result}`)
    * background (default for `all`, or `?wait=false`): returns at once with
      `{job_id, scope, status: "running", background: true, started_at, progress}`; poll
      `GET /market/status` → `refresh` or `GET /market/refresh/{job_id}`.
    """
    body = body or RefreshRequest()
    run_in_background = (body.scope == "all") if wait is None else not wait
    if run_in_background:
        return background.start(body.scope, body.instrument_ids, body.force)
    return jobs.run_job(body.scope, body.instrument_ids, db=db, force=body.force)


@router.get("/refresh/{job_id}")
def refresh_job(job_id: str) -> dict:
    """State of a background refresh: `status` running → ok|partial|error, `result` = job result when finished."""
    job = background.get(job_id)
    if job is None:
        raise HTTPException(404, "Unbekannter Job (nach einem Neustart sind Hintergrund-Jobs vergessen)")
    return job


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    last_ids = select(func.max(MarketDataLog.id)).group_by(MarketDataLog.job)
    last = list(db.scalars(select(MarketDataLog).where(MarketDataLog.id.in_(last_ids)).order_by(MarketDataLog.job)))
    recent = list(db.scalars(select(MarketDataLog).order_by(MarketDataLog.id.desc()).limit(30)))
    tracked = positions.tracked_instruments(db)
    tracked_ids = [i.id for i in tracked]
    quotes_n, quotes_latest = 0, None
    if tracked_ids:
        quotes_n, quotes_latest = db.execute(
            select(func.count(), func.max(Quote.updated_at)).where(Quote.instrument_id.in_(tracked_ids))).one()
    fx_latest = db.scalar(select(func.max(FxRate.date)))

    def row(entry: MarketDataLog) -> dict:
        return {"id": entry.id, "job": entry.job, "status": entry.status, "detail": entry.detail,
                "created_at": entry.created_at.isoformat() if entry.created_at else None}

    return {
        "provider": get_provider().name,
        "scheduler": scheduler.status(),
        "refresh": background.state(),
        "jobs": [row(e) for e in last],
        "recent": [row(e) for e in recent],
        "errors": [row(e) for e in recent if e.status != "ok"][:10],
        "counts": {
            "tracked_instruments": len(tracked),
            "without_symbol": sum(1 for i in tracked if not i.symbol),
            "quotes": quotes_n,
            "price_history_rows": db.scalar(select(func.count()).select_from(PriceHistory)),
            "events": db.scalar(select(func.count()).select_from(InstrumentEvent)),
            "etfs_with_holdings": db.scalar(select(func.count(func.distinct(EtfHolding.etf_instrument_id)))),
            "fundamentals": db.scalar(select(func.count()).select_from(Fundamentals)),
        },
        "quotes_updated_at": quotes_latest.isoformat() if quotes_latest else None,
        "fx_latest_date": fx_latest.isoformat() if fx_latest else None,
        "sources": {
            "quotes": "Yahoo Finance (yfinance) – kostenlos, bis ~15 min verzögert",
            "fx": "EZB-Referenzkurse (täglich ~16 Uhr), Yahoo als Fallback",
            "etf_holdings": {name: prov.status for name, prov in etf_registry.providers().items()}
            | {"yahoo_top10": "fallback", "manual": "upload"},
        },
    }


# ---------------------------------------------------------------- quotes / history
@router.get("/quotes", response_model=list[QuoteOut])
def quotes(instrument_ids: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    ids = _parse_ids(instrument_ids)
    if ids is None:
        ids = [i.id for i in positions.tracked_instruments(db)]
    if not ids:
        return []
    out = []
    for q in db.scalars(select(Quote).where(Quote.instrument_id.in_(ids)).order_by(Quote.instrument_id)):
        price = float(q.price)
        prev = float(q.previous_close) if q.previous_close is not None else None
        change = price - prev if prev is not None else None
        out.append({
            "instrument_id": q.instrument_id, "price": price, "previous_close": prev,
            "change": round(change, 6) if change is not None else None,
            "change_pct": round(change / prev * 100, 4) if change is not None and prev else None,
            "currency": q.currency, "market_time": q.market_time, "updated_at": q.updated_at, "source": q.source,
        })
    return out


_RANGES = {"1W": 7, "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "2Y": 731, "3Y": 1096, "5Y": 1827, "10Y": 3653}


def range_start(range_: str | None, today: date | None = None) -> date | None:
    today = today or date.today()
    key = (range_ or "1Y").upper()
    if key in ("MAX", "ALL"):
        return None
    if key == "YTD":
        return date(today.year, 1, 1)
    if key not in _RANGES:
        raise HTTPException(422, f"range muss eines von {', '.join(_RANGES)}, YTD, MAX sein")
    return today - timedelta(days=_RANGES[key])


@router.get("/history/{instrument_id}", response_model=list[HistoryPoint])
def history(instrument_id: int, range: str = "1Y", db: Session = Depends(get_db)) -> list[dict]:  # noqa: A002
    _instrument(db, instrument_id)
    start = range_start(range)
    stmt = select(PriceHistory.date, PriceHistory.close).where(PriceHistory.instrument_id == instrument_id)
    if start is not None:
        stmt = stmt.where(PriceHistory.date >= start)
    return [{"date": d, "close": float(c)} for d, c in db.execute(stmt.order_by(PriceHistory.date))]


# ---------------------------------------------------------------- events
def _parse_types(types: str | None) -> list[EventType] | None:
    if not types:
        return None
    try:
        return [EventType(t.strip()) for t in types.split(",") if t.strip()]
    except ValueError as exc:
        raise HTTPException(422, f"Unbekannter Termin-Typ. Erlaubt: {', '.join(t.value for t in EventType)}") from exc


@router.get("/events", response_model=list[EventOut])
def list_events(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    types: str | None = None,
    instrument_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Events of held + watchlist instruments (default window: −30 … +365 days)."""
    return events_mod.list_events(db, date_from, date_to, _parse_types(types), instrument_id)


def _event_out(db: Session, ev: InstrumentEvent) -> dict:
    return events_mod.event_to_dict(ev, db.get(Instrument, ev.instrument_id))


@router.post("/events", response_model=EventOut, status_code=201)
def create_event(body: EventCreate, db: Session = Depends(get_db)) -> dict:
    """Manual event (AGM, investor day, …). Same instrument+type+date replaces the existing row."""
    inst = _instrument(db, body.instrument_id)
    ev = events_mod.upsert_event(
        db, inst.id, body.type, body.date, amount=body.amount, currency=body.currency or (inst.currency if body.amount else None),
        title=body.title, url=body.url, is_estimate=body.is_estimate, source="manual")
    db.commit()
    return _event_out(db, ev)


@router.patch("/events/{event_id}", response_model=EventOut)
def update_event(event_id: int, body: EventUpdate, db: Session = Depends(get_db)) -> dict:
    ev = db.get(InstrumentEvent, event_id)
    if ev is None:
        raise HTTPException(404, "Termin nicht gefunden")
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if key == "amount":
            value = to_decimal(value, 6)
        if key in ("type", "date", "is_estimate") and value is None:
            continue
        setattr(ev, key, value)
    ev.source = "manual"           # edited rows are protected from provider syncs
    ev.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Für dieses Instrument existiert bereits ein Termin dieses Typs an dem Datum") from exc
    return _event_out(db, ev)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, db: Session = Depends(get_db)) -> None:
    ev = db.get(InstrumentEvent, event_id)
    if ev is None:
        raise HTTPException(404, "Termin nicht gefunden")
    db.delete(ev)
    db.commit()


@router.post("/events/import-ics")
async def import_ics(
    instrument_id: int | None = Query(None),
    instrument_id_form: int | None = Form(None, alias="instrument_id"),
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    text: str | None = Form(None),
    only_future: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict:
    """Import an IR calendar (ICS) for an instrument: upload a file, paste the text or pass a URL."""
    inst_id = instrument_id if instrument_id is not None else instrument_id_form
    if inst_id is None:
        raise HTTPException(422, "instrument_id fehlt")
    inst = _instrument(db, inst_id)
    if file is not None:
        content: bytes | str = await file.read()
    elif text:
        content = text
    elif url:
        if not url.lower().startswith(("http://", "https://", "webcal://")):
            raise HTTPException(422, "url muss mit http(s):// oder webcal:// beginnen")
        try:
            content = market_http.get_bytes(url.replace("webcal://", "https://", 1), timeout=30)
        except market_http.RemoteError as exc:
            raise HTTPException(502, f"ICS konnte nicht geladen werden: {exc}") from exc
    else:
        raise HTTPException(422, "Bitte file, text oder url angeben")
    result = events_mod.import_ics(db, inst, content, only_future=only_future)
    if result["parsed"] == 0:
        raise HTTPException(422, "Keine Termine (VEVENT) in der Datei gefunden")
    db.commit()
    return result


# ---------------------------------------------------------------- fundamentals
#: after a failed on-demand fetch (offline, unknown symbol) GET does not try again for this long
FUNDAMENTALS_RETRY = timedelta(minutes=15)
_fundamentals_failed: dict[int, datetime] = {}


@router.get("/fundamentals/{instrument_id}")
def fundamentals(instrument_id: int, refresh: bool = False, db: Session = Depends(get_db)) -> dict:
    """aktienfinder-style data (layout: app/market/README.md). `available=false` until the first fetch."""
    inst = _instrument(db, instrument_id)
    row = db.get(Fundamentals, instrument_id)
    now = datetime.now(timezone.utc)
    cooling_down = (failed_at := _fundamentals_failed.get(instrument_id)) is not None and now - failed_at < FUNDAMENTALS_RETRY
    if refresh or (row is None and not cooling_down):
        try:
            if inst.symbol:
                fundamentals_mod.refresh_fundamentals(db, inst, get_provider())
                db.commit()
                row = db.get(Fundamentals, instrument_id)
                _fundamentals_failed.pop(instrument_id, None)
        except Exception as exc:  # noqa: BLE001 – offline etc.: fall back to what we have
            db.rollback()
            _fundamentals_failed[instrument_id] = now      # do not block every page view while offline
            jobs.log_job(db, "fundamentals", "error", f"[{inst.symbol}] {type(exc).__name__}: {exc}")
            row = db.get(Fundamentals, instrument_id)
    data = dict(row.data) if row is not None and row.data else fundamentals_mod.empty_fundamentals()
    data["instrument_id"] = instrument_id
    return data


# ---------------------------------------------------------------- ETF holdings / look-through
@router.get("/etf/{instrument_id}/holdings")
def etf_holdings(instrument_id: int, db: Session = Depends(get_db)) -> dict:
    return etf_service.get_holdings(db, _instrument(db, instrument_id))


@router.post("/etf/{instrument_id}/holdings/upload")
async def etf_holdings_upload(
    instrument_id: int,
    file: UploadFile = File(...),
    as_of: date | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    """Manual CSV/XLSX upload (columns are detected: Name, ISIN, Gewicht/Weight, Sektor, Land, Ticker)."""
    inst = _instrument(db, instrument_id)
    content = await file.read()
    try:
        result = etf_service.upload_holdings(db, inst, content, file.filename, as_of)
    except HoldingsUnavailable as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return result | etf_service.get_holdings(db, inst)


@router.post("/etf/{instrument_id}/holdings/refresh")
def etf_holdings_refresh(instrument_id: int, force: bool = False, db: Session = Depends(get_db)) -> dict:
    """Run the issuer/Yahoo fetch chain for one ETF now."""
    inst = _instrument(db, instrument_id)
    try:
        result = etf_service.refresh_etf_holdings(db, inst, get_provider(), force=force)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
    return result


@router.delete("/etf/{instrument_id}/holdings", status_code=204)
def etf_holdings_delete(instrument_id: int, db: Session = Depends(get_db)) -> None:
    _instrument(db, instrument_id)
    db.query(EtfHolding).filter(EtfHolding.etf_instrument_id == instrument_id).delete()
    db.commit()


@router.get("/lookthrough")
def lookthrough(account_id: int | None = None, db: Session = Depends(get_db)) -> dict:
    return compute_lookthrough(db, account_id)


# ---------------------------------------------------------------- search / fx
@router.get("/search", response_model=list[SearchResultOut])
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=25)) -> list[dict]:
    """Yahoo search (name or ISIN -> symbol); OpenFIGI fallback for ISINs. `preferred` marks the listing
    the app would pick automatically (XETRA if available, else primary)."""
    try:
        results = service.search_symbols(get_provider(), q, limit)
    except ProviderError as exc:
        raise HTTPException(503, f"Suche derzeit nicht möglich: {exc}") from exc
    best = service.pick_listing(results)
    return [asdict(r) | {"preferred": r is best} for r in results]


@router.get("/fx")
def fx_latest(db: Session = Depends(get_db)) -> dict:
    """Latest known FX rates (units of currency per 1 EUR)."""
    latest = db.scalar(select(func.max(FxRate.date)))
    if latest is None:
        return {"date": None, "base": "EUR", "rates": {}}
    sub = select(FxRate.currency, func.max(FxRate.date).label("d")).group_by(FxRate.currency).subquery()
    rows = db.execute(select(FxRate.currency, FxRate.rate, FxRate.date).join(
        sub, (sub.c.currency == FxRate.currency) & (sub.c.d == FxRate.date)).order_by(FxRate.currency))
    return {"date": latest.isoformat(), "base": "EUR",
            "rates": {c: float(r) for c, r, _d in rows}}
