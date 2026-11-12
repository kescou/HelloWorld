"""In-process data structures of the pipeline (independent of DB and HTTP)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TX_FIELDS = (
    "account_id", "type", "booking_date", "value_date", "isin", "wkn", "name", "symbol",
    "quantity", "price", "amount", "fees", "taxes", "currency", "fx_rate", "counterparty",
    "description", "category_hint",
)
SECURITY_TYPES = {"buy", "sell", "dividend", "split", "delivery_in", "delivery_out"}
TX_TYPES = {
    "buy", "sell", "dividend", "interest", "deposit", "withdrawal", "transfer_in", "transfer_out",
    "fee", "tax", "split", "delivery_in", "delivery_out",
}


@dataclass
class Draft:
    kind: str                      # 'transaction' | 'balance'
    payload: dict[str, Any]
    confidence: float = 0.95
    warnings: list[str] = field(default_factory=list)
    review_required: bool = False  # excluded from accept-all until the user looked at it
    status: str = "pending"        # pending | duplicate
    role: str | None = None        # 'brokerage' | 'cash' – which account of the institution

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "payload": self.payload, "confidence": round(self.confidence, 3),
                "status": self.status}


@dataclass
class ParseResult:
    parser: str
    drafts: list[Draft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # facts the parser learned about the document (used for account assignment)
    iban: str | None = None
    account_number: str | None = None
    account_role: str | None = None   # 'brokerage' | 'cash' (informational)
    settlement_iban: str | None = None
    doc_type: str | None = None
    coverage: float | None = None     # generic statement parser: share of lines understood


@dataclass
class DocContext:
    """What a parser gets to see."""
    text: str
    institution: str | None = None
    doc_type: str | None = None
    filename: str | None = None
    source_kind: str = "text"
    raw_bytes: bytes | None = None    # CSV parsers decode themselves


@dataclass
class Extraction:
    raw_text: str = ""
    institution: str | None = None
    doc_type: str | None = None
    parser: str | None = None
    drafts: list[Draft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    iban: str | None = None
    account_number: str | None = None
    settlement_iban: str | None = None
    used_llm: bool = False
    error: str | None = None
    timings: dict[str, int] = field(default_factory=dict)
    pages: int | None = None
