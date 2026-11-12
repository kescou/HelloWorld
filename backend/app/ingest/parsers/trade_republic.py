"""Trade Republic: Wertpapierabrechnung (Kauf/Verkauf/Sparplan/Saveback/Round up), Dividende/
Ausschüttung, Abrechnung Zinsen, Kontoauszug (Umsatzübersicht).

Layout knowledge (PDF text layer, upper-case section headers):
    ÜBERSICHT  -> sentence "Market-Order Kauf am 02.01.2024, um 10:23 Uhr an der ..."
    POSITION ANZAHL KURS BETRAG -> "<Name> 3 Stk. 108,10 EUR 324,30 EUR" + "ISIN: ..."
    ABRECHNUNG -> signed cost lines, "GESAMT -325,30 EUR"
    BUCHUNG    -> "<IBAN> <Valuta> <signed amount> EUR"
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.ingest.dto import DocContext, Draft, ParseResult
from app.ingest.parsers.common import (
    FEE_LABELS, TAX_LABELS, balance_draft, cash_line_draft, clean_security_name, section, sum_costs, tx_draft,
)
from app.ingest.util import (
    DATE_DE, MONTH_WORD, find_ibans, find_isin, line_amounts, month_number, parse_date, parse_decimal, q2, squash,
)

NAME = "trade_republic"

_POS_ROW = re.compile(
    r"^(?P<name>.*?)\s*(?P<qty>\d[\d.]*(?:,\d+)?)\s+Stk\.?\s+(?P<price>\d[\d.]*,\d+)\s+(?P<pcur>[A-Z]{3})\s+"
    r"(?P<total>\d[\d.]*,\d{2})\s+(?P<tcur>[A-Z]{3})\s*$", re.M)
_ORDER_RX = re.compile(
    r"(?P<what>Kauf|Verkauf|Sparplanausf[üu]hrung|Ausf[üu]hrung von Sparplan|Saveback|Round[- ]?up|Tilgung|Ausbuchung)"
    r"\s+am\s+(?P<date>" + DATE_DE + ")", re.I)
_BOOKING_RX = re.compile(r"(?P<iban>[A-Z]{2}\d{2}[A-Z0-9 ]{10,30}?)\s+(?P<date>" + DATE_DE + r")\s+(?P<amt>-?\s?\d[\d.]*,\d{2})\s+EUR")
_FX_RX = re.compile(r"(?P<rate>\d[\d.]*,\d+)\s+EUR/(?P<cur>[A-Z]{3})")


def matches(ctx: DocContext) -> bool:
    if ctx.source_kind == "csv":
        return False
    return ctx.institution == "trade_republic" or bool(re.search(r"TRADE REPUBLIC", ctx.text[:3000], re.I))


def parse(ctx: DocContext) -> ParseResult | None:
    text = ctx.text
    if re.search(r"UMSATZ[ÜU]BERSICHT|KONTO[ÜU]BERSICHT", text) or re.search(r"^\s*KONTOAUSZUG", text, re.M):
        res = _statement(text)
        if res and res.drafts:
            return res
    if re.search(r"ABRECHNUNG\s+ZINSEN|ZINSABRECHNUNG", text, re.I):
        return _interest(text)
    if re.search(r"^\s*(?:DIVIDENDE|AUSSCH[ÜU]TTUNG|ERTRAGSAUSSCH[ÜU]TTUNG|KAPITALERTRAG)\b|mit dem Ex-Tag", text, re.M | re.I):
        return _dividend(text)
    if re.search(r"WERTPAPIERABRECHNUNG|ABRECHNUNG\s+(?:SPARPLAN|SAVEBACK|ROUND)", text, re.I):
        return _trade(text)
    return None


# ----------------------------------------------------------------------------- shared

def _position(text: str):
    """(name, qty, price, price_cur, total, total_cur) from the POSITION table."""
    body = section(text, r"^\s*POSITION\s+ANZAHL.*$", r"^\s*ABRECHNUNG\b") or text
    m = _POS_ROW.search(body)
    if not m:
        return None
    name = m.group("name").strip()
    lines = body[:m.start()].strip().split("\n") if not name else []
    if not name and lines:
        name = lines[-1]
    # second name line (share class) follows the row, before the ISIN line
    after = body[m.end():].strip().split("\n")
    extra = []
    for ln in after[:2]:
        if re.search(r"ISIN|GESAMT|^\s*$", ln) or find_isin(ln):
            break
        extra.append(ln.strip())
    full_name = clean_security_name(" ".join([name, *extra]))
    return (full_name, parse_decimal(m.group("qty"), style="de"), parse_decimal(m.group("price"), style="de"),
            m.group("pcur"), parse_decimal(m.group("total"), style="de"), m.group("tcur"))


def _booking(text: str):
    body = section(text, r"^\s*BUCHUNG\s*$") or text
    m = _BOOKING_RX.search(body)
    if not m:
        return None, None, None
    ibans = find_ibans(m.group("iban"))
    return (ibans[0] if ibans else None), parse_date(m.group("date")), parse_decimal(m.group("amt"), style="de")


def _total(text: str) -> Decimal | None:
    body = section(text, r"^\s*ABRECHNUNG\s*$", r"^\s*BUCHUNG\s*$")
    found = [a for ln in body.split("\n") if re.match(r"\s*GESAMT\b", ln) for a in line_amounts(ln)[-1:]]
    return found[-1][0] if found else None


def _fx(text: str) -> tuple[Decimal | None, str | None]:
    m = _FX_RX.search(text)
    if not m:
        return None, None
    return parse_decimal(m.group("rate"), style="de"), m.group("cur")


def _doc_date(text: str) -> date | None:
    m = re.search(r"DATUM\s+(" + DATE_DE + ")", text)
    return parse_date(m.group(1)) if m else None


# ----------------------------------------------------------------------------- trade

def _trade(text: str) -> ParseResult | None:
    pos = _position(text)
    if not pos:
        return None
    name, qty, price, pcur, total, tcur = pos
    res = ParseResult(parser=f"{NAME}.trade", account_role="brokerage")
    m = _ORDER_RX.search(text)
    what = (m.group("what") if m else "").lower()
    head = text[:1500].upper()
    if not what:
        what = "verkauf" if "VERKAUF" in head else "kauf"
    is_sell = what.startswith("verkauf") or what in ("tilgung", "ausbuchung")
    is_plan = "sparplan" in what or "SPARPLAN" in head
    trade_date = parse_date(m.group("date")) if m else _doc_date(text)
    iban, valuta, booked = _booking(text)
    res.iban = iban
    abrechnung = section(text, r"^\s*ABRECHNUNG\s*$", r"^\s*BUCHUNG\s*$") or text
    fx_rate, _ = _fx(abrechnung)

    def to_eur(v, cur):
        return v / fx_rate if fx_rate and cur != "EUR" else v

    fees, _ = sum_costs(abrechnung, FEE_LABELS, sign_aware=True, fx_to_eur=to_eur)
    taxes, _ = sum_costs(abrechnung, TAX_LABELS, sign_aware=True, fx_to_eur=to_eur)
    amount = booked if booked is not None else _total(text)
    warnings: list[str] = []
    currency = "EUR"
    if pcur != "EUR" and fx_rate:
        price = price / fx_rate
        total = total / fx_rate
    elif pcur != "EUR":
        currency = pcur
        warnings.append(f"Kurs in {pcur} ohne Devisenkurs – bitte prüfen.")
    if amount is None and total is not None:
        amount = -(total + fees + taxes) if not is_sell else total - fees - taxes
        warnings.append("Kein Buchungsbetrag gefunden – aus Kurswert berechnet.")
    desc = "Sparplan" if is_plan else ("Saveback" if "saveback" in what else ("Round up" if "round" in what else None))
    d = tx_draft("sell" if is_sell else "buy", trade_date, amount, value_date=valuta,
                 isin=find_isin(section(text, r"^\s*POSITION\b") or text), name=name, quantity=qty, price=price,
                 fees=q2(fees), taxes=q2(taxes), currency=currency, fx_rate=fx_rate if pcur != "EUR" else None,
                 description=" ".join(x for x in ("Verkauf" if is_sell else "Kauf", desc, name) if x),
                 counterparty="Trade Republic", warnings=warnings, role="brokerage")
    d.payload["_doc_type"] = "savings_plan" if is_plan else ("trade_sell" if is_sell else "trade_buy")
    res.drafts.append(d)
    return res


# ----------------------------------------------------------------------------- dividend

def _dividend(text: str) -> ParseResult | None:
    pos = _position(text)
    iban, valuta, booked = _booking(text)
    if not pos and booked is None:
        return None
    res = ParseResult(parser=f"{NAME}.dividend", account_role="brokerage", iban=iban)
    abrechnung = section(text, r"^\s*ABRECHNUNG\s*$", r"^\s*BUCHUNG\s*$") or text
    fx_rate, fx_cur = _fx(abrechnung)

    def to_eur(v, cur):
        return v / fx_rate if fx_rate and cur != "EUR" else v

    taxes, _ = sum_costs(abrechnung, TAX_LABELS, sign_aware=True, fx_to_eur=to_eur)
    fees, _ = sum_costs(abrechnung, FEE_LABELS, sign_aware=True, fx_to_eur=to_eur)
    name = qty = price = None
    warnings: list[str] = []
    if pos:
        name, qty, price, pcur, total, tcur = pos
        if pcur != "EUR":
            if fx_rate:
                price = price / fx_rate
            else:
                price = None
                warnings.append(f"Ertrag in {pcur} ohne Devisenkurs – Betrag je Stück nicht umgerechnet.")
    amount = booked if booked is not None else _total(text)
    m = re.search(r"Ex-Tag\s+(" + DATE_DE + ")", text)
    ex_date = parse_date(m.group(1)) if m else None
    booking = valuta or _doc_date(text) or ex_date
    d = tx_draft("dividend", booking, amount, value_date=valuta, isin=find_isin(section(text, r"^\s*POSITION\b") or text),
                 name=name, quantity=qty, price=price, fees=q2(fees), taxes=q2(taxes), fx_rate=fx_rate,
                 description=f"Dividende {name or ''}".strip() + (f" (Ex-Tag {ex_date:%d.%m.%Y})" if ex_date else ""),
                 counterparty="Trade Republic", warnings=warnings, role="brokerage")
    d.payload["_doc_type"] = "dividend"
    res.drafts.append(d)
    return res


# ----------------------------------------------------------------------------- interest

def _interest(text: str) -> ParseResult | None:
    res = ParseResult(parser=f"{NAME}.interest", account_role="cash")
    abrechnung = section(text, r"^\s*ABRECHNUNG\s*$", r"^\s*BUCHUNG\s*$")
    taxes, _ = sum_costs(abrechnung or text, TAX_LABELS, sign_aware=True)
    booking_body = section(text, r"^\s*BUCHUNG\s*$") or text
    m = re.search(r"(?P<iban>[A-Z]{2}\d{2}[A-Z0-9 ]{10,30}?)\s+(?P<date>" + DATE_DE + r")\s+(?P<amt>-?\d[\d.]*,\d{2})\s+EUR", booking_body)
    amount = booking = None
    if m:
        ibans = find_ibans(m.group("iban"))
        res.iban = ibans[0] if ibans else None
        booking, amount = parse_date(m.group("date")), parse_decimal(m.group("amt"), style="de")
    if amount is None:
        amount = _total(text)
    if booking is None:
        m2 = re.search(r"zum\s+(" + DATE_DE + ")", text)
        booking = parse_date(m2.group(1)) if m2 else _doc_date(text)
    if amount is None:
        return None
    gross = None
    period = None
    m3 = re.search(r"(\d+(?:,\d+)?)\s?%\s+(" + DATE_DE + r")\s*-\s*(" + DATE_DE + r")\s+(\d[\d.]*,\d{2})\s+EUR", text)
    if m3:
        gross = parse_decimal(m3.group(4), style="de")
        period = f"{m3.group(2)} - {m3.group(3)}, {m3.group(1)} %"
    d = tx_draft("interest", booking, amount, taxes=q2(taxes), counterparty="Trade Republic",
                 description="Zinsen" + (f" {period}" if period else ""), category_hint="Zinsen", role="cash")
    if gross is not None:
        d.payload["gross_amount"] = gross
    d.payload["_doc_type"] = "interest"
    res.drafts.append(d)
    return res


# ----------------------------------------------------------------------------- statement

_ST_START = re.compile(rf"^(?P<day>\d{{1,2}})\.?\s+(?P<mon>{MONTH_WORD})\.?(?:\s+(?P<year>20\d{{2}}))?(?:\s+(?P<rest>.*))?$", re.I)
_ST_TYPES = (
    "Überweisung", "Handel", "Zinszahlung", "Zinsen", "Kartentransaktion", "Erträge", "Ertrag", "Dividende", "Steuern",
    "Gebühren", "Gebühr", "Prämie", "Empfehlung", "Lastschrift", "Kartenzahlung", "Bonus", "Einzahlung", "Auszahlung",
    "SEPA-Lastschrift", "Rückerstattung", "Kapitalmaßnahme", "Sparplan", "Transfer", "Card Transaction", "Trade",
    "Interest Payment", "Earnings",
)
_ST_TYPE_RX = re.compile(r"(?<![\wäöüÄÖÜ])(" + "|".join(sorted(map(re.escape, _ST_TYPES), key=len, reverse=True)) + r")(?![\wäöü])")
_EURO_AMT = re.compile(r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s?€")
_ST_END = re.compile(r"^\s*(?:BARMITTEL[ÜU]BERSICHT|GELDMARKTFONDS|TRANSAKTIONS[ÜU]BERSICHT|HINWEISE ZUM KONTOAUSZUG|"
                     r"DEPOTAUSZUG|WERTPAPIERE\s*$)", re.I)
_ST_NOISE = re.compile(r"^(?:Trade Republic Bank GmbH|TRADE REPUBLIC BANK|Erstellt am|Seite \d+|DATUM\s+TYP|www\.traderepublic|"
                       r"Brunnenstra|AG Charlottenburg|Gesch[äa]ftsf[üu]hrer|Sitz der Gesellschaft|USt-ID|\f)", re.I)
_QTY_RX = re.compile(r"quantity:\s*(\d+(?:[.,]\d+)?)|(?:Stück|Stk\.?|Anzahl):?\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:Stück|Stk\.?)", re.I)


def _statement(text: str) -> ParseResult | None:
    res = ParseResult(parser=f"{NAME}.statement", account_role="cash")
    ibans = find_ibans(text[:3000])
    res.iban = ibans[0] if ibans else None

    # --- summary: "Cash-Konto 1.000,00 € 500,00 € 300,00 € 1.200,00 €"
    opening = closing = None
    m = re.search(r"^(?:Cash-?Konto|Verrechnungskonto|Girokonto|Cash)\s+((?:-?\d[\d.]*,\d{2}\s?€\s*){4})$", text, re.M | re.I)
    if m:
        vals = [parse_decimal(v, style="de") for v in _EURO_AMT.findall(m.group(1))]
        opening, closing = vals[0], vals[-1]
    period_end = None
    pm = re.search(r"(\d{1,2}\.?\s+(?:" + MONTH_WORD + r")\.?\s+20\d{2}|" + DATE_DE + r")\s*-\s*(\d{1,2}\.?\s+(?:" + MONTH_WORD + r")\.?\s+20\d{2}|" + DATE_DE + ")", text[:3000], re.I)
    if pm:
        period_end = parse_date(pm.group(2))

    # --- transaction blocks
    body = section(text, r"^\s*UMSATZ[ÜU]BERSICHT\s*$") or text
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or _ST_NOISE.search(line):
            continue
        if _ST_END.search(line):
            break
        if _ST_START.match(line) and not re.match(r"^\d{4}\b", line):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)

    prev = opening
    for block in blocks:
        d, saldo = _statement_block(block, prev)
        if d is None:
            continue
        res.drafts.append(d)
        if saldo is not None:
            prev = saldo
    if not res.drafts:
        return None
    last_date = max((d.payload["booking_date"] for d in res.drafts if d.payload.get("booking_date")), default=None)
    end_balance = closing if closing is not None else prev
    if end_balance is not None and (period_end or last_date):
        res.drafts.append(balance_draft(period_end or last_date, end_balance))
    if closing is not None and prev is not None and abs(closing - prev) > Decimal("0.005"):
        res.warnings.append(f"Endsaldo laut Übersicht {closing} weicht vom letzten Zeilensaldo {prev} ab – evtl. fehlen Zeilen.")
    for d in res.drafts:
        d.payload.setdefault("_doc_type", "account_statement")
    return res


def _statement_block(block: list[str], prev_saldo: Decimal | None) -> tuple[Draft | None, Decimal | None]:
    m = _ST_START.match(block[0])
    day, mon = int(m.group("day")), month_number(m.group("mon"))
    year = int(m.group("year")) if m.group("year") else None
    parts = [m.group("rest") or ""]
    for line in block[1:]:
        ym = re.match(r"^(20\d{2})\b\s*(.*)$", line)
        if ym and year is None:
            year = int(ym.group(1))
            parts.append(ym.group(2))
        else:
            parts.append(line)
    joined = squash(" ".join(parts))
    amounts = [parse_decimal(a, style="de") for a in _EURO_AMT.findall(joined)]
    if not amounts or not mon or not year:
        return None, None
    try:
        booking = date(year, mon, day)
    except ValueError:
        return None, None
    saldo = amounts[-1] if len(amounts) >= 2 else None
    amount = amounts[-2] if len(amounts) >= 2 else amounts[-1]
    desc = squash(_EURO_AMT.sub(" ", joined))
    tm = _ST_TYPE_RX.search(desc)
    typ = tm.group(1) if tm else ""
    if tm:
        desc = squash(desc[:tm.start()] + " " + desc[tm.end():])
    low = f"{typ} {desc}".lower()

    # direction: saldo chain is authoritative, keywords are the fallback
    warnings: list[str] = []
    sign = None
    if saldo is not None and prev_saldo is not None:
        delta = saldo - prev_saldo
        if abs(abs(delta) - abs(amount)) <= Decimal("0.01"):
            sign = 1 if delta >= 0 else -1
    if sign is None:
        outgoing = re.search(r"kartentransaktion|kartenzahlung|card transaction|buy trade|savings plan|sparplan|kauf|"
                             r"lastschrift|geb[üu]hr|steuern|outgoing|auszahlung|payout|abbuchung|round up", low)
        incoming = re.search(r"sell trade|verkauf|einzahlung|incoming|zins|interest|dividend|ertr[äa]g|pr[äa]mie|bonus|"
                             r"erstattung|gutschrift|empfehlung", low)
        sign = -1 if outgoing and not incoming else 1
        if prev_saldo is not None or not (outgoing or incoming):
            warnings.append("Richtung (Ein-/Ausgang) aus Stichworten abgeleitet – bitte prüfen.")
    amount = abs(amount) * sign

    isin = find_isin(desc)
    qty = None
    qm = _QTY_RX.search(desc)
    if qm:
        qty = parse_decimal(next(g for g in qm.groups() if g), style="en" if "quantity" in qm.group(0).lower() else "auto")
    is_trade = typ.lower() in ("handel", "trade", "sparplan") or re.search(r"\b(buy|sell) trade\b|savings plan execution|direktkauf|direktverkauf", low)
    is_div = typ.lower() in ("erträge", "ertrag", "dividende", "earnings") or re.search(r"dividend|aussch[üu]ttung", low)
    conf = 0.9 if not warnings else 0.7
    if isin and (is_trade or is_div):
        name = None
        nm = re.search(re.escape(isin) + r"\s+(.+?)(?:,\s*quantity|,\s*Anzahl|\s+\d+(?:[.,]\d+)?\s*(?:Stk|Stück)|$)", desc)
        if nm:
            name = clean_security_name(nm.group(1))
        if is_div:
            d = tx_draft("dividend", booking, amount, isin=isin, name=name, quantity=qty, description=desc,
                         counterparty="Trade Republic", role="brokerage", confidence=conf, warnings=warnings)
        else:
            tx_type = "buy" if amount < 0 else "sell"
            price = (abs(amount) / qty).quantize(Decimal("0.0001")) if qty else None
            d = tx_draft(tx_type, booking, amount, isin=isin, name=name, quantity=qty, price=price, description=desc,
                         counterparty="Trade Republic", role="brokerage", confidence=conf - 0.05, warnings=warnings)
            d.payload["_price_derived"] = True
            d.warnings.append("Aus Kontoauszug: Kurs = Betrag ÷ Stück (inkl. Gebühren/Steuern). Die Wertpapierabrechnung ist genauer.")
            if not qty:
                d.review_required = True
                d.warnings.append("Stückzahl fehlt im Kontoauszug.")
        return d, saldo
    d = cash_line_draft(booking, amount, booking_text=typ or None, purpose=desc, confidence=conf)
    d.warnings.extend(warnings)
    if is_trade or is_div:
        d.review_required = True
        d.warnings.append("Wertpapierumsatz ohne ISIN – bitte über die Wertpapierabrechnung erfassen.")
    cp = re.search(r"(?:Kartentransaktion|Card Transaction)\s*", typ, re.I)
    if cp or typ.lower() in ("kartentransaktion", "kartenzahlung", "lastschrift", "sepa-lastschrift"):
        d.payload["counterparty"] = desc[:200] or None
    if typ.lower() in ("überweisung", "transfer", "einzahlung", "auszahlung"):
        nm = re.search(r"(?:von|from|an|to)\s+(.+?)(?:\s+\(?[A-Z]{2}\d{2}[A-Z0-9 ]{10,}|$)", desc)
        if nm and not re.match(r"[A-Z]{2}\d{2}", nm.group(1)):
            d.payload["counterparty"] = squash(nm.group(1))[:200]
    return d, saldo
