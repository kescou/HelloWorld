"""Tier 3 – keyword classification: which institution, which kind of document."""
from __future__ import annotations

import re

INSTITUTIONS = ("trade_republic", "scalable", "ing", "postbank")

DOC_TYPES = (
    "trade_buy", "trade_sell", "savings_plan", "dividend", "interest", "tax_statement",
    "account_statement", "depot_statement", "csv_export", "unknown",
)

_INST_RULES: list[tuple[str, list[tuple[str, int]]]] = [
    ("trade_republic", [(r"trade\s*republic", 5), (r"TRBKDEBB", 5), (r"Brunnenstra(?:ß|ss)e\s*19", 2),
                        (r"Lang\s*&\s*Schwarz", 1), (r"traderepublic\.com", 3)]),
    ("scalable", [(r"scalable\s*capital", 5), (r"Baader\s*Bank", 4), (r"BDWBDEMM", 4), (r"SCALDE", 4),
                  (r"Weihenstephaner\s*Str", 2), (r"gettex", 1), (r"Seitzstr", 2)]),
    ("ing", [(r"ING-DiBa", 5), (r"INGDDEFF", 5), (r"\bING\b(?![A-Za-z-])", 2), (r"60628\s+Frankfurt", 3),
             (r"Theodor-Heuss-Allee\s*2", 2), (r"ing\.de", 3), (r"^Bank;ING", 5), (r"Umsatzanzeige;", 3)]),
    ("postbank", [(r"postbank", 5), (r"PBNKDEFF", 5), (r"Giro\s*(?:direkt|plus|extra\s*plus)", 3),
                  (r"Niederlassung der Deutsche Bank", 3), (r"Filial-/Kontonummer", 3)]),
]


def detect_institution(text: str, filename: str | None = None) -> str | None:
    hay = text[:20000]
    best, best_score = None, 0
    for slug, rules in _INST_RULES:
        score = sum(w for rx, w in rules if re.search(rx, hay, re.I | re.M))
        if filename:
            fn = filename.lower()
            if slug.replace("_", "") in fn.replace("_", "").replace("-", "").replace(" ", "") or (slug == "ing" and re.search(r"(?:^|[^a-z])ing(?:[^a-z]|diba)", fn)):
                score += 2
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 2 else None


_TYPE_RULES: list[tuple[str, str]] = [
    # order matters: most specific first
    ("tax_statement", r"Jahressteuerbescheinigung|Steuerbescheinigung|Erträgnisaufstellung|Jährlicher Steuerreport|Vorabpauschale"),
    ("interest", r"Abrechnung\s+Zinsen|Zinsabrechnung|Zinsgutschrift|Zinsabschluss|Abrechnung der Zinsen"),
    ("dividend", r"Dividendengutschrift|Ertragsgutschrift|Dividendenabrechnung|Ertragsmitteilung|Fondsaussch[üu]ttung|"
                 r"Ertr[äa]gnisgutschrift|Aussch[üu]ttung mit dem Ex-Tag|Dividende mit dem Ex-Tag|^\s*DIVIDENDE\s*$|^\s*AUSSCH[ÜU]TTUNG\s*$|"
                 r"Wertpapierertr[äa]ge|Kapitalma[ßs]nahme.*Bardividende"),
    ("savings_plan", r"Sparplanausf[üu]hrung|Wertpapierabrechnung\s+Sparplan|Kauf aus Sparplan|Ausf[üu]hrung von Sparplan|"
                     r"WERTPAPIERABRECHNUNG\s+SAVEBACK|WERTPAPIERABRECHNUNG\s+ROUND\s*UP|Sparplan-?Ausf[üu]hrung"),
    ("trade_sell", r"Wertpapierabrechnung:?\s+Verkauf|Verkauf am \d|Market-Order Verkauf|Limit-Order Verkauf|Stop-Market-Order Verkauf|"
                   r"Endbetrag zu Ihren Gunsten|Typ\s+Verkauf|Transaktionsart:?\s+Verkauf"),
    ("trade_buy", r"Wertpapierabrechnung:?\s+Kauf|Kauf am \d|Market-Order Kauf|Limit-Order Kauf|Endbetrag zu Ihren Lasten|"
                  r"Typ\s+Kauf|Transaktionsart:?\s+Kauf"),
    ("depot_statement", r"Depotauszug|Depotbestand|Depot[üu]bersicht|Jahresdepotauszug|Verm[öo]gens[üu]bersicht"),
    ("account_statement", r"Kontoauszug|Umsatz[üu]bersicht|Konto[üu]bersicht|Finanzstatus|Alter Saldo|Neuer Saldo|"
                          r"Alter Kontostand|Neuer Kontostand|Rechnungsabschluss|Ums[äa]tze"),
]


def detect_doc_type(text: str, source_kind: str = "text") -> str:
    if source_kind == "csv":
        return "csv_export"
    hay = text[:30000]
    for doc_type, rx in _TYPE_RULES:
        if re.search(rx, hay, re.I | re.M):
            # a trade confirmation that merely mentions "Sparplan" in small print stays a trade
            return doc_type
    return "unknown"


def classify(text: str, filename: str | None = None, source_kind: str = "text") -> tuple[str | None, str]:
    return detect_institution(text, filename), detect_doc_type(text, source_kind)
