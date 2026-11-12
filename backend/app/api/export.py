"""Data export / backup (prefix /api/export).

* GET /transactions.csv – the journal as CSV (Excel-friendly German format by default)
* GET /backup.json      – logical dump of all tables as JSON (for archiving / inspection)

A real, restorable backup is a database dump – see docs/ARCHITECTURE.md ("Datenexport & Backup"):
    pg_dump "postgresql://wealth:wealth@localhost:5432/wealth" -Fc -f vermoegen-$(date +%F).dump
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.core.db import Base, get_db
from app.models.core import Account, Category, Instrument, Transaction

router = APIRouter()

#: large tables that can be re-fetched from the market-data sources at any time
REGENERABLE_TABLES = ("price_history", "market_data_log")
#: order in which the tables appear in the dump (parents first); unknown tables follow alphabetically
TABLE_ORDER = (
    "accounts", "instruments", "categories", "documents", "transactions", "balance_snapshots", "extraction_drafts",
    "settings", "fx_rates", "quotes", "instrument_events", "etf_holdings", "fundamentals", "price_history",
    "market_data_log",
)

_CSV_COLUMNS = (
    "id", "booking_date", "value_date", "account", "cash_account", "type", "isin", "symbol", "instrument",
    "quantity", "price", "amount", "fees", "taxes", "currency", "fx_rate", "category", "counterparty",
    "description", "notes", "transfer_peer_id", "source_document_id",
)


def _num(value: Decimal | None, german: bool, places: int | None = None) -> str:
    if value is None:
        return ""
    value = Decimal(value)
    text = format(value.quantize(Decimal(1).scaleb(-places)) if places is not None else value.normalize(), "f")
    return text.replace(".", ",") if german else text


@router.get("/transactions.csv")
def transactions_csv(
    account_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
    fmt: Literal["de", "en"] = Query("de", alias="format", description="de: Semikolon + Dezimalkomma (Excel), en: Komma + Punkt"),
    db: Session = Depends(get_db),
) -> Response:
    """All transactions (oldest first). Columns: see `_CSV_COLUMNS`; `amount` is the signed cash effect."""
    german = fmt == "de"
    cash = aliased(Account)
    stmt = (
        select(Transaction, Account.name, cash.name, Instrument.isin, Instrument.symbol, Instrument.name, Category.name)
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(cash, cash.id == Transaction.cash_account_id)
        .outerjoin(Instrument, Instrument.id == Transaction.instrument_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .order_by(Transaction.booking_date, Transaction.id)
    )
    if account_id is not None:
        stmt = stmt.where(or_(Transaction.account_id == account_id, Transaction.cash_account_id == account_id))
    if date_from is not None:
        stmt = stmt.where(Transaction.booking_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.booking_date <= date_to)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";" if german else ",", lineterminator="\r\n")
    writer.writerow(_CSV_COLUMNS)
    for tx, account, cash_account, isin, symbol, instrument, category in db.execute(stmt):
        writer.writerow([
            tx.id, tx.booking_date.isoformat(), tx.value_date.isoformat() if tx.value_date else "",
            account, cash_account or "", tx.type.value, isin or "", symbol or "", instrument or "",
            _num(tx.quantity, german), _num(tx.price, german), _num(tx.amount, german, 2),
            _num(tx.fees, german, 2), _num(tx.taxes, german, 2), tx.currency, _num(tx.fx_rate, german),
            category or "", tx.counterparty or "", (tx.description or "").replace("\r", " ").replace("\n", " "),
            (tx.notes or "").replace("\r", " ").replace("\n", " "), tx.transfer_peer_id or "", tx.source_document_id or "",
        ])
    body = ("﻿" if german else "") + buf.getvalue()      # BOM: Excel then reads UTF-8 correctly
    filename = f"vermoegen-transaktionen-{date.today().isoformat()}.csv"
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, str, list, dict)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, Decimal):
        return format(value, "f")          # lossless
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return str(value)


def _tables(include_market_history: bool):
    known = {t.name: t for t in Base.metadata.tables.values()}
    names = [n for n in TABLE_ORDER if n in known] + sorted(n for n in known if n not in TABLE_ORDER)
    return [known[n] for n in names if include_market_history or n not in REGENERABLE_TABLES]


@router.get("/backup.json")
def backup_json(
    include_market_history: bool = Query(False, description="auch price_history + market_data_log (groß, wiederbeschaffbar)"),
    db: Session = Depends(get_db),
) -> Response:
    """`{meta:{app,version,created_at,format,tables:{name:rows}}, tables:{name:[{column:value}]}}`.
    Numerics are strings (lossless), dates ISO. Uploaded original files are NOT included (data/uploads)."""
    dump: dict[str, list[dict]] = {}
    for table in _tables(include_market_history):
        order = list(table.primary_key.columns) or list(table.columns)[:1]
        dump[table.name] = [
            {k: _jsonable(v) for k, v in row.items()}
            for row in db.execute(select(table).order_by(*order)).mappings()
        ]
    meta = {"app": "vermoegen", "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "include_market_history": include_market_history, "tables": {name: len(rows) for name, rows in dump.items()}}
    body = json.dumps({"meta": meta, "tables": dump}, ensure_ascii=False, allow_nan=False)
    filename = f"vermoegen-backup-{date.today().isoformat()}.json"
    return Response(body.encode("utf-8"), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
