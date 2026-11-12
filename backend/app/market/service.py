"""DB-facing market-data operations: symbol resolution, quotes, price history, instrument profile.

All functions take a Session + a provider, flush but do not commit (jobs commit per instrument).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.market import http
from app.market.providers import openfigi
from app.market.providers.base import (
    HistoryData,
    MarketDataProvider,
    ProviderError,
    QuoteData,
    SearchResult,
)
from app.market.util import is_isin, normalize_name, to_decimal
from app.models.core import Instrument, InstrumentType
from app.models.market import PriceHistory, Quote

log = logging.getLogger("market.service")

GERMAN_SUFFIXES = (".DE",)                       # XETRA
EUR_SUFFIXES = (".DE", ".F", ".SG", ".MU", ".DU", ".HM", ".BE", ".AS", ".PA", ".MI", ".MC", ".BR", ".VI", ".IR")


# ---------------------------------------------------------------- instrument.meta helpers
def meta_get(inst: Instrument, key: str, default=None):
    return ((inst.meta or {}).get("market") or {}).get(key, default)


def meta_set(inst: Instrument, key: str, value) -> None:
    meta = dict(inst.meta or {})
    market = dict(meta.get("market") or {})
    if value is None:
        market.pop(key, None)
    else:
        market[key] = value
    meta["market"] = market
    inst.meta = meta
    flag_modified(inst, "meta")


# ---------------------------------------------------------------- symbol resolution
def pick_listing(results: list[SearchResult], prefer_types: tuple[str, ...] = ("stock", "etf", "fund")) -> SearchResult | None:
    """Prefer the XETRA (.DE) listing, else the best-scored (primary) one."""
    cands = [r for r in results if r.type in prefer_types] or list(results)
    if not cands:
        return None
    for r in cands:
        if r.symbol.upper().endswith(GERMAN_SUFFIXES):
            return r
    return cands[0]


def search_symbols(provider: MarketDataProvider, query: str, limit: int = 10) -> list[SearchResult]:
    """Yahoo search (name or ISIN); for ISINs also the XETRA sibling and an OpenFIGI fallback."""
    query = (query or "").strip()
    if not query:
        return []
    results: list[SearchResult] = []
    try:
        results = provider.search(query, limit)
    except ProviderError as exc:
        log.warning("search %r failed: %s", query, exc)
    isin = query.upper() if is_isin(query) else None
    if isin:
        for r in results:
            r.isin = isin
        if results and not any(r.symbol.upper().endswith(GERMAN_SUFFIXES) for r in results):
            # Yahoo returns just one listing for an ISIN – look for the XETRA line of the same company by name
            first = results[0]
            if first.name:
                try:
                    key = normalize_name(first.name)
                    for r in provider.search(first.name, 15):
                        if (r.symbol.upper().endswith(GERMAN_SUFFIXES) and r.type == first.type
                                and normalize_name(r.name) == key
                                and r.symbol not in {x.symbol for x in results}):
                            r.isin = isin
                            results.insert(0, r)
                            break
                except ProviderError:
                    pass
        if not results:
            try:
                figi = openfigi.lookup_isin(isin)
            except http.RemoteError as exc:
                log.warning("openfigi %s failed: %s", isin, exc)
                figi = []
            # keep only candidates Yahoo actually knows (cheap quote probe for the best few)
            probe = [r.symbol for r in figi[:4]]
            known: dict[str, QuoteData] = {}
            if probe:
                try:
                    known = provider.get_quotes(probe)
                except ProviderError:
                    known = {}
            for r in figi:
                if r.symbol in known:
                    r.currency = known[r.symbol].currency
                    results.append(r)
            if not results:
                results = figi[:5]       # unverified, still better than nothing for the UI
    return results[:limit]


def resolve_symbol(db: Session, inst: Instrument, provider: MarketDataProvider) -> str | None:
    """Fill `instrument.symbol` (Yahoo ticker) from ISIN or name. Returns the symbol or None."""
    if inst.symbol:
        return inst.symbol
    results: list[SearchResult] = []
    if inst.isin:
        results = search_symbols(provider, inst.isin)
    if not results and inst.name and not inst.isin:
        results = search_symbols(provider, inst.name)
    types = ("etf", "fund", "stock") if inst.type in (InstrumentType.etf, InstrumentType.fund) else ("stock", "etf", "fund")
    best = pick_listing(results, types)
    if best is None:
        meta_set(inst, "symbol_lookup_failed_at", datetime.now(timezone.utc).isoformat())
        db.flush()
        return None
    inst.symbol = best.symbol[:32]
    if best.currency:
        inst.currency = best.currency
    meta_set(inst, "symbol_source", best.source)
    meta_set(inst, "symbol_lookup_failed_at", None)
    db.flush()
    return inst.symbol


# ---------------------------------------------------------------- quotes
def upsert_quote(db: Session, inst: Instrument, q: QuoteData, source: str = "yahoo") -> Quote:
    row = db.get(Quote, inst.id)
    currency = (q.currency or inst.currency or "EUR")[:3]
    now = datetime.now(timezone.utc)
    if row is None:
        row = Quote(instrument_id=inst.id, price=to_decimal(q.price, 8), currency=currency)
        db.add(row)
    row.price = to_decimal(q.price, 8)
    row.previous_close = to_decimal(q.previous_close, 8)
    row.currency = currency
    row.market_time = q.market_time
    row.source = source
    row.updated_at = now
    if q.currency and inst.currency != currency:
        inst.currency = currency
    return row


def refresh_quotes(db: Session, instruments: list[Instrument], provider: MarketDataProvider) -> dict:
    by_symbol: dict[str, list[Instrument]] = {}
    for inst in instruments:
        if inst.symbol:
            by_symbol.setdefault(inst.symbol, []).append(inst)
    if not by_symbol:
        return {"requested": 0, "updated": 0, "missing": []}
    quotes = provider.get_quotes(list(by_symbol))
    updated = 0
    for sym, q in quotes.items():
        for inst in by_symbol.get(sym, []):
            upsert_quote(db, inst, q, provider.name)
            updated += 1
    db.flush()
    return {"requested": len(by_symbol), "updated": updated,
            "missing": sorted(s for s in by_symbol if s not in quotes)}


# ---------------------------------------------------------------- history
def last_history_date(db: Session, instrument_id: int) -> date | None:
    return db.scalar(select(func.max(PriceHistory.date)).where(PriceHistory.instrument_id == instrument_id))


def _volume(value: int | None) -> int | None:
    """price_history.volume is a 32-bit INTEGER – clamp (crypto / penny-stock volumes exceed it)."""
    if value is None:
        return None
    return max(0, min(int(value), 2_147_483_647))


def store_bars(db: Session, instrument_id: int, hist: HistoryData) -> int:
    if not hist.bars:
        return 0
    dmin = min(b.date for b in hist.bars)
    existing = {r.date: r for r in db.scalars(
        select(PriceHistory).where(PriceHistory.instrument_id == instrument_id, PriceHistory.date >= dmin))}
    new, written = [], 0
    seen: set[date] = set()
    for b in hist.bars:
        if b.date in seen:
            continue
        seen.add(b.date)
        close, adj = to_decimal(b.close, 8), to_decimal(b.adj_close, 8)
        row = existing.get(b.date)
        if row is None:
            new.append({"instrument_id": instrument_id, "date": b.date, "close": close, "adj_close": adj,
                        "volume": _volume(b.volume)})
        elif row.close != close or row.adj_close != adj:
            row.close, row.adj_close, row.volume = close, adj, _volume(b.volume)
            written += 1
    for i in range(0, len(new), 5000):
        db.execute(PriceHistory.__table__.insert(), new[i:i + 5000])
    db.flush()
    return written + len(new)


def refresh_history(db: Session, inst: Instrument, provider: MarketDataProvider, full: bool = False) -> tuple[int, HistoryData]:
    """Backfill (max range) when empty, else incremental from the last stored day (7-day overlap).

    Yahoo back-adjusts closes after a split, so a split inside the incremental window triggers a full reload.
    Returns (rows written, HistoryData) – the dividends/splits in it feed the event sync.
    """
    if not inst.symbol:
        return 0, HistoryData()
    last = None if full else last_history_date(db, inst.id)
    start = last - timedelta(days=7) if last else None
    hist = provider.get_history(inst.symbol, start)
    if start is not None and any(s.date > last for s in hist.splits):
        start = None
        hist = provider.get_history(inst.symbol, None)
    hist.full = start is None
    written = store_bars(db, inst.id, hist)
    if hist.currency and inst.currency != hist.currency:
        inst.currency = hist.currency
    meta_set(inst, "history_at", datetime.now(timezone.utc).isoformat())
    # no live quote yet (e.g. first import on a weekend) -> seed from the last two bars
    if hist.bars and db.get(Quote, inst.id) is None:
        bars = sorted(hist.bars, key=lambda b: b.date)
        upsert_quote(db, inst, QuoteData(
            symbol=inst.symbol, price=bars[-1].close, currency=hist.currency or inst.currency,
            previous_close=bars[-2].close if len(bars) > 1 else None,
            market_time=datetime(bars[-1].date.year, bars[-1].date.month, bars[-1].date.day, 17, 30, tzinfo=timezone.utc),
        ), source=f"{provider.name}_history")
    db.flush()
    return written, hist


# ---------------------------------------------------------------- profile
_TYPE_ENUM = {t.value: t for t in InstrumentType}


def refresh_profile(db: Session, inst: Instrument, provider: MarketDataProvider, overwrite: bool = False) -> dict:
    """Fill empty master-data fields from the provider (never overwrites user edits unless overwrite=True)."""
    if not inst.symbol:
        return {"updated": []}
    p = provider.get_profile(inst.symbol)
    updated: list[str] = []

    def put(field: str, value, maxlen: int | None = None):
        if value in (None, ""):
            return
        if isinstance(value, str) and maxlen:
            value = value[:maxlen]
        current = getattr(inst, field)
        if current in (None, "") or (overwrite and current != value):
            setattr(inst, field, value)
            updated.append(field)

    put("exchange", p.exchange, 40)
    put("sector", p.sector, 80)
    put("industry", p.industry, 120)
    put("country", p.country, 80)
    put("website", p.website, 300)
    put("ir_url", p.ir_url, 300)
    put("logo_url", p.logo_url, 300)
    put("distribution", p.distribution, 20)
    if p.ter is not None:
        put("ter", Decimal(str(round(p.ter, 4))))
    if p.fund_provider:
        from app.market.etf.registry import detect_issuer

        put("fund_provider", detect_issuer(p.fund_provider) or p.fund_provider.lower(), 60)
    if p.name and (overwrite or not inst.name or inst.name in (inst.isin, inst.symbol)):
        if inst.name != p.name[:200]:
            inst.name = p.name[:200]
            updated.append("name")
    if p.currency and inst.currency != p.currency:
        inst.currency = p.currency
        updated.append("currency")
    new_type = _TYPE_ENUM.get(p.type or "")
    if new_type and new_type != inst.type and (overwrite or inst.type in (InstrumentType.other, InstrumentType.stock)):
        # default type of freshly imported instruments is usually "stock"/"other" -> trust Yahoo's quoteType
        if new_type != InstrumentType.other:
            inst.type = new_type
            updated.append("type")
    if p.name:
        meta_set(inst, "yahoo_name", p.name[:200])     # alias for look-through name matching
    meta_set(inst, "profile_at", datetime.now(timezone.utc).isoformat())
    db.flush()
    return {"updated": updated}
