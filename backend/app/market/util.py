"""Small shared helpers for the market module (pure functions, no I/O)."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# Yahoo quotes some listings in minor units (London: pence). factor -> major currency.
MINOR_CURRENCIES: dict[str, tuple[str, int]] = {
    "GBp": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100), "ZAc": ("ZAR", 100), "ZAC": ("ZAR", 100),
}


def normalize_currency(currency: str | None) -> tuple[str | None, int]:
    """Return (major currency, divisor). ("GBp") -> ("GBP", 100)."""
    if not currency:
        return None, 1
    if currency in MINOR_CURRENCIES:
        return MINOR_CURRENCIES[currency]
    return currency.upper()[:3], 1


def to_float(value: Any) -> float | None:
    """Defensive float conversion: None/NaN/inf/garbage -> None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def to_decimal(value: Any, places: int | None = None) -> Decimal | None:
    f = to_float(value)
    if f is None:
        return None
    try:
        d = Decimal(repr(f))
    except InvalidOperation:
        return None
    if places is not None:
        d = round(d, places)
    return d


def to_date(value: Any) -> date | None:
    """Convert pandas Timestamp / datetime / date / epoch seconds / ISO string to a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime().date()
        except Exception:  # noqa: BLE001  (NaT etc.)
            return None
    if isinstance(value, (int, float)):
        f = to_float(value)
        if f is None or f <= 0:
            return None
        try:
            return (datetime(1970, 1, 1) + timedelta(seconds=f)).date()
        except (OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


_NUM_RE = re.compile(r"[^0-9,.\-eE+]")


def parse_number(value: Any) -> float | None:
    """Parse numbers in German ("1.234,56") or English ("1,234.56") notation, with % etc."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return to_float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not s or s in {"-", "--", "n/a", "N/A", "nan"}:
        return None
    s = _NUM_RE.sub("", s)
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):   # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                              # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # only comma: decimal comma unless it looks like thousands grouping (1,234,567)
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:               # 1.234.567
        s = s.replace(".", "")
    try:
        return to_float(float(s))
    except ValueError:
        return None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "mär": 3, "mrz": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
}


