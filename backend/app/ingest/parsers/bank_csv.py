"""Header-driven engine for bank CSV exports (ING, Postbank old+new, and look-alikes).

Finds the header row inside a preamble, maps columns by German header synonyms and emits one
cash draft per row + a balance draft when the file tells us a balance.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal

from app.ingest.dto import ParseResult
from app.ingest.parsers.common import balance_draft, cash_line_draft
from app.ingest.util import DATE_DE, find_ibans, parse_date, parse_decimal, squash

_SYN: dict[str, tuple[str, ...]] = {
    "date": ("buchung", "buchungstag", "buchungsdatum", "datum", "date", "buchungstermin"),
    "value_date": ("valuta", "wert", "wertstellung", "wertstellungsdatum", "wertstellungstag", "valutadatum"),
    "counterparty": ("auftraggeber/empfänger", "begünstigter / auftraggeber", "begünstigter/auftraggeber", "name",
                     "zahlungsempfänger", "begünstigter/zahlungspflichtiger", "beguenstigter/zahlungspflichtiger",
                     "zahlungspflichtige*r", "zahlungsempfänger*in", "empfänger/auftraggeber", "gegenkonto", "partner"),
    "sender": ("auftraggeber",),
    "recipient": ("empfänger",),
    "booking_text": ("buchungstext", "umsatzart", "typ", "vorgang", "art"),
    "purpose": ("verwendungszweck", "buchungsdetails", "beschreibung", "text", "details"),
    "amount": ("betrag", "betrag (eur)", "betrag (€)", "umsatz", "betrag in eur", "umsatz in eur", "betrag eur"),
    "debit": ("soll",),
    "credit": ("haben",),
    "balance": ("saldo", "saldo (€)", "saldo (eur)", "kontostand", "saldo nach buchung"),
    "currency": ("währung", "waehrung"),
    "iban": ("iban / kontonummer", "iban", "kontonummer/iban", "iban/kontonummer"),
}
_LOOKUP = {name: key for key, names in _SYN.items() for name in names}


def sniff_delimiter(text: str) -> str:
    sample = [ln for ln in text.split("\n")[:60] if ln.strip()]
    best = max((";", "\t", ","), key=lambda d: sum(ln.count(d) for ln in sample))
    return best


def _norm_header(h: str) -> str:
    return squash(h).lower().strip('"').replace("€", "€")


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows[:60]):
        cols: dict[str, int] = {}
        cells = [_norm_header(c) for c in row]
        for j, c in enumerate(cells):
            key = _LOOKUP.get(c)
            if key == "currency":
                # ING has two "Währung" columns: after Saldo and after Betrag – keep the latter
                if "amount" in cols and j == cols["amount"] + 1:
                    cols["currency"] = j
                elif "currency" not in cols:
                    cols["currency"] = j
            elif key and key not in cols:
                cols[key] = j
        if "date" in cols and ("amount" in cols or ("debit" in cols and "credit" in cols)) and len(cols) >= 3:
            return i, cols
    return None


def parse_bank_csv(text: str, *, parser: str, confidence: float = 0.95) -> ParseResult | None:
    delim = sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    found = find_header(rows)
    if not found:
        return None
    hi, cols = found
    res = ParseResult(parser=parser, account_role="cash")

    # ---- preamble facts
    pre = "\n".join(delim.join(r) for r in rows[:hi])
    ibans = find_ibans(pre)
    res.iban = ibans[0] if ibans else None
    period_end = None
    pm = re.search(r"(" + DATE_DE + r")\s*-\s*(" + DATE_DE + ")", pre)
    if pm:
        period_end = parse_date(pm.group(2))
    pre_balance = None
    for r in rows[:hi]:
        label = (r[0] if r else "").strip().lower()
        if label in ("saldo", "letzter kontostand", "kontostand", "aktueller kontostand", "neuer kontostand"):
            nums = [parse_decimal(c, style="de") for c in r[1:] if re.search(r"\d,\d{2}", c)]
            if nums:
                pre_balance = nums[0]

    def cell(row: list[str], key: str) -> str:
        j = cols.get(key)
        return row[j].strip() if j is not None and j < len(row) else ""

    seq: list[tuple[date, Decimal | None]] = []
    footer_balance: tuple[date | None, Decimal] | None = None
    for row in rows[hi + 1:]:
        if not any(c.strip() for c in row):
            continue
        first = row[0].strip()
        if first.lower() in ("kontostand", "saldo", "endsaldo"):
            dt = next((parse_date(c) for c in row[1:] if parse_date(c)), None)
            nums = [parse_decimal(c, style="de") for c in row[1:] if re.fullmatch(r"\s*-?[\d.]+,\d{2}\s*€?\s*", c)]
            if nums:
                footer_balance = (dt, nums[0])
            continue
        booking = parse_date(cell(row, "date"))
        if booking is None:
            continue
        amount = parse_decimal(cell(row, "amount"), style="de") if "amount" in cols else None
        if amount is None:
            debit = parse_decimal(cell(row, "debit"), style="de")
            credit = parse_decimal(cell(row, "credit"), style="de")
            if credit:
                amount = abs(credit)
            elif debit:
                amount = -abs(debit)
        if amount is None:
            continue
        cp = cell(row, "counterparty")
        if not cp:
            cp = cell(row, "recipient") if amount < 0 else cell(row, "sender")
            cp = cp or cell(row, "sender") or cell(row, "recipient")
        d = cash_line_draft(booking, amount, booking_text=cell(row, "booking_text"), counterparty=cp,
                            purpose=cell(row, "purpose"), value_date=parse_date(cell(row, "value_date")),
                            confidence=confidence, currency=(cell(row, "currency") or "EUR").replace("€", "EUR")[:3].upper() or "EUR")
        iban = cell(row, "iban")
        if iban:
            d.payload["counterparty_iban"] = re.sub(r"\s+", "", iban)[:34]
        d.payload["_doc_type"] = "csv_export"
        res.drafts.append(d)
        seq.append((booking, parse_decimal(cell(row, "balance"), style="de") if "balance" in cols else None))

    if not res.drafts:
        return None
    # ---- balance: explicit footer > per-row saldo of the newest row > preamble balance
    newest = max(d for d, _ in seq)
    if footer_balance:
        res.drafts.append(balance_draft(footer_balance[0] or period_end or newest, footer_balance[1]))
    elif any(b is not None for _, b in seq):
        descending = seq[0][0] >= seq[-1][0]
        candidates = [b for d, b in seq if d == newest and b is not None]
        if candidates:
            res.drafts.append(balance_draft(newest, candidates[0] if descending else candidates[-1]))
    elif pre_balance is not None:
        res.drafts.append(balance_draft(period_end or newest, pre_balance, confidence=0.8))
    return res
