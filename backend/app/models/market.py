"""Market data models. OWNER: market-data agent (may add columns/tables here)."""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.core import JSONType


class EventType(str, enum.Enum):
    ex_dividend = "ex_dividend"
    dividend_payment = "dividend_payment"
    earnings = "earnings"            # Quartals-/Jahreszahlen
    agm = "agm"                      # Hauptversammlung
    investor_day = "investor_day"    # Capital Markets Day / IR-Event
    split = "split"
    other = "other"


class Quote(Base):
    """Latest quote per instrument (one row per instrument, upserted)."""

    __tablename__ = "quotes"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(3))
    market_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(30), default="yahoo")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("instrument_id", "date"), Index("ix_ph_instr_date", "instrument_id", "date"))

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    date: Mapped[date] = mapped_column(Date)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    volume: Mapped[int | None] = mapped_column()


class InstrumentEvent(Base):
    __tablename__ = "instrument_events"
    __table_args__ = (UniqueConstraint("instrument_id", "type", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"))
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))  # dividend per share
    currency: Mapped[str | None] = mapped_column(String(3))
    title: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(400))
    is_estimate: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(30), default="yahoo")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EtfHolding(Base):
    """Constituent of an ETF (look-through)."""

    __tablename__ = "etf_holdings"
    __table_args__ = (Index("ix_etf_holdings_etf", "etf_instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    etf_instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    holding_isin: Mapped[str | None] = mapped_column(String(12), index=True)
    holding_symbol: Mapped[str | None] = mapped_column(String(32))
    holding_name: Mapped[str] = mapped_column(String(200))
    weight: Mapped[Decimal] = mapped_column(Numeric(9, 6))  # percent, e.g. 4.512300
    sector: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(80))
    asset_class: Mapped[str | None] = mapped_column(String(40))
    as_of: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(30))  # ishares|xtrackers|yahoo_top10|manual


class Fundamentals(Base):
    """Snapshot of fundamentals for aktienfinder-style charts. `data` layout documented in market/README.md."""

    __tablename__ = "fundamentals"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MarketDataLog(Base):
    __tablename__ = "market_data_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
