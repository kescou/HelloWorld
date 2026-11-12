"""Building blocks shared by the deterministic parsers."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.ingest.categorize import SECURITIES_RX, cash_tx_type, category_hint
from app.ingest.dto import Draft
from app.ingest.util import line_amounts, squash

PARSER_CONFIDENCE = 0.95

FEE_LABELS = (
    r"Provision|Fremdkostenzuschlag|Fremde\s+Spesen|Fremdkosten|Fremde\s+Auslagen|Courtage|Maklercourtage|"
    r"Handelsplatzgeb[üu]hr|Handelsplatzentgelt|Handelsentgelt|Ordergeb[üu]hr(?:en)?|Orderentgelt|Orderprovision|"
    r"Transaktionsentgelt|B[öo]rsenentgelt|B[öo]rsenplatzentgelt|Variable\s+B[öo]rsenspesen|Abwicklungsgeb[üu]hr|"
    r"Wechselprovision|Mindermengenzuschlag|Spesen|Geb[üu]hr(?:en)?|Entgelt(?:e)?|Kosten"
)
TAX_LABELS = (
    r"Kapitalertrags?steuer|Kapitalertragst\.|KapSt|Solidarit[äa]tszuschlag|SolZ|Kirchensteuer|KiSt|"
    r"(?:US-|Ausl[äa]ndische\s+)?Quellensteuer|QuSt|Einbehaltene\s+Steuer|Finanztransaktionss?teuer|Abgeltungs?steuer"
)
# informational lines that mention a tax/fee word but are not a cash deduction
NOT_A_COST = re.compile(
    r"Bemessungsgrundlage|Besteuerungsgrundlage|anrechenbar|angerechnet|Freistellungsauftrag|Verlustverrechnung|"
    r"Verlusttopf|Steuerpflichtig|vor Steuern|nach Steuern|Sparer-?Pauschbetrag|Summe Steuern|Ertrag vor|"
    r"zu versteuern|pflichtig|Kursgewinn|Ver[äa]u[ßs]erungsgewinn|Ver[äa]u[ßs]erungsverlust|Rechnungsbetrag|Hinweis|Aktiengewinn|Kosteninformation|kostenlos|kostenfrei",
    re.I,
)
REFUND = re.compile(r"erstattung|Optimierung|erstattet|R[üu]ckerstattung|Gutschrift", re.I)


def sum_costs(text: str, labels: str, *, sign_aware: bool = False,
              fx_to_eur=None) -> tuple[Decimal, list[str]]:
    """Sum the cost lines (fees or taxes) of a settlement note. Returns a POSITIVE total for
    deductions (refunds reduce it) and the matched lines.

    sign_aware=True: trust the printed sign (Trade Republic prints '-1,00 EUR' for costs and a
    positive number for refunds). Otherwise every match counts as a deduction unless the line
    says 'Erstattung/Optimierung'.
    """
    rx = re.compile(rf"^\s*(?:\*\s*)?(?:abz(?:ü|ue)gl(?:ich|\.)\s+|zzgl\.\s+)?(?:{labels})\b", re.I)
    total = Decimal("0")
    used: list[str] = []
    for line in text.split("\n"):
        if not rx.search(line) or NOT_A_COST.search(line):
            continue
        amounts = line_amounts(line)
        if not amounts:
            continue
        val, cur = amounts[-1]
        if fx_to_eur and cur and cur != "EUR":
            val = fx_to_eur(val, cur)
        if sign_aware:
            cost = -val
        else:
            cost = -abs(val) if REFUND.search(line) else abs(val)
        total += cost
        used.append(line.strip())
    return total, used


def tx_payload(tx_type: str, booking_date: date | None, amount: Decimal | None, **kw) -> dict:
    payload = {
        "account_id": None, "type": tx_type, "booking_date": booking_date, "value_date": None,
        "isin": None, "wkn": None, "name": None, "symbol": None, "quantity": None, "price": None,
        "amount": amount, "fees": Decimal("0"), "taxes": Decimal("0"), "currency": "EUR",
        "fx_rate": None, "counterparty": None, "description": None, "category_hint": None,
    }
    payload.update(kw)
    return payload


def tx_draft(tx_type: str, booking_date: date | None, amount: Decimal | None, *, confidence: float = PARSER_CONFIDENCE,
             role: str | None = None, warnings: list[str] | None = None, review_required: bool = False, **kw) -> Draft:
    return Draft(kind="transaction", payload=tx_payload(tx_type, booking_date, amount, **kw),
                 confidence=confidence, role=role, warnings=list(warnings or []), review_required=review_required)


def balance_draft(on: date | None, balance: Decimal | None, *, confidence: float = PARSER_CONFIDENCE,
                  role: str | None = "cash") -> Draft:
    return Draft(kind="balance", payload={"account_id": None, "date": on, "balance": balance},
                 confidence=confidence, role=role)


def cash_line_draft(booking_date: date | None, amount: Decimal | None, *, booking_text: str | None = None,
                    counterparty: str | None = None, purpose: str | None = None, value_date: date | None = None,
                    confidence: float = PARSER_CONFIDENCE, currency: str = "EUR") -> Draft:
    """One giro/savings statement line -> typed draft with category hint."""
    counterparty = squash(counterparty)[:200] or None
    purpose = squash(purpose) or None
    booking_text = squash(booking_text) or None
    full = " ".join(x for x in (booking_text, counterparty, purpose) if x)
    tx_type = cash_tx_type(full, amount, booking_text)
    description = " | ".join(x for x in (booking_text, purpose) if x) or None
    hint = {"interest": "Zinsen", "fee": "Bankgebühren", "tax": "Steuern"}.get(tx_type) or category_hint(counterparty, purpose, booking_text)
    d = tx_draft(tx_type, booking_date, amount, value_date=value_date, counterparty=counterparty,
                 description=description, category_hint=hint, confidence=confidence, role="cash", currency=currency)
    if SECURITIES_RX.search(full):
        d.review_required = True
        d.payload["category_hint"] = "Wertpapiere"
        d.warnings.append("Wertpapierbuchung auf dem Verrechnungskonto – die Cash-Wirkung steckt bereits in der "
                          "Wertpapierabrechnung/Ertragsgutschrift. Nur übernehmen, wenn diese nicht importiert wird.")
    return d


def clean_security_name(name: str | None) -> str | None:
    name = squash(name)
    name = re.sub(r"\s*(?:ISIN|WKN)\b.*$", "", name)
    name = name.strip(" ,;:-")
    return name[:200] or None


def section(text: str, start: str, end: str | None = None, flags: int = re.I | re.M) -> str:
    """Text between regex ``start`` and regex ``end`` ('' if start is missing)."""
    m = re.search(start, text, flags)
    if not m:
        return ""
    rest = text[m.end():]
    if end:
        e = re.search(end, rest, flags)
        if e:
            rest = rest[:e.start()]
    return rest
