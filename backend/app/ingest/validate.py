"""Tier 6 – validation & normalisation of drafts (parser AND LLM output), account assignment
and duplicate detection. This is where extraction quality is enforced."""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.categorize import category_hint
from app.ingest.config import get_ingest_settings
from app.ingest.dto import SECURITY_TYPES, TX_TYPES, Draft
from app.ingest.util import isin_valid, norm_currency, norm_iban, parse_date, parse_decimal, squash
from app.models import Account, AccountType, BalanceSnapshot, Instrument, Transaction

CASH_ROLE_TYPE = {
    "trade_republic": [AccountType.savings, AccountType.checking],
    "scalable": [AccountType.savings, AccountType.checking],
    "ing": [AccountType.checking, AccountType.savings],
    "postbank": [AccountType.checking, AccountType.savings],
}
_TYPE_ALIASES = {
    "kauf": "buy", "verkauf": "sell", "dividende": "dividend", "ausschüttung": "dividend", "ausschuettung": "dividend",
    "zinsen": "interest", "zins": "interest", "einzahlung": "deposit", "gutschrift": "deposit", "auszahlung": "withdrawal",
    "lastschrift": "withdrawal", "überweisung": "withdrawal", "gebühr": "fee", "gebuehr": "fee", "steuer": "tax",
    "steuern": "tax", "sparplan": "buy", "savings_plan": "buy", "purchase": "buy", "sale": "sell", "distribution": "dividend",
    "transfer": "withdrawal", "payment": "withdrawal", "income": "deposit", "expense": "withdrawal",
}
D0 = Decimal("0")
CENT = Decimal("0.01")


def _f(d: Decimal | None, places: int = 2) -> float | None:
    if d is None:
        return None
    return float(round(d, places))


def _tol(amount: Decimal, rel: Decimal = Decimal("0.001")) -> Decimal:
    return max(Decimal("0.02"), abs(amount) * rel)


