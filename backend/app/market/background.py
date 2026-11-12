"""Background execution of `POST /market/refresh` (one refresh at a time, own DB session).

`scope="all"` can take minutes (Yahoo is throttled on purpose), so the request returns immediately
with a job descriptor; `GET /market/status` → `refresh` (or `GET /market/refresh/{job_id}`) reports
progress and the final result. State lives in memory: after a restart the last job is gone, the
persistent history is `market_data_log` (`status.jobs` / `status.recent`).
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_lock = threading.Lock()
_current: dict | None = None
_last: dict | None = None
_history: dict[str, dict] = {}
_HISTORY_MAX = 20

#: tests replace this to run the job inline (the test DB session lives inside a rolled-back transaction)
spawn: Callable[[Callable[[], None]], None] = lambda target: threading.Thread(  # noqa: E731
    target=target, name="market-refresh", daemon=True).start()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(job: dict | None) -> dict | None:
    return None if job is None else {k: v for k, v in job.items() if not k.startswith("_")}


def start(scope: str, instrument_ids: list[int] | None = None, force: bool = False,
          runner: Callable[..., dict] | None = None) -> dict:
    """Start a background refresh. If one is already running, that job is returned with
    `already_running=true` (no second job is queued)."""
    global _current
    from app.market import jobs

    runner = runner or jobs.run_job
    with _lock:
        if _current is not None:
            return {**_public(_current), "already_running": True}
        job = {
            "job_id": uuid.uuid4().hex[:12], "scope": scope, "instrument_ids": instrument_ids, "force": force,
            "status": "running", "background": True, "started_at": _now(), "finished_at": None,
            "progress": {"done": 0, "total": len(jobs.ALL_ORDER) if scope == "all" else 1, "current": None},
            "result": None,
        }
        _current = job
        _history[job["job_id"]] = job
        while len(_history) > _HISTORY_MAX:
            _history.pop(next(iter(_history)))

    def on_progress(done: int, total: int, current: str | None) -> None:
        job["progress"] = {"done": done, "total": total, "current": current}

    def work() -> None:
        global _current, _last
        try:
            result = runner(scope, instrument_ids, force=force, on_progress=on_progress)
        except Exception as exc:  # noqa: BLE001 – run_job never raises; belt and braces
            log.exception("background market refresh crashed")
            result = {"scope": scope, "status": "error", "errors": [{"error": f"{type(exc).__name__}: {exc}"[:500]}]}
        job["result"] = result
        job["status"] = result.get("status", "error")
        job["finished_at"] = _now()
        job["progress"] = {**job["progress"], "done": job["progress"]["total"], "current": None}
        with _lock:
            _last = job
            _current = None

    snapshot = _public(job)      # as started ("running"), even when `spawn` runs inline
    try:
        spawn(work)
    except Exception as exc:  # noqa: BLE001 – e.g. cannot start a thread
        with _lock:
            _current = None
        job.update(status="error", finished_at=_now(),
                   result={"scope": scope, "status": "error", "errors": [{"error": str(exc)}]})
        return _public(job)
    return snapshot


def get(job_id: str) -> dict | None:
    return _public(_history.get(job_id))


def state() -> dict:
    """For GET /market/status: `{running, current, last}` (`last` = last finished job incl. result)."""
    with _lock:
        return {"running": _current is not None, "current": _public(_current), "last": _public(_last)}


def reset() -> None:
    """Tests only."""
    global _current, _last
    with _lock:
        _current = _last = None
        _history.clear()
