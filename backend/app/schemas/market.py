"""Pydantic schemas of the market-data API (see docs/ARCHITECTURE.md, section "Marktdaten")."""
from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.market import EventType

RefreshScope = Literal["quotes", "events", "history", "etf", "fundamentals", "fx", "profile", "all"]


class RefreshRequest(BaseModel):
    scope: RefreshScope = "quotes"
    instrument_ids: list[int] | None = None
    force: bool = False      # history: full reload; etf: overwrite manual upload; fx: full ECB history


class QuoteOut(BaseModel):
    instrument_id: int
    price: float
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    currency: str
    market_time: datetime | None = None
    updated_at: datetime | None = None
    source: str | None = None


class HistoryPoint(BaseModel):
    date: dt.date
    close: float


class EventOut(BaseModel):
    id: int
    instrument_id: int
    instrument_name: str | None = None
    symbol: str | None = None
    isin: str | None = None
    type: EventType
    date: dt.date
    amount: float | None = None
    currency: str | None = None
    title: str | None = None
    url: str | None = None
    is_estimate: bool = False
    source: str | None = None
    position_quantity: float | None = None
    expected_payment: float | None = None     # EUR, gross


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instrument_id: int
    type: EventType
    date: dt.date
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    title: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=400)
    is_estimate: bool = False


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: EventType | None = None
    date: dt.date | None = None
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    title: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=400)
    is_estimate: bool | None = None


class SearchResultOut(BaseModel):
    symbol: str
    name: str | None = None
    exchange: str | None = None
    type: str | None = None
    currency: str | None = None
    isin: str | None = None
    score: float | None = None
    source: str = "yahoo"
    preferred: bool = False
