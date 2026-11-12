"""Pydantic schemas of the document-ingestion API (contract: docs/ARCHITECTURE.md → Dokumente)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TxType


class TxPayload(BaseModel):
    """Draft payload of kind 'transaction' as it must look when accepted."""
    model_config = ConfigDict(extra="ignore")

    account_id: int
    type: TxType
    booking_date: date
    value_date: date | None = None
    isin: str | None = None
    wkn: str | None = None
    name: str | None = None
    symbol: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal
    fees: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")
    currency: str = "EUR"
    fx_rate: Decimal | None = None
    counterparty: str | None = None
    description: str | None = None
    category_hint: str | None = None
    dup_index: int = 0

    @field_validator("quantity", "price", "amount", "fees", "taxes", "fx_rate", mode="before")
    @classmethod
    def _num(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, float):
            return Decimal(str(v))
        if isinstance(v, str):
            from app.ingest.util import parse_decimal

            return parse_decimal(v)
        return v

    @field_validator("fees", "taxes", mode="after")
    @classmethod
    def _zero(cls, v):
        return v if v is not None else Decimal("0")

    @field_validator("isin", "wkn", "name", "symbol", "counterparty", "description", "category_hint", mode="before")
    @classmethod
    def _blank(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v or None

    @field_validator("currency", mode="before")
    @classmethod
    def _cur(cls, v):
        return (v or "EUR").strip().upper()[:3]


class BalancePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_id: int
    date: date
    balance: Decimal

    @field_validator("balance", mode="before")
    @classmethod
    def _num(cls, v):
        return Decimal(str(v)) if isinstance(v, float) else v


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    kind: str
    payload: dict[str, Any]
    confidence: float | None = None
    status: str
    transaction_id: int | None = None
    balance_snapshot_id: int | None = None


class DocumentOut(BaseModel):
    id: int
    filename: str
    mime_type: str | None = None
    source_kind: str
    status: str
    account_id: int | None = None
    detected_institution: str | None = None
    detected_doc_type: str | None = None
    parser: str | None = None
    draft_count: int = 0
    pending_count: int = 0
    duplicate_count: int = 0
    accepted_count: int = 0
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    processed_at: datetime | None = None
    processing_ms: int | None = None
    used_llm: bool = False
    has_file: bool = False
    duplicate_upload: bool = False   # true when this upload matched an existing document (sha256)


class DocumentDetail(DocumentOut):
    drafts: list[DraftOut] = Field(default_factory=list)
    raw_text: str | None = None
    timings: dict[str, int] = Field(default_factory=dict)
    pages: int | None = None


class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    account_id: int | None = None


class DraftPatch(BaseModel):
    payload: dict[str, Any]
    status: str | None = None   # optional: 'pending' re-opens a rejected/duplicate draft


class ReprocessIn(BaseModel):
    force_llm: bool = False


class AcceptAllOut(BaseModel):
    accepted: int
    skipped: int
    duplicates: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    document: DocumentDetail


class LlmStatus(BaseModel):
    available: bool
    models: list[str] = Field(default_factory=list)
    text_model: str
    vision_model: str
    text_model_available: bool = False
    vision_model_available: bool = False
    ocr_available: bool = False
    url: str | None = None
    error: str | None = None
    hint: str | None = None
