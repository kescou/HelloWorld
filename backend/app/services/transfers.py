"""Own-account transfer matching.

Statement imports produce two independent lines for one transfer between own accounts (debit on the
giro, credit on the savings account). `link_transfers` finds such pairs, types them
transfer_out / transfer_in and links them via `transfer_peer_id`, so cashflow analytics do not count
them as income/expense.

Evidence for a pair (opposite amount, different own accounts, booking dates at most WINDOW_DAYS apart):

    4  IBAN of the other account appears in counterparty/description/notes (or the import supplied
       the other account explicitly)
    3  both lines are already typed transfer_out / transfer_in
    2  one line is already typed as transfer, or a line mentions the other account's name or
       institution ("Trade Republic", "Scalable", ...)
    1  amount + date only – accepted only when the match is unambiguous for both lines and neither
       line carries an income/expense category

Lines that name another own account by IBAN but have no counterpart yet (the other statement is not
imported) are re-typed to transfer_in/out and stay unlinked until the counterpart arrives.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import Account, Category, CategoryKind, Setting, Transaction, TxType

WINDOW_DAYS = 3
EXCLUSION_KEY = "transfer_link_exclusions"
_CANDIDATE_TYPES = (TxType.deposit, TxType.withdrawal, TxType.transfer_in, TxType.transfer_out)
_TRANSFER = (TxType.transfer_in, TxType.transfer_out)

#: how banks show up in the booking text of the OTHER bank
INSTITUTION_ALIASES: dict[str, tuple[str, ...]] = {
    "postbank": ("postbank",),
    "ing": ("ing-diba", "ing diba", "ing bank", "ing"),
    "scalable": ("scalable capital", "scalable", "baader bank", "baader"),
    "trade_republic": ("trade republic", "traderepublic"),
    "dkb": ("dkb", "deutsche kreditbank"),
    "comdirect": ("comdirect",),
    "consorsbank": ("consorsbank", "consors"),
    "n26": ("n26",),
    "sparkasse": ("sparkasse",),
}


def _norm_iban(value: str | None) -> str:
    return re.sub(r"\s", "", value or "").upper()


def _text(tx: Transaction) -> str:
    return " ".join(t for t in (tx.counterparty, tx.description, tx.notes) if t)


def _word(needle: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)", re.IGNORECASE)


def _name_patterns(account: Account) -> tuple[list[re.Pattern], list[re.Pattern]]:
    """(patterns for the account name, patterns for the institution)."""
    names = [_word(account.name)] if account.name and len(account.name) >= 5 else []
    slug = (account.institution or "").lower()
    aliases = INSTITUTION_ALIASES.get(slug) or ((slug.replace("_", " "),) if len(slug) >= 4 and slug != "other" else ())
    return names, [_word(a) for a in aliases]


def get_exclusions(db: Session) -> set[int]:
    row = db.get(Setting, EXCLUSION_KEY)
    return {int(x) for x in (row.value or [])} if row is not None and isinstance(row.value, list) else set()


def _add_exclusions(db: Session, ids: Iterable[int]) -> None:
    row = db.get(Setting, EXCLUSION_KEY)
    current = get_exclusions(db) | set(ids)
    if row is None:
        db.add(Setting(key=EXCLUSION_KEY, value=sorted(current)))
    else:
        row.value = sorted(current)


def unlink(db: Session, tx: Transaction) -> None:
    """Dissolve a transfer link. Both rows are remembered so the automatic matcher leaves them alone."""
    peer = db.get(Transaction, tx.transfer_peer_id) if tx.transfer_peer_id else None
    ids = [tx.id]
    tx.transfer_peer_id = None
    if peer is not None:
        peer.transfer_peer_id = None
        ids.append(peer.id)
    _add_exclusions(db, ids)
    db.flush()


def link_transfers(
    db: Session, *, only_ids: Iterable[int] | None = None, account_hints: dict[int, int] | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict:
    """Find and link own-account transfer pairs (flushes, does NOT commit).

    only_ids       – restrict to pairs/lines involving at least one of these transaction ids
    account_hints  – {transaction_id: other_own_account_id} known from the import (counterparty IBAN)
    Returns {"linked": pairs, "retyped": one-sided lines, "pairs": [...]}.
    """
    only = set(only_ids) if only_ids is not None else None
    hints = dict(account_hints or {})
    if only is not None and not only:
        return {"linked": 0, "retyped": 0, "pairs": []}
    db.flush()
    accounts = {a.id: a for a in db.scalars(select(Account))}
    if len(accounts) < 2:
        return {"linked": 0, "retyped": 0, "pairs": []}
    ibans = {acc_id: _norm_iban(a.iban) for acc_id, a in accounts.items() if len(_norm_iban(a.iban)) >= 15}
    patterns = {acc_id: _name_patterns(a) for acc_id, a in accounts.items()}
    categories = {c.id: c for c in db.scalars(select(Category))}
    transfer_cat = next((c for c in categories.values() if c.name.lower() == "umbuchung"), None)
    excluded = get_exclusions(db)

    rows = list(db.scalars(
        select(Transaction).where(
            Transaction.transfer_peer_id.is_(None), Transaction.instrument_id.is_(None),
            Transaction.cash_account_id.is_(None), Transaction.type.in_(_CANDIDATE_TYPES), Transaction.amount != 0,
        ).order_by(Transaction.booking_date, Transaction.id)
    ))
    rows = [t for t in rows if t.id not in excluded and t.account_id in accounts]
    squashed = {t.id: re.sub(r"\s", "", _text(t)).upper() for t in rows}

    def iban_target(tx: Transaction) -> int | None:
        """Other own account referenced by this line (import hint or IBAN in the text)."""
        if hints.get(tx.id) in accounts and hints[tx.id] != tx.account_id:
            return hints[tx.id]
        hay = squashed[tx.id]
        if not hay:
            return None
        return next((acc_id for acc_id, iban in ibans.items() if acc_id != tx.account_id and iban in hay), None)

    def mentions(tx: Transaction, other: Account) -> bool:
        text = _text(tx)
        if not text:
            return False
        names, institutions = patterns[other.id]
        if any(p.search(text) for p in names):
            return True
        mine = accounts[tx.account_id]
        return (mine.institution or "") != (other.institution or "") and any(p.search(text) for p in institutions)

    def neutral(tx: Transaction) -> bool:
        cat = categories.get(tx.category_id) if tx.category_id else None
        return cat is None or cat.kind in (CategoryKind.transfer, CategoryKind.investment)

    incoming: dict = defaultdict(list)
    for t in rows:
        if t.amount > 0 and t.type in (TxType.deposit, TxType.transfer_in):
            incoming[t.amount].append(t)
    window = timedelta(days=window_days)
    pairs: list[tuple[int, int, Transaction, Transaction, str]] = []
    n_candidates: dict[int, int] = defaultdict(int)
    for out in rows:
        if out.amount >= 0 or out.type not in (TxType.withdrawal, TxType.transfer_out):
            continue
        for inc in incoming.get(-out.amount, ()):
            if inc.account_id == out.account_id or abs(inc.booking_date - out.booking_date) > window:
                continue
            if only is not None and out.id not in only and inc.id not in only:
                continue
            out_target, inc_target = iban_target(out), iban_target(inc)
            if (out_target is not None and out_target != inc.account_id) or \
                    (inc_target is not None and inc_target != out.account_id):
                continue        # explicitly points at a different own account
            typed = (out.type == TxType.transfer_out) + (inc.type == TxType.transfer_in)
            if out_target is not None or inc_target is not None:
                score, reason = 4, "iban"
            elif typed == 2:
                score, reason = 3, "typed"
            elif typed == 1:
                score, reason = 2, "typed"
            elif mentions(out, accounts[inc.account_id]) or mentions(inc, accounts[out.account_id]):
                score, reason = 2, "name"
            elif neutral(out) and neutral(inc):
                score, reason = 1, "amount"
            else:
                continue
            n_candidates[out.id] += 1
            n_candidates[inc.id] += 1
            pairs.append((score, abs((inc.booking_date - out.booking_date).days), out, inc, reason))

    pairs.sort(key=lambda p: (-p[0], p[1], p[2].id, p[3].id))
    used: set[int] = set()
    linked: list[dict] = []
    for score, _gap, out, inc, reason in pairs:
        if out.id in used or inc.id in used:
            continue
        if score == 1 and (n_candidates[out.id] != 1 or n_candidates[inc.id] != 1):
            continue            # ambiguous amount-only match: leave it to the user
        used.update((out.id, inc.id))
        out.type, inc.type = TxType.transfer_out, TxType.transfer_in
        out.transfer_peer_id, inc.transfer_peer_id = inc.id, out.id
        if transfer_cat is not None:
            for tx in (out, inc):
                cat = categories.get(tx.category_id) if tx.category_id else None
                if cat is None or (reason != "amount" and cat.kind in (CategoryKind.income, CategoryKind.expense)):
                    tx.category_id = transfer_cat.id
        linked.append({"out_id": out.id, "in_id": inc.id, "amount": float(inc.amount),
                       "from_account_id": out.account_id, "to_account_id": inc.account_id,
                       "date": out.booking_date.isoformat(), "reason": reason})

    retyped = 0
    for tx in rows:
        if tx.id in used or tx.type in _TRANSFER or (only is not None and tx.id not in only):
            continue
        if iban_target(tx) is None:
            continue
        tx.type = TxType.transfer_in if tx.amount > 0 else TxType.transfer_out
        if transfer_cat is not None:
            tx.category_id = transfer_cat.id
        retyped += 1
    db.flush()
    return {"linked": len(linked), "retyped": retyped, "pairs": linked}
