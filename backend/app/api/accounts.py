"""Konten. OWNER: Backend-Core."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.core import Account, BalanceSnapshot, Transaction
from app.schemas.core import AccountCreate, AccountDetail, AccountOut, AccountUpdate
from app.services import portfolio

router = APIRouter()


def _out(account: Account, values: dict[int, dict[str, float]], cls=AccountOut):
    v = values.get(account.id, {})
    out = cls.model_validate(account)
    out.balance = v.get("balance", float(account.opening_balance or 0))
    out.market_value = v.get("market_value", 0.0)
    out.total_value = v.get("total_value", out.balance)
    return out


def _get(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Konto nicht gefunden")
    return account


def _check_settlement(db: Session, settlement_id: int | None, own_id: int | None = None) -> None:
    if settlement_id is None:
        return
    if settlement_id == own_id:
        raise HTTPException(422, "Ein Konto kann nicht sein eigenes Verrechnungskonto sein")
    if db.get(Account, settlement_id) is None:
        raise HTTPException(404, "Verrechnungskonto nicht gefunden")


@router.get("", response_model=list[AccountOut])
def list_accounts(include_inactive: bool = True, db: Session = Depends(get_db)):
    stmt = select(Account).order_by(Account.sort_order, Account.id)
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    accounts = db.scalars(stmt).all()
    values = portfolio.account_values(db)
    return [_out(a, values) for a in accounts]


@router.post("", response_model=AccountOut, status_code=201)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    _check_settlement(db, data.settlement_account_id)
    payload = data.model_dump()
    payload["currency"] = payload["currency"].upper()
    account = Account(**payload)
    db.add(account)
    db.commit()
    return _out(account, portfolio.account_values(db))


@router.get("/{account_id}", response_model=AccountDetail)
def get_account(account_id: int, days: int = 730, db: Session = Depends(get_db)):
    account = _get(db, account_id)
    val = portfolio.compute_valuation(db)
    out = _out(account, portfolio.account_values(db, val))
    history = portfolio.account_history(db, account_id, days=max(1, min(days, 36500)))
    return AccountDetail.model_validate({**out.model_dump(), "history": history})


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    account = _get(db, account_id)
    fields = data.model_dump(exclude_unset=True)
    if "settlement_account_id" in fields:
        _check_settlement(db, fields["settlement_account_id"], account.id)
    nullable = {"iban", "account_number", "color", "opening_date", "interest_rate", "settlement_account_id", "notes"}
    for key, value in fields.items():
        if value is None and key not in nullable:
            continue
        setattr(account, key, value.upper() if key == "currency" else value)
    db.commit()
    return _out(account, portfolio.account_values(db))


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """Deletes an account without transactions; accounts with transactions are deactivated instead."""
    account = _get(db, account_id)
    tx_count = db.scalar(
        select(func.count()).select_from(Transaction).where(
            or_(Transaction.account_id == account_id, Transaction.cash_account_id == account_id)
        )
    )
    if tx_count:
        account.is_active = False
        db.commit()
        return {"deleted": False, "deactivated": True, "transaction_count": tx_count}
    db.execute(Account.__table__.update().where(Account.settlement_account_id == account_id)
               .values(settlement_account_id=None))
    db.execute(BalanceSnapshot.__table__.delete().where(BalanceSnapshot.account_id == account_id))
    db.delete(account)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - still referenced (e.g. by documents): deactivate instead
        db.rollback()
        account = _get(db, account_id)
        account.is_active = False
        db.commit()
        return {"deleted": False, "deactivated": True, "transaction_count": 0}
    return {"deleted": True, "deactivated": False, "transaction_count": 0}