def normalize_draft(d: Draft, *, source: str = "parser", today: date | None = None) -> Draft:
    """Coerce types, enforce sign conventions, run sanity/arithmetic checks.

    Returns the same Draft with a JSON-serialisable payload; problems land in
    ``payload['warnings']`` and lower ``confidence``; serious ones set ``review_required``.
    """
    today = today or date.today()
    p = d.payload
    warnings = list(d.warnings)
    conf = d.confidence
    review = d.review_required

    if d.kind == "balance":
        on = parse_date(p.get("date"))
        bal = parse_decimal(p.get("balance"))
        if on is None or bal is None:
            warnings.append("Kontostand unvollständig (Datum oder Betrag fehlt).")
            review, conf = True, min(conf, 0.3)
        elif on > today + timedelta(days=3):
            warnings.append("Datum des Kontostands liegt in der Zukunft.")
            review, conf = True, min(conf, 0.5)
        d.payload = {"account_id": p.get("account_id"), "date": on.isoformat() if on else None, "balance": _f(bal),
                     "warnings": warnings, "review_required": review}
        d.warnings, d.confidence, d.review_required = warnings, round(max(0.05, conf), 3), review
        return d

    # ---- type
    tx_type = squash(str(p.get("type") or "")).lower().replace(" ", "_")
    tx_type = _TYPE_ALIASES.get(tx_type, tx_type)
    amount = parse_decimal(p.get("amount"))
    if tx_type not in TX_TYPES:
        guessed = "deposit" if (amount or D0) >= 0 else "withdrawal"
        warnings.append(f"Unbekannte Buchungsart „{p.get('type')}“ – als {guessed} übernommen.")
        tx_type, review, conf = guessed, True, conf - 0.2

    # ---- dates
    booking = parse_date(p.get("booking_date"))
    value = parse_date(p.get("value_date"))
    if booking is None and value is not None:
        booking = value
        warnings.append("Buchungsdatum fehlt – Valuta übernommen.")
    if booking is None:
        warnings.append("Buchungsdatum fehlt.")
        review, conf = True, min(conf, 0.3)
    else:
        if booking > today + timedelta(days=7):
            warnings.append(f"Buchungsdatum {booking:%d.%m.%Y} liegt in der Zukunft.")
            review, conf = True, conf - 0.3
        elif booking.year < 1990:
            warnings.append(f"Buchungsdatum {booking:%d.%m.%Y} ist unplausibel.")
            review, conf = True, conf - 0.3
        if value and abs((value - booking).days) > 45:
            warnings.append("Valuta weicht stark vom Buchungsdatum ab.")
            conf -= 0.05

    # ---- numbers
    qty = parse_decimal(p.get("quantity"))
    price = parse_decimal(p.get("price"))
    fees = parse_decimal(p.get("fees")) or D0
    taxes = parse_decimal(p.get("taxes")) or D0
    fx = parse_decimal(p.get("fx_rate"))
    gross = parse_decimal(p.get("gross_amount"))
    if qty is not None:
        qty = abs(qty)
        if qty == 0:
            qty = None
    if price is not None and price < 0:
        price = abs(price)
    fees = abs(fees)
    if fx is not None and fx <= 0:
        fx = None

    # ---- ISIN / WKN
    isin = re.sub(r"[\s.:-]", "", str(p.get("isin") or "")).upper() or None
    if isin and not isin_valid(isin):
        warnings.append(f"ISIN {isin} hat eine falsche Prüfziffer – bitte korrigieren.")
        review, conf = True, conf - 0.3
    wkn = re.sub(r"\s", "", str(p.get("wkn") or "")).upper() or None
    if wkn and not re.fullmatch(r"[A-Z0-9]{6}", wkn):
        wkn = None
    name = squash(p.get("name"))[:200] or None

    # ---- amount: derive when missing, then sign conventions
    if amount is None and qty and price is not None and tx_type in ("buy", "sell", "dividend"):
        g = qty * price
        amount = -(g + fees + taxes) if tx_type == "buy" else g - fees - taxes
        warnings.append("Betrag fehlte – aus Stück × Kurs ± Gebühren/Steuern berechnet.")
        conf -= 0.1
    if amount is None and tx_type in ("split", "delivery_in", "delivery_out"):
        amount = D0
    if amount is None:
        warnings.append("Betrag fehlt.")
        review, conf = True, min(conf, 0.3)
    else:
        flipped = False
        if tx_type in ("buy", "withdrawal", "fee", "transfer_out") and amount > 0:
            amount, flipped = -amount, True
        elif tx_type in ("sell", "deposit", "transfer_in") and amount < 0:
            amount, flipped = -amount, True
        elif tx_type in ("dividend", "interest") and amount < 0 and source == "llm":
            amount, flipped = -amount, True
        if flipped and source == "parser":
            warnings.append("Vorzeichen des Betrags an die Buchungsart angepasst.")
            conf -= 0.05

    # ---- arithmetic self-check
    verified = False
    if amount is not None and tx_type in ("buy", "sell"):
        if not qty:
            warnings.append("Stückzahl fehlt.")
            review, conf = True, conf - 0.3
        if price is None and qty:
            base = abs(amount) - fees - taxes if tx_type == "buy" else abs(amount) + fees + taxes
            price = (base / qty).quantize(Decimal("0.000001"))
            warnings.append("Kurs fehlte – aus Betrag ÷ Stück berechnet.")
            conf -= 0.05
        elif qty and price is not None and not p.get("_price_derived"):
            g = qty * price
            expected = -(g + fees + taxes) if tx_type == "buy" else g - fees - taxes
            if abs(expected - amount) <= _tol(amount):
                verified = True
            else:
                warnings.append(
                    f"Rechenprobe: {qty.normalize():f} × {price.normalize():f} "
                    f"{'+' if tx_type == 'buy' else '−'} Gebühren {fees} {'+' if tx_type == 'buy' else '−'} Steuern {taxes} "
                    f"= {abs(expected):.2f}, im Dokument steht {abs(amount):.2f}.")
                review, conf = True, conf - 0.3
    elif amount is not None and tx_type in ("dividend", "interest"):
        g = gross if gross is not None else (qty * price if qty and price is not None and tx_type == "dividend" else None)
        if g is not None:
            expected = g - taxes - fees
            if abs(expected - amount) <= _tol(amount, Decimal("0.01")):
                verified = True
            else:
                warnings.append(f"Rechenprobe: Brutto {g:.2f} − Steuern {taxes} − Gebühren {fees} = {expected:.2f}, "
                                f"gutgeschrieben wurden {amount:.2f}.")
                review, conf = True, conf - 0.25
    if tx_type in ("buy", "sell", "dividend") and not isin and not name:
        warnings.append("Wertpapier nicht erkannt (ISIN und Name fehlen).")
        review, conf = True, conf - 0.3
    elif tx_type in ("buy", "sell", "dividend") and not isin:
        warnings.append("ISIN fehlt – Wertpapier wird nur über den Namen zugeordnet.")
        review, conf = True, conf - 0.1
    if source == "llm" and verified:
        conf += 0.15

    counterparty = squash(p.get("counterparty"))[:200] or None
    description = squash(p.get("description"))[:1000] or None
    hint = squash(p.get("category_hint")) or None
    if not hint and tx_type in ("deposit", "withdrawal", "fee", "interest", "tax", "transfer_in", "transfer_out"):
        hint = {"interest": "Zinsen", "fee": "Bankgebühren", "tax": "Steuern"}.get(tx_type) or category_hint(counterparty, description)

    out = {
        "account_id": p.get("account_id"),
        "type": tx_type,
        "booking_date": booking.isoformat() if booking else None,
        "value_date": value.isoformat() if value else None,
        "isin": isin, "wkn": wkn, "name": name, "symbol": squash(p.get("symbol")) or None,
        "quantity": _f(qty, 8), "price": _f(price, 8), "amount": _f(amount),
        "fees": _f(fees) or 0.0, "taxes": _f(taxes) or 0.0,
        "currency": norm_currency(p.get("currency")), "fx_rate": _f(fx, 8),
        "counterparty": counterparty, "description": description, "category_hint": hint,
    }
    for extra in ("gross_amount", "reference", "counterparty_iban"):
        if p.get(extra) not in (None, ""):
            v = p[extra]
            out[extra] = _f(parse_decimal(v)) if extra == "gross_amount" else str(v)
    out["verified"] = verified
    out["warnings"] = warnings
    out["review_required"] = review
    d.payload = out
    d.warnings, d.review_required = warnings, review
    d.confidence = round(min(0.99, max(0.05, conf)), 3)
    return d


