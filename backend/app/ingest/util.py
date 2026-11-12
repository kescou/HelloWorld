"""German number/date/ISIN helpers shared by parsers, LLM normalisation and validation."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------------- text

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_SPACES = dict.fromkeys(map(ord, "     "), " ")


def normalize_text(text: str) -> str:
    """Unify exotic dashes/spaces, line endings; strip trailing blanks. Keeps \\f page breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    text = text.translate(_DASHES).translate(_SPACES).replace("​", "")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def squash(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# --------------------------------------------------------------------------- numbers

# 1.234,56 | 1234,56 | 0,1234 | 12 (integers only where the caller expects them)
NUM_DE = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
MONEY_DE = r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}"
CUR = r"EUR|USD|GBP|GBX|CHF|JPY|CAD|AUD|SEK|NOK|DKK|HKD|PLN|SGD|€|\$"

_CUR_MAP = {"€": "EUR", "$": "USD", "EURO": "EUR", "GBX": "GBP"}


def norm_currency(cur: str | None, default: str = "EUR") -> str:
    if not cur:
        return default
    cur = cur.strip().upper()
    return _CUR_MAP.get(cur, cur)


def parse_decimal(value, *, style: str = "auto") -> Decimal | None:
    """Parse '1.234,56', '-85,00', '85,00-', '+ 2.500,00', '1,234.56', '12,5 %', 3.5 ...

    style: 'de' (comma decimal), 'en' (dot decimal) or 'auto' (guess; German wins on ties,
    i.e. '1.234' -> 1234 and '1,234' -> 1.234).
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.translate(_DASHES).translate(_SPACES)
    neg = False
    if re.search(r"\d\s*-\s*(?:[A-Z€$]{0,3})?$", s) or re.match(r"^\(.*\)$", s):
        neg = True
    if re.search(r"(?<![A-Za-z])S$", s.strip()) and re.search(r"\d\s*S$", s.strip()):
        neg = True  # 85,00 S (Soll)
    if re.match(r"^\s*(?:[A-Z€$]{0,3}\s*)?-", s):
        neg = True
    m = re.search(r"\d[\d.,' ]*", s)
    if not m:
        return None
    num = m.group(0).replace(" ", "").replace("'", "").rstrip(".,")
    has_c, has_d = "," in num, "." in num
    if style == "de":
        num = num.replace(".", "").replace(",", ".")
    elif style == "en":
        num = num.replace(",", "")
    elif has_c and has_d:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif has_c:
        num = num.replace(",", ".") if num.count(",") == 1 else num.replace(",", "")
    elif has_d:
        # '1.234' / '12.345.678' -> thousands; '12.5' / '0.123' / '1.2345' -> decimal
        if num.count(".") > 1 or re.fullmatch(r"[1-9]\d{0,2}\.\d{3}", num):
            num = num.replace(".", "")
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    return -d if neg else d


def q2(d: Decimal | None) -> Decimal | None:
    return None if d is None else d.quantize(Decimal("0.01"))


def to_float(d) -> float | None:
    if d is None:
        return None
    return float(d)


_AMOUNT_TOKEN = re.compile(
    rf"(?P<pre>(?:{CUR})\s*)?(?P<sign>[-+]\s?)?(?P<num>\d{{1,3}}(?:\.\d{{3}})+,\d{{1,8}}|\d+,\d{{1,8}})"
    rf"(?P<trail>\s?-(?!\d))?(?P<post>\s*(?:{CUR})(?![A-Za-z]))?(?P<pct>\s?%)?"
)


def line_amounts(line: str) -> list[tuple[Decimal, str | None]]:
    """All German decimal tokens on a line with sign and adjacent currency; percentages skipped."""
    out: list[tuple[Decimal, str | None]] = []
    for m in _AMOUNT_TOKEN.finditer(line):
        if m.group("pct"):
            continue
        # skip dates like 01,02 never happen; skip tokens glued to letters (e.g. 'DL-,001')
        start = m.start("num")
        if start > 0 and (line[start - 1].isalnum() or line[start - 1] == ","):
            continue
        d = parse_decimal(m.group("num"), style="de")
        if d is None:
            continue
        sign = (m.group("sign") or "").strip()
        if sign == "-" or m.group("trail"):
            d = -d
        cur = (m.group("post") or m.group("pre") or "").strip() or None
        out.append((d, norm_currency(cur) if cur else None))
    return out


def label_amount(text: str, label: str, *, pick: int = -1, flags: int = re.I) -> tuple[Decimal, str | None] | None:
    """Amount on the first line matching ``label`` (regex). ``pick`` = index into the amounts."""
    rx = re.compile(label, flags)
    for line in text.split("\n"):
        m = rx.search(line)
        if not m:
            continue
        amounts = line_amounts(line[m.end():]) or line_amounts(line)
        if amounts:
            try:
                return amounts[pick]
            except IndexError:
                return amounts[-1]
    return None


def label_amounts_all(text: str, label: str, *, flags: int = re.I) -> list[tuple[Decimal, str | None, str]]:
    """Last amount of EVERY line matching label -> [(amount, currency, line)]."""
    rx = re.compile(label, flags)
    out = []
    for line in text.split("\n"):
        m = rx.search(line)
        if m:
            amounts = line_amounts(line[m.end():])
            if amounts:
                out.append((amounts[-1][0], amounts[-1][1], line))
    return out


# --------------------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1, "januar": 1, "january": 1, "feb": 2, "februar": 2, "february": 2,
    "mär": 3, "mrz": 3, "märz": 3, "maerz": 3, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dez": 12, "dezember": 12, "dec": 12, "december": 12,
}
MONTH_WORD = r"Jan(?:uar)?|Feb(?:ruar)?|M[äa]r(?:z)?|Mrz|Apr(?:il)?|Mai|May|Juni?|Juli?|Aug(?:ust)?|Sept?(?:ember)?|Okt(?:ober)?|Oct|Nov(?:ember)?|Dez(?:ember)?|Dec"
DATE_DE = r"\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d)"
_DATE_WORD = re.compile(rf"(\d{{1,2}})\.?\s+({MONTH_WORD})\.?\s+(\d{{4}})", re.I)


def month_number(word: str) -> int | None:
    return _MONTHS.get(word.strip().rstrip(".").lower())


def parse_date(value, *, default_year: int | None = None) -> date | None:
    """'02.01.2023', '2.1.23', '2023-01-02', '02 Jan. 2024', '2. Januar 2024', '02.01.' (+year)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)", s)
        if m:
            y = int(m.group(3))
            if y < 100:
                y += 2000 if y < 70 else 1900
            return date(y, int(m.group(2)), int(m.group(1)))
        m = _DATE_WORD.search(s)
        if m:
            mon = month_number(m.group(2))
            if mon:
                return date(int(m.group(3)), mon, int(m.group(1)))
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:  # dd/mm/yyyy (European)
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.?(?!\d)", s)
        if m and default_year:
            return date(default_year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# --------------------------------------------------------------------------- ISIN / WKN / IBAN

ISIN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}\d)(?![A-Z0-9])")
IBAN_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?)(?![A-Z0-9])")
WKN_RE = re.compile(r"WKN[:\s/]*\(?([A-Z0-9]{6})\)?")


def isin_valid(isin: str | None) -> bool:
    if not isin or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_isins(text: str) -> list[str]:
    seen: list[str] = []
    for m in ISIN_RE.finditer(text):
        c = m.group(1)
        if isin_valid(c) and c not in seen:
            seen.append(c)
    return seen


def find_isin(text: str) -> str | None:
    found = find_isins(text)
    return found[0] if found else None


def find_wkn(text: str) -> str | None:
    m = WKN_RE.search(text)
    return m.group(1) if m else None


def norm_iban(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", "", s).upper()
    return s if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s) else None


def find_ibans(text: str) -> list[str]:
    out = []
    for m in IBAN_RE.finditer(text):
        iban = norm_iban(m.group(1))
        if iban and 15 <= len(iban) <= 34 and iban not in out:
            # German IBANs have exactly 22 chars; trim over-greedy matches
            if iban.startswith("DE") and len(iban) > 22:
                iban = iban[:22]
            if iban not in out:
                out.append(iban)
    return out