def parse_date_loose(value: Any) -> date | None:
    """Parse '31.10.2024', '31.Okt.2024', 'Nov 08, 2024', '08/Nov/2024', '2024-11-08', '08-Nov-2024', '20241108'."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)) or hasattr(value, "to_pydatetime"):
        return to_date(value)
    s = str(value).strip().strip('"').strip()
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", s)
    if m:
        return _mk(int(m[3]), int(m[2]), int(m[1]))
    m = re.search(r"(\d{1,2})[./\-\s]+([A-Za-zäÄ]{3,9})\.?[./\-\s,]+(\d{4})", s)   # 31.Okt.2024 / 08/Nov/2024
    if m and m[2][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[2][:3].lower()], int(m[1]))
    m = re.search(r"([A-Za-zäÄ]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)             # Nov 08, 2024
    if m and m[1][:3].lower() in _MONTHS:
        return _mk(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)                              # dd/mm/yyyy (European default)
    if m:
        d, mo = int(m[1]), int(m[2])
        if mo > 12 and d <= 12:
            d, mo = mo, d
        return _mk(int(m[3]), mo, d)
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    return None


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------------------------------------------------------------- name / symbol normalisation
_SUFFIX_TOKENS = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COMPANIES", "LTD", "LIMITED", "PLC", "AG", "SE",
    "SA", "NV", "N.V", "SPA", "AB", "ASA", "OYJ", "AS", "A/S", "KGAA", "KG", "GMBH", "LLC", "LP", "HOLDING",
    "HOLDINGS", "HLDGS", "GROUP", "GRP", "CLASS", "CL", "ADR", "ADS", "GDR", "REG", "REGISTERED", "SHS", "SHARES",
    "ORD", "ORDINARY", "COMMON", "STOCK", "NAMENS", "AKTIEN", "INHABER", "VINKULIERTE", "NA", "ON", "O.N", "N",
    "THE", "DR", "PREF", "VZ", "ST", "STAMM", "VORZUG", "UNSPONSORED", "SPONSORED", "TRUST", "REIT", "NPV", "DL",
    "EO", "A", "B", "C", "SERIES", "CAP", "NON", "VOTING", "BEARER", "AND", "CIE", "SOCIETE", "ANONYME", "TBK", "PT",
    "BHD", "PCL", "JSC", "PJSC", "OJSC", "SAB", "DE", "CV", "S", "SPON", "USD", "EUR", "O", "NAM", "INH", "AKT",
    "ADRS", "NAMENSAKTIEN", "INHABERAKTIEN", "STAMMAKTIEN", "VORZUGSAKTIEN", "SHS", "YC", "LS", "SF", "RC", "CL",
}


def _ascii_upper(text: str) -> str:
    text = text.replace("ß", "ss").replace("&", " AND ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.upper()


def normalize_name(name: str | None) -> str:
    """Company key for fuzzy matching: 'MICROSOFT CORP' == 'Microsoft Corporation' -> 'MICROSOFT'.

    Share classes collapse deliberately ('ALPHABET INC CLASS A' / '... CLASS C' -> 'ALPHABET').
    """
    if not name:
        return ""
    s = _ascii_upper(name)
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    tokens = [t for t in s.split() if t]
    # broker spellings: "MICROSOFT CORP. REGISTERED SHARES DL-,00000625" -> drop nominal values / pure numbers
    kept = [t for t in tokens if t not in _SUFFIX_TOKENS and not t.isdigit()]
    if not kept:
        kept = tokens
    return " ".join(kept)


def normalize_symbol(symbol: str | None) -> str:
    """'MSFT' / 'BRK.B' / 'BRK-B' / 'SAP.DE' -> 'MSFT' / 'BRKB' / 'BRKB' / 'SAP' (exchange suffix stripped)."""
    if not symbol:
        return ""
    s = symbol.strip().upper()
    m = re.fullmatch(r"([A-Z0-9\-]+)\.([A-Z]{1,3})", s)
    if m and not (len(m[2]) == 1 and len(m[1]) <= 4 and m[2] in "ABC"):   # keep BRK.B share class
        s = m[1]
    return re.sub(r"[^A-Z0-9]", "", s)


ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def is_isin(text: str | None) -> bool:
    return bool(text) and bool(ISIN_RE.match(text.strip().upper()))


# ---------------------------------------------------------------- sectors / countries (canonical English labels)
_SECTOR_MAP = {
    "technology": "Information Technology", "information technology": "Information Technology", "it": "Information Technology",
    "informationstechnologie": "Information Technology", "technologie": "Information Technology",
    "financial services": "Financials", "financials": "Financials", "financial": "Financials", "finanzen": "Financials",
    "finanzwesen": "Financials", "financial_services": "Financials",
    "healthcare": "Health Care", "health care": "Health Care", "gesundheit": "Health Care", "gesundheitswesen": "Health Care",
    "gesundheitsversorgung": "Health Care",
    "consumer cyclical": "Consumer Discretionary", "consumer discretionary": "Consumer Discretionary",
    "zyklische konsumgüter": "Consumer Discretionary", "nicht-basiskonsumgüter": "Consumer Discretionary",
    "zyklischer konsum": "Consumer Discretionary", "consumer_cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples", "consumer staples": "Consumer Staples", "basiskonsumgüter": "Consumer Staples",
    "nichtzyklische konsumgüter": "Consumer Staples", "consumer_defensive": "Consumer Staples",
    "communication services": "Communication Services", "communication": "Communication Services",
    "kommunikation": "Communication Services", "kommunikationsdienste": "Communication Services",
    "telecommunication services": "Communication Services", "telekommunikation": "Communication Services",
    "communication_services": "Communication Services", "telecommunications": "Communication Services",
    "industrials": "Industrials", "industrie": "Industrials", "industriegüter": "Industrials",
    "energy": "Energy", "energie": "Energy",
    "basic materials": "Materials", "materials": "Materials", "roh-, hilfs- & betriebsstoffe": "Materials",
    "materialien": "Materials", "grundstoffe": "Materials", "basic_materials": "Materials", "werkstoffe": "Materials",
    "utilities": "Utilities", "versorger": "Utilities", "versorgungsbetriebe": "Utilities",
    "real estate": "Real Estate", "immobilien": "Real Estate", "realestate": "Real Estate", "real_estate": "Real Estate",
    "cash and/or derivatives": "Cash/Derivate", "cash und/oder derivate": "Cash/Derivate", "cash": "Cash/Derivate",
    "other": "Sonstige", "sonstige": "Sonstige", "andere": "Sonstige",
}

SECTOR_LABELS_DE = {
    "Information Technology": "Informationstechnologie", "Financials": "Finanzen", "Health Care": "Gesundheit",
    "Consumer Discretionary": "Zyklischer Konsum", "Consumer Staples": "Basiskonsum",
    "Communication Services": "Kommunikation", "Industrials": "Industrie", "Energy": "Energie",
    "Materials": "Grundstoffe", "Utilities": "Versorger", "Real Estate": "Immobilien",
    "Cash/Derivate": "Cash/Derivate", "Sonstige": "Sonstige", "Unbekannt": "Unbekannt",
}


def normalize_sector(sector: str | None) -> str | None:
    if not sector or not str(sector).strip() or str(sector).strip() in {"-", "--"}:
        return None
    s = str(sector).strip()
    return _SECTOR_MAP.get(s.lower(), s)


_COUNTRY_MAP = {
    "vereinigte staaten": "United States", "usa": "United States", "us": "United States", "u.s.": "United States",
    "united states of america": "United States", "vereinigte staaten von amerika": "United States",
    "deutschland": "Germany", "de": "Germany", "frankreich": "France", "fr": "France", "schweiz": "Switzerland",
    "ch": "Switzerland", "vereinigtes königreich": "United Kingdom", "großbritannien": "United Kingdom",
    "grossbritannien": "United Kingdom", "uk": "United Kingdom", "gb": "United Kingdom", "niederlande": "Netherlands",
    "nl": "Netherlands", "japan": "Japan", "jp": "Japan", "kanada": "Canada", "ca": "Canada", "australien": "Australia",
    "au": "Australia", "irland": "Ireland", "ie": "Ireland", "spanien": "Spain", "es": "Spain", "italien": "Italy",
    "it": "Italy", "schweden": "Sweden", "se": "Sweden", "dänemark": "Denmark", "dk": "Denmark", "norwegen": "Norway",
    "no": "Norway", "finnland": "Finland", "fi": "Finland", "belgien": "Belgium", "be": "Belgium",
    "österreich": "Austria", "at": "Austria", "china": "China", "cn": "China", "indien": "India", "in": "India",
    "taiwan": "Taiwan", "tw": "Taiwan", "südkorea": "South Korea", "korea": "South Korea", "kr": "South Korea",
    "korea (south)": "South Korea", "republic of korea": "South Korea", "korea, republic of": "South Korea",
    "brasilien": "Brazil", "br": "Brazil", "hongkong": "Hong Kong", "hk": "Hong Kong", "singapur": "Singapore",
    "sg": "Singapore", "israel": "Israel", "il": "Israel", "portugal": "Portugal", "pt": "Portugal",
    "neuseeland": "New Zealand", "nz": "New Zealand", "südafrika": "South Africa", "za": "South Africa",
    "mexiko": "Mexico", "mx": "Mexico", "luxemburg": "Luxembourg", "lu": "Luxembourg", "polen": "Poland",
    "pl": "Poland", "saudi-arabien": "Saudi Arabia", "sa": "Saudi Arabia", "europäische union": "European Union",
}


def normalize_country(country: str | None) -> str | None:
    if not country or not str(country).strip() or str(country).strip() in {"-", "--"}:
        return None
    s = str(country).strip()
    return _COUNTRY_MAP.get(s.lower(), s)


def next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d
