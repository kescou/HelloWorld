"""Label-driven parser for securities settlement notes ("Wertpapierabrechnung", "Dividenden-
gutschrift" ...). German brokers print the same facts under slightly different labels; this
engine knows the synonyms. It powers the ING and Scalable parsers and serves as a lower-
confidence fallback for unknown brokers before the LLM is asked.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.ingest.dto import Draft
from app.ingest.parsers.common import FEE_LABELS, TAX_LABELS, clean_security_name, sum_costs, tx_draft
from app.ingest.util import DATE_DE, NUM_DE, find_isin, find_wkn, label_amount, line_amounts, parse_date, parse_decimal, q2, squash

_QTY = [
    re.compile(rf"^\s*(?:Nominale\s+)?(?:St[üu]ck|STK|Stk\.?|Anzahl|Menge|Anteile|Nominale|Bestand)(?:\s*/\s*Nominale)?\s*:?\s+(?P<q>{NUM_DE})(?!\s*%)(?=\s|$)", re.M | re.I),
    re.compile(rf"(?<![\w,.])(?P<q>{NUM_DE})\s+(?:St[üu]ck|Stk\.?|STK|Anteile)\b", re.I),
]
_PRICE = r"^\s*(?:Ausf[üu]hrungskurs|Durchschnittskurs|Abrechnungskurs|Kurs|Preis(?:\s+(?:pro|je)\s+(?:St[üu]ck|Anteil))?)\b(?!\s*w[äa]hrung)"
_GROSS_TRADE = r"^\s*(?:Kurswert|Marktwert|Ordervolumen|Brutto(?:betrag)?)\b"
_GROSS_DIV = r"^\s*(?:Brutto(?:betrag|ertrag)?|Bruttodividende|Dividendengutschrift\s+brutto|Ertrag\s+brutto|Aussch[üu]ttung\s+brutto)\b"
_PER_SHARE = (r"(?:Dividende|Aussch[üu]ttung|Ertragsaussch[üu]ttung|Ertrag|Zins-/Dividendensatz|Dividendensatz)"
              r"(?:\s+(?:pro|per|je)\s+(?:St[üu]ck|Stk\.?|Anteil))?")
_FINAL = [
    (r"Zu\s+Lasten\s+(?:Konto|Ihres)", -1), (r"Zu\s+Gunsten\s+(?:Konto|Ihres)", 1), (r"Zugunsten\s+Konto", 1),
    (r"(?:End|Gesamt)betrag\s+zu\s+Ihren\s+Lasten", -1), (r"(?:End|Gesamt)betrag\s+zu\s+Ihren\s+Gunsten", 1),
    (r"Betrag\s+zu\s+Ihren\s+Lasten", -1), (r"Betrag\s+zu\s+Ihren\s+Gunsten", 1),
    (r"^\s*Belastung\b", -1), (r"^\s*Lastschrift\b", -1), (r"^\s*Gutschrift(?:sbetrag)?\b", 1),
    (r"Ausmachender\s+Betrag", 0), (r"^\s*(?:Gesamtbetrag|Endbetrag|Auszahlungsbetrag|Abrechnungsbetrag|Nettobetrag|Gesamt|Summe)\b", 0),
]
_TRADE_DATE = r"(?:Handelsdatum|Handelstag|Ausf[üu]hrungsdatum|Ausf[üu]hrungstag(?:\s*/\s*-?zeit)?|Ausf[üu]hrung|Schlusstag(?:/-Zeit)?|Gesch[äa]ftstag|Orderdatum|Auftragsdatum)"
_VALUTA = r"(?:Valuta|Wertstellung|Valutadatum|Wert\b)"
_PAY_DATE = r"(?:Zahltag|Zahlungstag|Zahlbarkeitstag|Zahltermin|Valuta|Wertstellung)"
_FX = re.compile(r"(?:Devisenkurs|Dev\.-?\s?Kurs|Umrechnungskurs|Wechselkurs|Umrechnung)[^\n]*?(?P<rate>\d+,\d{3,})", re.I)
_FX_CUR = re.compile(r"(?:EUR\s*/\s*([A-Z]{3})|([A-Z]{3})\s*/\s*EUR|\(([A-Z]{3})\s*=)")
_NAME = re.compile(r"^\s*(?:Wertpapierbezeichnung|Wertpapier(?:name)?|Bezeichnung|Produkt|Titel)\s*:?\s+(?P<n>\S.*)$", re.M | re.I)

DIVIDEND_RX = re.compile(r"Dividendengutschrift|Ertragsgutschrift|Dividendenabrechnung|Ertragsmitteilung|Fondsaussch[üu]ttung|"
                         r"Ertr[äa]gnisgutschrift|Aussch[üu]ttung|Dividende\b|Ertragsabrechnung|Zinsgutschrift|Kupon", re.I)
TRADE_RX = re.compile(r"Wertpapierabrechnung|Wertpapierkauf|Wertpapierverkauf|Orderausf[üu]hrung|Transaktionsabrechnung|Abrechnung\s+(?:Kauf|Verkauf)", re.I)


def detect_kind(text: str) -> str | None:
    """'buy' | 'sell' | 'dividend' | None from the title area."""
    head = text[:4000]
    t = TRADE_RX.search(head)
    d = DIVIDEND_RX.search(head)
    if t and (not d or t.start() <= d.start()):
        after = head[t.start():]
        m = re.search(r"\b(Verkauf|Kauf|Sparplan|Zeichnung|R[üu]cknahme|Ausgabe)\b", after, re.I)
        if m:
            return "sell" if m.group(1).lower() in ("verkauf", "rücknahme", "ruecknahme") else "buy"
        return None
    if d:
        return "dividend"
    m = re.search(r"\b(?:Typ|Transaktionsart|Auftragsart|Art)\s*:?\s+(Kauf|Verkauf)\b", head, re.I)
    if m:
        return "sell" if m.group(1).lower() == "verkauf" else "buy"
    return None


def find_quantity(text: str) -> Decimal | None:
    for rx in _QTY:
        m = rx.search(text)
        if m:
            q = parse_decimal(m.group("q"), style="de")
            if q is not None and q > 0:
                return q
    return None


def find_date(text: str, label: str) -> date | None:
    m = re.search(label + r"[^\n\d]{0,25}(" + DATE_DE + r"|\d{4}-\d{2}-\d{2})", text, re.I)
    return parse_date(m.group(1)) if m else None


def find_fx(text: str) -> tuple[Decimal | None, str | None]:
    m = _FX.search(text)
    if not m:
        return None, None
    rate = parse_decimal(m.group("rate"), style="de")
    line_start = text.rfind("\n", 0, m.start()) + 1
    line_end = text.find("\n", m.end())
    line = text[line_start: line_end if line_end != -1 else None]
    cur = None
    for cm in _FX_CUR.finditer(line):
        cur = next(g for g in cm.groups() if g)
        if cur != "EUR":
            break
    if not rate or rate <= 0:
        return None, None
    return rate, cur


def find_name(text: str, isin: str | None) -> str | None:
    m = _NAME.search(text)
    if m:
        name = m.group("n")
        nxt = text[m.end():].lstrip("\n").split("\n", 1)[0]
        # share-class continuation line ("Registered Shares o.N.")
        if nxt and not re.match(r"\s*(?:Nominale|St[üu]ck|ISIN|WKN|Handels|Kurs|Zahltag|Ex-Tag|Anzahl|Typ|Datum|Order)\b", nxt, re.I) \
                and not line_amounts(nxt) and len(nxt) < 60:
            name = f"{name} {nxt.strip()}"
        return clean_security_name(re.sub(r"\(?\b[A-Z]{2}[A-Z0-9]{9}\d\b\)?", "", name))
    if isin:
        for line in text.split("\n"):
            if isin in line:
                rest = clean_security_name(re.sub(r"ISIN|WKN|\(|\)|:|" + re.escape(isin), " ", line))
                if rest and len(rest) > 3 and not re.fullmatch(r"[A-Z0-9 ]{6,8}", rest):
                    return rest
    return None


def final_amount(text: str) -> tuple[Decimal | None, int]:
    """(absolute final amount in EUR, sign hint -1/0/+1)."""
    for label, sign in _FINAL:
        rx = re.compile(label, re.I | re.M)
        for line in text.split("\n"):
            m = rx.search(line)
            if not m:
                continue
            amounts = [a for a in line_amounts(line[m.end():]) if a[1] in (None, "EUR")] or line_amounts(line[m.end():])
            if amounts:
                val = amounts[-1][0]
                if sign == 0 and val < 0:
                    return abs(val), -1
                return abs(val), sign
    return None, 0


def parse_security(text: str, *, parser: str, confidence: float = 0.9, counterparty: str | None = None,
                   kind: str | None = None) -> Draft | None:
    kind = kind or detect_kind(text)
    isin = find_isin(text)
    if kind is None or not isin:
        return None
    wkn = find_wkn(text)
    if not wkn:
        m = re.search(re.escape(isin) + r"\s*\(([A-Z0-9]{6})\)", text)
        wkn = m.group(1) if m else None
    qty = find_quantity(text)
    name = find_name(text, isin)
    fx_rate, fx_cur = find_fx(text)

    def to_eur(v: Decimal, cur: str | None) -> Decimal:
        return v / fx_rate if fx_rate and cur and cur != "EUR" else v

    fees, _ = sum_costs(text, FEE_LABELS, fx_to_eur=to_eur)
    taxes, _ = sum_costs(text, TAX_LABELS, fx_to_eur=to_eur)
    final, sign = final_amount(text)
    warnings: list[str] = []
    valuta = find_date(text, _VALUTA)

    if kind == "dividend":
        gross = label_amount(text, _GROSS_DIV, flags=re.I | re.M)
        gross_eur = to_eur(*gross) if gross else None
        if gross and gross[1] and gross[1] != "EUR" and not fx_rate:
            gross_eur = None
            warnings.append(f"Bruttobetrag in {gross[1]} ohne Devisenkurs.")
        amount = final
        if amount is None and gross_eur is not None:
            amount = gross_eur - taxes - fees
            warnings.append("Kein Gutschriftsbetrag gefunden – aus Brutto abzüglich Steuern berechnet.")
        if amount is None:
            return None
        if gross_eur is None:
            gross_eur = amount + taxes + fees
        price = (gross_eur / qty).quantize(Decimal("0.000001")) if qty else None
        pay = find_date(text, _PAY_DATE) or valuta or find_date(text, r"Datum")
        ex = find_date(text, r"Ex-?Tag|Extag|Ex-Datum")
        d = tx_draft("dividend", pay, abs(amount), value_date=valuta or pay, isin=isin, wkn=wkn, name=name, quantity=qty,
                     price=price, fees=q2(fees), taxes=q2(taxes), fx_rate=fx_rate,
                     description=squash(f"Dividende {name or isin}" + (f" (Ex-Tag {ex:%d.%m.%Y})" if ex else "")),
                     counterparty=counterparty, confidence=confidence, warnings=warnings, role="brokerage")
        d.payload["gross_amount"] = q2(gross_eur)
        d.payload["_doc_type"] = "dividend"
        return d

    gross = label_amount(text, _GROSS_TRADE, flags=re.I | re.M)
    price = label_amount(text, _PRICE, flags=re.I | re.M)
    gross_eur = to_eur(*gross) if gross else None
    price_eur = to_eur(*price) if price else None
    foreign = any(x and x[1] and x[1] != "EUR" for x in (gross, price))
    if foreign and not fx_rate:
        warnings.append("Fremdwährung ohne Devisenkurs – Kurs/Kurswert nicht in EUR umgerechnet.")
    if price_eur is None and gross_eur is not None and qty:
        price_eur = (gross_eur / qty).quantize(Decimal("0.000001"))
    if price_eur is not None and foreign and fx_rate and gross_eur is not None and qty:
        price_eur = (gross_eur / qty).quantize(Decimal("0.000001"))
    if final is None:
        if gross_eur is None:
            return None
        final = gross_eur + fees + taxes if kind == "buy" else gross_eur - fees - taxes
        warnings.append("Kein Endbetrag gefunden – aus Kurswert, Gebühren und Steuern berechnet.")
    amount = -abs(final) if kind == "buy" else abs(final)
    if sign and ((sign < 0) != (amount < 0)):
        warnings.append("Richtung des Endbetrags (zu Lasten/zu Gunsten) passt nicht zur Art des Geschäfts.")
    trade_date = find_date(text, _TRADE_DATE) or find_date(text, r"Datum") or valuta
    plan = bool(re.search(r"Sparplan", text[:4000], re.I))
    d = tx_draft(kind, trade_date, amount, value_date=valuta, isin=isin, wkn=wkn, name=name, quantity=qty, price=price_eur,
                 fees=q2(fees), taxes=q2(taxes), fx_rate=fx_rate if foreign else None,
                 description=squash(("Verkauf " if kind == "sell" else "Kauf ") + ("Sparplan " if plan else "") + (name or isin)),
                 counterparty=counterparty, confidence=confidence, warnings=warnings, role="brokerage")
    d.payload["_doc_type"] = "savings_plan" if plan and kind == "buy" else f"trade_{kind}"
    return d
