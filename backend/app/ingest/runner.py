"""Background execution of document processing (small thread pool, own DB session per job).

Tests switch to inline mode (``set_inline(session)``) so processing happens synchronously inside
the test transaction.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.config import get_ingest_settings
from app.models import Document, DocumentStatus

log = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_inline_session: Session | None = None
_recovered = False


def set_inline(session: Session | None) -> None:
    global _inline_session
    _inline_session = session


def is_inline() -> bool:
    return _inline_session is not None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=get_ingest_settings().workers, thread_name_prefix="ingest")
        return _pool


def _job(doc_id: int, force_llm: bool) -> None:
    from app.core.db import SessionLocal
    from app.ingest.pipeline import process_document

    db = SessionLocal()
    try:
        process_document(db, doc_id, force_llm=force_llm)
    except Exception:  # noqa: BLE001
        log.exception("ingest job for document %s crashed", doc_id)
    finally:
        db.close()


def submit(doc_id: int, *, force_llm: bool = False) -> None:
    """Call AFTER the document row is committed."""
    if _inline_session is not None:
        from app.ingest.pipeline import process_document

        process_document(_inline_session, doc_id, force_llm=force_llm)
        return
    _get_pool().submit(_job, doc_id, force_llm)


def reset_recovery() -> None:
    """Allow `recover_stale` to run again (startup could not reach the database)."""
    global _recovered
    _recovered = False


def shutdown() -> None:
    """Stop accepting work on app shutdown. Running jobs finish; queued documents stay `uploaded`
    and are picked up by `recover_stale` on the next start."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def recover_stale(db: Session) -> int:
    """Re-queue documents left in uploaded/processing by a previous process (crash/restart).
    Runs once per process: at startup (app.main lifespan), else lazily on the first list request."""
    global _recovered
    if _recovered or _inline_session is not None:
        return 0
    _recovered = True
    ids = list(db.scalars(select(Document.id).where(Document.status.in_([DocumentStatus.uploaded, DocumentStatus.processing]))))
    for doc_id in ids:
        submit(doc_id)
    return len(ids)
