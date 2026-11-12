"""Cheap keyword hints for household-book categories and cash-line typing (giro statements)."""
from __future__ import annotations

import re
from decimal import Decimal

# (category name, regex) – first hit wins. Names are German display names; on commit they are
# matched case-insensitively against `categories.name` (and categories.match_rules as fallback).
_RULES: list[tuple[str, str]] = [
    ("Gehalt", r"\b(lohn|gehalt|bez[üu]ge|verg[üu]tung|entgeltabrechnung|salary)\b"),
    ("Rente", r"\b(rente|pension|deutsche rentenversicherung)\b"),
    ("Kindergeld", r"kindergeld|familienkasse"),
    ("Wertpapiere", r"wertpapier|wp-abrechnung|wp-kauf|wp-verkauf|sparplan|depot|trade republic|scalable|baader|dividende|ertr[äa]gnis|aussch[üu]ttung"),
    ("Miete", r"\bmiete\b|kaltmiete|warmmiete|nebenkosten|hausverwaltung|wohnungsbau|vonovia|hausgeld"),
    ("Strom & Gas", r"stadtwerke|\w*strom\b|\bgas\b|energie|vattenfall|e\.on|eon energie|enbw|rwe|eprimo|yello|lichtblick|fernw[äa]rme"),
    ("Internet & Telefon", r"telekom|vodafone|\bo2\b|telefonica|1&1|1und1|congstar|freenet|kabel deutschland|unitymedia|mobilfunk|aldi talk"),
    ("Rundfunkbeitrag", r"rundfunk|beitragsservice|ard zdf"),
    ("Versicherungen", r"\w*versicherung|allianz|huk|axa|ergo|debeka|generali|signal iduna|techniker krankenkasse|\baok\b|barmer|\bdak\b|haftpflicht|hausrat"),
    ("Lebensmittel", r"rewe|edeka|aldi|lidl|netto|penny|kaufland|\bdm\b|dm-drogerie|rossmann|\w*b[äa]ckerei|supermarkt|alnatura|denns|tegut|globus|norma|hit markt|flaschenpost|picnic"),
    ("Restaurant & Lieferdienst", r"\w*restaurant|lieferando|wolt|uber ?eats|mcdonald|burger king|pizzeria|cafe|café|starbucks|imbiss|gastst[äa]tte"),
    ("Mobilität", r"\w*tankstelle|aral|shell|esso|\bjet\b|total ?energies|deutsche bahn|db vertrieb|\bbvg\b|\bmvg\b|\bhvv\b|\brmv\b|\bvrr\b|deutschlandticket|flixbus|tier mobility|share now|miles mobility|\badac\b|kfz|parkhaus|uber\b"),
    ("Shopping", r"amazon|amzn|zalando|otto\b|ebay|ikea|media ?markt|saturn|h&m|\bc&a\b|decathlon|obi\b|bauhaus|hornbach|zara|about you|temu|kleinanzeigen"),
    ("Abos & Streaming", r"netflix|spotify|disney|apple\.com/bill|apple services|google ?play|youtube|audible|dazn|prime video|sky deutschland|icloud|microsoft|adobe|openai|chatgpt"),
    ("Gesundheit", r"\w*apotheke|\w*arzt\b|\w*praxis|zahnarzt|klinik|physio|optiker|fielmann|krankenhaus"),
    ("Freizeit & Sport", r"fitness|mcfit|urban sports|kino|cinemaxx|theater|konzert|eventim|ticketmaster|museum|schwimmbad|verein"),
    ("Reisen", r"booking\.com|airbnb|lufthansa|ryanair|eurowings|easyjet|hotel|hostel|expedia|tui\b|check24 reise"),
    ("Bargeld", r"bargeldauszahlung|geldautomat|\batm\b|auszahlung ga|bargeld"),
    ("Kredit & Zinsen", r"darlehen|kreditrate|tilgung|sollzins|dispo|ratenkredit"),
    ("Steuern", r"finanzamt|steuerverwaltung|kfz-steuer|grundsteuer|einkommensteuer"),
    ("Bankgebühren", r"kontof[üu]hrung|entgelt|kartenpreis|kartenentgelt|jahresgeb[üu]hr|geb[üu]hr"),
    ("Spenden", r"spende|unicef|[äa]rzte ohne grenzen|rotes kreuz|greenpeace|wwf\b"),
    ("Kinder & Bildung", r"kita|kindergarten|schule|hort|studienbeitrag|semesterbeitrag|volkshochschule|udemy"),
    ("Umbuchung", r"umbuchung|[üu]bertrag|eigen[üu]bertrag|tagesgeld|extra-konto|sparkonto|sparen"),
    ("PayPal", r"paypal"),
]
_COMPILED = [(name, re.compile(r"(?<![a-zäöüß])(?:" + rx + ")", re.I)) for name, rx in _RULES]

_INTEREST_RX = re.compile(r"\b(?:haben)?zins(?:en|gutschrift|abschluss|zahlung|ertrag)?\b|interest payment|\binterest\b|abschluss.*zinsen", re.I)
_LOAN_RX = re.compile(r"darlehen|kredit|sollzins|dispo|[üu]berziehung", re.I)
_FEE_RX = re.compile(r"kontof[üu]hrung|entgelt(?!abrechnung)|kartenpreis|geb[üu]hr(?:en)?\b|\bfee\b", re.I)
_TAX_RX = re.compile(r"^(?:kapitalertrags?steuer|solidarit[äa]tszuschlag|kirchensteuer|steuern?\b|steuererstattung|steuerkorrektur|tax)", re.I)
SECURITIES_RX = re.compile(r"wertpapier(?:kauf|verkauf|gutschrift|abrechnung|ertr)|wp-?abrechnung|wp-?kauf|wp-?verkauf|"
                           r"dividendengutschrift|ertr[äa]gnisgutschrift|ertragsgutschrift|wertpapiersparplan", re.I)


def category_hint(*texts: str | None) -> str | None:
    hay = " ".join(t for t in texts if t)
    if not hay:
        return None
    for name, rx in _COMPILED:
        if rx.search(hay):
            return name
    return None


def cash_tx_type(text: str, amount: Decimal | None, booking_text: str | None = None) -> str:
    """deposit/withdrawal by sign, refined to interest/fee/tax via keywords."""
    head = (booking_text or "").strip() or text
    positive = amount is None or amount >= 0
    if _INTEREST_RX.search(head) and not _LOAN_RX.search(text):
        return "interest" if positive else "fee"
    if _TAX_RX.search(head.strip()):
        return "tax"
    if not positive and _FEE_RX.search(head) and not re.search(r"lastschrift|kartenzahlung|[üu]berweisung", head, re.I):
        return "fee"
    return "deposit" if positive else "withdrawal"
