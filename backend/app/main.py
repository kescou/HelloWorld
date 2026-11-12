"""FastAPI entrypoint. Routers are mounted under /api. The built frontend is served from /."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import accounts, analytics, categories, documents, instruments, market, portfolio, transactions
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # market-data agent: start scheduler here via app.market.scheduler.start()/stop()
    yield


app = FastAPI(title="Vermögen", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(categories.router, prefix="/api/categories", tags=["categories"])
app.include_router(instruments.router, prefix="/api/instruments", tags=["instruments"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


_dist = get_settings().frontend_dist
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = _dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
