"""Scalable Capital: Baader Bank documents (until 2025) and Scalable Capital Bank documents,
plus the transactions CSV export of the Scalable web app.

Baader layout (text layer):
    Wertpapierabrechnung: Kauf
    Handelsdatum: 03.01.2022 ...
    Nominale ISIN: IE00B4L5Y983 WKN: A0RPWH Kurs
    STK 10 iShsIII-Core MSCI World U.ETF EUR 78,50
    Kurswert EUR 785,00 / Provision EUR 0,99
    Zu Lasten Konto 1234567890 Valuta: 05.01.2022 EUR 785,99
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from app.ingest.dto import DocContext, ParseResult
from app.ingest.parsers import generic_security as gs
from app.ingest.parsers.common import FEE_LABELS, TAX_LABELS, cash_line_draft, clean_security_name, sum_costs, tx_draft
from app.ingest.util import DATE_DE, NUM_DE, find_ibans, find_isin, isin_valid, label_amount, parse_date, parse_decimal, q2, squash

NAME = "scalable"

_STK_ROW = re.compile(
    rf"^\s*STK\s+(?P<qty>{NUM_DE})\s+(?P<name>.+?)\s+(?P<cur>[A-Z]{{3}})\s+(?P<price>\d[\d.]*,\d+)(?:\s+p\.\s?STK)?\s*$", re.M)
_SETTLE = re.compile(
    r"Zu\s+(?P<dir>Lasten|Gunsten)\s+Konto\s+(?P<acct>\d+)\s+Valuta:?\s+(?P<date>" + DATE_DE + r")\s+EUR\s+(?P<amt>\d[\d.]*,\d{2})", re.I)


def matches(ctx: DocContext) -> bool:
    if ctx.source_kind == "csv":
        return _is_scalable_csv(ctx.text)
    return ctx.institution == "scalable" or bool(re.search(r"Baader\s*Bank|Scalable\s*Capital", ctx.text[:4000], re.I))


def parse(ctx: DocContext) -> ParseResult | None:
    if ctx.source_kind == "csv" or _is_scalable_csv(ctx.text):
        return parse_csv(ctx.text)
    text = ctx.text
    if _STK_ROW.search(text) or _SETTLE.search(text):
        res = _baader(text)
        if res:
            return res
    kind = gs.detect_kind(text)
    if kind is None and re.search(r"\bVerkauf\b", text[:3000]):
        kind = "sell"
    elif kind is None and re.search(r"\bKauf\b|Sparplan", text[:3000]):
        kind = "buy"
    d = gs.parse_security(text, parser=NAME, confidence=0.88, counterparty="Scalable Capital", kind=kind)
    if d:
        ibans = find_ibans(text)
        return ParseResult(parser=f"{NAME}.bank", drafts=[d], account_role="brokerage",
                           iban=next((i for i in ibans if i.startswith("DE")), None))
    return None


def _baader(text: str) -> ParseResult | None:
    kind = gs.detect_kind(text)
    if kind is None:
        return None
    row = _STK_ROW.search(text)
    settle = _SETTLE.search(text)
    if not row and not settle:
        return None
    isin = find_isin(text)
    m = re.search(r"WKN:?\s*([A-Z0-9]{6})\b", text)
    wkn = m.group(1) if m else None
    name = qty = price = None
    pcur = "EUR"
    if row:
        qty = parse_decimal(row.group("qty"), style="de")
        price = parse_decimal(row.group("price"), style="de")
        pcur = row.group("cur")
        nxt = text[row.end():].lstrip("\n").split("\n", 1)[0]
        name = row.group("name")
        if nxt and not re.search(r"\d,\d|:", nxt) and len(nxt) < 60:
            name = f"{name} {nxt.strip()}"
        name = clean_security_name(name)
    fx_rate, _ = gs.find_fx(text)

    def to_eur(v, cur):
        return v / fx_rate if fx_rate and cur and cur != "EUR" else v

    fees, _ = sum_costs(text, FEE_LABELS, fx_to_eur=to_eur)
    taxes, _ = sum_costs(text, TAX_LABELS, fx_to_eur=to_eur)
    warnings: list[str] = []
    amount = valuta = None
    acct = None
    if settle:
        amount = parse_decimal(settle.group("amt"), style="de")
        valuta = parse_date(settle.group("date"))
        acct = settle.group("acct")
        if settle.group("dir").lower() == "lasten":
            amount = -amount
    res = ParseResult(parser=f"{NAME}.baader", account_role="brokerage", account_number=acct)

    if kind == "dividend":
        gross = label_amount(text, r"^\s*Bruttobetrag\s+EUR", flags=re.I | re.M) or label_amount(text, r"^\s*Bruttobetrag", flags=re.I | re.M)
        gross_eur = to_eur(*gross) if gross else None
        if amount is None:
            if gross_eur is None:
                return None
            amount = gross_eur - taxes - fees
            warnings.append("Kein Gutschriftsbetrag gefunden – aus Brutto abzüglich Steuern berechnet.")
        if gross_eur is None:
            gross_eur = abs(amount) + taxes + fees
        per_share = (gross_eur / qty).quantize(Decimal("0.000001")) if qty else None
        pay = gs.find_date(text, r"Zahltag|Zahlungstag") or valuta
        ex = gs.find_date(text, r"Ex-?Tag")
        d = tx_draft("dividend", pay, abs(amount), value_date=valuta, isin=isin, wkn=wkn, name=name, quantity=qty,
                     price=per_share, fees=q2(fees), taxes=q2(taxes), fx_rate=fx_rate,
                     description=squash(f"Ausschüttung {name or isin or ''}" + (f" (Ex-Tag {ex:%d.%m.%Y})" if ex else "")),
                     counterparty="Scalable Capital / Baader Bank", warnings=warnings, role="brokerage")
        d.payload["gross_amount"] = q2(gross_eur)
        d.payload["_doc_type"] = "dividend"
        res.drafts.append(d)
        return res

    gross = label_amount(text, r"^\s*Kurswert\b", flags=re.I | re.M)
    gross_eur = to_eur(*gross) if gross else None
    foreign = pcur != "EUR"
    if price is not None and foreign:
        price = (gross_eur / qty).quantize(Decimal("0.000001")) if (gross_eur is not None and qty and fx_rate) else (price / fx_rate if fx_rate else price)
        if not fx_rate:
            warnings.append(f"Kurs in {pcur} ohne Devisenkurs – bitte prüfen.")
    if amount is None:
        if gross_eur is None:
            return None
        amount = -(gross_eur + fees + taxes) if kind == "buy" else gross_eur - fees - taxes
        warnings.append("Keine Buchungszeile gefunden – Betrag aus Kurswert berechnet.")
    if (kind == "buy") != (amount < 0):
        warnings.append("Richtung der Buchung passt nicht zur Art des Geschäfts.")
        amount = -abs(amount) if kind == "buy" else abs(amount)
    trade_date = gs.find_date(text, r"Handelsdatum|Handelstag") or gs.find_date(text, r"Auftragsdatum") or valuta
    plan = bool(re.search(r"Sparplan", text, re.I))
    d = tx_draft(kind, trade_date, amount, value_date=valuta, isin=isin, wkn=wkn, name=name, quantity=qty, price=price,
                 fees=q2(fees), taxes=q2(taxes), fx_rate=fx_rate if foreign else None,
                 description=squash(("Verkauf " if kind == "sell" else "Kauf ") + ("Sparplan " if plan else "") + (name or isin or "")),
                 counterparty="Scalable Capital / Baader Bank", warnings=warnings, role="brokerage")
    d.payload["_doc_type"] = "savings_plan" if plan and kind == "buy" else f"trade_{kind}"
    res.drafts.append(d)
    return res


# ----------------------------------------------------------------------------- CSV export

_CSV_TYPES = {
    "buy": "buy", "kauf": "buy", "savings plan": "buy", "sparplan": "buy", "sell": "sell", "verkauf": "sell",
    "distribution": "dividend", "dividend": "dividend", "ausschüttung": "dividend", "dividende": "dividend",
    "deposit": "deposit", "einzahlung": "deposit", "withdrawal": "withdrawal", "auszahlung": "withdrawal",
    "interest": "interest", "zinsen": "interest", "fee": "fee", "gebühr": "fee", "gebühren": "fee",
    "taxes": "tax", "tax": "tax", "steuern": "tax",
    "security transfer": "delivery_in", "wertpapierübertrag": "delivery_in", "corporate action": None,
}


def _is_scalable_csv(text: str) -> bool:
    head = text[:400].lower()
    return "isin" in head and "shares" in head and "assettype" in head.replace("_", "").replace(" ", "")


def parse_csv(text: str) -> ParseResult | None:
    first = text.lstrip().split("\n", 1)[0]
    delim = ";" if first.count(";") >= first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text.lstrip()), delimiter=delim))
    if len(rows) < 2:
        return None
    header = [re.sub(r"[^a-z]", "", h.lower()) for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}
    if "isin" not in idx or "amount" not in idx or "date" not in idx:
        return None
    num_style = "de" if delim == ";" else "en"
    res = ParseResult(parser=f"{NAME}.csv")

    def cell(row, key):
        i = idx.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        status = cell(row, "status").lower()
        if status and status not in ("executed", "ausgeführt", "settled", "completed", "abgeschlossen"):
            continue
        raw_type = cell(row, "type").lower()
        tx_type = _CSV_TYPES.get(raw_type, "?")
        booking = parse_date(cell(row, "date"))
        amount = parse_decimal(cell(row, "amount"), style=num_style)
        fee = abs(parse_decimal(cell(row, "fee"), style=num_style) or Decimal("0"))
        tax = parse_decimal(cell(row, "tax"), style=num_style) or Decimal("0")
        qty = parse_decimal(cell(row, "shares"), style=num_style)
        price = parse_decimal(cell(row, "price"), style=num_style)
        isin = cell(row, "isin").upper() or None
        desc = cell(row, "description")
        cur = cell(row, "currency") or "EUR"
        warnings: list[str] = []
        review = False
        if tx_type in (None, "?"):
            warnings.append(f"Unbekannter Vorgangstyp „{cell(row, 'type')}“ – bitte Art prüfen.")
            review = True
            tx_type = ("deposit" if (amount or 0) >= 0 else "withdrawal") if not isin else ("delivery_in" if (qty or 0) >= 0 else "delivery_out")
        if tx_type == "delivery_in" and qty is not None and qty < 0:
            tx_type = "delivery_out"
        if isin and not isin_valid(isin):
            isin = None
        # Scalable reports `amount` without fee/tax; the cash effect includes them.
        if amount is not None and tx_type in ("buy", "sell", "dividend", "interest"):
            amount = amount - fee - abs(tax) if tax >= 0 else amount - fee + abs(tax)
        elif amount is None and tx_type in ("delivery_in", "delivery_out"):
            amount = Decimal("0")
        plan = raw_type in ("savings plan", "sparplan")
        is_sec = tx_type in ("buy", "sell", "dividend", "delivery_in", "delivery_out")
        if is_sec:
            d = tx_draft(tx_type, booking, amount, isin=isin, name=desc or None, quantity=abs(qty) if qty is not None else None,
                         price=price, fees=q2(fee), taxes=q2(abs(tax) if tax >= 0 else tax), currency=cur,
                         description=squash(f"{cell(row, 'type')} {desc}" + (" (Sparplan)" if plan and "plan" not in raw_type else "")),
                         counterparty="Scalable Capital", warnings=warnings, review_required=review, role="brokerage")
        else:
            d = cash_line_draft(booking, amount, booking_text=cell(row, "type"), purpose=desc, currency=cur)
            d.payload["type"] = tx_type
            d.payload["fees"], d.payload["taxes"] = q2(fee), q2(abs(tax))
            d.warnings.extend(warnings)
            d.review_required = d.review_required or review
            if tx_type in ("deposit", "withdrawal"):
                d.payload["category_hint"] = "Umbuchung"
        ref = cell(row, "reference")
        if ref:
            d.payload["reference"] = ref
        d.payload["_doc_type"] = "csv_export"
        res.drafts.append(d)
    return res if res.drafts else None
