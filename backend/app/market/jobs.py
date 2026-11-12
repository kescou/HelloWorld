"""Market-data jobs: the same functions run from the scheduler and from POST /market/refresh.

Guarantees
* a job never raises – remote/DB problems are caught, logged to `market_data_log`, and returned
* one failing instrument does not stop the others (except Yahoo rate-limiting, which aborts the batch)
* progress is committed per instrument
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.market import events as events_mod
from app.market import fundamentals as fundamentals_mod
from app.market import fx as fx_mod
from app.market import positions, service
from app.market.etf import service as etf_service
from app.market.providers import get_provider
from app.market.providers.base import MarketDataProvider, ProviderRateLimited
from app.models.core import Instrument, InstrumentType
from app.models.market import EtfHolding, MarketDataLog, PriceHistory

log = logging.getLogger("market.jobs")

SCOPES = ("quotes", "history", "events", "fundamentals", "etf", "fx", "profile", "all")
FUND_TYPES = (InstrumentType.etf, InstrumentType.fund)
MAX_BACKFILLS_PER_QUOTE_RUN = 5
MAX_SYMBOL_LOOKUPS_PER_RUN = 10
LOG_RETENTION_DAYS = 30
OFFLINE_ABORT_AFTER = 5          # first N instruments all fail -> assume the source is down, stop the batch
_scope_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


# ---------------------------------------------------------------- logging
def log_job(db: Session, job: str, status: str, detail: str | None, collapse: bool = False) -> None:
    """Write a row to market_data_log. collapse=True re-uses the previous row of the same job+status
    (used by the 2-minute quote job so the log does not grow by 700 rows a day)."""
    try:
        detail = (detail or "")[:4000] or None
        if collapse:
            last = db.scalar(select(MarketDataLog).where(MarketDataLog.job == job)
                             .order_by(MarketDataLog.id.desc()).limit(1))
            if last is not None and last.status == status:
                last.detail = detail
                last.created_at = datetime.now(timezone.utc)
                db.commit()
                return
        db.add(MarketDataLog(job=job, status=status, detail=detail))
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not write market_data_log")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def prune_log(db: Session) -> None:
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
        db.execute(delete(MarketDataLog).where(MarketDataLog.created_at < cutoff))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def last_success(db: Session, job: str) -> datetime | None:
    return db.scalar(select(func.max(MarketDataLog.created_at)).where(
        MarketDataLog.job == job, MarketDataLog.status.in_(["ok", "partial"])))


# ---------------------------------------------------------------- per-instrument loop helper
def _select(db: Session, instrument_ids: list[int] | None, default: Callable[[], list[Instrument]]) -> list[Instrument]:
    if instrument_ids:
        return list(db.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids)).order_by(Instrument.id)))
    return default()


def _each(db: Session, instruments: list[Instrument], fn: Callable[[Instrument], object]) -> dict:
    """Run fn per instrument with error isolation + commit. Returns counters and error list."""
    ok, errors, results = 0, [], {}
    for inst in instruments:
        try:
            results[inst.id] = fn(inst)
            db.commit()
            ok += 1
        except ProviderRateLimited as exc:
            db.rollback()
            errors.append({"instrument_id": inst.id, "symbol": inst.symbol, "error": str(exc)})
            errors.append({"instrument_id": None, "symbol": None, "error": "abgebrochen: Yahoo-Rate-Limit"})
            break
        except Exception as exc:  # noqa: BLE001 – isolation is the whole point
            db.rollback()
            log.warning("job step failed for %s (%s): %s", inst.symbol or inst.isin, inst.id, exc)
            errors.append({"instrument_id": inst.id, "symbol": inst.symbol, "error": f"{type(exc).__name__}: {exc}"[:300]})
            if ok == 0 and len(errors) >= OFFLINE_ABORT_AFTER and len(instruments) > OFFLINE_ABORT_AFTER:
                errors.append({"instrument_id": None, "symbol": None,
                               "error": f"abgebrochen: die ersten {OFFLINE_ABORT_AFTER} Abrufe schlugen fehl (offline?)"})
                break
    return {"processed": ok, "total": len(instruments), "errors": errors, "results": results}


def _ensure_symbols(db: Session, instruments: list[Instrument], provider: MarketDataProvider,
                    limit: int = MAX_SYMBOL_LOOKUPS_PER_RUN, force: bool = False) -> list[dict]:
    errors: list[dict] = []
    looked_up = 0
    now = datetime.now(timezone.utc)
    for inst in instruments:
        if inst.symbol or looked_up >= limit:
            continue
        failed_at = service.meta_get(inst, "symbol_lookup_failed_at")
        if failed_at and not force:
            try:
                if now - datetime.fromisoformat(failed_at) < timedelta(hours=24):
                    continue
            except ValueError:
                pass
        looked_up += 1
        try:
            if not service.resolve_symbol(db, inst, provider):
                errors.append({"instrument_id": inst.id, "symbol": None,
                               "error": f"kein Yahoo-Symbol für {inst.isin or inst.name} gefunden"})
            db.commit()
        except ProviderRateLimited as exc:
            db.rollback()
            errors.append({"instrument_id": inst.id, "symbol": None, "error": str(exc)})
            break
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"instrument_id": inst.id, "symbol": None, "error": f"{type(exc).__name__}: {exc}"[:300]})
    return errors


# ---------------------------------------------------------------- backfill of a new instrument
def backfill_instrument(db: Session, inst: Instrument, provider: MarketDataProvider) -> dict:
    """Everything a freshly added instrument needs: symbol, profile, full history, events, fundamentals, holdings."""
    steps: dict[str, object] = {}
    if not inst.symbol and not service.resolve_symbol(db, inst, provider):
        db.commit()
        return {"symbol": None, "error": "kein Yahoo-Symbol gefunden"}
    steps["symbol"] = inst.symbol
    db.commit()
    hist = None

    def step(name: str, fn: Callable[[], object]) -> None:
        try:
            steps[name] = fn()
            db.commit()
        except ProviderRateLimited:
            db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            steps[name] = f"error: {type(exc).__name__}: {exc}"[:300]

    step("profile", lambda: service.refresh_profile(db, inst, provider))

    def _history():
        nonlocal hist
        written, hist = service.refresh_history(db, inst, provider, full=True)
        return written

    step("history", _history)
    step("events", lambda: events_mod.refresh_events(db, inst, provider, hist))
    step("fundamentals", lambda: fundamentals_mod.refresh_fundamentals(db, inst, provider))
    if inst.type in FUND_TYPES:
        step("etf", lambda: etf_service.refresh_etf_holdings(db, inst, provider))
    return steps


def _needs_backfill(db: Session, instruments: list[Instrument]) -> list[Instrument]:
    ids = [i.id for i in instruments if i.symbol]
    if not ids:
        return []
    have = {r[0] for r in db.execute(select(PriceHistory.instrument_id).where(PriceHistory.instrument_id.in_(ids)).distinct())}
    out = []
    for inst in instruments:
        if not inst.symbol or inst.id in have:
            continue
        tried = service.meta_get(inst, "backfill_at")
        if tried:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(tried) < timedelta(hours=6):
                    continue
            except ValueError:
                pass
        out.append(inst)
    return out


# ---------------------------------------------------------------- scopes
def job_quotes(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
               force: bool = False, quotes: bool = True) -> dict:
    """Quotes for held + watchlist instruments; resolves missing symbols and backfills new instruments."""
    instruments = _select(db, instrument_ids, lambda: positions.tracked_instruments(db))
    errors = _ensure_symbols(db, instruments, provider, force=force or bool(instrument_ids))
    result: dict = {"requested": 0, "updated": 0, "missing": []}
    if quotes:
        result = service.refresh_quotes(db, instruments, provider)
        db.commit()
    # new instruments (also sold ones that still need history for the portfolio chart)
    candidates = instruments if instrument_ids else positions.tracked_instruments(db, include_past=True)
    todo = _needs_backfill(db, candidates)[:MAX_BACKFILLS_PER_QUOTE_RUN]
    backfilled = {}
    for inst in todo:
        try:
            service.meta_set(inst, "backfill_at", datetime.now(timezone.utc).isoformat())
            db.commit()
            backfilled[inst.id] = backfill_instrument(db, inst, provider)
        except ProviderRateLimited as exc:
            db.rollback()
            errors.append({"instrument_id": inst.id, "symbol": inst.symbol, "error": str(exc)})
            break
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append({"instrument_id": inst.id, "symbol": inst.symbol, "error": f"{type(exc).__name__}: {exc}"[:300]})
    if instrument_ids:
        # asked for these instruments explicitly (e.g. right after a symbol change): "no quote" is the answer the
        # user is waiting for, not a detail – report it instead of a silent ok
        by_symbol = {i.symbol: i for i in instruments if i.symbol}
        for sym in result.get("missing") or []:
            errors.append({"instrument_id": by_symbol[sym].id if sym in by_symbol else None, "symbol": sym,
                           "error": f"kein Kurs für {sym}: Symbol unbekannt oder Yahoo nicht erreichbar"})
    result.update({"backfilled": backfilled, "errors": errors, "total": len(instruments),
                   "processed": result.get("updated", 0)})
    return result


def job_history(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
                force: bool = False) -> dict:
    instruments = _select(db, instrument_ids, lambda: positions.tracked_instruments(db, include_past=True))
    errors = _ensure_symbols(db, instruments, provider)

    def one(inst: Instrument):
        if not inst.symbol:
            return 0
        written, hist = service.refresh_history(db, inst, provider, full=force)
        events_mod.sync_actions(db, inst, hist)
        if hist.full and hist.bars:
            service.meta_set(inst, "actions_full_at", datetime.now(timezone.utc).isoformat())
        events_mod.rebuild_estimates(db, inst)
        return written

    out = _each(db, instruments, one)
    out["errors"] = errors + out["errors"]
    out["rows"] = sum(v for v in out.pop("results").values() if isinstance(v, int))
    return out


def job_events(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
               force: bool = False) -> dict:
    instruments = [i for i in _select(db, instrument_ids, lambda: positions.tracked_instruments(db)) if i.symbol]
    out = _each(db, instruments, lambda inst: events_mod.refresh_events(db, inst, provider))
    out.pop("results")
    return out


def job_fundamentals(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
                     force: bool = False) -> dict:
    instruments = [i for i in _select(db, instrument_ids, lambda: positions.tracked_instruments(db))
                   if i.symbol and i.type in (InstrumentType.stock, *FUND_TYPES)]
    out = _each(db, instruments, lambda inst: fundamentals_mod.refresh_fundamentals(db, inst, provider))
    out.pop("results")
    return out


def job_etf(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
            force: bool = False) -> dict:
    instruments = [i for i in _select(db, instrument_ids, lambda: positions.tracked_instruments(db))
                   if i.type in FUND_TYPES]
    out = _each(db, instruments, lambda inst: etf_service.refresh_etf_holdings(db, inst, provider, force=force))
    results = out.pop("results")
    out["etfs"] = {str(k): {kk: vv for kk, vv in v.items() if kk != "attempts"} for k, v in results.items()}
    out["processed"] = sum(1 for r in results.values() if r.get("status") in ("ok", "kept"))
    for inst_id, res in results.items():
        if res.get("status") == "unavailable":
            out["errors"].append({"instrument_id": inst_id, "symbol": None,
                                  "error": "keine Holdings-Quelle erfolgreich: " + "; ".join(
                                      f"{a['provider']}: {a.get('error')}" for a in res.get("attempts", []))[:400]})
    return out


def job_fx(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
           force: bool = False) -> dict:
    out = fx_mod.update_fx(db, provider, full=True if force else None)
    db.commit()
    out.update({"processed": out.get("rows", 0), "errors": []})
    return out


def job_profile(db: Session, provider: MarketDataProvider, instrument_ids: list[int] | None = None,
                force: bool = False) -> dict:
    def default() -> list[Instrument]:
        return [i for i in positions.tracked_instruments(db, include_past=True)
                if force or not service.meta_get(i, "profile_at")]

    instruments = [i for i in _select(db, instrument_ids, default) if i.symbol]
    out = _each(db, instruments, lambda inst: service.refresh_profile(db, inst, provider, overwrite=False))
    out.pop("results")
    return out


_JOBS: dict[str, Callable[..., dict]] = {
    "fx": job_fx, "quotes": job_quotes, "profile": job_profile, "history": job_history, "events": job_events,
    "fundamentals": job_fundamentals, "etf": job_etf,
}
ALL_ORDER = ("fx", "quotes", "profile", "history", "events", "fundamentals", "etf")


def _status_of(result: dict) -> str:
    errors = result.get("errors") or []
    if not errors:
        return "ok"
    return "partial" if result.get("processed") else "error"


def _summary(result: dict) -> str:
    parts = [f"{k}={result[k]}" for k in ("processed", "total", "updated", "rows", "requested", "source", "latest")
             if result.get(k) is not None]
    if result.get("missing"):
        parts.append("missing=" + ",".join(result["missing"][:10]))
    if result.get("backfilled"):
        parts.append(f"backfilled={len(result['backfilled'])}")
    for err in (result.get("errors") or [])[:8]:
        parts.append(f"[{err.get('symbol') or err.get('instrument_id') or '-'}] {err.get('error')}")
    return "; ".join(parts)


def _run_one(db: Session, scope: str, provider: MarketDataProvider, instrument_ids: list[int] | None,
             force: bool, collapse: bool = False, **kwargs) -> dict:
    started = time.monotonic()
    lock = _scope_locks[scope]
    if not lock.acquire(blocking=False):      # scheduler and POST /refresh must not run the same scope twice
        return {"scope": scope, "status": "busy", "errors": [], "duration_ms": 0,
                "detail": "Job läuft bereits (Scheduler oder anderer Aufruf)"}
    try:
        result = _JOBS[scope](db, provider, instrument_ids, force, **kwargs)
        status = _status_of(result)
    except Exception as exc:  # noqa: BLE001 – offline, DB hiccup, parser bug: log and carry on
        log.warning("market job %s failed: %s", scope, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        result = {"errors": [{"instrument_id": None, "symbol": None, "error": f"{type(exc).__name__}: {exc}"[:500]}]}
        status = "error"
    finally:
        lock.release()
    result["scope"] = scope
    result["status"] = status
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    log_job(db, scope, status, _summary(result), collapse=collapse and status == "ok")
    return result


def run_job(scope: str, instrument_ids: list[int] | None = None, db: Session | None = None,
            provider: MarketDataProvider | None = None, force: bool = False, collapse: bool = False,
            on_progress: Callable[[int, int, str | None], None] | None = None, **kwargs) -> dict:
    """Run one scope (or "all"). Opens its own session when none is given. Never raises."""
    if scope not in SCOPES:
        return {"scope": scope, "status": "error", "errors": [{"error": f"unknown scope {scope!r}"}]}
    own = db is None
    try:
        if own:
            from app.core.db import SessionLocal

            db = SessionLocal()
        provider = provider or get_provider()
        if scope != "all":
            return _run_one(db, scope, provider, instrument_ids, force, collapse, **kwargs)
        results = {}
        for i, name in enumerate(ALL_ORDER):
            if on_progress is not None:
                on_progress(i, len(ALL_ORDER), name)
            results[name] = _run_one(db, name, provider, instrument_ids, force)
        statuses = {r["status"] for r in results.values()}
        status = "ok" if statuses == {"ok"} else ("error" if statuses == {"error"} else "partial")
        return {"scope": "all", "status": status, "jobs": results,
                "duration_ms": sum(r["duration_ms"] for r in results.values())}
    except Exception as exc:  # noqa: BLE001 – e.g. database not reachable
        log.warning("market job %s could not run: %s", scope, exc)
        return {"scope": scope, "status": "error", "errors": [{"error": f"{type(exc).__name__}: {exc}"[:500]}]}
    finally:
        if own and db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def etf_holdings_stale(db: Session, max_age_days: int = 30) -> bool:
    """True when a tracked ETF has no holdings or the newest fetch is older than max_age_days."""
    funds = [i for i in positions.tracked_instruments(db) if i.type in FUND_TYPES]
    if not funds:
        return False
    have = {r[0] for r in db.execute(select(EtfHolding.etf_instrument_id).distinct())}
    cutoff = date.today() - timedelta(days=max_age_days)
    for inst in funds:
        checked = service.meta_get(inst, "etf_checked_at")
        if inst.id not in have and not checked:
            return True
        if checked:
            try:
                if datetime.fromisoformat(checked).date() < cutoff:
                    return True
            except ValueError:
                return True
    return False