def normalize_all(drafts: list[Draft], *, source: str = "parser") -> list[Draft]:
    return [normalize_draft(d, source=source) for d in drafts]


def quality_ok(drafts: list[Draft]) -> bool:
    """Self-validation verdict used to decide whether the LLM should have a go."""
    tx = [d for d in drafts if d.kind == "transaction"]
    if not tx:
        return False
    for d in tx:
        p = d.payload
        if not p.get("booking_date") or p.get("amount") is None:
            return False
        if p.get("type") in ("buy", "sell", "dividend") and d.review_required and not p.get("verified") \
                and any(w.startswith(("Rechenprobe", "Stückzahl fehlt", "Wertpapier nicht erkannt", "ISIN ")) for w in d.warnings):
            return False
    return True


# ----------------------------------------------------------------------------- accounts

def _role(d: Draft) -> str:
    if d.role:
        return d.role
    p = d.payload
    if d.kind == "transaction" and p.get("type") in SECURITY_TYPES and (p.get("isin") or p.get("name")):
        return "brokerage"
    return "cash"


def _pick(accounts: list[Account], institution: str | None, role: str) -> Account | None:
    if not institution:
        return None
    mine = [a for a in accounts if a.institution == institution]
    if role == "brokerage":
        return next((a for a in mine if a.type == AccountType.brokerage), None)
    depot = next((a for a in mine if a.type == AccountType.brokerage), None)
    for t in CASH_ROLE_TYPE.get(institution, [AccountType.checking, AccountType.savings]):
        hit = [a for a in mine if a.type == t]
        if depot and depot.settlement_account_id:
            pref = [a for a in hit if a.id == depot.settlement_account_id]
            if pref and institution in ("trade_republic", "scalable"):
                return pref[0]
        if hit:
            return hit[0]
    if depot and depot.settlement_account_id:
        return next((a for a in accounts if a.id == depot.settlement_account_id), None)
    return next((a for a in mine if a.type != AccountType.brokerage), None)


