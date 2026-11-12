"""Document ingestion models. OWNER: ingestion agent (may add columns/tables here)."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.core import JSONType


class DocumentStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    review = "review"        # drafts waiting for user confirmation
    imported = "imported"    # all drafts decided
    failed = "failed"


class DraftStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    duplicate = "duplicate"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    storage_path: Mapped[str | None] = mapped_column(String(500))  # null for pasted text
    source_kind: Mapped[str] = mapped_column(String(20))  # pdf|image|text|csv|paste
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus, name="document_status"), default=DocumentStatus.uploaded)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))  # hint from user or detected
    detected_institution: Mapped[str | None] = mapped_column(String(60))
    detected_doc_type: Mapped[str | None] = mapped_column(String(60))  # trade_confirmation|dividend|statement|...
    parser: Mapped[str | None] = mapped_column(String(60))  # which parser/model produced drafts
    raw_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    # added by ingestion: {account_hint_id, size, pages, timings:{extract_ms,parse_ms,llm_ms}, warnings:[], used_llm}
    meta: Mapped[dict | None] = mapped_column(JSONType)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    drafts: Mapped[list[ExtractionDraft]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ExtractionDraft.id")


class ExtractionDraft(Base):
    """A proposed transaction/balance extracted from a document, confirmed by the user."""

    __tablename__ = "extraction_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="transaction")  # transaction|balance
    payload: Mapped[dict] = mapped_column(JSONType)  # matches TransactionCreate schema (+ isin/name hints)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[DraftStatus] = mapped_column(Enum(DraftStatus, name="draft_status"), default=DraftStatus.pending)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    balance_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("balance_snapshots.id"))  # added by ingestion
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="drafts")
