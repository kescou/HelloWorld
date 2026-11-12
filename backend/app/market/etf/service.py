"""Fetch chain + persistence for ETF holdings.

Chain per ETF:  custom URL (meta.market.holdings_url)  ->  issuer provider  ->  Yahoo top-10.
Rules: replace-all per ETF; a manual upload is never overwritten automatically (force=True does);
a full issuer list is never downgraded to the Yahoo top-10.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.market import http
from app.market.etf import registry
from app.market.etf.base import HoldingRow, HoldingsResult, HoldingsUnavailable
from app.market.etf.tabular import parse_holdings_file
from app.market.providers.base import MarketDataProvider, ProviderError
from app.market.util import is_isin
from app.models.core import Instrument
from app.models.market import EtfHolding

log = logging.getLogger("market.etf")

FULL_SOURCES_KEEP = {"manual"}
TOP10_SOURCE = "yahoo_top10"


def store_holdings(db: Session, etf_id: int, result: HoldingsResult) -> int:
    """Replace all holdings of the ETF."""
    db.execute(delete(EtfHolding).where(EtfHolding.etf_instrument_id == etf_id))
    rows = []
    for h in result.rows:
        weight = round(Decimal(repr(float(h.weight))), 6)
        if weight == 0 or abs(weight) >= 1000:
            continue
        isin = h.isin.strip().upper() if h.isin else None
        rows.append({
            "etf_instrument_id": etf_id, "holding_isin": isin if is_isin(isin) else None,
            "holding_symbol": h.symbol[:32] if h.symbol else None, "holding_name": (h.name or "?")[:200],
            "weight": weight, "sector": h.sector[:80] if h.sector else None,
            "country": h.country[:80] if h.country else None,
            "asset_class": h.asset_class[:40] if h.asset_class else None,
            "as_of": result.as_of or date.today(), "source": result.source[:30],
        })
    if rows:
        db.execute(EtfHolding.__table__.insert(), rows)
    db.flush()
    db.expire_all()
    return len(rows)


def current_source(db: Session, etf_id: int) -> str | None:
    return db.scalar(select(EtfHolding.source).where(EtfHolding.etf_instrument_id == etf_id).limit(1))


def fetch_custom_url(url: str) -> HoldingsResult:
    try:
        rows, as_of = parse_holdings_file(http.get_bytes(url, timeout=60))
    except http.RemoteError as exc:
        raise HoldingsUnavailable(str(exc)) from exc
    return HoldingsResult(rows=rows, as_of=as_of, source="custom_url")


def fetch_yahoo_top10(inst: Instrument, provider: MarketDataProvider) -> HoldingsResult:
    if not inst.symbol:
        raise HoldingsUnavailable("kein Yahoo-Symbol")
    try:
        top = provider.get_fund_top_holdings(inst.symbol)
    except ProviderError as exc:
        raise HoldingsUnavailable(str(exc)) from exc
    if not top:
        raise HoldingsUnavailable("Yahoo liefert keine Top-Holdings")
    return HoldingsResult(
        rows=[HoldingRow(name=h.name, weight=h.weight, symbol=h.symbol, asset_class="equity") for h in top],
        as_of=date.today(), source=TOP10_SOURCE)


def refresh_etf_holdings(db: Session, inst: Instrument, provider: MarketDataProvider | None = None,
                         force: bool = False) -> dict:
    """Run the fetch chain for one ETF. Never raises for remote problems – returns what happened."""
    from app.market.service import meta_get, meta_set

    existing = current_source(db, inst.id)
    if existing in FULL_SOURCES_KEEP and not force:
        return {"status": "kept", "source": existing, "detail": "manueller Upload bleibt erhalten (force=true überschreibt)"}
    attempts: list[dict] = []
    result: HoldingsResult | None = None

    chain: list[tuple[str, callable]] = []
    custom = meta_get(inst, "holdings_url")
    if custom:
        chain.append(("custom_url", lambda: fetch_custom_url(custom)))
    issuer = registry.issuer_of(inst)
    if issuer and issuer in registry.providers():
        prov = registry.providers()[issuer]
        chain.append((issuer, lambda: prov.fetch(inst)))
    for name, fn in chain:
        try:
            candidate = fn()
            if not candidate.rows:
                raise HoldingsUnavailable("leere Liste")
            result = candidate
            attempts.append({"provider": name, "ok": True, "rows": len(candidate.rows)})
            break
        except HoldingsUnavailable as exc:
            attempts.append({"provider": name, "ok": False, "error": str(exc)[:300]})
        except Exception as exc:  # noqa: BLE001 – a broken issuer parser must not kill the job
            log.exception("ETF provider %s crashed for %s", name, inst.isin)
            attempts.append({"provider": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
    if result is None and provider is not None:
        if existing and existing != TOP10_SOURCE and not force:
            attempts.append({"provider": TOP10_SOURCE, "ok": False, "error": f"übersprungen – vorhandene '{existing}'-Daten bleiben"})
        else:
            try:
                result = fetch_yahoo_top10(inst, provider)
                attempts.append({"provider": TOP10_SOURCE, "ok": True, "rows": len(result.rows)})
            except HoldingsUnavailable as exc:
                attempts.append({"provider": TOP10_SOURCE, "ok": False, "error": str(exc)[:300]})
    meta_set(inst, "etf_attempts", attempts)
    meta_set(inst, "etf_checked_at", datetime.now(timezone.utc).isoformat())
    if issuer and not inst.fund_provider:
        inst.fund_provider = issuer
    if result is None:
        db.flush()
        return {"status": "unavailable", "source": existing, "attempts": attempts}
    n = store_holdings(db, inst.id, result)
    return {"status": "ok", "source": result.source, "rows": n, "coverage_pct": min(100.0, result.coverage_pct),
            "as_of": result.as_of.isoformat() if result.as_of else None, "attempts": attempts}


def upload_holdings(db: Session, inst: Instrument, content: bytes, filename: str | None = None,
                    as_of: date | None = None) -> dict:
    """Manual CSV/XLSX/JSON upload – always works, flexible column detection."""
    rows, detected = parse_holdings_file(content, filename)
    result = HoldingsResult(rows=rows, as_of=as_of or detected or date.today(), source="manual")
    n = store_holdings(db, inst.id, result)
    return {"status": "ok", "source": "manual", "rows": n, "coverage_pct": min(100.0, result.coverage_pct),
            "as_of": result.as_of.isoformat()}


def get_holdings(db: Session, inst: Instrument) -> dict:
    """Contract shape of GET /market/etf/{id}/holdings (+ residual 'Sonstige/Unbekannt')."""
    from app.market.service import meta_get

    rows = list(db.scalars(select(EtfHolding).where(EtfHolding.etf_instrument_id == inst.id)
                           .order_by(EtfHolding.weight.desc(), EtfHolding.id)))
    coverage = max(0.0, min(100.0, float(sum((r.weight for r in rows), Decimal(0)))))
    return {
        "instrument_id": inst.id, "name": inst.name, "isin": inst.isin,
        "as_of": rows[0].as_of.isoformat() if rows and rows[0].as_of else None,
        "source": rows[0].source if rows else None,
        "issuer": registry.issuer_of(inst),
        "coverage_pct": round(coverage, 4), "residual_pct": round(100.0 - coverage, 4) if rows else None,
        "residual_label": "Sonstige/Unbekannt", "count": len(rows),
        "holdings": [{"name": r.holding_name, "isin": r.holding_isin, "symbol": r.holding_symbol,
                      "weight": float(r.weight), "sector": r.sector, "country": r.country,
                      "asset_class": r.asset_class} for r in rows],
        "attempts": meta_get(inst, "etf_attempts", []),
    }
