"""Analytics: Dividenden, Cashflow, Ausgaben, Prognose, realisierte Gewinne. OWNER: Backend-Core."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services import analytics

router = APIRouter()


@router.get("/dividends")
def dividends(year: int | None = Query(None, ge=1990, le=2200), db: Session = Depends(get_db)):
    return analytics.dividends(db, year)


@router.get("/cashflow")
def cashflow(months: int = Query(12, ge=1, le=240), db: Session = Depends(get_db)):
    return analytics.cashflow(db, months)


@router.get("/expenses-by-category")
def expenses_by_category(
    date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
):
    return analytics.expenses_by_category(db, date_from, date_to)


@router.get("/forecast")
def forecast(
    years: int = Query(10, ge=1, le=60), monthly_savings: float | None = None,
    expected_return: float | None = Query(None, description="percent (6) or fraction (0.06)"),
    volatility: float | None = Query(None, description="percent (15) or fraction (0.15)"),
    start_value: float | None = None, db: Session = Depends(get_db),
):
    return analytics.wealth_forecast(db, years, monthly_savings, expected_return, volatility, start_value)


@router.get("/realized-gains")
def realized_gains(year: int | None = Query(None, ge=1990, le=2200), db: Session = Depends(get_db)):
    return analytics.realized_gains(db, year)


@router.get("/taxes")
def taxes(year: int | None = Query(None, ge=1990, le=2200), db: Session = Depends(get_db)):
    """Tax overview of one calendar year (estimate, not tax advice). See `analytics.taxes`."""
    return analytics.taxes(db, year)


@router.get("/recurring")
def recurring(db: Session = Depends(get_db)):
    """Detected recurring bookings: fixed costs and savings plans."""
    return analytics.recurring(db)
