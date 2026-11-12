"""ING (ING-DiBa): Wertpapierabrechnung Kauf/Verkauf, Dividenden-/Ertragsgutschrift,
Girokonto-Kontoauszug (PDF) and the Umsatzanzeige CSV export.

Statement layout (text layer), two lines per booking + continuation lines:
    01.03.2023 Gehalt/Rente Arbeitgeber GmbH 2.500,00
    01.03.2023 Lohn Gehalt 03/2023
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.ingest.dto import DocContext, ParseResult
from app.ingest.parsers import generic_security as gs
from app.ingest.parsers.bank_csv import parse_bank_csv
from app.ingest.parsers.common import balance_draft, cash_line_draft
from app.ingest.util import DATE_DE, find_ibans, label_amount, parse_date, parse_decimal

NAME = "ing"

_BOOKING_TEXTS = (
    "Gutschrift aus Dauerauftrag", "Dauerauftrag/Terminueberw.", "Dauerauftrag / Terminüberweisung", "Dauerauftrag",
    "Gehalt/Rente", "Lastschrifteinzug", "Lastschrift", "Gutschrift", "Echtzeitüberweisung", "Echtzeitueberweisung",
    "Überweisung", "Ueberweisung", "Entgelt", "Abschluss", "Zinsen", "Zinsgutschrift", "Retoure", "Bargeldauszahlung",
    "Bargeldeinzahlung", "Kartenzahlung", "VISA Card", "Wertpapierkauf", "Wertpapierverkauf", "Wertpapiergutschrift",
    "Wertpapierertrag", "Abbuchung", "Storno", "Rücküberweisung", "Auszahlung", "Einzahlung", "Umbuchung", "Übertrag",
    "Kapitalertragsteuer", "Solidaritätszuschlag", "Kirchensteuer", "Steuererstattung", "Rückbuchung",
)
_BT_RX = re.compile(r"^(" + "|".join(sorted(map(re.escape, _BOOKING_TEXTS), key=len, reverse=True)) + r")\b\s*(.*)$", re.I)
_START = re.compile(r"^(?P<date>" + DATE_DE + r")\s+(?P<text>.+?)\s+(?P<amt>[-+]?\s?\d{1,3}(?:\.\d{3})*,\d{2})$")
_VALUTA = re.compile(r"^(?P<date>" + DATE_DE + r")(?:\s+(?P<text>.*))?$")
_NOISE = re.compile(
    r"^(?:Buchung\s+Buchung|Valuta$|Seite \d|ING-DiBa AG|Girokonto Nummer|Kontoauszug\b|Datum \d|Auszugsnummer|IBAN\b|BIC\b|"
    r"Kunden-Information|Vorliegender Freistellungsauftrag|Bitte beachten|34GKKA|Neuer Saldo|Alter Saldo|Eingeräumte|"
    r"Abschluss für Konto|Betrag \(EUR\)|\f|Übertrag)", re.I)
_STOP = re.compile(r"^(?:Kunden-Information|Abschluss für Konto|Rechnungsabschluss|Wichtige Hinweise)", re.I)


def matches(ctx: DocContext) -> bool:
    if ctx.source_kind == "csv":
        return bool(re.search(r"Umsatzanzeige|Bank;ING|Auftraggeber/Empf", ctx.text[:3000]))
    return ctx.institution == "ing" or bool(re.search(r"ING-DiBa|INGDDEFF", ctx.text[:4000]))


def parse(ctx: DocContext) -> ParseResult | None:
    text = ctx.text
    if ctx.source_kind == "csv" or re.search(r"^Umsatzanzeige;", text, re.M):
        return parse_bank_csv(text, parser=f"{NAME}.csv")
    head = text[:4000]
    if re.search(r"Wertpapierabrechnung|Dividendengutschrift|Ertragsgutschrift|Zinsgutschrift|R[üu]ckzahlung", head, re.I) \
            and re.search(r"ISIN", head):
        return _security(text)
    if re.search(r"Kontoauszug|Alter Saldo|Neuer Saldo", head, re.I):
        return _statement(text)
    return None


def _security(text: str) -> ParseResult | None:
    d = gs.parse_security(text, parser=NAME, confidence=0.95, counterparty="ING")
    if not d:
        return None
    # "Den Endbetrag buchen wir mit Valuta 17.03.2023 zu Lasten Ihres Kontos 1234567890 (IBAN DE..)"
    m = re.search(r"mit Valuta\s+(" + DATE_DE + ")", text)
    if m:
        d.payload["value_date"] = parse_date(m.group(1))
        if d.payload["type"] == "dividend" and not d.payload.get("booking_date"):
            d.payload["booking_date"] = d.payload["value_date"]
    ibans = find_ibans(text)
    res = ParseResult(parser=f"{NAME}.{'dividend' if d.payload['type'] == 'dividend' else 'trade'}", drafts=[d],
                      account_role="brokerage")
    res.settlement_iban = ibans[0] if ibans else None
    m = re.search(r"Depot(?:nummer|konto)?\s*:?\s*(\d{8,10})", text)
    res.account_number = m.group(1) if m else None
    return res


def _statement(text: str) -> ParseResult | None:
    res = ParseResult(parser=f"{NAME}.statement", account_role="cash")
    ibans = find_ibans(text[:3000])
    res.iban = ibans[0] if ibans else None
    new_bal = label_amount(text, r"Neuer Saldo")
    old_bal = label_amount(text, r"Alter Saldo")
    m = re.search(r"^Datum\s+(" + DATE_DE + ")", text, re.M)
    stmt_date = parse_date(m.group(1)) if m else None

    records: list[dict] = []
    cur: dict | None = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if cur is not None and _STOP.search(line):
            cur = None
            continue
        m = _START.match(line)
        if m and cur is not None and cur["expect_valuta"] and not _BT_RX.match(m.group("text")):
            m = None   # "<Valuta> <Verwendungszweck ... 12,50>": purpose line that merely ends with a number
        if m and not _NOISE.search(line):
            cur = {"date": parse_date(m.group("date")), "head": m.group("text"), "amount": parse_decimal(m.group("amt"), style="de"),
                   "valuta": None, "lines": [], "expect_valuta": True}
            records.append(cur)
            continue
        if cur is None or _NOISE.search(line):
            continue
        v = _VALUTA.match(line)
        if cur["expect_valuta"] and v:
            cur["valuta"] = parse_date(v.group("date"))
            if v.group("text"):
                cur["lines"].append(v.group("text"))
        else:
            cur["lines"].append(line)
        cur["expect_valuta"] = False

    total = Decimal("0")
    for r in records:
        bt = _BT_RX.match(r["head"])
        booking_text, cp = (bt.group(1), bt.group(2)) if bt else (None, r["head"])
        d = cash_line_draft(r["date"], r["amount"], booking_text=booking_text, counterparty=cp or None,
                            purpose=" ".join(r["lines"]), value_date=r["valuta"])
        d.payload["_doc_type"] = "account_statement"
        res.drafts.append(d)
        total += r["amount"] or 0
    if not res.drafts:
        return None
    if new_bal:
        last = max((d.payload["booking_date"] for d in res.drafts if d.payload["booking_date"]), default=None)
        res.drafts.append(balance_draft(stmt_date or last, new_bal[0]))
        if old_bal and abs(old_bal[0] + total - new_bal[0]) > Decimal("0.005"):
            res.warnings.append(
                f"Summe der erkannten Buchungen ({total}) erklärt die Saldoänderung {old_bal[0]} → {new_bal[0]} nicht – "
                "evtl. fehlen Zeilen oder Beträge sind falsch gelesen.")
            for d in res.drafts:
                d.confidence = min(d.confidence, 0.75)
    return res
