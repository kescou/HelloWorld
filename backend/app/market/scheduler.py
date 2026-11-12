"""Background scheduler (APScheduler). Enabled via settings.enable_scheduler.

Two recurring jobs only – robust for a laptop that is not always on:
* quotes_tick       every `quote_refresh_seconds`: quotes for held + watchlist instruments during rough
                    market hours (Mon–Fri 08–22 Europe/Berlin); new instruments are backfilled at any time
* maintenance_tick  every 30 min: runs whatever is overdue according to `market_data_log`
                    (FX + history + events + profile daily, fundamentals weekly, ETF holdings monthly)

Nothing in here may raise into the app: every job goes through jobs.run_job (catch + log + continue).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings

log = logging.getLogger("market.scheduler")
BERLIN = ZoneInfo("Europe/Berlin")

DAILY = timedelta(hours=20)
WEEKLY = timedelta(days=7)
MONTHLY = timedelta(days=30)
RETRY_AFTER_FAILURE = timedelta(hours=2)

_scheduler = None
_lock = threading.Lock()
_last_attempt: dict[str, datetime] = {}


def is_market_hours(now: datetime | None = None) -> bool:
    """Rough trading window covering XETRA/Tradegate (from 08:00) to the US close (22:00 Berlin time)."""
    now = (now or datetime.now(BERLIN)).astimezone(BERLIN)
    return now.weekday() < 5 and 8 <= now.hour < 22


def quotes_tick(now: datetime | None = None) -> dict:
    from app.market import jobs

    try:
        if is_market_hours(now):
            return jobs.run_job("quotes", collapse=True)
        # market closed: no quote polling, but still pick up newly added instruments (symbol + backfill)
        return jobs.run_job("quotes", collapse=True, quotes=False)
    except Exception as exc:  # noqa: BLE001 – belt and braces, run_job already catches
        log.warning("quotes_tick failed: %s", exc)
        return {"status": "error", "errors": [{"error": str(exc)}]}


def _due(db, job: str, interval: timedelta, now: datetime) -> bool:
    from app.market import jobs

    last_try = _last_attempt.get(job)
    if last_try and now - last_try < RETRY_AFTER_FAILURE:
        return False
    last_ok = jobs.last_success(db, job)
    return last_ok is None or now - last_ok >= interval


def maintenance_tick(now: datetime | None = None) -> dict:
    """Run overdue daily/weekly/monthly jobs. Returns {job: status}."""
    from app.market import jobs

    now = now or datetime.now(timezone.utc)
    ran: dict[str, str] = {}
    try:
        from app.core.db import SessionLocal

        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance_tick: no database session: %s", exc)
        return {"_error": str(exc)}
    try:
        plan = [("fx", DAILY), ("profile", DAILY), ("history", DAILY), ("events", DAILY),
                ("fundamentals", WEEKLY), ("etf", MONTHLY)]
        for job, interval in plan:
            try:
                due = _due(db, job, interval, now)
                if job == "etf" and not due:
                    last_try = _last_attempt.get(job)
                    due = (not last_try or now - last_try >= RETRY_AFTER_FAILURE) and jobs.etf_holdings_stale(db)
                if not due:
                    continue
                _last_attempt[job] = now
                ran[job] = jobs.run_job(job, db=db).get("status", "error")
            except Exception as exc:  # noqa: BLE001
                log.warning("maintenance job %s failed: %s", job, exc)
                ran[job] = "error"
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        jobs.prune_log(db)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    return ran


def start() -> bool:
    """Start the scheduler (idempotent). Returns True when running. Never raises."""
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        return False
    with _lock:
        if _scheduler is not None:
            return True
        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            sched = BackgroundScheduler(
                timezone=BERLIN,
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
            )
            now = datetime.now(BERLIN)
            sched.add_job(quotes_tick, "interval", seconds=max(30, int(settings.quote_refresh_seconds)),
                          id="market_quotes", next_run_time=now + timedelta(seconds=10))
            sched.add_job(maintenance_tick, "interval", minutes=30, id="market_maintenance",
                          next_run_time=now + timedelta(seconds=45))
            sched.start()
            _scheduler = sched
            log.info("market scheduler started (quotes every %ss)", settings.quote_refresh_seconds)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("market scheduler could not start: %s", exc)
            _scheduler = None
            return False


def stop() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("market scheduler shutdown: %s", exc)
            _scheduler = None


def status() -> dict:
    sched = _scheduler
    out = {"enabled": bool(get_settings().enable_scheduler), "running": bool(sched and sched.running),
           "market_hours": is_market_hours(), "jobs": []}
    if sched:
        for job in sched.get_jobs():
            out["jobs"].append({"id": job.id,
                                "next_run": job.next_run_time.isoformat() if job.next_run_time else None})
    return out
