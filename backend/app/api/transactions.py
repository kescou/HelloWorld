"""Transaktionen (Journal). OWNER: Backend-Core."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.db import get_db
from app.models.core import Account, Category, Instrument, Transaction, TxType
from app.schemas.core import (
    TransactionCreate, TransactionList, TransactionOut, TransactionUpdate, TransferCreate,
)
from app.services import ledger

router = APIRouter()

_CashAccount = aliased(Account)


def _base_select():
    return (
        select(
            Transaction, Account.name, _CashAccount.name, Instrument.name, Instrument.isin, Instrument.symbol,
            Category.name,
        )
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(_CashAccount, _CashAccount.id == Transaction.cash_account_id)
        .outerjoin(Instrument, Instrument.id == Transaction.instrument_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
    )


def _row_out(row) -> TransactionOut:
    tx, account_name, cash_name, inst_name, inst_isin, inst_symbol, cat_name = row
    out = TransactionOut.model_validate(tx)
    out.account_name = account_name
    out.cash_account_name = cash_name
    out.instrument_name = inst_name
    out.instrument_isin = inst_isin
    out.instrument_symbol = inst_symbol
    out.category_name = cat_name
    return out


def tx_out(db: Session, tx_id: int) -> TransactionOut:
    row = db.execute(_base_select().where(Transaction.id == tx_id)).first()
    if row is None:
        raise HTTPException(404, "Transaktion nicht gefunden")
    return _row_out(row)


def query_transactions(
    db: Session, *, account_id: int | None = None, instrument_id: int | None = None, type_: str | None = None,
    category_id: int | None = None, q: str | None = None, date_from: date | None = None,
    date_to: date | None = None, uncategorized: bool = False, limit: int = 100, offset: int = 0,
) -> TransactionList:
    conds = []
    if account_id is not None:
        conds.append(or_(Transaction.account_id == account_id, Transaction.cash_account_id == account_id))
    if instrument_id is not None:
        conds.append(Transaction.instrument_id == instrument_id)
    if type_:
        try:
            types = [TxType(t.strip()) for t in type_.split(",") if t.strip()]
        except ValueError as exc:
            raise HTTPException(422, f"Unbekannter Transaktionstyp: {exc}") from exc
        conds.append(Transaction.type.in_(types))
    if category_id is not None:
        conds.append(Transaction.category_id == category_id)
    if uncategorized:
        conds.append(Transaction.category_id.is_(None))
    if date_from is not None:
        conds.append(Transaction.booking_date >= date_from)
    if date_to is not None:
        conds.append(Transaction.booking_date <= date_to)
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(
            Transaction.counterparty.ilike(like), Transaction.description.ilike(like),
            Transaction.notes.ilike(like), Instrument.name.ilike(like), Instrument.isin.ilike(like),
            Instrument.symbol.ilike(like), Category.name.ilike(like),
        ))
    stmt = _base_select().where(*conds)
    total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(Transaction.id).subquery())) or 0
    rows = db.execute(
        stmt.order_by(Transaction.booking_date.desc(), Transaction.id.desc()).limit(limit).offset(offset)
    ).all()
    return TransactionList(items=[_row_out(r) for r in rows], total=total)


@router.get("", response_model=TransactionList)
def list_transactions(
    account_id: int | None = None, instrument_id: int | None = None,
    type: str | None = Query(None, description="one type or comma separated list"),
    category_id: int | None = None, q: str | None = None,
    date_from: date | None = None, date_to: date | None = None, uncategorized: bool = False,
    limit: int = Query(100, ge=1, le=5000), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return query_transactions(
        db, account_id=account_id, instrument_id=instrument_id, type_=type, category_id=category_id, q=q,
        date_from=date_from, date_to=date_to, uncategorized=uncategorized, limit=limit, offset=offset,
    )


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(data: TransactionCreate, db: Session = Depends(get_db)):
    try:
        tx = ledger.create_transaction(db, data)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return tx_out(db, tx.id)


@router.post("/transfer", response_model=list[TransactionOut], status_code=201)
def create_transfer(data: TransferCreate, db: Session = Depends(get_db)):
    try:
        out_tx, in_tx = ledger.create_transfer(db, data)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return [tx_out(db, out_tx.id), tx_out(db, in_tx.id)]


@router.get("/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, db: Session = Depends(get_db)):
    return tx_out(db, tx_id)


@router.patch("/{tx_id}", response_model=TransactionOut)
def update_transaction(tx_id: int, data: TransactionUpdate, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "Transaktion nicht gefunden")
    try:
        ledger.update_transaction(db, tx, data)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    return tx_out(db, tx_id)


@router.delete("/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "Transaktion nicht gefunden")
    ids = ledger.delete_transaction(db, tx)
    db.commit()
    return {"deleted": ids}
