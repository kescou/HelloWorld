"""Tier 7 – commit reviewed drafts into the ledger.

Transactions are created by ``app.services.ledger.create_transaction`` (the one place for amount
normalisation, instrument resolution, dedupe hash and auto-categorisation via ``match_rules``).
This module only adds the import specifics:

* ``payload.dup_index`` -> occurrence suffix of the dedupe hash (n-th identical line of one statement)
* duplicates -> draft status ``duplicate`` (``force`` books them under the next free occurrence)
* balance drafts -> ``balance_snapshots``
* after every accepted cash line the own-account transfer matcher runs (``app.services.transfers``)
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.pipeline import refresh_status
from app.models import Account, BalanceSnapshot, DraftStatus, ExtractionDraft, Transaction, TxType
from app.schemas.ingest import BalancePayload, TxPayload
from app.services import ledger, transfers
from app.services.ledger import DuplicateTransactionError, dedupe_hash, guess_instrument_type  # noqa: F401 (re-export)

CENT = Decimal("0.01")
SECURITY_TX = {TxType.buy, TxType.sell, TxType.dividend, TxType.split, TxType.delivery_in, TxType.delivery_out}

_FIELD_DE = {
    "account_id": "Konto", "type": "Buchungsart", "booking_date": "Buchungsdatum", "amount": "Betrag",
    "date": "Datum", "balance": "Kontostand", "quantity": "Stück", "price": "Kurs", "value_date": "Valuta",
}


class CommitError(ValueError):
    """Draft cannot be committed as is (German, user-facing message)."""


class DuplicateDraft(CommitError):
    def __init__(self, transaction_id: int):
        super().__init__(f"Diese Buchung existiert bereits (Transaktion #{transaction_id}).")
        self.transaction_id = transaction_id


def _validation_message(exc: ValidationError) -> str:
    fields = []
    for err in exc.errors():
        loc = str(err["loc"][0]) if err.get("loc") else "?"
        fields.append(_FIELD_DE.get(loc, loc))
    return "Bitte ergänzen/korrigieren: " + ", ".join(dict.fromkeys(fields)) + "."


def _http_message(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        detail = detail.get("message") or str(detail)
    return str(detail)


def create_transaction(db: Session, p: TxPayload, *, source_document_id: int | None, force: bool = False,
                       counterparty_iban: str | None = None) -> Transaction:
    """Book one draft payload through the ledger service. Raises CommitError / DuplicateDraft."""
    if db.get(Account, p.account_id) is None:
        raise CommitError(f"Konto {p.account_id} existiert nicht.")
    if p.type in SECURITY_TX:
        if not (p.isin or p.name or p.symbol):
            raise CommitError("Für Wertpapierbuchungen wird eine ISIN oder ein Name benötigt.")
        if p.type in (TxType.buy, TxType.sell, TxType.delivery_in, TxType.delivery_out) and not p.quantity:
            raise CommitError("Bitte ergänzen/korrigieren: Stück.")
    notes = None
    iban = "".join((counterparty_iban or "").split()).upper()
    if iban and iban not in "".join(f"{p.counterparty or ''}{p.description or ''}".split()).upper():
        notes = f"Gegenkonto-IBAN: {iban}"      # keeps the evidence for the transfer matcher
    data = {
        "account_id": p.account_id, "type": p.type, "booking_date": p.booking_date, "value_date": p.value_date,
        "isin": p.isin, "wkn": p.wkn, "name": p.name, "symbol": p.symbol,
        "quantity": p.quantity, "price": p.price, "amount": p.amount, "fees": p.fees, "taxes": p.taxes,
        "currency": p.currency, "fx_rate": p.fx_rate, "counterparty": p.counterparty, "description": p.description,
        "category_hint": p.category_hint, "notes": notes, "source_document_id": source_document_id,
    }
    try:
        with db.begin_nested():     # a failed attempt must not leave a half-created instrument behind
            return ledger.create_transaction(db, data, occurrence=p.dup_index, force_duplicate=force)
    except DuplicateTransactionError as exc:
        raise DuplicateDraft(exc.existing_id or 0) from exc
    except HTTPException as exc:
        raise CommitError(_http_message(exc)) from exc
    except ValidationError as exc:
        raise CommitError(_validation_message(exc)) from exc


def accept_draft(db: Session, draft: ExtractionDraft, *, force: bool = False) -> ExtractionDraft:
    """Create the Transaction / BalanceSnapshot for a draft. Raises CommitError / DuplicateDraft.
    The caller commits."""
    if draft.status == DraftStatus.accepted:
        return draft
    if draft.status == DraftStatus.duplicate and not force:
        raise DuplicateDraft(draft.payload.get("duplicate_of") or draft.transaction_id or 0)
    if draft.kind == "balance":
        try:
            b = BalancePayload.model_validate(draft.payload)
        except ValidationError as exc:
            raise CommitError(_validation_message(exc)) from exc
        if db.get(Account, b.account_id) is None:
            raise CommitError(f"Konto {b.account_id} existiert nicht.")
        snap = db.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == b.account_id, BalanceSnapshot.date == b.date))
        if snap is None:
            snap = BalanceSnapshot(account_id=b.account_id, date=b.date, balance=b.balance.quantize(CENT),
                                   source_document_id=draft.document_id)
            db.add(snap)
        else:
            snap.balance = b.balance.quantize(CENT)
            snap.source_document_id = draft.document_id
        db.flush()
        draft.balance_snapshot_id = snap.id
    else:
        try:
            p = TxPayload.model_validate(draft.payload)
        except ValidationError as exc:
            raise CommitError(_validation_message(exc)) from exc
        payload = draft.payload or {}
        try:
            tx = create_transaction(db, p, source_document_id=draft.document_id, force=force,
                                    counterparty_iban=payload.get("counterparty_iban"))
        except DuplicateDraft as dup:
            draft.status = DraftStatus.duplicate
            draft.transaction_id = None
            payload = dict(draft.payload)
            payload["duplicate_of"] = dup.transaction_id
            draft.payload = payload
            refresh_status(draft.document)
            raise
        draft.transaction_id = tx.id
        if tx.instrument_id is None and tx.cash_account_id is None:
            # own-account transfer? link with the counter booking (or re-type when only the IBAN is known)
            other = payload.get("transfer_account_id")
            hints = {tx.id: other} if isinstance(other, int) else None
            transfers.link_transfers(db, only_ids=[tx.id], account_hints=hints)
    draft.status = DraftStatus.accepted
    refresh_status(draft.document)
    return draft


def reject_draft(db: Session, draft: ExtractionDraft) -> ExtractionDraft:
    if draft.status == DraftStatus.accepted:
        raise CommitError("Bereits übernommene Entwürfe können nicht abgelehnt werden – bitte die Transaktion löschen.")
    draft.status = DraftStatus.rejected
    refresh_status(draft.document)
    return draft
