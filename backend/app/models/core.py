"""Core ledger models: accounts, instruments, transactions, categories, balances.

OWNER: chief of staff (shared contract). Agents may ADD nullable columns if needed and
must mention it in their report. Never rename/remove columns without coordination.

Conventions
-----------
* Money: Numeric(18, 2) in the transaction currency; quantities/prices: Numeric(24, 8).
* `Transaction.amount` is the SIGNED cash effect on the cash side (settlement account or
  the account itself for cash accounts): buy -> negative, sell/dividend/interest/deposit ->
  positive, fee/tax/withdrawal -> negative. It already includes fees and taxes.
* For securities transactions in a depot, `account_id` is the depot and
  `cash_account_id` the settlement account (defaults to Account.settlement_account_id).
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class AccountType(str, enum.Enum):
    checking = "checking"          # Girokonto
    savings = "savings"            # Tagesgeld / Verrechnungskonto mit Zins
    brokerage = "brokerage"        # Depot
    credit_card = "credit_card"
    other = "other"


class InstrumentType(str, enum.Enum):
    stock = "stock"
    etf = "etf"
    fund = "fund"
    bond = "bond"
    crypto = "crypto"
    commodity = "commodity"
    other = "other"


class TxType(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    dividend = "dividend"
    interest = "interest"
    deposit = "deposit"            # Einzahlung / Gehalt / sonstige Gutschrift
    withdrawal = "withdrawal"      # Auszahlung / Lastschrift / Kartenzahlung
    transfer_in = "transfer_in"    # Umbuchung zwischen eigenen Konten (Eingang)
    transfer_out = "transfer_out"  # Umbuchung zwischen eigenen Konten (Ausgang)
    fee = "fee"
    tax = "tax"                    # Steuer / Steuererstattung (amount positiv)
    split = "split"                # quantity = Faktor (z.B. 4 für 1:4)
    delivery_in = "delivery_in"    # Depotübertrag / Einbuchung ohne Cash (Sparplan-Bonus, Spin-off)
    delivery_out = "delivery_out"


class CategoryKind(str, enum.Enum):
    income = "income"
    expense = "expense"
    transfer = "transfer"
    investment = "investment"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    institution: Mapped[str] = mapped_column(String(60))  # slug: postbank|ing|scalable|trade_republic|...
    type: Mapped[AccountType] = mapped_column(Enum(AccountType, name="account_type"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    iban: Mapped[str | None] = mapped_column(String(34))
    account_number: Mapped[str | None] = mapped_column(String(40))  # Depotnummer etc.
    color: Mapped[str | None] = mapped_column(String(9))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    opening_date: Mapped[date | None] = mapped_column(Date)
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))  # % p.a. for savings
    settlement_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    settlement_account: Mapped[Account | None] = relationship(remote_side="Account.id")


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True)
    wkn: Mapped[str | None] = mapped_column(String(6))
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)  # Yahoo ticker, e.g. SAP.DE
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[InstrumentType] = mapped_column(Enum(InstrumentType, name="instrument_type"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")  # quote currency of `symbol`
    exchange: Mapped[str | None] = mapped_column(String(40))
    sector: Mapped[str | None] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    # ETF/fund specific
    fund_provider: Mapped[str | None] = mapped_column(String(60))  # ishares|xtrackers|vanguard|...
    ter: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    distribution: Mapped[str | None] = mapped_column(String(20))  # distributing|accumulating
    logo_url: Mapped[str | None] = mapped_column(String(300))
    website: Mapped[str | None] = mapped_column(String(300))
    ir_url: Mapped[str | None] = mapped_column(String(300))
    meta: Mapped[dict | None] = mapped_column(JSONType)
    is_watchlist: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("parent_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[CategoryKind] = mapped_column(Enum(CategoryKind, name="category_kind"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    color: Mapped[str | None] = mapped_column(String(9))
    # simple auto-categorisation: list of case-insensitive substrings matched on counterparty/description
    match_rules: Mapped[list | None] = mapped_column(JSONType)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_tx_account_date", "account_id", "booking_date"),
        Index("ix_tx_instrument", "instrument_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    cash_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    type: Mapped[TxType] = mapped_column(Enum(TxType, name="tx_type"))
    booking_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))  # per unit, in `currency`
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # signed cash effect incl. fees/taxes
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    taxes: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))  # 1 EUR = x currency
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    counterparty: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    transfer_peer_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    cash_account: Mapped[Account | None] = relationship(foreign_keys=[cash_account_id])
    instrument: Mapped[Instrument | None] = relationship()
    category: Mapped[Category | None] = relationship()


class BalanceSnapshot(Base):
    """Known account balance at a date (from a statement) – used for reconciliation."""

    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    date: Mapped[date] = mapped_column(Date)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("date", "currency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))  # 1 EUR = rate * currency


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSONType)
