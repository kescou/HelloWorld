"""Postbank: Kontoauszug PDF (new Deutsche-Bank-platform layout and the classic one) and the
Umsatz-CSV export (both generations).

Statement rows (text layer):
    02.03./02.03. SEPA Lastschrift Einzug von Stadtwerke Musterstadt - 85,00     (new)
    02.03. 02.03. Lastschrift Stadtwerke Musterstadt - 85,00                      (classic)
continuation lines carry counterparty / Verwendungszweck. The year comes from the header.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.ingest.dto import DocContext, ParseResult
from app.ingest.parsers.bank_csv import parse_bank_csv
from app.ingest.parsers.common import balance_draft, cash_line_draft
from app.ingest.util import DATE_DE, find_ibans, line_amounts, parse_date, parse_decimal, squash

NAME = "postbank"

_ROW = re.compile(
    r"^(?P<d1>\d{2}\.\d{2}\.)(?:\d{4})?\s*/?\s*(?P<d2>\d{2}\.\d{2}\.)(?:\d{4})?\s+(?P<text>.+?)\s+"
    r"(?P<amt>[-+]\s?\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,3}(?:\.\d{3})*,\d{2}\s?[-+SH])$")
_NOISE = re.compile(
    r"^(?:Buchung\s*/?\s*Wert|Buchung\s+Wert|Vorgang/Buchungsinformation|Soll\s+Haben|Seite\s+\d|Auszug\s+Seite|Kontoauszug\b|"
    r"Postbank\b|Deutsche Bank AG|IBAN\b|BIC\b|Kontonummer\b|Filial|Übertrag|Uebertrag|\f|Zwischensumme|Kontoinhaber|"
    r"Blatt\s+\d|Auszug\s+\d|Rechnungsabschluss)", re.I)
_STOP = re.compile(r"^(?:Alter Kontostand|Neuer Kontostand|Kontostand am|Summe Zahlungs|Rechnungsabschluss|Kontonummer\s+BLZ|"
                   r"Wichtige Hinweise|Bitte beachten Sie|Zahlungseing[äa]nge|Zahlungsausg[äa]nge|Dispositionskredit|Zinss[äa]tze)", re.I)
_BT = re.compile(
    r"^(SEPA[- ]?(?:Überweisung|Ueberweisung|Lastschrift(?:\s+Einzug)?|Basislastschrift|Dauerauftrag|Echtzeitüberweisung|Gutschrift)|"
    r"Echtzeitüberweisung|Kartenzahlung|Kartenverfügung|Bargeldauszahlung(?:\s+Geldautomat)?|Dauerauftrag|Lastschrift|Gutschrift|"
    r"Überweisung|Ueberweisung|Zinsen/Entgelt|Entgelt|Zinsen|Gehalt/Rente|Lohn/Gehalt|Auszahlung Geldautomat|Rechnungsabschluss|"
    r"Kontoführung|Einzahlung|Scheckeinreichung|Storno)\b\s*(?:Einzug\s+)?(?:(?:von|an|für)\s+)?(.*)$", re.I)


def matches(ctx: DocContext) -> bool:
    if ctx.source_kind == "csv":
        return bool(re.search(r"Postbank|Filial-/Kontonummer|Buchungsdetails|Begünstigter / Auftraggeber|Letzter Kontostand", ctx.text[:3000], re.I))
    return ctx.institution == "postbank" or bool(re.search(r"Postbank|PBNKDEFF", ctx.text[:4000], re.I))


def parse(ctx: DocContext) -> ParseResult | None:
    if ctx.source_kind == "csv":
        return parse_bank_csv(ctx.text, parser=f"{NAME}.csv")
    if re.search(r"Kontoauszug|Kontostand|Buchung\s*/?\s*Wert", ctx.text[:6000], re.I):
        return _statement(ctx.text)
    return None


def _signed(tok: str) -> Decimal | None:
    tok = tok.strip()
    if tok[-1] in "+-SH":
        v = parse_decimal(tok[:-1], style="de")
        return -v if tok[-1] in "-S" else v
    return parse_decimal(tok, style="de")


def _statement(text: str) -> ParseResult | None:
    res = ParseResult(parser=f"{NAME}.statement", account_role="cash")
    ibans = find_ibans(text[:4000])
    res.iban = ibans[0] if ibans else None

    # period / year
    start = end = None
    m = re.search(r"vom\s+(" + DATE_DE + r")\s+bis\s+(" + DATE_DE + ")", text)
    if m:
        start, end = parse_date(m.group(1)), parse_date(m.group(2))
    if not end:
        m = re.search(r"(?:Kontoauszug|Auszug|Datum|Erstellt am)[^\n]{0,40}?(" + DATE_DE + ")", text)
        end = parse_date(m.group(1)) if m else None
    if not end:
        m = re.search(DATE_DE, text)
        end = parse_date(m.group(0)) if m else None
    if not end:
        return None

    def with_year(ddmm: str) -> date | None:
        d = parse_date(ddmm, default_year=end.year)
        if d and d > end and (d - end).days > 31:   # Dec bookings on a January statement
            d = parse_date(ddmm, default_year=end.year - 1)
        if d and start and d < start and (start - d).days > 300:
            d = parse_date(ddmm, default_year=end.year)
        return d

    records: list[dict] = []
    cur: dict | None = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _ROW.match(line)
        if m:
            cur = {"date": with_year(m.group("d1")), "valuta": with_year(m.group("d2")), "head": m.group("text"),
                   "amount": _signed(m.group("amt")), "lines": []}
            records.append(cur)
            continue
        if _STOP.search(line):
            cur = None
            continue
        if cur is None or _NOISE.search(line):
            continue
        cur["lines"].append(line)

    total = Decimal("0")
    for r in records:
        bt = _BT.match(r["head"])
        booking_text, cp = (bt.group(1), bt.group(2).strip()) if bt else (None, r["head"])
        lines = [ln for ln in r["lines"] if not re.fullmatch(r"Verwendungszweck/?\s*Kundenreferenz", ln, re.I)]
        if not cp and lines:
            cp = lines.pop(0)
        purpose = re.sub(r"^Verwendungszweck/?\s*(?:Kundenreferenz)?\s*", "", " ".join(lines), flags=re.I)
        d = cash_line_draft(r["date"], r["amount"], booking_text=booking_text, counterparty=cp or None, purpose=purpose,
                            value_date=r["valuta"])
        d.payload["_doc_type"] = "account_statement"
        res.drafts.append(d)
        total += r["amount"] or 0
    if not res.drafts:
        return None

    def bal(label: str) -> Decimal | None:
        for line in text.split("\n"):
            mm = re.search(label, line, re.I)
            if mm:
                tail = line[mm.end():]
                t = re.search(r"([-+]\s?\d{1,3}(?:\.\d{3})*,\d{2}|\d{1,3}(?:\.\d{3})*,\d{2}\s?[-+SH]?)\s*(?:EUR|€)?\s*$", tail)
                if t:
                    return _signed(t.group(1))
        return None

    new_bal = bal(r"Neuer Kontostand|Kontostand am\s+" + end.strftime("%d.%m.%Y") + r"|Neuer Saldo")
    old_bal = bal(r"Alter Kontostand|Letzter Kontostand|Alter Saldo")
    if new_bal is not None:
        res.drafts.append(balance_draft(end, new_bal))
        if old_bal is not None and abs(old_bal + total - new_bal) > Decimal("0.005"):
            res.warnings.append(
                f"Summe der erkannten Buchungen ({total}) erklärt die Saldoänderung {old_bal} → {new_bal} nicht – "
                "evtl. fehlen Zeilen oder Beträge sind falsch gelesen.")
            for d in res.drafts:
                d.confidence = min(d.confidence, 0.75)
    return res
