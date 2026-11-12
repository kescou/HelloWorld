"""Portfolio: Übersicht, Positionen, Verlauf, Performance, Allokation. OWNER: Backend-Core."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
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