def _via_anchor(accounts: list[Account], anchor: Account, role: str) -> Account:
    if role == "brokerage":
        if anchor.type == AccountType.brokerage:
            return anchor
        depot = next((a for a in accounts if a.type == AccountType.brokerage and a.settlement_account_id == anchor.id), None)
        return depot or next((a for a in accounts if a.type == AccountType.brokerage and a.institution == anchor.institution), None) or anchor
    if anchor.type == AccountType.brokerage:
        return next((a for a in accounts if a.id == anchor.settlement_account_id), None) or anchor
    return anchor


def assign_accounts(db: Session, drafts: list[Draft], *, hint_account_id: int | None = None, institution: str | None = None,
                    iban: str | None = None, account_number: str | None = None, settlement_iban: str | None = None) -> int | None:
    """Fill payload.account_id. Priority: user hint > IBAN/depot number printed on the document >
    detected institution + role. Returns the document-level account id (first cash/any match)."""
    accounts = list(db.scalars(select(Account).where(Account.is_active.is_(True)).order_by(Account.sort_order, Account.id)))
    by_id = {a.id: a for a in accounts}
    by_iban = {norm_iban(a.iban): a for a in accounts if norm_iban(a.iban)}
    anchor = by_id.get(hint_account_id) if hint_account_id else None
    hinted = anchor is not None
    if anchor is None:
        for cand in (iban, settlement_iban):
            if cand and norm_iban(cand) in by_iban:
                anchor = by_iban[norm_iban(cand)]
                break
    if anchor is None and account_number:
        num = re.sub(r"\D", "", account_number)
        anchor = next((a for a in accounts if a.account_number and re.sub(r"\D", "", a.account_number) == num and num), None)
    own_ibans = {i: a for i, a in by_iban.items()}

    doc_account: int | None = anchor.id if anchor else None
    for d in drafts:
        role = _role(d)
        acc = _via_anchor(accounts, anchor, role) if anchor else _pick(accounts, institution, role)
        if d.payload.get("account_id") and d.payload["account_id"] in by_id and not hinted:
            acc = by_id[d.payload["account_id"]]
        d.payload["account_id"] = acc.id if acc else None
        if acc is None:
            msg = "Konto nicht erkannt – bitte Konto auswählen."
            if msg not in d.payload["warnings"]:
                d.payload["warnings"].append(msg)
            d.payload["review_required"] = d.review_required = True
            d.confidence = round(max(0.05, d.confidence - 0.1), 3)
            continue
        if doc_account is None:
            doc_account = acc.id
        # own-account transfers: counterparty IBAN (or an IBAN in the text) belongs to another own account
        p = d.payload
        if d.kind == "transaction" and p.get("type") in ("deposit", "withdrawal") and own_ibans:
            hay = re.sub(r"\s", "", " ".join(str(p.get(k) or "") for k in ("counterparty_iban", "description", "counterparty"))).upper()
            other = next((a for i, a in own_ibans.items() if i in hay and a.id != acc.id), None)
            if other:
                p["type"] = "transfer_in" if p["type"] == "deposit" else "transfer_out"
                p["category_hint"] = "Umbuchung"
                p["transfer_account_id"] = other.id
    return doc_account


