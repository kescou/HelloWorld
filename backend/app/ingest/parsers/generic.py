"""Institution-agnostic fallbacks:

* ``parse_statement`` – pasted online-banking tables / plain text statements: one booking per
  line (``date [valuta] text amount [EUR] [S|H]``) or multi-line blocks that start with a date
  and carry the amount on a later line. Tab-separated copies from HTML tables work too.
* ``parse_csv`` – unknown bank CSV with recognisable German headers.
* ``parse_security`` – unknown broker settlement note (label engine).
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.ingest.dto import DocContext, ParseResult
from app.ingest.parsers import generic_security as gs
from app.ingest.parsers.bank_csv import parse_bank_csv
from app.ingest.parsers.common import balance_draft, cash_line_draft
from app.ingest.util import CUR, DATE_DE, MONTH_WORD, find_ibans, parse_date, parse_decimal, squash

NAME = "generic"
CONFIDENCE = 0.78

_DATE_ANY = rf"(?:{DATE_DE}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\.?\s+(?:{MONTH_WORD})\.?\s+\d{{4}}|\d{{2}}\.\d{{2}}\.?(?=\s))"
_AMT = rf"(?:[-+]\s?)?(?:(?:{CUR})\s?)?(?:[-+]\s?)?\d{{1,3}}(?:\.\d{{3}})*,\d{{2}}(?:\s?-)?(?:\s?(?:{CUR}))?(?:\s?[SH](?![A-Za-z]))?"
_LINE = re.compile(
    rf"^(?P<date>{_DATE_ANY})(?:\s*[/|]?\s*(?P<valuta>{_DATE_ANY}))?\s+(?P<text>.*?)\s*(?P<amt>{_AMT})"
    rf"(?:\s+(?P<bal>{_AMT}))?\s*$", re.I)
_DATE_START = re.compile(rf"^(?P<date>{_DATE_ANY})(?:\s*[/|]?\s*(?P<valuta>{_DATE_ANY}))?(?:\s+(?P<rest>.*))?$", re.I)
_AMT_ONLY = re.compile(rf"^(?P<amt>{_AMT})$", re.I)
_AMT_END = re.compile(rf"^(?P<text>.*?)\s*(?P<amt>{_AMT})$", re.I)
_BAL_LINE = re.compile(
    rf"(?:Neuer\s+(?:Saldo|Kontostand)|Kontostand|Endsaldo|Aktueller\s+Saldo|Saldo|Verf[üu]gbarer\s+Betrag)"
    rf"(?:\s+(?:am|vom|per|zum))?\s*:?\s*(?P<date>{DATE_DE})?\s*:?\s*(?P<amt>{_AMT})", re.I)
_HEADER = re.compile(r"^(?:Datum|Buchung(?:stag)?|Valuta|Wert(?:stellung)?|Betrag|Verwendungszweck|Empf[äa]nger|Saldo|Umsatz)\b", re.I)


def _amount(tok: str) -> Decimal | None:
    tok = tok.strip()
    neg = False
    m = re.search(r"\s?([SH])$", tok)
    if m:
        neg = m.group(1) == "S"
        tok = tok[:m.start()]
    val = parse_decimal(tok, style="de")
    if val is None:
        return None
    return -abs(val) if neg else val


def parse_statement(ctx: DocContext) -> ParseResult | None:
    text = ctx.text.replace("\t", "   ")
    lines = [ln.strip() for ln in text.split("\n")]
    res = ParseResult(parser=f"{NAME}.statement", account_role="cash")
    ibans = find_ibans(text[:4000])
    res.iban = ibans[0] if ibans else None
    year_hint = None
    ym = re.search(r"\b(20\d{2})\b", text)
    if ym:
        year_hint = int(ym.group(1))

    records: list[dict] = []
    balances: list[tuple] = []
    cur: dict | None = None
    dated_lines = 0
    for line in lines:
        if not line:
            if cur is not None and cur.get("amount") is not None:
                cur = None
            continue
        bm = _BAL_LINE.search(line)
        if bm and not _LINE.match(line):
            balances.append((parse_date(bm.group("date")), _amount(bm.group("amt"))))
            continue
        if _HEADER.match(line) and not re.search(r"\d,\d{2}", line):
            continue
        m = _LINE.match(line)
        if m:
            dated_lines += 1
            cur = {"date": m.group("date"), "valuta": m.group("valuta"), "texts": [m.group("text")],
                   "amount": _amount(m.group("amt")), "balance": _amount(m.group("bal")) if m.group("bal") else None}
            records.append(cur)
            continue
        ds = _DATE_START.match(line)
        if ds and (cur is None or cur.get("amount") is not None or not cur["texts"] or not any(cur["texts"])):
            dated_lines += 1
            cur = {"date": ds.group("date"), "valuta": ds.group("valuta"), "texts": [ds.group("rest") or ""],
                   "amount": None, "balance": None}
            records.append(cur)
            continue
        if cur is None:
            continue
        if cur["amount"] is None:
            ao = _AMT_ONLY.match(line) or _AMT_END.match(line)
            if ao:
                cur["amount"] = _amount(ao.group("amt"))
                if "text" in ao.groupdict() and ao.group("text"):
                    cur["texts"].append(ao.group("text"))
                continue
            if ds and not cur.get("valuta"):
                cur["valuta"] = ds.group("date")
                if ds.group("rest"):
                    cur["texts"].append(ds.group("rest"))
                continue
        elif cur["balance"] is None and _AMT_ONLY.match(line) and cur.get("multiline_done") is None:
            cur["balance"] = _amount(line)
            cur["multiline_done"] = True
            continue
        cur["texts"].append(line)

    records = [r for r in records if r["amount"] is not None]
    if not records:
        return None
    # two trailing numbers: decide which one is the booking amount via the balance chain
    _fix_amount_vs_balance(records)
    for r in records:
        booking = parse_date(r["date"], default_year=year_hint)
        if booking is None:
            continue
        texts = [squash(t) for t in r["texts"] if squash(t)]
        cp = texts[0] if texts else None
        purpose = " ".join(texts[1:]) if len(texts) > 1 else None
        if cp and len(texts) == 1 and "  " in r["texts"][0]:
            parts = [p.strip() for p in re.split(r"\s{2,}", r["texts"][0]) if p.strip()]
            if len(parts) > 1:
                cp, purpose = parts[0], " ".join(parts[1:])
        d = cash_line_draft(booking, r["amount"], counterparty=cp, purpose=purpose,
                            value_date=parse_date(r["valuta"], default_year=year_hint) if r["valuta"] else None,
                            confidence=CONFIDENCE)
        d.payload["_doc_type"] = "account_statement"
        res.drafts.append(d)
    if not res.drafts:
        return None
    last = max(d.payload["booking_date"] for d in res.drafts)
    for bdate, bal in balances[-1:]:
        if bal is not None:
            res.drafts.append(balance_draft(bdate or last, bal, confidence=CONFIDENCE))
    res.coverage = dated_lines / max(1, sum(1 for ln in lines if ln))
    return res


def _fix_amount_vs_balance(records: list[dict]) -> None:
    with_bal = [r for r in records if r["balance"] is not None]
    if len(with_bal) < 2:
        return
    ok = 0
    for a, b in zip(with_bal, with_bal[1:]):
        if abs((a["balance"] + b["amount"]) - b["balance"]) <= Decimal("0.01") or \
                abs((b["balance"] + a["amount"]) - a["balance"]) <= Decimal("0.01"):
            ok += 1
    if ok >= max(1, (len(with_bal) - 1) // 2):
        return
    # maybe the columns are (debit, credit)-less "balance amount" order -> swap and re-check
    swapped = 0
    for a, b in zip(with_bal, with_bal[1:]):
        if abs((a["amount"] + b["balance"]) - b["amount"]) <= Decimal("0.01") or \
                abs((b["amount"] + a["balance"]) - a["amount"]) <= Decimal("0.01"):
            swapped += 1
    if swapped > ok:
        for r in with_bal:
            r["amount"], r["balance"] = r["balance"], r["amount"]


def parse_csv(ctx: DocContext) -> ParseResult | None:
    return parse_bank_csv(ctx.text, parser=f"{NAME}.csv", confidence=0.85)


def parse_security(ctx: DocContext) -> ParseResult | None:
    d = gs.parse_security(ctx.text, parser=NAME, confidence=0.8)
    if not d:
        return None
    return ParseResult(parser=f"{NAME}.security", drafts=[d], account_role="brokerage")
