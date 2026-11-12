"""FastAPI entrypoint. Routers are mounted under /api. The built frontend is served from /."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api import accounts, analytics, categories, documents, instruments, market, portfolio, transactions
from app.api import export as export_api
from app.api import settings as settings_api
from app.core.config import get_settings

log = logging.getLogger("app.main")


def _startup_recover_documents() -> None:
    """Re-queue documents a previous process left in uploaded/processing (crash, restart)."""
    from app.core.db import SessionLocal
    from app.ingest import runner

    try:
        with SessionLocal() as db:
            n = runner.recover_stale(db)
        if n:
            log.info("re-queued %s document(s) left unprocessed by the previous run", n)
    except Exception as exc:  # noqa: BLE001 – database down/not migrated: the app must still start
        runner.reset_recovery()      # try again lazily on the first GET /documents
        log.warning("could not re-queue unprocessed documents: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup must never fail because of the outside world: an unreachable database, Ollama or
    Yahoo only produces a log line – the affected endpoints report the problem themselves
    (`/api/health`, `/api/documents/llm/status`, `/api/market/status`)."""
    import asyncio

    from app.ingest import runner
    from app.market import scheduler as market_scheduler

    # ingestion: documents stuck in `processing` (off the event loop – waits for the DB at most once)
    try:
        await asyncio.wait_for(asyncio.to_thread(_startup_recover_documents), timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.warning("document recovery skipped: %s", exc)
    # market-data: background jobs (quotes, FX, events, fundamentals, ETF holdings); no-op if disabled
    try:
        market_scheduler.start()
    except Exception as exc:  # noqa: BLE001 – start() already swallows, belt and braces
        log.warning("market scheduler not started: %s", exc)
    try:
        yield
    finally:
        try:
            market_scheduler.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("market scheduler stop: %s", exc)
        runner.shutdown()


app = FastAPI(title="Vermögen", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    """Database down / not reachable: a clear 503 instead of a bare 500."""
    log.error("database error on %s %s: %s", request.method, request.url.path, str(exc).splitlines()[0])
    return JSONResponse(status_code=503, content={
        "detail": "Datenbank nicht erreichbar. Läuft PostgreSQL (docker compose up -d db)? DATABASE_URL in .env prüfen."})


app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(instruments.router, prefix="/api/instruments", tags=["instruments"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(export_api.router, prefix="/api/export", tags=["export"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])


@app.get("/api/health")
def health() -> dict:
    """Liveness + database check. Always 200; `status` is "ok" or "degraded" (`database: false`)."""
    from sqlalchemy import text

    from app.core.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return {"status": "ok", "database": True, "version": app.version}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": False, "version": app.version,
                "error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str):
    """Unknown API paths are a JSON 404 – they must not fall through to the SPA's index.html."""
    raise HTTPException(404, f"Unbekannter API-Pfad: /api/{rest}")


_dist = get_settings().frontend_dist
if (_dist / "index.html").is_file():
    if (_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = (_dist / full_path).resolve()
        if full_path and candidate.is_file() and _dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        # index.html must not be cached, otherwise the browser keeps pointing at old hashed assets
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
else:
    @app.get("/", include_in_schema=False)
    def no_frontend() -> dict:
        return {"app": "Vermögen", "api": "/api", "docs": "/docs",
                "hint": "Frontend ist nicht gebaut – `make start` (bzw. `cd frontend && npm run build`) ausführen."}
