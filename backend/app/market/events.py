"""Corporate events -> `instrument_events`.

Sources and precedence (higher wins, lower never overwrites higher):
    manual / ics  >  yahoo (announced / historical)  >  estimate (projected from payment pattern)

* ex_dividend       – history: Yahoo dividend series (amount per share); upcoming: Yahoo calendar
* dividend_payment  – upcoming pay date from Yahoo calendar; otherwise ex-date + typical lag (is_estimate)
* earnings          – Yahoo calendar (+ past dates when the yfinance scrape works)
* split             – Yahoo split series
* agm / investor_day / other – only manual entry or ICS import (not available from free APIs)
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market import positions
from app.market.fx import FxConverter
from app.market.providers.base import CalendarData, HistoryData, MarketDataProvider
from app.market.service import meta_get, meta_set
from app.market.util import next_weekday, to_decimal
from app.models.core import Instrument
from app.models.market import EventType, InstrumentEvent

log = logging.getLogger("market.events")

PROTECTED_SOURCES = {"manual", "ics"}
DIVIDEND_TYPES = {EventType.ex_dividend, EventType.dividend_payment}
ESTIMATE_SOURCE = "estimate"


# ---------------------------------------------------------------- low-level upsert
def upsert_event(
    db: Session, instrument_id: int, type_: EventType, day: date, *, amount: float | Decimal | None = None,
    currency: str | None = None, title: str | None = None, url: str | None = None, is_estimate: bool = False,
    source: str = "yahoo",
) -> InstrumentEvent | None:
    """Insert or update respecting source precedence. Returns the row, or None when a protected row won."""
    row = db.scalar(select(InstrumentEvent).where(
        InstrumentEvent.instrument_id == instrument_id, InstrumentEvent.type == type_, InstrumentEvent.date == day))
    amt = to_decimal(amount, 6) if amount is not None and not isinstance(amount, Decimal) else amount
    if row is None:
        row = InstrumentEvent(instrument_id=instrument_id, type=type_, date=day)
        db.add(row)
    else:
        if row.source in PROTECTED_SOURCES and source not in PROTECTED_SOURCES:
            return None
        if not row.is_estimate and is_estimate:
            return None
    row.amount = amt
    row.currency = currency[:3] if currency else None
    row.title = title[:200] if title else None
    row.url = url[:400] if url else row.url
    row.is_estimate = is_estimate
    row.source = source
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


# ---------------------------------------------------------------- dividend pattern estimation (pure)
def detect_frequency(dates: list[date]) -> int:
    """Payments per year (12/4/2/1) from the median gap of the most recent payments."""
    dates = sorted(dates)[-9:]
    if len(dates) < 2:
        return 1
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 5]
    if not gaps:
        return 1
    gap = statistics.median(gaps)
    if gap <= 45:
        return 12
    if gap <= 135:
        return 4
    if gap <= 250:
        return 2
    return 1


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:      # 29 Feb
        return d.replace(year=d.year + years, day=28)


def estimate_dividends(
    past: list[tuple[date, float]], today: date, horizon_days: int = 365, known_future: list[date] | None = None,
) -> list[tuple[date, float]]:
    """Project ex-dividend dates/amounts for the next `horizon_days` from the historical pattern.

    * frequency = median gap of the recent payments (monthly / quarterly / semi-annual / annual)
    * dates     = last cycle's dates shifted by whole years (weekends -> Monday)
    * amount    = last paid amount; for uneven cycles (interim/final) the same slot of the last cycle
    * nothing is projected if the payer has stopped (last payment older than 1.5 periods + 45 days)
    * projections close to an already announced date (`known_future`) are dropped
    """
    past = sorted((d, a) for d, a in past if a and a > 0 and d <= today)
    if not past:
        return []
    known_future = sorted(known_future or [])
    freq = detect_frequency([d for d, _ in past])
    period = 365.0 / freq
    last_date, last_amount = past[-1]
    if (today - last_date).days > period * 1.5 + 45:
        return []
    cycle = [p for p in past if p[0] > last_date - timedelta(days=365 - period / 2)][-freq:]
    amounts = [a for _, a in cycle]
    uneven = len(amounts) > 1 and freq <= 2 and (max(amounts) - min(amounts)) / max(amounts) > 0.25
    horizon = today + timedelta(days=horizon_days)
    out: list[tuple[date, float]] = []
    for years in (1, 2):
        for d, a in cycle:
            proj = next_weekday(_add_years(d, years))
            if proj <= today or proj > horizon or proj <= last_date + timedelta(days=period / 2):
                continue
            if any(abs((proj - k).days) <= period / 2 for k in known_future):
                continue
            out.append((proj, round(a if uneven else last_amount, 6)))
    out.sort()
    return out


# ---------------------------------------------------------------- sync from provider data
def _default_pay_lag(inst: Instrument) -> int:
    isin = (inst.isin or "").upper()
    if isin.startswith(("DE", "AT", "CH", "NL", "FR")) and inst.type.value == "stock":
        return 3
    if isin.startswith(("IE", "LU")):       # UCITS ETFs
        return 14
    return 21


def sync_actions(db: Session, inst: Instrument, hist: HistoryData) -> int:
    """Historical dividends (ex-date) and splits from price-history actions."""
    n = 0
    cur = hist.currency or inst.currency
    for d in hist.dividends:
        if upsert_event(db, inst.id, EventType.ex_dividend, d.ex_date, amount=d.amount, currency=d.currency or cur,
                        title="Ex-Dividende", source="yahoo"):
            n += 1
    for s in hist.splits:
        if s.ratio >= 1:
            title = f"Aktiensplit {s.ratio:g}:1"
        else:
            title = f"Reverse Split 1:{1 / s.ratio:g}"
        if upsert_event(db, inst.id, EventType.split, s.date, amount=s.ratio, title=title, source="yahoo"):
            n += 1
    return n


def _purge_future(db: Session, inst: Instrument, today: date, types: set[EventType], sources: set[str]) -> None:
    for ev in db.scalars(select(InstrumentEvent).where(
            InstrumentEvent.instrument_id == inst.id, InstrumentEvent.date >= today,
            InstrumentEvent.type.in_(list(types)), InstrumentEvent.source.in_(list(sources)))):
        db.delete(ev)
    db.flush()


def sync_calendar(db: Session, inst: Instrument, cal: CalendarData, today: date | None = None) -> int:
    """Upcoming earnings / ex-dividend / pay date. Stale future provider rows are replaced."""
    today = today or date.today()
    n = 0
    _purge_future(db, inst, today, {EventType.earnings}, {"yahoo"})
    for d in cal.earnings_dates:
        est = cal.earnings_is_estimate and d >= today
        title = "Quartalszahlen" + (" (voraussichtlich)" if est else "")
        if upsert_event(db, inst.id, EventType.earnings, d, title=title, is_estimate=est, source="yahoo",
                        url=inst.ir_url):
            n += 1
    for d in cal.past_earnings_dates:
        if upsert_event(db, inst.id, EventType.earnings, d, title="Quartalszahlen", source="yahoo"):
            n += 1
    ex, pay = cal.ex_dividend_date, cal.dividend_pay_date
    cur = cal.currency or inst.currency
    if ex and pay and 0 <= (pay - ex).days <= 90:
        meta_set(inst, "pay_lag_days", (pay - ex).days)
    if ex and ex >= today:
        # announced ex-date; amount = already recorded amount for that date, else last known amount
        known = db.scalar(select(InstrumentEvent.amount).where(
            InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.ex_dividend,
            InstrumentEvent.is_estimate.is_(False), InstrumentEvent.amount.is_not(None), InstrumentEvent.date <= ex)
            .order_by(InstrumentEvent.date.desc()).limit(1))
        amount = (float(known) if known is not None else None) if cal.dividend_amount is None else cal.dividend_amount
        _purge_future(db, inst, today, {EventType.ex_dividend}, {"yahoo"})
        if upsert_event(db, inst.id, EventType.ex_dividend, ex, amount=amount, currency=cur,
                        title="Ex-Dividende", source="yahoo"):
            n += 1
    if pay and pay >= today:
        amount = cal.dividend_amount
        if amount is None:
            known = db.scalar(select(InstrumentEvent.amount).where(
                InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.ex_dividend,
                InstrumentEvent.is_estimate.is_(False), InstrumentEvent.date <= pay)
                .order_by(InstrumentEvent.date.desc()).limit(1))
            amount = float(known) if known is not None else None
        _purge_future(db, inst, today, {EventType.dividend_payment}, {"yahoo"})
        if upsert_event(db, inst.id, EventType.dividend_payment, pay, amount=amount, currency=cur,
                        title="Dividendenzahlung", source="yahoo"):
            n += 1
    db.flush()
    return n


def rebuild_estimates(db: Session, inst: Instrument, today: date | None = None) -> int:
    """Replace all projected dividend rows of an instrument (next 12 months)."""
    today = today or date.today()
    for ev in db.scalars(select(InstrumentEvent).where(
            InstrumentEvent.instrument_id == inst.id, InstrumentEvent.source == ESTIMATE_SOURCE)):
        db.delete(ev)
    db.flush()
    rows = db.execute(select(InstrumentEvent.date, InstrumentEvent.amount, InstrumentEvent.currency).where(
        InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.ex_dividend,
        InstrumentEvent.is_estimate.is_(False)).order_by(InstrumentEvent.date)).all()
    past = [(d, float(a)) for d, a, _c in rows if a is not None and d <= today]
    future_real = [d for d, _a, _c in rows if d > today]
    currency = next((c for _d, _a, c in reversed(rows) if c), None) or inst.currency
    lag = meta_get(inst, "pay_lag_days")
    lag = int(lag) if isinstance(lag, (int, float)) else _default_pay_lag(inst)
    n = 0
    for d, amount in estimate_dividends(past, today, known_future=future_real):
        if upsert_event(db, inst.id, EventType.ex_dividend, d, amount=amount, currency=currency,
                        title="Ex-Dividende (Prognose)", is_estimate=True, source=ESTIMATE_SOURCE):
            n += 1
        if upsert_event(db, inst.id, EventType.dividend_payment, next_weekday(d + timedelta(days=lag)),
                        amount=amount, currency=currency, title="Dividendenzahlung (Prognose)",
                        is_estimate=True, source=ESTIMATE_SOURCE):
            n += 1
    # announced ex-dates without a known pay date -> estimated payment row
    pays = [d for (d,) in db.execute(select(InstrumentEvent.date).where(
        InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.dividend_payment,
        InstrumentEvent.date >= today))]
    for d, a, c in rows:
        if d <= today:
            continue
        if any(0 <= (p - d).days <= 60 for p in pays):
            continue
        if upsert_event(db, inst.id, EventType.dividend_payment, next_weekday(d + timedelta(days=lag)),
                        amount=a, currency=c or currency, title="Dividendenzahlung (Termin geschätzt)",
                        is_estimate=True, source=ESTIMATE_SOURCE):
            n += 1
    return n


def refresh_events(db: Session, inst: Instrument, provider: MarketDataProvider, hist: HistoryData | None = None,
                   today: date | None = None) -> dict:
    """Full event sync for one instrument. `hist` (from refresh_history) avoids a second history download."""
    if not inst.symbol:
        return {"actions": 0, "calendar": 0, "estimates": 0}
    if not ((hist is not None and hist.full) or meta_get(inst, "actions_full_at")):
        # first sync needs the complete dividend/split history, not just an incremental window
        hist = provider.get_history(inst.symbol, None)
        hist.full = True
    if hist is None:
        hist = HistoryData()            # actions are kept current by the daily history job
    if hist.full:
        meta_set(inst, "actions_full_at", datetime.now(timezone.utc).isoformat())
    actions = sync_actions(db, inst, hist)
    cal_n = sync_calendar(db, inst, provider.get_calendar(inst.symbol), today)
    est = rebuild_estimates(db, inst, today)
    if inst.type.value in ("etf", "fund") and not inst.distribution:
        recent = db.scalar(select(InstrumentEvent.id).where(
            InstrumentEvent.instrument_id == inst.id, InstrumentEvent.type == EventType.ex_dividend,
            InstrumentEvent.is_estimate.is_(False),
            InstrumentEvent.date >= (today or date.today()) - timedelta(days=550)).limit(1))
        inst.distribution = "distributing" if recent else "accumulating"
    meta_set(inst, "events_at", datetime.now(timezone.utc).isoformat())
    db.flush()
    return {"actions": actions, "calendar": cal_n, "estimates": est}


# ---------------------------------------------------------------- ICS import (IR calendars)
_ICS_TYPE_RULES: list[tuple[str, EventType]] = [
    (r"hauptversammlung|annual general meeting|general meeting|\bagm\b|shareholders'? meeting|aktionärsversammlung", EventType.agm),
    (r"capital markets? day|kapitalmarkttag|investor day|investorentag|roadshow|conference|konferenz", EventType.investor_day),
    (r"ex[- ]?dividend|ex[- ]?tag", EventType.ex_dividend),
    (r"dividend(en)?(zahlung| payment| payout)|payment date|zahltag", EventType.dividend_payment),
    (r"quartal|quarter|q[1-4]\b|halbjahr|half[- ]year|interim|results|earnings|ergebnis|geschäftsbericht|"
     r"annual report|jahresabschluss|zwischenbericht|mitteilung|statement|bilanz|9m\b|h1\b|fy\b", EventType.earnings),
]


def classify_event_title(title: str | None) -> EventType:
    low = (title or "").lower()
    for pattern, typ in _ICS_TYPE_RULES:
        if re.search(pattern, low):
            return typ
    return EventType.other


def _ics_unescape(value: str) -> str:
    for src, dst in (("\\n", " "), ("\\N", " "), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")):
        value = value.replace(src, dst)
    return value.strip()


def parse_ics(text: str | bytes) -> list[dict]:
    """Minimal RFC 5545 reader: VEVENT -> {date, title, url, description}. No external dependency."""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:       # folded line
            lines[-1] += line[1:]
        else:
            lines.append(line)
    events: list[dict] = []
    cur: dict | None = None
    for line in lines:
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            cur = {}
            continue
        if upper == "END:VEVENT":
            if cur and cur.get("date"):
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        name = key.split(";", 1)[0].strip().upper()
        if name == "DTSTART":
            m = re.match(r"(\d{4})(\d{2})(\d{2})", value.strip())
            if m:
                try:
                    cur["date"] = date(int(m[1]), int(m[2]), int(m[3]))
                except ValueError:
                    pass
        elif name == "SUMMARY":
            cur["title"] = _ics_unescape(value)
        elif name == "URL":
            cur["url"] = value.strip()
        elif name == "DESCRIPTION":
            cur["description"] = _ics_unescape(value)
    for ev in events:
        if not ev.get("url") and ev.get("description"):
            m = re.search(r"https?://\S+", ev["description"])
            if m:
                ev["url"] = m[0].rstrip(".,)")
    return events


def import_ics(db: Session, inst: Instrument, text: str | bytes, only_future: bool = False) -> dict:
    parsed = parse_ics(text)
    today = date.today()
    imported = 0
    for ev in parsed:
        if only_future and ev["date"] < today:
            continue
        typ = classify_event_title(ev.get("title"))
        if upsert_event(db, inst.id, typ, ev["date"], title=ev.get("title") or typ.value,
                        url=ev.get("url") or inst.ir_url, source="ics"):
            imported += 1
    db.flush()
    return {"parsed": len(parsed), "imported": imported}


# ---------------------------------------------------------------- read side
def event_to_dict(ev: InstrumentEvent, inst: Instrument | None = None) -> dict:
    return {
        "id": ev.id, "instrument_id": ev.instrument_id,
        "instrument_name": inst.name if inst else None, "symbol": inst.symbol if inst else None,
        "isin": inst.isin if inst else None,
        "type": ev.type.value, "date": ev.date.isoformat(),
        "amount": float(ev.amount) if ev.amount is not None else None, "currency": ev.currency,
        "title": ev.title, "url": ev.url, "is_estimate": ev.is_estimate, "source": ev.source,
        "position_quantity": None, "expected_payment": None,
    }


def list_events(db: Session, date_from: date | None = None, date_to: date | None = None,
                types: list[EventType] | None = None, instrument_id: int | None = None) -> list[dict]:
    """Events of held + watchlist instruments, enriched with position size and expected payment (EUR).

    For past dividend events the quantity held on the event date is used, for future ones today's position.
    """
    today = date.today()
    date_from = date_from or today - timedelta(days=30)
    date_to = date_to or today + timedelta(days=365)
    current = positions.compute_quantities(db)
    if instrument_id is not None:
        inst_ids = {instrument_id}
    else:
        inst_ids = {i for i, q in current.items() if q > positions.EPS}
        inst_ids |= {i for (i,) in db.execute(select(Instrument.id).where(Instrument.is_watchlist.is_(True)))}
    if not inst_ids:
        return []
    stmt = (select(InstrumentEvent, Instrument).join(Instrument, Instrument.id == InstrumentEvent.instrument_id)
            .where(InstrumentEvent.instrument_id.in_(inst_ids), InstrumentEvent.date >= date_from,
                   InstrumentEvent.date <= date_to))
    if types:
        stmt = stmt.where(InstrumentEvent.type.in_(types))
    fx = FxConverter(db)
    timelines: dict[int, list] = {}
    out: list[dict] = []
    for ev, inst in db.execute(stmt.order_by(InstrumentEvent.date, InstrumentEvent.id)):
        item = event_to_dict(ev, inst)
        if ev.date < today:
            if inst.id not in timelines:
                timelines[inst.id] = positions.quantity_timeline(db, inst.id)
            # entitlement: shares held at close of the day before the ex-date
            ref = ev.date - timedelta(days=1) if ev.type == EventType.ex_dividend else ev.date
            qty = positions.quantity_at(timelines[inst.id], ref)
        else:
            qty = current.get(inst.id, Decimal(0))
        item["position_quantity"] = float(qty)
        if ev.type in DIVIDEND_TYPES and ev.amount is not None and qty > 0:
            gross = fx.to_eur(float(ev.amount) * float(qty), ev.currency or inst.currency)
            item["expected_payment"] = round(gross, 2) if gross is not None else None
        out.append(item)
    return out