# ----------------------------------------------------------------------------- duplicates

def dedupe_key(p: dict) -> tuple:
    return (p.get("account_id"), p.get("type"), p.get("booking_date"), p.get("isin"),
            p.get("quantity"), p.get("amount"))


def mark_duplicates(db: Session, drafts: list[Draft], *, base_index: int = 0) -> None:
    """Flag drafts that already exist as transactions (same account / amount / ISIN, date within a
    small window for securities, exact date for cash lines) or as balance snapshots.

    Identical lines inside ONE document (two 2,50 € coffees on the same day) get an occurrence
    index (payload.dup_index) so only as many drafts are flagged as transactions exist.
    """
    window = get_ingest_settings().duplicate_window_days
    seen: dict[tuple, int] = {}
    for d in drafts:
        p = d.payload
        if d.kind == "balance":
            on, acc = parse_date(p.get("date")), p.get("account_id")
            if on and acc:
                snap = db.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == acc, BalanceSnapshot.date == on))
                if snap is not None and p.get("balance") is not None and abs(snap.balance - Decimal(str(p["balance"]))) < CENT:
                    d.status = "duplicate"
                elif snap is not None:
                    p["warnings"].append(f"Für dieses Datum ist bereits ein Kontostand von {snap.balance} gespeichert – "
                                         "Übernehmen überschreibt ihn.")
            continue
        key = dedupe_key(p)
        n = seen.get(key, base_index)
        seen[key] = n + 1
        p["dup_index"] = n
        acc, on, amount = p.get("account_id"), parse_date(p.get("booking_date")), p.get("amount")
        if not acc or not on or amount is None:
            continue
        amt = Decimal(str(amount)).quantize(CENT)
        isin = p.get("isin")
        days = window if (isin or p.get("type") in SECURITY_TYPES) else 0
        q = select(Transaction).where(
            Transaction.account_id == acc, Transaction.amount == amt,
            Transaction.booking_date >= on - timedelta(days=days), Transaction.booking_date <= on + timedelta(days=days))
        if isin:
            q = q.join(Instrument, Transaction.instrument_id == Instrument.id).where(Instrument.isin == isin)
        elif days == 0:
            q = q.where(Transaction.instrument_id.is_(None))
        existing = list(db.scalars(q.order_by(Transaction.id)))
        if p.get("type") in ("buy", "sell") and isin:
            existing = [t for t in existing if t.type.value == p["type"]]
        if len(existing) > n:
            d.status = "duplicate"
            p["duplicate_of"] = existing[n].id
            continue
        if not isin:
            # cash side of a securities transaction that was booked from the depot already
            peer = db.scalars(select(Transaction).where(
                Transaction.cash_account_id == acc, Transaction.account_id != acc, Transaction.amount == amt,
                Transaction.booking_date >= on - timedelta(days=window),
                Transaction.booking_date <= on + timedelta(days=window))).first()
            if peer is not None:
                d.status = "duplicate"
                p["duplicate_of"] = peer.id
                p["warnings"].append("Cash-Seite einer bereits erfassten Wertpapierbuchung (über das Depot gebucht).")
        else:
            cash_acc = db.get(Account, acc)
            settle = cash_acc.settlement_account_id if cash_acc else None
            if settle:
                peer = db.scalars(select(Transaction).where(
                    Transaction.account_id == settle, Transaction.cash_account_id.is_(None), Transaction.amount == amt,
                    Transaction.instrument_id.is_(None),
                    Transaction.booking_date >= on - timedelta(days=window),
                    Transaction.booking_date <= on + timedelta(days=window))).first()
                if peer is not None:
                    p["warnings"].append(
                        f"Auf dem Verrechnungskonto ist bereits eine Buchung über {amt} € erfasst (Tx #{peer.id}) – "
                        "nach dem Übernehmen wäre die Cash-Wirkung doppelt; bitte die Kontobuchung löschen.")
                    p["review_required"] = d.review_required = True
