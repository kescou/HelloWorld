"""Ledger: cash balances, transaction creation (amount/instrument/dedupe), transfers, categories.

Balance rule (docs/ARCHITECTURE.md "Saldo-Logik"):
    cash balance(A) = A.opening_balance
                      + sum(amount) of tx with (account_id = A and cash_account_id IS NULL)
                      + sum(amount) of tx with cash_account_id = A
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.models.core import (
    Account, AccountType, Category, Instrument, InstrumentType, Transaction, TxType,
)
from app.schemas.core import TransactionCreate, TransactionUpdate, TransferCreate

CENT = Decimal("0.01")
ZERO = Decimal("0")

SECURITY_TYPES = {TxType.buy, TxType.sell, TxType.split, TxType.delivery_in, TxType.delivery_out}
TRANSFER_TYPES = {TxType.transfer_in, TxType.transfer_out}
NO_CASH_TYPES = {TxType.split, TxType.delivery_in, TxType.delivery_out}
#: types for which match_rules based auto-categorisation makes sense
CATEGORIZABLE_TYPES = {TxType.deposit, TxType.withdrawal, TxType.fee, TxType.interest, TxType.tax}


class DuplicateTransactionError(HTTPException):
    """Raised (HTTP 409) when a transaction with the same dedupe hash already exists."""

    def __init__(self, existing_id: int | None = None):
        super().__init__(
            status_code=409,
            detail={"message": "Transaktion existiert bereits (Duplikat)", "existing_id": existing_id},
        )
        self.existing_id = existing_id


def q2(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- balances
def _cash_account_expr():
    return func.coalesce(Transaction.cash_account_id, Transaction.account_id)


def cash_balances(db: Session, as_of: date | None = None) -> dict[int, Decimal]:
    """Cash balance for every account (account currency)."""
    stmt = select(_cash_account_expr().label("acc"), func.coalesce(func.sum(Transaction.amount), 0)).group_by("acc")
    if as_of is not None:
        stmt = stmt.where(Transaction.booking_date <= as_of)
    sums = {acc: Decimal(total) for acc, total in db.execute(stmt)}
    out: dict[int, Decimal] = {}
    for acc_id, opening, opening_date in db.execute(select(Account.id, Account.opening_balance, Account.opening_date)):
        opening = Decimal(opening or 0)
        if as_of is not None and opening_date is not None and opening_date > as_of:
            opening = ZERO
        out[acc_id] = q2(opening + sums.get(acc_id, ZERO))
    return out


def cash_balance(db: Session, account_id: int, as_of: date | None = None) -> Decimal:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Konto nicht gefunden")
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        or_(
            (Transaction.account_id == account_id) & Transaction.cash_account_id.is_(None),
            Transaction.cash_account_id == account_id,
        )
    )
    opening = Decimal(account.opening_balance or 0)
    if as_of is not None:
        stmt = stmt.where(Transaction.booking_date <= as_of)
        if account.opening_date is not None and account.opening_date > as_of:
            opening = ZERO
    return q2(opening + Decimal(db.scalar(stmt) or 0))


# --------------------------------------------------------------------------- helpers
def compute_amount(
    tx_type: TxType,
    quantity: Decimal | None,
    price: Decimal | None,
    fees: Decimal = ZERO,
    taxes: Decimal = ZERO,
) -> Decimal | None:
    """Signed cash effect derived from quantity x price -/+ fees/taxes. None if not derivable."""
    fees = fees or ZERO
    taxes = taxes or ZERO
    gross = quantity * price if quantity is not None and price is not None else None
    if tx_type in NO_CASH_TYPES:
        return ZERO
    if tx_type == TxType.buy:
        return None if gross is None else q2(-(gross + fees + taxes))
    if tx_type == TxType.sell:
        return None if gross is None else q2(gross - fees - taxes)
    if tx_type == TxType.dividend:
        return None if gross is None else q2(gross - taxes - fees)
    if tx_type == TxType.fee:
        return q2(-fees) if fees else None
    if tx_type == TxType.tax:
        return q2(-taxes) if taxes else None
    return None


def dedupe_hash(
    account_id: int, tx_type: TxType | str, booking_date: date, isin: str | None,
    quantity: Decimal | None, amount: Decimal, occurrence: int = 0,
) -> str:
    """sha256 over account_id|type|booking_date|isin|quantity|amount.

    `occurrence` (n >= 1) appends `|n`: the n-th further identical line of ONE statement (two 2,50 €
    coffees on the same day) – used by the document import, see app.ingest.commit.
    """
    type_s = tx_type.value if isinstance(tx_type, TxType) else str(tx_type)
    qty_s = "" if quantity is None else format(Decimal(quantity).quantize(Decimal("0.00000001")).normalize(), "f")
    parts = [str(account_id), type_s, booking_date.isoformat(), (isin or "").upper(), qty_s, format(q2(amount), "f")]
    if occurrence:
        parts.append(str(occurrence))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _tx_hash(tx: Transaction, instrument: Instrument | None, occurrence: int = 0) -> str:
    ident = None
    if instrument is not None:
        ident = instrument.isin or instrument.symbol or f"id:{instrument.id}"
    return dedupe_hash(tx.account_id, tx.type, tx.booking_date, ident, tx.quantity, tx.amount, occurrence)


def _hash_key(tx: Transaction) -> tuple:
    """The fields that go into the dedupe hash (to detect whether an update touched them)."""
    return (tx.account_id, tx.type, tx.booking_date, tx.instrument_id,
            None if tx.quantity is None else Decimal(tx.quantity), q2(tx.amount))


def guess_instrument_type(name: str | None) -> InstrumentType:
    n = (name or "").upper()
    if any(k in n for k in ("ETF", "UCITS", "ISHARES", "ISHS", "XTRACKERS", "VANGUARD", "AMUNDI", "LYXOR", "SPDR", "INDEX")):
        return InstrumentType.etf
    if any(k in n for k in ("ETC", "XETRA-GOLD", "GOLD ", "PHYSICAL")):
        return InstrumentType.commodity
    if "BITCOIN" in n or "ETHEREUM" in n:
        return InstrumentType.crypto
    if any(k in n for k in ("ANLEIHE", "BOND", "SCHATZ", "BUNDESOBL", "PFANDBRIEF")):
        return InstrumentType.bond
    if any(k in n for k in ("FONDS", " FUND", "INVEST")):
        return InstrumentType.fund
    return InstrumentType.stock


def resolve_instrument(
    db: Session, *, instrument_id: int | None = None, isin: str | None = None, symbol: str | None = None,
    wkn: str | None = None, name: str | None = None, instrument_type: InstrumentType | None = None,
    currency: str | None = None, create: bool = True,
) -> Instrument | None:
    """Find an instrument by id / ISIN / symbol / WKN / name; create it when unknown."""
    if instrument_id is not None:
        inst = db.get(Instrument, instrument_id)
        if inst is None:
            raise HTTPException(404, "Instrument nicht gefunden")
        return inst
    isin = isin.strip().upper() if isin else None
    symbol = symbol.strip().upper() if symbol else None
    wkn = wkn.strip().upper() if wkn else None
    inst = None
    if isin:
        inst = db.scalar(select(Instrument).where(Instrument.isin == isin))
    if inst is None and symbol:
        inst = db.scalar(select(Instrument).where(func.upper(Instrument.symbol) == symbol).order_by(Instrument.id))
        if inst is not None and isin and inst.isin and inst.isin != isin:
            inst = None
    if inst is None and wkn:
        inst = db.scalar(select(Instrument).where(func.upper(Instrument.wkn) == wkn).order_by(Instrument.id))
        if inst is not None and isin and inst.isin and inst.isin != isin:
            inst = None
    if inst is None and name and not (isin or symbol or wkn):
        inst = db.scalar(select(Instrument).where(func.lower(Instrument.name) == name.strip().lower()).order_by(Instrument.id))
    if inst is not None:
        # enrich missing identifiers
        if isin and not inst.isin:
            inst.isin = isin
        if symbol and not inst.symbol:
            inst.symbol = symbol
        if wkn and not inst.wkn:
            inst.wkn = wkn[:6]
        if name and (not inst.name or inst.name in (inst.isin, inst.symbol, inst.wkn)):
            inst.name = name[:200]      # placeholder name (created from a bare ISIN) -> real name
        return inst
    if not create or not (isin or symbol or wkn or name):
        return None
    inst = Instrument(
        isin=isin, symbol=symbol, wkn=wkn[:6] if wkn else None,
        name=(name or isin or symbol or wkn)[:200],
        type=instrument_type or guess_instrument_type(name),
        currency=(currency or "EUR").upper(),
    )
    db.add(inst)
    db.flush()
    return inst


# --------------------------------------------------------------------------- categories
def _rules(cat: Category) -> list[str]:
    rules = cat.match_rules or []
    if isinstance(rules, str):
        rules = [rules]
    return [str(r).strip().lower() for r in rules if str(r).strip()]


#: rules up to this length must match a whole word ("Uber" must not hit "Huber", "TIER" not "Tierarzt")
SHORT_RULE_LEN = 4


def _compile_rules(categories: Iterable[Category]) -> list[tuple[int, Category, str, re.Pattern | None]]:
    compiled = []
    for cat in categories:
        for rule in _rules(cat):
            pattern = re.compile(rf"(?<!\w){re.escape(rule)}(?!\w)") if len(rule) <= SHORT_RULE_LEN else None
            compiled.append((len(rule), cat, rule, pattern))
    compiled.sort(key=lambda c: -c[0])   # longest rule wins
    return compiled


def _match_compiled(compiled, *texts: str | None) -> Category | None:
    hay = " ".join(t for t in texts if t).lower()
    if not hay:
        return None
    for _length, cat, rule, pattern in compiled:
        if (pattern.search(hay) if pattern is not None else rule in hay):
            return cat
    return None


def match_category(categories: Iterable[Category], *texts: str | None) -> Category | None:
    """Category whose longest match rule hits counterparty/description.

    Rules are case-insensitive substrings; rules of up to 4 characters must match a whole word.
    """
    return _match_compiled(_compile_rules(categories), *texts)


def auto_assign_categories(db: Session, only_ids: Iterable[int] | None = None) -> int:
    """Apply match_rules to all uncategorised transactions. Returns number of updated rows."""
    compiled = _compile_rules(db.scalars(select(Category)).all())
    defaults = {t: default_category(db, t) for t in TYPE_DEFAULT_CATEGORY}
    if not compiled and not any(defaults.values()):
        return 0
    stmt = select(Transaction).where(
        Transaction.category_id.is_(None), Transaction.type.in_(CATEGORIZABLE_TYPES | set(TYPE_DEFAULT_CATEGORY))
    )
    if only_ids is not None:
        stmt = stmt.where(Transaction.id.in_(list(only_ids)))
    updated = 0
    for tx in db.scalars(stmt):
        cat = _match_compiled(compiled, tx.counterparty, tx.description) if tx.type in CATEGORIZABLE_TYPES else None
        cat = cat or defaults.get(tx.type)
        if cat is not None:
            tx.category_id = cat.id
            updated += 1
    db.flush()
    return updated


def _category_from_hint(db: Session, hint: str | None) -> Category | None:
    if not hint:
        return None
    return db.scalar(select(Category).where(func.lower(Category.name) == hint.strip().lower()).order_by(Category.id))


def resolve_category(db: Session, tx_type: TxType, hint: str | None, *texts: str | None) -> Category | None:
    """Automatic category of a new non-securities transaction.

    Priority: the user's `match_rules` (longest rule wins; only for deposit/withdrawal/fee/interest/tax)
    > category named exactly like `hint` > category whose name contains the hint (or vice versa).
    Own-account transfers only ever get the hinted category or "Umbuchung".
    """
    if tx_type in TRANSFER_TYPES:
        return _category_from_hint(db, hint) or _category_from_hint(db, "Umbuchung")
    if tx_type == TxType.dividend:
        return _category_from_hint(db, hint) or _category_from_hint(db, "Dividenden")
    categories = db.scalars(select(Category).order_by(Category.id)).all()
    if not categories:
        return None
    if tx_type in CATEGORIZABLE_TYPES:
        cat = match_category(categories, *texts)
        if cat is not None:
            return cat
    if hint:
        h = hint.strip().lower()
        for cat in categories:
            if cat.name.lower() == h:
                return cat
        if len(h) >= 4:
            for cat in categories:
                name = cat.name.lower()
                if h in name or (len(name) >= 4 and name in h):
                    return cat
    return default_category(db, tx_type)


#: category a booking gets from its TYPE alone when no rule and no hint matched (Kapitalerträge)
TYPE_DEFAULT_CATEGORY = {TxType.interest: "Zinsen", TxType.dividend: "Dividenden"}


def default_category(db: Session, tx_type: TxType) -> Category | None:
    name = TYPE_DEFAULT_CATEGORY.get(tx_type)
    return _category_from_hint(db, name) if name else None


# --------------------------------------------------------------------------- create / update / delete
def _get_account(db: Session, account_id: int, what: str = "Konto") -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, f"{what} {account_id} nicht gefunden")
    return account


def _default_cash_account(account: Account, cash_account_id: int | None) -> int | None:
    if cash_account_id is not None:
        return None if cash_account_id == account.id else cash_account_id
    if account.type == AccountType.brokerage and account.settlement_account_id:
        return account.settlement_account_id
    return None


def _find_duplicate(db: Session, digest: str, exclude_id: int | None = None) -> int | None:
    stmt = select(Transaction.id).where(Transaction.dedupe_hash == digest)
    if exclude_id is not None:
        stmt = stmt.where(Transaction.id != exclude_id)
    return db.scalar(stmt)


def create_transaction(
    db: Session, data: TransactionCreate | Mapping[str, Any], *, occurrence: int = 0, force_duplicate: bool = False,
) -> Transaction:
    """Create one journal entry (flushes, does NOT commit). The ONE place transactions are created –
    used by the API, the demo seed and the document import (app.ingest.commit).

    `occurrence`       – n-th identical line within one statement (dedupe hash gets a `|n` suffix)
    `force_duplicate`  – book although the hash exists: the next free occurrence index is used, so the
                         row keeps a (unique) dedupe hash

    * resolves/creates the instrument via isin / symbol / wkn / name when no instrument_id is given
    * defaults cash_account_id to the depot's settlement account
    * computes `amount` when missing and normalises its sign for buy/fee/deposit/withdrawal
    * computes the dedupe hash and raises DuplicateTransactionError (HTTP 409) on duplicates
    * auto-assigns a category via category_hint / match_rules
    """
    if not isinstance(data, TransactionCreate):
        data = TransactionCreate.model_validate(dict(data))
    account = _get_account(db, data.account_id)
    tx_type = data.type

    instrument: Instrument | None = None
    wants_instrument = tx_type in SECURITY_TYPES or tx_type == TxType.dividend
    if data.instrument_id is not None or (wants_instrument and (data.isin or data.symbol or data.wkn or data.name)):
        instrument = resolve_instrument(
            db, instrument_id=data.instrument_id, isin=data.isin, symbol=data.symbol, wkn=data.wkn,
            name=data.name, instrument_type=data.instrument_type,
        )
    if tx_type in SECURITY_TYPES and instrument is None:
        raise HTTPException(422, "Wertpapier-Transaktion benötigt instrument_id, isin oder symbol")
    if tx_type in SECURITY_TYPES and (data.quantity is None or data.quantity <= 0):
        raise HTTPException(422, "quantity muss > 0 sein")

    amount = data.amount
    if tx_type in NO_CASH_TYPES:
        amount = ZERO
    elif amount is None:
        # price is quoted in `currency`; the cash effect is booked in EUR (fees/taxes are EUR already)
        price_cash = data.price
        if price_cash is not None and data.currency != (account.currency or "EUR") and data.fx_rate:
            price_cash = price_cash / data.fx_rate
        amount = compute_amount(tx_type, data.quantity, price_cash, data.fees, data.taxes)
        if amount is None:
            raise HTTPException(422, "amount fehlt und kann nicht aus quantity × price berechnet werden")
    amount = q2(amount)
    # sign normalisation
    if tx_type in (TxType.buy, TxType.fee, TxType.transfer_out):
        amount = -abs(amount)
    elif tx_type == TxType.transfer_in:
        amount = abs(amount)
    elif tx_type == TxType.deposit and amount < 0:
        tx_type = TxType.withdrawal
    elif tx_type == TxType.withdrawal and amount > 0:
        tx_type = TxType.deposit

    price = data.price
    if price is None and tx_type in (TxType.buy, TxType.sell) and data.quantity:
        # derive the unit price from the cash amount
        gross = abs(amount) - (data.fees + data.taxes) if tx_type == TxType.buy else abs(amount) + data.fees + data.taxes
        if data.currency != (account.currency or "EUR") and data.fx_rate:
            gross = gross * data.fx_rate
        price = (gross / data.quantity).quantize(Decimal("0.00000001"))

    cash_account_id = _default_cash_account(account, data.cash_account_id)
    if cash_account_id is not None:
        _get_account(db, cash_account_id, "Verrechnungskonto")

    category_id = data.category_id
    if category_id is None and tx_type not in SECURITY_TYPES:
        cat = resolve_category(db, tx_type, data.category_hint, data.counterparty, data.description)
        category_id = cat.id if cat else None
    elif category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(404, "Kategorie nicht gefunden")

    tx = Transaction(
        account_id=account.id, cash_account_id=cash_account_id,
        instrument_id=instrument.id if instrument else None,
        type=tx_type, booking_date=data.booking_date, value_date=data.value_date,
        quantity=data.quantity, price=price, amount=amount,
        fees=q2(data.fees), taxes=q2(data.taxes), currency=data.currency or account.currency or "EUR",
        fx_rate=data.fx_rate,
        category_id=category_id, counterparty=(data.counterparty or None) and data.counterparty[:200],
        description=data.description, notes=data.notes, source_document_id=data.source_document_id,
    )
    if not data.allow_duplicate:
        digest = _tx_hash(tx, instrument, occurrence)
        existing = _find_duplicate(db, digest)
        if existing is not None and not force_duplicate:
            raise DuplicateTransactionError(existing)
        while existing is not None:
            occurrence += 1
            digest = _tx_hash(tx, instrument, occurrence)
            existing = _find_duplicate(db, digest)
        tx.dedupe_hash = digest
    db.add(tx)
    db.flush()
    return tx


def create_transfer(db: Session, data: TransferCreate | Mapping[str, Any]) -> tuple[Transaction, Transaction]:
    """Transfer between two own accounts: two linked transactions (transfer_out, transfer_in)."""
    if not isinstance(data, TransferCreate):
        data = TransferCreate.model_validate(dict(data))
    if data.from_account_id == data.to_account_id:
        raise HTTPException(422, "Quell- und Zielkonto müssen verschieden sein")
    src = _get_account(db, data.from_account_id, "Quellkonto")
    dst = _get_account(db, data.to_account_id, "Zielkonto")
    amount = q2(data.amount)
    desc = data.description or f"Umbuchung {src.name} → {dst.name}"
    category = db.scalar(
        select(Category).where(func.lower(Category.name) == "umbuchung").order_by(Category.id)
    )
    out_tx = Transaction(
        account_id=src.id, type=TxType.transfer_out, booking_date=data.date, amount=-amount,
        fees=ZERO, taxes=ZERO, currency=src.currency or "EUR", counterparty=dst.name, description=desc,
        category_id=category.id if category else None,
    )
    in_tx = Transaction(
        account_id=dst.id, type=TxType.transfer_in, booking_date=data.date, amount=amount,
        fees=ZERO, taxes=ZERO, currency=dst.currency or "EUR", counterparty=src.name, description=desc,
        category_id=category.id if category else None,
    )
    if not data.allow_duplicate:
        for tx in (out_tx, in_tx):
            digest = _tx_hash(tx, None)
            existing = _find_duplicate(db, digest)
            if existing is not None:
                raise DuplicateTransactionError(existing)
            tx.dedupe_hash = digest
    db.add_all([out_tx, in_tx])
    db.flush()
    out_tx.transfer_peer_id = in_tx.id
    in_tx.transfer_peer_id = out_tx.id
    db.flush()
    return out_tx, in_tx


def update_transaction(db: Session, tx: Transaction, data: TransactionUpdate) -> Transaction:
    fields = data.model_dump(exclude_unset=True)
    if "account_id" in fields and fields["account_id"] is not None:
        _get_account(db, fields["account_id"])
    if fields.get("cash_account_id") is not None:
        _get_account(db, fields["cash_account_id"], "Verrechnungskonto")
    if fields.get("instrument_id") is not None and db.get(Instrument, fields["instrument_id"]) is None:
        raise HTTPException(404, "Instrument nicht gefunden")
    if fields.get("category_id") is not None and db.get(Category, fields["category_id"]) is None:
        raise HTTPException(404, "Kategorie nicht gefunden")
    not_nullable = {"account_id", "type", "booking_date", "amount", "fees", "taxes", "currency"}
    key_before = _hash_key(tx)
    for key, value in fields.items():
        if value is None and key in not_nullable:
            continue
        setattr(tx, key, value)
    if tx.cash_account_id == tx.account_id:
        tx.cash_account_id = None
    # recompute amount if trade parameters changed but amount was not given explicitly
    if "amount" not in fields and {"quantity", "price", "fees", "taxes"} & fields.keys():
        computed = compute_amount(tx.type, tx.quantity, tx.price, tx.fees, tx.taxes)
        if computed is not None and tx.type in (TxType.buy, TxType.sell):
            tx.amount = computed
    if tx.type in NO_CASH_TYPES:
        tx.amount = ZERO
    elif tx.type in (TxType.buy, TxType.fee, TxType.transfer_out):
        tx.amount = -abs(tx.amount)
    elif tx.type == TxType.transfer_in:
        tx.amount = abs(tx.amount)
    tx.amount = q2(tx.amount)
    key_changed = _hash_key(tx) != key_before
    if tx.transfer_peer_id and tx.type not in TRANSFER_TYPES:
        # re-typed to a normal booking: dissolve the link (and remember it so the matcher does not re-link)
        from app.services import transfers

        transfers.unlink(db, tx)
    elif tx.transfer_peer_id:
        # keep the transfer peer in sync. Manually created pairs mirror date + amount + description;
        # imported pairs are two real statement lines (dates may differ by a few days) -> amount only.
        peer = db.get(Transaction, tx.transfer_peer_id)
        if peer is not None:
            imported = tx.source_document_id is not None or peer.source_document_id is not None
            peer_key = _hash_key(peer)
            if "amount" in fields or {"quantity", "price", "fees", "taxes"} & fields.keys():
                peer.amount = -tx.amount
            if "booking_date" in fields and not imported:
                peer.booking_date = tx.booking_date
            if "description" in fields and not imported:
                peer.description = tx.description
            if peer.dedupe_hash is not None and _hash_key(peer) != peer_key:
                digest = _tx_hash(peer, None)
                peer.dedupe_hash = None if _find_duplicate(db, digest, peer.id) else digest
    if tx.dedupe_hash is not None and key_changed:
        # (unchanged key fields keep their hash – it may carry an occurrence suffix from the import)
        digest = _tx_hash(tx, tx.instrument if tx.instrument_id else None)
        if digest != tx.dedupe_hash:
            existing = _find_duplicate(db, digest, tx.id)
            if existing is not None:
                raise DuplicateTransactionError(existing)
            tx.dedupe_hash = digest
    db.flush()
    return tx


def delete_transaction(db: Session, tx: Transaction) -> list[int]:
    """Delete a transaction. Returns deleted ids.

    A manually created transfer (POST /transactions/transfer) is deleted as a pair. If either side was
    imported from a document, the two rows are independent statement lines: only `tx` is deleted and
    the peer is unlinked (it keeps its transfer type).
    """
    ids = [tx.id]
    peer = db.get(Transaction, tx.transfer_peer_id) if tx.transfer_peer_id else None
    if peer is not None and (tx.source_document_id is not None or peer.source_document_id is not None):
        peer.transfer_peer_id = None
        peer = None
    if peer is not None:
        ids.append(peer.id)
    # break references (self reference + ingestion drafts) before deleting
    db.execute(
        Transaction.__table__.update().where(Transaction.transfer_peer_id.in_(ids)).values(transfer_peer_id=None)
    )
    db.execute(
        text("UPDATE extraction_drafts SET transaction_id = NULL WHERE transaction_id = ANY(:ids)"), {"ids": ids}
    )
    db.execute(Transaction.__table__.delete().where(Transaction.id.in_(ids)))
    db.flush()
    for obj in (tx, peer):
        if obj is not None and obj in db:
            db.expunge(obj)
    return ids
