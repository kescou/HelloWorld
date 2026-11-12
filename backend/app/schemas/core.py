"""Pydantic schemas of the Backend-Core domain (accounts, transactions, categories, instruments).

Conventions: money is accepted as Decimal and returned as JSON number (float).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.core import AccountType, CategoryKind, InstrumentType, TxType


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- accounts
class AccountCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    institution: str = Field(default="other", max_length=60)
    type: AccountType = AccountType.checking
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    iban: str | None = None
    account_number: str | None = None
    color: str | None = None
    opening_balance: Decimal = Decimal("0")
    opening_date: date | None = None
    interest_rate: Decimal | None = None
    settlement_account_id: int | None = None
    is_active: bool = True
    sort_order: int = 0
    notes: str | None = None


class AccountUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    institution: str | None = None
    type: AccountType | None = None
    currency: str | None = None
    iban: str | None = None
    account_number: str | None = None
    color: str | None = None
    opening_balance: Decimal | None = None
    opening_date: date | None = None
    interest_rate: Decimal | None = None
    settlement_account_id: int | None = None
    is_active: bool | None = None
    sort_order: int | None = None
    notes: str | None = None


class AccountOut(_Out):
    id: int
    name: str
    institution: str
    type: AccountType
    currency: str
    iban: str | None = None
    account_number: str | None = None
    color: str | None = None
    opening_balance: float = 0.0
    opening_date: date | None = None
    interest_rate: float | None = None
    settlement_account_id: int | None = None
    is_active: bool = True
    sort_order: int = 0
    notes: str | None = None
    # computed
    balance: float = 0.0        # cash balance
    market_value: float = 0.0   # securities value (brokerage accounts), EUR
    total_value: float = 0.0


class HistoryPoint(BaseModel):
    date: date
    value: float


class SnapshotOut(BaseModel):
    id: int
    date: date
    balance: float                 # balance printed on the statement
    computed_balance: float        # ledger balance at the end of that day
    difference: float              # balance - computed_balance (0 = reconciled)
    source_document_id: int | None = None


class AccountDetail(AccountOut):
    history: list[HistoryPoint] = []
    snapshots: list[SnapshotOut] = []


# --------------------------------------------------------------------------- transactions
class TransactionCreate(_In):
    """Also accepts the ingestion draft payload (isin/wkn/name/symbol/category_hint)."""

    account_id: int
    type: TxType
    booking_date: date
    value_date: date | None = None
    cash_account_id: int | None = None
    instrument_id: int | None = None
    # instrument hints (used when instrument_id is missing)
    isin: str | None = None
    wkn: str | None = None
    symbol: str | None = None
    name: str | None = None
    instrument_type: InstrumentType | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None   # signed cash effect; computed when missing
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")
    currency: str = "EUR"
    fx_rate: Decimal | None = None
    category_id: int | None = None
    category_hint: str | None = None
    counterparty: str | None = None
    description: str | None = None
    notes: str | None = None
    source_document_id: int | None = None
    allow_duplicate: bool = False   # skip the dedupe check (stores no dedupe_hash)

    @field_validator("fees", "taxes", mode="before")
    @classmethod
    def _none_to_zero(cls, v: Any) -> Any:
        return Decimal("0") if v is None or v == "" else v

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, v: Any) -> Any:
        return (v or "EUR").upper() if isinstance(v, str) or v is None else v

    @field_validator("isin", "wkn", "symbol", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v.upper() or None
        return v


class TransactionUpdate(_In):
    account_id: int | None = None
    type: TxType | None = None
    booking_date: date | None = None
    value_date: date | None = None
    cash_account_id: int | None = None
    instrument_id: int | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    fees: Decimal | None = None
    taxes: Decimal | None = None
    currency: str | None = None
    fx_rate: Decimal | None = None
    category_id: int | None = None
    counterparty: str | None = None
    description: str | None = None
    notes: str | None = None


class TransferCreate(_In):
    from_account_id: int
    to_account_id: int
    amount: Decimal = Field(gt=0)
    date: date
    description: str | None = None
    allow_duplicate: bool = False


class TransactionOut(_Out):
    id: int
    account_id: int
    cash_account_id: int | None = None
    instrument_id: int | None = None
    type: TxType
    booking_date: date
    value_date: date | None = None
    quantity: float | None = None
    price: float | None = None
    amount: float
    fees: float = 0.0
    taxes: float = 0.0
    currency: str = "EUR"
    fx_rate: float | None = None
    category_id: int | None = None
    counterparty: str | None = None
    description: str | None = None
    transfer_peer_id: int | None = None
    source_document_id: int | None = None
    dedupe_hash: str | None = None
    notes: str | None = None
    created_at: datetime | None = None
    # joined
    account_name: str | None = None
    cash_account_name: str | None = None
    instrument_name: str | None = None
    instrument_isin: str | None = None
    instrument_symbol: str | None = None
    category_name: str | None = None


class TransactionList(BaseModel):
    items: list[TransactionOut]
    total: int


# --------------------------------------------------------------------------- categories
class CategoryCreate(_In):
    name: str = Field(min_length=1, max_length=80)
    kind: CategoryKind = CategoryKind.expense
    parent_id: int | None = None
    color: str | None = None
    match_rules: list[str] | None = None


class CategoryUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: CategoryKind | None = None
    parent_id: int | None = None
    color: str | None = None
    match_rules: list[str] | None = None


class CategoryOut(_Out):
    id: int
    name: str
    kind: CategoryKind
    parent_id: int | None = None
    color: str | None = None
    match_rules: list[str] | None = None
    transaction_count: int = 0


# --------------------------------------------------------------------------- instruments
class InstrumentCreate(_In):
    isin: str | None = None
    symbol: str | None = None
    wkn: str | None = None
    name: str | None = None
    type: InstrumentType | None = None
    currency: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    fund_provider: str | None = None
    ter: Decimal | None = None
    distribution: str | None = None
    logo_url: str | None = None
    website: str | None = None
    ir_url: str | None = None
    meta: dict | None = None
    is_watchlist: bool | None = None

    @field_validator("isin", "wkn", "symbol", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().upper() or None
        return v


class InstrumentUpdate(InstrumentCreate):
    pass


class InstrumentOut(_Out):
    id: int
    isin: str | None = None
    wkn: str | None = None
    symbol: str | None = None
    name: str
    type: InstrumentType
    currency: str
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    fund_provider: str | None = None
    ter: float | None = None
    distribution: str | None = None
    logo_url: str | None = None
    website: str | None = None
    ir_url: str | None = None
    meta: dict | None = None
    is_watchlist: bool = False
    created_at: datetime | None = None
    # computed (list view)
    price: float | None = None
    price_currency: str | None = None
    day_change_pct: float | None = None
    quantity: float | None = None
    quote: dict | None = None      # latest quote (same shape as GET /market/quotes), null when unknown
