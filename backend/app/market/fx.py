"""FX rates: ECB euro reference rates (free XML feed) -> `fx_rates`. rate = units of currency per 1 EUR.

Yahoo (`EURXXX=X`) is used only for currencies the ECB does not publish (e.g. TWD, RUB since 2022).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.market import http
from app.market.providers.base import MarketDataProvider, ProviderError
from app.market.util import normalize_currency
from app.models.core import FxRate, Instrument

log = logging.getLogger("market.fx")

ECB_BASE = "https://www.ecb.europa.eu/stats/eurofxref/"
ECB_DAILY = ECB_BASE + "eurofxref-daily.xml"
ECB_90D = ECB_BASE + "eurofxref-hist-90d.xml"
ECB_HIST = ECB_BASE + "eurofxref-hist.xml"


def parse_ecb_xml(xml: bytes | str) -> list[tuple[date, str, Decimal]]:
    """Parse any of the three ECB eurofxref files into (date, currency, rate) rows."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise http.RemoteError(f"ECB XML not parseable: {exc}") from exc
    rows: list[tuple[date, str, Decimal]] = []
    for el in root.iter():
        if not el.tag.endswith("Cube") or "time" not in el.attrib:
            continue
        try:
            day = date.fromisoformat(el.attrib["time"])
        except ValueError:
            continue
        for child in el:
            cur, rate = child.attrib.get("currency"), child.attrib.get("rate")
            if not cur or not rate:
                continue
            try:
                value = Decimal(rate)
            except ArithmeticError:
                continue
            if value > 0:
                rows.append((day, cur.upper(), value))
    return rows


def fetch_ecb(full: bool = False) -> list[tuple[date, str, Decimal]]:
    return parse_ecb_xml(http.get_bytes(ECB_HIST if full else ECB_90D, timeout=60 if full else 20))


def store_rates(db: Session, rows: list[tuple[date, str, Decimal]]) -> int:
    """Insert new / update changed rates. Returns number of rows written."""
    if not rows:
        return 0
    dmin, dmax = min(r[0] for r in rows), max(r[0] for r in rows)
    existing = {(d, c): (i, r) for i, d, c, r in db.execute(
        select(FxRate.id, FxRate.date, FxRate.currency, FxRate.rate).where(FxRate.date >= dmin, FxRate.date <= dmax))}
    new, changed = [], 0
    seen: set[tuple[date, str]] = set()
    for d, c, r in rows:
        if (d, c) in seen:
            continue
        seen.add((d, c))
        hit = existing.get((d, c))
        r = round(r, 8)
        if hit is None:
            new.append({"date": d, "currency": c, "rate": r})
        elif hit[1] != r:
            db.query(FxRate).filter(FxRate.id == hit[0]).update({"rate": r})
            changed += 1
    for i in range(0, len(new), 5000):
        db.execute(FxRate.__table__.insert(), new[i:i + 5000])
    db.flush()
    return len(new) + changed


def update_fx(db: Session, provider: MarketDataProvider | None = None, full: bool | None = None) -> dict:
    """Load ECB rates (full history on first run, else last 90 days) + Yahoo fallback for exotic currencies."""
    first, last = db.execute(select(func.min(FxRate.date), func.max(FxRate.date))).one()
    if full is None:
        full = first is None or last is None or (date.today() - last).days > 80 or (last - first).days < 400
    try:
        rows = fetch_ecb(full=full)
    except http.RemoteError:
        if not full:
            raise
        log.warning("ECB full history not available – falling back to the 90-day file")
        full = False
        rows = fetch_ecb(full=False)
    written = store_rates(db, rows)
    result = {"source": "ecb", "full": full, "rows": written, "latest": max((r[0] for r in rows), default=None)}
    ecb_currencies = {r[1] for r in rows}
    if provider is not None:
        needed = {normalize_currency(c)[0] for (c,) in db.execute(select(Instrument.currency).distinct()) if c}
        extra = sorted(c for c in needed if c and c != "EUR" and c not in ecb_currencies)
        fallback: dict[str, int | str] = {}
        for cur in extra:
            try:
                last_cur = db.scalar(select(func.max(FxRate.date)).where(FxRate.currency == cur))
                start = last_cur - timedelta(days=7) if last_cur else None
                hist = provider.get_fx_history(cur, start)
                fallback[cur] = store_rates(db, [(d, cur, Decimal(repr(v))) for d, v in hist if v and v > 0])
            except (ProviderError, http.RemoteError) as exc:
                fallback[cur] = f"error: {exc}"
        if fallback:
            result["yahoo_fallback"] = fallback
    if result["latest"] is not None:
        result["latest"] = result["latest"].isoformat()
    return result


def get_rate(db: Session, currency: str | None, on: date | None = None) -> Decimal | None:
    """Units of `currency` per 1 EUR at (or last before) `on`. EUR -> 1. None if unknown."""
    cur, _ = normalize_currency(currency)
    if not cur or cur == "EUR":
        return Decimal(1)
    stmt = select(FxRate.rate).where(FxRate.currency == cur)
    if on is not None:
        stmt = stmt.where(FxRate.date <= on)
    rate = db.scalar(stmt.order_by(FxRate.date.desc()).limit(1))
    if rate is None and on is not None:   # before first known rate -> earliest available
        rate = db.scalar(select(FxRate.rate).where(FxRate.currency == cur).order_by(FxRate.date).limit(1))
    return rate


class FxConverter:
    """Caches latest rates for many conversions in one request."""

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[str, float | None] = {"EUR": 1.0}

    def rate(self, currency: str | None) -> float | None:
        cur, _ = normalize_currency(currency)
        cur = cur or "EUR"
        if cur not in self._cache:
            r = get_rate(self.db, cur)
            self._cache[cur] = float(r) if r else None
        return self._cache[cur]

    def to_eur(self, amount: float | None, currency: str | None) -> float | None:
        """amount in `currency` -> EUR. Minor units (GBp) are handled. Unknown currency -> None."""
        if amount is None:
            return None
        _, div = normalize_currency(currency)
        r = self.rate(currency)
        if not r:
            return None
        return amount / div / r


def to_eur(db: Session, amount: float | None, currency: str | None) -> float | None:
    return FxConverter(db).to_eur(amount, currency)
