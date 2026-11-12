"""User settings (key/value table `settings`). Only keys the UI edits are exposed."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.core import Setting
from app.services import analytics

router = APIRouter()


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Sparerpauschbetrag in EUR (1.000 € single, 2.000 € jointly assessed)
    sparerpauschbetrag: float | None = Field(default=None, ge=0, le=100_000)


def _out(db: Session) -> dict:
    return {"sparerpauschbetrag": analytics.tax_allowance(db)}


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return _out(db)


@router.patch("")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    if body.sparerpauschbetrag is not None:
        row = db.get(Setting, analytics.TAX_SETTING_KEY)
        value = {"sparerpauschbetrag": round(body.sparerpauschbetrag, 2)}
        if row is None:
            db.add(Setting(key=analytics.TAX_SETTING_KEY, value=value))
        else:
            row.value = {**(row.value if isinstance(row.value, dict) else {}), **value}
        db.commit()
    return _out(db)
