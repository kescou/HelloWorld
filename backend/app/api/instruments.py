"""Instrumente (Stammdaten + Position). OWNER: Backend-Core (Marktdaten: Market-Data)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.transactions import query_transactions
from app.core.db import get_db
from app.market import service as market_service
from app.models.core import Instrument
from app.schemas.core import InstrumentCreate, InstrumentOut, InstrumentUpdate
from app.services import ledger, portfolio

router = APIRouter()

_QUOTE_SQL = (
    "SELECT instrument_id, price, previous_close, currency, market_time, source, updated_at "
    "FROM quotes WHERE instrument_id = ANY(:ids)"
)


def _quotes(db: Session, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not ids:
        return out
    for r in db.execute(text(_QUOTE_SQL), {"ids": ids}):
        price = float(r.price) if r.price is not None else None
        prev = float(r.previous_close) if r.previous_close is not None else None
        change = price - prev if price is not None and prev else None
        out[r.instrument_id] = {
            "instrument_id": r.instrument_id, "price": price, "previous_close": prev,
            "change": round(change, 6) if change is not None else None,
            "change_pct": round(change / prev * 100, 4) if change is not None and prev else None,
            "currency": r.currency, "market_time": r.market_time.isoformat() if r.market_time else None,
            "source": r.source, "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
    return out


def _get(db: Session, instrument_id: int) -> Instrument:
    inst = db.get(Instrument, instrument_id)
    if inst is None:
        raise HTTPException(404, "Instrument nicht gefunden")
    return inst


@router.get("", response_model=list[InstrumentOut])
def list_instruments(
    q: str | None = None, watchlist: bool | None = None, held: bool | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Instrument).order_by(Instrument.name)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Instrument.name.ilike(like), Instrument.isin.ilike(like), Instrument.symbol.ilike(like),
            Instrument.wkn.ilike(like),
        ))
    if watchlist is not None:
        stmt = stmt.where(Instrument.is_watchlist.is_(watchlist))
    instruments = db.scalars(stmt).all()
    quantities = {h["instrument_id"]: h["quantity"] for h in portfolio.compute_valuation(db).holdings}
    if held is not None:
        instruments = [i for i in instruments if (i.id in quantities) == held]
    quotes = _quotes(db, [i.id for i in instruments])
    out = []
    for inst in instruments:
        item = InstrumentOut.model_validate(inst)
        quote = quotes.get(inst.id)
        if quote:
            item.price = quote["price"]
            item.price_currency = quote["currency"]
            item.day_change_pct = quote["change_pct"]
        item.quote = quote
        item.quantity = quantities.get(inst.id)
        out.append(item)
    return out


@router.post("", response_model=InstrumentOut, status_code=201)
def create_instrument(data: InstrumentCreate, response: Response, db: Session = Depends(get_db)):
    """Get-or-create by ISIN / symbol. Returns 200 with the existing instrument if already known."""
    if not (data.isin or data.symbol):
        raise HTTPException(422, "isin oder symbol erforderlich")
    if data.isin and len(data.isin) != 12:
        raise HTTPException(422, "ISIN muss 12 Zeichen haben")
    existing = ledger.resolve_instrument(db, isin=data.isin, symbol=data.symbol, create=False)
    fields = data.model_dump(exclude_unset=True, exclude_none=True)
    if existing is not None:
        response.status_code = 200
        for key, value in fields.items():
            if key in ("isin", "symbol"):
                continue
            if key == "is_watchlist" or getattr(existing, key, None) in (None, ""):
                setattr(existing, key, value)
        db.commit()
        return InstrumentOut.model_validate(existing)
    inst = ledger.resolve_instrument(
        db, isin=data.isin, symbol=data.symbol, wkn=data.wkn, name=data.name, instrument_type=data.type,
        currency=data.currency,
    )
    for key, value in fields.items():
        if key in ("isin", "symbol", "wkn", "name", "type", "currency"):
            continue
        setattr(inst, key, value)
    db.commit()
    return InstrumentOut.model_validate(inst)


@router.get("/{instrument_id}")
def get_instrument(instrument_id: int, db: Session = Depends(get_db)):
    inst = _get(db, instrument_id)
    out = InstrumentOut.model_validate(inst).model_dump(mode="json")
    quote = _quotes(db, [inst.id]).get(inst.id)
    position = portfolio.position_for_instrument(db, inst.id)
    txs = query_transactions(db, instrument_id=inst.id, limit=5000)
    if quote:
        out["price"], out["price_currency"], out["day_change_pct"] = quote["price"], quote["currency"], quote["change_pct"]
    out["quantity"] = position["quantity"] if position else None
    out["quote"] = quote
    out["position"] = position
    out["transactions"] = [t.model_dump(mode="json") for t in txs.items]
    return out


@router.patch("/{instrument_id}", response_model=InstrumentOut)
def update_instrument(instrument_id: int, data: InstrumentUpdate, db: Session = Depends(get_db)):
    """Edit master data. Two special cases:

    * `symbol` changed → everything that was loaded for the OLD symbol (quote, price history, provider events,
      fundamentals) is dropped, so a wrong ISIN→symbol resolution leaves no wrong prices behind. The caller then
      runs `POST /market/refresh {scope:"quotes", instrument_ids:[id]}`, which backfills the new symbol.
    * `meta` is merged, not replaced: `{"meta": {"market": {"holdings_url": "https://…"}}}` sets one key and keeps
      the bookkeeping of the market module; a `null` value removes the key.
    """
    inst = _get(db, instrument_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("isin") and fields["isin"] != inst.isin:
        if len(fields["isin"]) != 12:
            raise HTTPException(422, "ISIN muss 12 Zeichen haben")
        clash = db.scalar(select(Instrument.id).where(Instrument.isin == fields["isin"], Instrument.id != inst.id))
        if clash:
            raise HTTPException(409, "ISIN bereits vergeben")
    for key in ("ir_url", "website"):
        if fields.get(key) and not str(fields[key]).lower().startswith(("http://", "https://")):
            raise HTTPException(422, f"{key} muss mit http:// oder https:// beginnen")
    if "meta" in fields:
        patch = fields.pop("meta") or {}
        url = (patch.get("market") or {}).get("holdings_url") if isinstance(patch.get("market"), dict) else None
        if url and not str(url).lower().startswith(("http://", "https://")):
            raise HTTPException(422, "holdings_url muss mit http:// oder https:// beginnen")
        inst.meta = _merge_meta(inst.meta, patch)
        flag_modified(inst, "meta")
    symbol_changed = "symbol" in fields and (fields["symbol"] or None) != (inst.symbol or None)
    not_nullable = {"name", "type", "currency", "is_watchlist"}
    for key, value in fields.items():
        if (value is None or value == "") and key in not_nullable:
            continue
        if isinstance(value, str) and key != "name":
            value = value.strip() or None
        setattr(inst, key, value.upper() if key == "currency" and value else value)
    if symbol_changed:
        market_service.reset_market_data(db, inst)
    db.commit()
    return InstrumentOut.model_validate(inst)


def _merge_meta(current: dict | None, patch: dict) -> dict:
    """Shallow merge, one level deeper for dict values (`market`); `None` removes a key."""
    out = dict(current or {})
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            inner = dict(out[key])
            for k, v in value.items():
                if v is None or v == "":
                    inner.pop(k, None)
                else:
                    inner[k] = v
            out[key] = inner
        elif isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if v is not None and v != ""}
        else:
            out[key] = value
    return out
