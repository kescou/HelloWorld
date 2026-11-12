"""Tier 7 – commit reviewed drafts into the ledger.

Standalone on purpose (ownership boundary); the hash formula is byte-compatible with
``app.services.ledger.dedupe_hash`` so manual entries and imports dedupe against each other.
The chief of staff may later replace ``create_transaction`` by the ledger service.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingest.pipeline import refresh_status
from app.models import (
    Account, AccountType, BalanceSnapshot, Category, DraftStatus, ExtractionDraft, Instrument, InstrumentType,
    Transaction, TxType,
)
from app.schemas.ingest import BalancePayload, TxPayload

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


def dedupe_hash(account_id: int, tx_type: TxType | str, booking_date: date, isin: str | None,
                quantity: Decimal | None, amount: Decimal, occurrence: int = 0) -> str:
    """sha256 over account_id|type|booking_date|isin|quantity|amount (+|n for the n-th identical
    line of one statement, n >= 1)."""
    type_s = tx_type.value if isinstance(tx_type, TxType) else str(tx_type)
    qty_s = "" if quantity is None else format(Decimal(quantity).quantize(Decimal("0.00000001")).normalize(), "f")
    parts = [str(account_id), type_s, booking_date.isoformat(), (isin or "").upper(), qty_s,
             format(Decimal(amount).quantize(CENT), "f")]
    if occurrence:
        parts.append(str(occurrence))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def guess_instrument_type(name: str | None) -> InstrumentType:
    n = (name or "").upper()
    if any(k in n for k in ("ETF", "UCITS", "ISHARES", "ISHS", "XTRACKERS", "VANGUARD", "AMUNDI", "LYXOR", "SPDR", "INDEX")):
        return InstrumentType.etf
    if any(k in n for k in ("ETC", "XETRA-GOLD", "GOLD ", "PHYSICAL")):
        return InstrumentType.commodity
    if any(k in n for k in ("FONDS", "FUND", "INVEST")):
        return InstrumentType.fund
    if any(k in n for k in ("ANLEIHE", "BOND", "SCHATZ", "BUNDESOBL", "PFANDBRIEF")):
        return InstrumentType.bond
    return InstrumentType.stock


def resolve_instrument(db: Session, *, isin: str | None, wkn: str | None = None, name: str | None = None,
                       symbol: str | None = None) -> Instrument | None:
    """Find by ISIN (then symbol, then exact name) or create. Fills missing name/wkn of an existing row."""
    inst = None
    if isin:
        inst = db.scalar(select(Instrument).where(Instrument.isin == isin.upper()))
    if inst is None and symbol:
        inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None and not isin and name:
        inst = db.scalar(select(Instrument).where(func.lower(Instrument.name) == name.lower()))
    if inst is None and not (isin or name or symbol):
        return None
    if inst is None:
        inst = Instrument(isin=isin.upper() if isin else None, wkn=wkn, symbol=symbol, name=(name or isin or symbol)[:200],
                          type=guess_instrument_type(name), currency="EUR")
        db.add(inst)
        db.flush()
        return inst
    if wkn and not inst.wkn:
        inst.wkn = wkn
    if name and (not inst.name or inst.name == inst.isin):
        inst.name = name[:200]
    return inst


def _category(db: Session, hint: str | None, *texts: str | None) -> Category | None:
    cats = list(db.scalars(select(Category)))
    if not cats:
        return None
    if hint:
        h = hint.strip().lower()
        for c in cats:
            if c.name.lower() == h:
                return c
    hay = " ".join(t for t in texts if t).lower()
    if hay:
        for c in cats:
            rules = c.match_rules if isinstance(c.match_rules, list) else []
            if any(isinstance(r, str) and r.strip() and r.strip().lower() in hay for r in rules):
                return c
    if hint:
        h = hint.strip().lower()
        for c in cats:
            if h in c.name.lower() or c.name.lower() in h:
                return c
    return None


def _validation_message(exc: ValidationError) -> str:
    fields = []
    for err in exc.errors():
        loc = str(err["loc"][0]) if err.get("loc") else "?"
        fields.append(_FIELD_DE.get(loc, loc))
    return "Bitte ergänzen/korrigieren: " + ", ".join(dict.fromkeys(fields)) + "."


def create_transaction(db: Session, p: TxPayload, *, source_document_id: int | None, force: bool = False) -> Transaction:
    account = db.get(Account, p.account_id)
    if account is None:
        raise CommitError(f"Konto {p.account_id} existiert nicht.")
    instrument = None
    if p.type in SECURITY_TX:
        instrument = resolve_instrument(db, isin=p.isin, wkn=p.wkn, name=p.name, symbol=p.symbol)
        if instrument is None:
            raise CommitError("Für Wertpapierbuchungen wird eine ISIN oder ein Name benötigt.")
        if p.type in (TxType.buy, TxType.sell, TxType.delivery_in, TxType.delivery_out) and not p.quantity:
            raise CommitError("Bitte ergänzen/korrigieren: Stück.")
    cash_account_id = None
    if account.type == AccountType.brokerage:
        cash_account_id = account.settlement_account_id
    ident = None
    if instrument is not None:
        ident = instrument.isin or instrument.symbol or f"id:{instrument.id}"
    occurrence = p.dup_index
    digest = dedupe_hash(account.id, p.type, p.booking_date, ident, p.quantity, p.amount, occurrence)
    existing = db.scalar(select(Transaction).where(Transaction.dedupe_hash == digest))
    if existing is not None:
        if not force:
            raise DuplicateDraft(existing.id)
        while existing is not None:
            occurrence += 1
            digest = dedupe_hash(account.id, p.type, p.booking_date, ident, p.quantity, p.amount, occurrence)
            existing = db.scalar(select(Transaction).where(Transaction.dedupe_hash == digest))
    category = None
    if instrument is None:
        category = _category(db, p.category_hint, p.counterparty, p.description)
    price = p.price
    tx = Transaction(
        account_id=account.id, cash_account_id=cash_account_id, instrument_id=instrument.id if instrument else None,
        type=p.type, booking_date=p.booking_date, value_date=p.value_date, quantity=p.quantity, price=price,
        amount=p.amount.quantize(CENT), fees=(p.fees or Decimal("0")).quantize(CENT), taxes=(p.taxes or Decimal("0")).quantize(CENT),
        currency=p.currency or account.currency or "EUR", fx_rate=p.fx_rate, category_id=category.id if category else None,
        counterparty=p.counterparty[:200] if p.counterparty else None, description=p.description,
        source_document_id=source_document_id, dedupe_hash=digest,
    )
    db.add(tx)
    db.flush()
    return tx


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
        try:
            tx = create_transaction(db, p, source_document_id=draft.document_id, force=force)
        except DuplicateDraft as dup:
            draft.status = DraftStatus.duplicate
            draft.transaction_id = None
            payload = dict(draft.payload)
            payload["duplicate_of"] = dup.transaction_id
            draft.payload = payload
            refresh_status(draft.document)
            raise
        draft.transaction_id = tx.id
    draft.status = DraftStatus.accepted
    refresh_status(draft.document)
    return draft


def reject_draft(db: Session, draft: ExtractionDraft) -> ExtractionDraft:
    if draft.status == DraftStatus.accepted:
        raise CommitError("Bereits übernommene Entwürfe können nicht abgelehnt werden – bitte die Transaktion löschen.")
    draft.status = DraftStatus.rejected
    refresh_status(draft.document)
    return draft
