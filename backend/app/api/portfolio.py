"""Portfolio: Übersicht, Positionen, Verlauf, Performance, Allokation. OWNER: Backend-Core."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services import portfolio

router = APIRouter()


def _check_range(range_: str) -> str:
    try:
        portfolio.range_start(range_)
    except ValueError as exc:
        raise HTTPException(422, "range muss 1W|1M|3M|6M|YTD|1Y|2Y|3Y|5Y|10Y|MAX sein") from exc
    return range_


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return portfolio.compute_summary(db)


@router.get("/holdings")
def holdings(account_id: int | None = None, db: Session = Depends(get_db)):
    return portfolio.compute_valuation(db, account_id).holdings


@router.get("/history")
def history(
    range_: str = Query("1Y", alias="range"), account_id: int | None = None, db: Session = Depends(get_db),
):
    return portfolio.compute_history(db, _check_range(range_), account_id)


@router.get("/performance")
def performance(
    range_: str = Query("1Y", alias="range"), account_id: int | None = None,
    scope: Literal["all", "investments"] = "all", db: Session = Depends(get_db),
):
    return portfolio.compute_performance(db, _check_range(range_), account_id, scope)


@router.get("/allocation")
def allocation(
    by: Literal["type", "sector", "country", "account", "currency"] = "type",
    lookthrough: bool = False, account_id: int | None = None, db: Session = Depends(get_db),
):
    return portfolio.compute_allocation(db, by, lookthrough, account_id)


class SplitBooking(BaseModel):
    instrument_id: int
    date: dt.date
    ratio: Decimal = Field(gt=0, le=10000, description="new shares per old share: 10 for 10:1, 0.1 for a 1:10 reverse split")


@router.post("/splits/book", status_code=201)
def book_split(body: SplitBooking, db: Session = Depends(get_db)) -> dict:
    """One click for an unbooked split (`unbooked_splits` in summary/holdings): books a `split` transaction in
    every depot that held the instrument before that date. → `{booked: n, transaction_ids: [...]}`"""
    if body.ratio == 1:
        raise HTTPException(422, "Ein Split im Verhältnis 1:1 ändert nichts")
    created = portfolio.book_split(db, body.instrument_id, body.date, body.ratio)
    if not created:
        raise HTTPException(409, "Kein Depot hielt dieses Wertpapier vor dem Split-Datum, oder der Split ist schon gebucht")
    db.commit()
    return {"booked": len(created), "transaction_ids": [t.id for t in created]}
