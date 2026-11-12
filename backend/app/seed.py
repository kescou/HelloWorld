"""Seed data.

    python -m app.seed               # 7 initial accounts + German default categories (idempotent)
    python -m app.seed --demo        # additionally a realistic synthetic demo dataset (offline)
    python -m app.seed --reset-demo  # remove the demo dataset again

Demo rows are marked: transactions via notes == DEMO_NOTE, market rows via source == "demo";
the ids of everything else are remembered in settings["demo_seed"].
"""
from __future__ import annotations

import argparse
import calendar
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.core import (
    Account, AccountType, Category, CategoryKind, FxRate, Instrument, InstrumentType, Setting,
    Transaction, TxType,
)
from app.models.market import EtfHolding, EventType, Fundamentals, InstrumentEvent, PriceHistory, Quote
from app.services.ledger import dedupe_hash, q2

DEMO_NOTE = "[demo]"
DEMO_SOURCE = "demo"
DEMO_SETTING = "demo_seed"
TAX_RATE = Decimal("0.26375")

# --------------------------------------------------------------------------- base data
ACCOUNTS = [
    # name, institution, type, settlement, color, interest
    ("Postbank Giro", "postbank", AccountType.checking, None, "#FFCC00", None),
    ("ING Girokonto", "ing", AccountType.checking, None, "#FF6200", None),
    ("ING Depot", "ing", AccountType.brokerage, "ING Girokonto", "#D9480F", None),
    ("Scalable Depot", "scalable", AccountType.brokerage, "Scalable Tagesgeld", "#28EBCF", None),
    ("Scalable Tagesgeld", "scalable", AccountType.savings, None, "#12B8A0", Decimal("2.000")),
    ("Trade Republic Depot", "trade_republic", AccountType.brokerage, "Trade Republic Tagesgeld", "#3C3C3C", None),
    ("Trade Republic Tagesgeld", "trade_republic", AccountType.savings, None, "#8A8A8A", Decimal("2.000")),
]

E, I, T, V = CategoryKind.expense, CategoryKind.income, CategoryKind.transfer, CategoryKind.investment
# (name, kind, color, rules, children[(name, rules)])
CATEGORIES: list[tuple[str, CategoryKind, str, list[str], list[tuple[str, list[str]]]]] = [
    ("Wohnen", E, "#4C6EF5", ["Hausverwaltung", "Wohnungsgesellschaft", "Vonovia"], [
        ("Miete", ["Miete", "Kaltmiete", "Warmmiete"]),
        ("Strom & Nebenkosten", ["Stadtwerke", "Vattenfall", "E.ON", "EnBW", "Nebenkosten", "Abschlag Strom", "Abschlag Gas"]),
        ("Internet & Telefon", ["Vodafone", "Telekom", "O2", "Telefonica", "1&1", "congstar", "Aldi Talk"]),
        ("Rundfunkbeitrag", ["Rundfunk", "ARD ZDF", "Beitragsservice"]),
        ("Einrichtung", ["IKEA", "OBI", "Bauhaus", "Hornbach"]),
    ]),
    ("Lebensmittel", E, "#40C057", [
        "REWE", "EDEKA", "ALDI", "LIDL", "PENNY", "NETTO", "KAUFLAND", "NORMA", "Denns", "Alnatura", "Bäckerei",
        "Baeckerei", "Metzgerei", "Getränke", "tegut", "Globus", "Flink", "Picnic",
    ], []),
    ("Mobilität", E, "#FAB005", [], [
        ("Tanken", ["ARAL", "Shell", "ESSO", "JET Tank", "TotalEnergies", "Tankstelle", "AVIA"]),
        ("ÖPNV & Bahn", ["DB Vertrieb", "Deutsche Bahn", "Deutschlandticket", "BVG", "MVG", "HVV", "KVB", "RMV", "VRR", "FlixBus"]),
        ("Auto", ["ATU", "Kfz-Werkstatt", "TÜV", "DEKRA", "Parkhaus", "Kfz-Steuer", "ADAC"]),
        ("Sharing & Taxi", ["Uber", "FREE NOW", "Share Now", "Miles Mobility", "TIER", "Lime", "Bolt"]),
    ]),
    ("Versicherungen", E, "#7950F2", [
        "Versicherung", "HUK", "Allianz Versicherung", "Allianz Leben", "AXA", "ERGO", "Generali", "DEVK", "Debeka", "LVM",
        "Signal Iduna", "R+V", "Gothaer", "CosmosDirekt", "Haftpflicht",
    ], []),
    ("Freizeit", E, "#F06595", [], [
        ("Restaurant & Café", ["Restaurant", "Lieferando", "Wolt", "Uber Eats", "McDonald", "Burger King", "Café", "Cafe", "Pizzeria", "Starbucks", "Bar"]),
        ("Reisen", ["Booking.com", "Airbnb", "Lufthansa", "Ryanair", "Eurowings", "easyJet", "Hotel", "TUI", "Expedia"]),
        ("Sport & Fitness", ["FitX", "McFit", "Fitness", "Urban Sports", "Decathlon", "Sportverein"]),
        ("Kultur & Unterhaltung", ["Kino", "Cinemaxx", "Eventim", "Ticketmaster", "Theater", "Museum", "Steam", "PlayStation", "Nintendo"]),
    ]),
    ("Abos", E, "#15AABF", [
        "Netflix", "Spotify", "Disney+", "Disney Plus", "Amazon Prime", "Prime Video", "Apple.com/bill", "iCloud",
        "YouTube Premium", "DAZN", "Audible", "Sky", "WOW", "Zeit Online", "Spiegel", "FAZ", "ChatGPT", "OpenAI",
        "Google One", "Microsoft 365", "Adobe",
    ], []),
    ("Gesundheit", E, "#E64980", [
        "Apotheke", "Arzt", "Hausarzt", "Zahnarzt", "Praxis", "Krankenhaus", "Physiotherapie", "Optiker", "Fielmann", "DocMorris",
        "Shop Apotheke",
    ], []),
    ("Shopping", E, "#FD7E14", ["Amazon", "AMZN", "PayPal", "eBay", "Otto"], [
        ("Kleidung", ["Zalando", "H&M", "C&A", "Zara", "About You", "Peek & Cloppenburg", "Uniqlo"]),
        ("Elektronik", ["MediaMarkt", "Media Markt", "Saturn", "Apple Store", "Cyberport", "Alternate", "notebooksbilliger"]),
        ("Drogerie", ["dm-drogerie", "DM Drogerie", "Rossmann", "Müller Drogerie", "Douglas"]),
    ]),
    ("Bildung", E, "#845EF7", ["Thalia", "Hugendubel", "Udemy", "Coursera", "Volkshochschule", "VHS", "Semesterbeitrag"], []),
    ("Kinder & Familie", E, "#FF8787", ["Kita", "Kindergarten", "Schule", "Spielwaren", "Smyths"], []),
    ("Bargeld", E, "#868E96", ["Geldautomat", "Bargeldauszahlung", "Auszahlung GA", "ATM"], []),
    ("Steuern & Gebühren", E, "#495057", [
        "Kontoführung", "Entgelt", "Kartenpreis", "Depotgebühr", "Steuerberater", "Lohnsteuerhilfe", "Bundeskasse",
    ], []),
    ("Spenden", E, "#94D82D", ["Spende", "UNICEF", "Ärzte ohne Grenzen", "Rotes Kreuz", "WWF", "Greenpeace", "Wikimedia"], []),
    ("Sonstiges", E, "#ADB5BD", [], []),
    ("Gehalt", I, "#2F9E44", ["Gehalt", "Lohn", "Bezüge", "Entgeltabrechnung", "Verdienstabrechnung"], []),
    ("Kapitalerträge", I, "#1098AD", [], [
        ("Dividenden", ["Dividende", "Ausschüttung", "Ertragsgutschrift"]),
        ("Zinsen", ["Zinsen", "Zinsgutschrift", "Habenzinsen", "Zinsabschluss"]),
    ]),
    ("Erstattungen", I, "#74B816", ["Finanzamt", "Steuererstattung", "Erstattung", "Rückerstattung", "Gutschrift Retoure"], []),
    ("Sonstige Einnahmen", I, "#A9E34B", ["Kindergeld", "Familienkasse", "Elterngeld", "Rente", "Bonus"], []),
    ("Umbuchung", T, "#CED4DA", ["Umbuchung", "Übertrag", "Uebertrag", "Eigene Konten"], []),
    ("Sparplan & Wertpapiere", V, "#364FC7", ["Sparplan", "Wertpapierkauf", "Wertpapierabrechnung", "Scalable Capital", "Trade Republic"], []),
]


def seed_accounts(db: Session) -> dict[str, Account]:
    existing = {a.name: a for a in db.scalars(select(Account))}
    for order, (name, institution, type_, _settle, color, rate) in enumerate(ACCOUNTS):
        if name not in existing:
            acc = Account(
                name=name, institution=institution, type=type_, currency="EUR", color=color,
                opening_balance=Decimal("0"), interest_rate=rate, is_active=True, sort_order=order * 10,
            )
            db.add(acc)
            existing[name] = acc
    db.flush()
    for name, _inst, _type, settle, _color, _rate in ACCOUNTS:
        acc = existing[name]
        if settle and acc.settlement_account_id is None:
            acc.settlement_account_id = existing[settle].id
    db.flush()
    return existing


def seed_categories(db: Session) -> dict[str, Category]:
    existing = {(c.parent_id, c.name): c for c in db.scalars(select(Category))}
    out: dict[str, Category] = {}

    def ensure(name: str, kind: CategoryKind, parent: Category | None, color: str | None, rules: list[str]) -> Category:
        key = (parent.id if parent else None, name)
        cat = existing.get(key)
        if cat is None:
            cat = Category(name=name, kind=kind, parent_id=key[0], color=color, match_rules=rules or None)
            db.add(cat)
            db.flush()
            existing[key] = cat
        out[name] = cat
        return cat

    for name, kind, color, rules, children in CATEGORIES:
        parent = ensure(name, kind, None, color, rules)
        for child_name, child_rules in children:
            ensure(child_name, kind, parent, color, child_rules)
    return out


def seed_base(db: Session) -> dict:
    accounts = seed_accounts(db)
    categories = seed_categories(db)
    db.flush()
    return {"accounts": accounts, "categories": categories}


# --------------------------------------------------------------------------- demo definitions
#: isin, symbol, name, type, currency, sector, country, anchor price, annual drift, annual vol, extra
DEMO_INSTRUMENTS = [
    dict(isin="IE00B4L5Y983", symbol="EUNL.DE", wkn="A0RPWH", name="iShares Core MSCI World UCITS ETF USD (Acc)",
         type=InstrumentType.etf, currency="EUR", sector=None, country=None, anchor=108.4, drift=0.10, vol=0.13,
         exchange="XETRA", fund_provider="ishares", ter=Decimal("0.0020"), distribution="accumulating"),
    dict(isin="IE00BK5BQT80", symbol="VWCE.DE", wkn="A2PKXG", name="Vanguard FTSE All-World UCITS ETF USD (Acc)",
         type=InstrumentType.etf, currency="EUR", sector=None, country=None, anchor=141.9, drift=0.095, vol=0.125,
         exchange="XETRA", fund_provider="vanguard", ter=Decimal("0.0019"), distribution="accumulating"),
    dict(isin="DE0008404005", symbol="ALV.DE", wkn="840400", name="Allianz SE", type=InstrumentType.stock,
         currency="EUR", sector="Financial Services", country="Germany", anchor=352.0, drift=0.14, vol=0.19,
         exchange="XETRA", industry="Insurance - Diversified", website="https://www.allianz.com",
         ir_url="https://www.allianz.com/de/investor_relations.html"),
    dict(isin="US5949181045", symbol="MSFT", wkn="870747", name="Microsoft Corporation", type=InstrumentType.stock,
         currency="USD", sector="Technology", country="United States", anchor=497.0, drift=0.16, vol=0.24,
         exchange="NASDAQ", industry="Software - Infrastructure", website="https://www.microsoft.com",
         ir_url="https://www.microsoft.com/en-us/investor"),
    dict(isin="US0378331005", symbol="AAPL", wkn="865985", name="Apple Inc.", type=InstrumentType.stock,
         currency="USD", sector="Technology", country="United States", anchor=262.0, drift=0.12, vol=0.26,
         exchange="NASDAQ", industry="Consumer Electronics", website="https://www.apple.com",
         ir_url="https://investor.apple.com"),
    dict(isin="DE0007164600", symbol="SAP.DE", wkn="716460", name="SAP SE", type=InstrumentType.stock,
         currency="EUR", sector="Technology", country="Germany", anchor=228.0, drift=0.17, vol=0.25,
         exchange="XETRA", industry="Software - Application", website="https://www.sap.com",
         ir_url="https://www.sap.com/investors/de.html"),
    dict(isin="US7561091049", symbol="O", wkn="899744", name="Realty Income Corporation", type=InstrumentType.stock,
         currency="USD", sector="Real Estate", country="United States", anchor=58.6, drift=0.02, vol=0.18,
         exchange="NYSE", industry="REIT - Retail", website="https://www.realtyincome.com",
         ir_url="https://www.realtyincome.com/investors"),
    dict(isin="DE0008430026", symbol="MUV2.DE", wkn="843002", name="Münchener Rückversicherungs-Gesellschaft AG",
         type=InstrumentType.stock, currency="EUR", sector="Financial Services", country="Germany", anchor=548.0,
         drift=0.15, vol=0.20, exchange="XETRA", industry="Insurance - Reinsurance",
         website="https://www.munichre.com", ir_url="https://www.munichre.com/de/unternehmen/investoren.html"),
]

#: dividend schedules: list of (ex_month, ex_day, pay_month, pay_day); dps per year (latest used for future)
DIVIDENDS = {
    "DE0008404005": dict(schedule=[(5, 8, 5, 12)], dps={2023: 11.40, 2024: 13.80, 2025: 15.40, 2026: 17.10}, growth=1.08),
    "DE0008430026": dict(schedule=[(4, 30, 5, 5)], dps={2023: 11.60, 2024: 15.00, 2025: 20.00, 2026: 21.00}, growth=1.07),
    "DE0007164600": dict(schedule=[(5, 14, 5, 18)], dps={2023: 2.05, 2024: 2.20, 2025: 2.35, 2026: 2.50}, growth=1.06),
    "US5949181045": dict(schedule=[(2, 19, 3, 12), (5, 21, 6, 11), (8, 20, 9, 10), (11, 19, 12, 10)],
                         dps={2023: 0.68, 2024: 0.75, 2025: 0.83, 2026: 0.91}, growth=1.10),
    "US0378331005": dict(schedule=[(2, 9, 2, 12), (5, 11, 5, 14), (8, 10, 8, 13), (11, 9, 11, 12)],
                         dps={2023: 0.24, 2024: 0.25, 2025: 0.26, 2026: 0.27}, growth=1.04),
    "US7561091049": dict(schedule=[(m, 1, m, 15) for m in range(1, 13)],
                         dps={2023: 0.2555, 2024: 0.2630, 2025: 0.2685, 2026: 0.2705}, growth=1.02),
}
EARNINGS = {
    "DE0008404005": [(2, 26), (5, 13), (8, 6), (11, 13)],
    "DE0008430026": [(2, 25), (5, 12), (8, 7), (11, 10)],
    "DE0007164600": [(1, 28), (4, 22), (7, 22), (10, 21)],
    "US5949181045": [(1, 28), (4, 28), (7, 28), (10, 27)],
    "US0378331005": [(1, 29), (4, 30), (7, 30), (10, 29)],
    "US7561091049": [(2, 24), (5, 5), (8, 5), (11, 3)],
}
AGM = {"DE0008404005": (5, 7), "DE0008430026": (4, 29), "DE0007164600": (5, 13)}

#: fundamentals base: net margin, shares (bn), growth, pb, target forward P/E (revenue is derived)
FUNDAMENTALS = {
    "DE0008404005": dict(revenue=170.0, margin=0.058, shares=0.386, growth=0.055, pb=2.1, cur="EUR", raised=4, pe=13.0),
    "DE0008430026": dict(revenue=62.0, margin=0.095, shares=0.131, growth=0.05, pb=2.0, cur="EUR", raised=5, pe=12.5),
    "DE0007164600": dict(revenue=38.5, margin=0.165, shares=1.166, growth=0.085, pb=5.6, cur="EUR", raised=3, pe=36.0),
    "US5949181045": dict(revenue=305.0, margin=0.36, shares=7.43, growth=0.135, pb=10.5, cur="USD", raised=21, pe=32.0),
    "US0378331005": dict(revenue=425.0, margin=0.255, shares=14.9, growth=0.06, pb=48.0, cur="USD", raised=13, pe=31.0),
    "US7561091049": dict(revenue=5.6, margin=0.17, shares=0.915, growth=0.04, pb=1.4, cur="USD", raised=30, pe=48.0),
}

_US, _DE, _TW, _CH = "United States", "Germany", "Taiwan", "Switzerland"
#: name, isin, symbol, sector, country, weight MSCI World, weight FTSE All-World
ETF_HOLDINGS = [
    ("NVIDIA Corp", "US67066G1040", "NVDA", "Technology", _US, 5.42, 4.71),
    ("Apple Inc", "US0378331005", "AAPL", "Technology", _US, 4.86, 4.25),
    ("Microsoft Corp", "US5949181045", "MSFT", "Technology", _US, 4.58, 4.02),
    ("Amazon.com Inc", "US0231351067", "AMZN", "Consumer Cyclical", _US, 2.84, 2.47),
    ("Alphabet Inc Class A", "US02079K3059", "GOOGL", "Communication Services", _US, 1.92, 1.68),
    ("Meta Platforms Inc Class A", "US30303M1027", "META", "Communication Services", _US, 1.88, 1.63),
    ("Broadcom Inc", "US11135F1012", "AVGO", "Technology", _US, 1.74, 1.52),
    ("Alphabet Inc Class C", "US02079K1079", "GOOG", "Communication Services", _US, 1.63, 1.38),
    ("Tesla Inc", "US88160R1014", "TSLA", "Consumer Cyclical", _US, 1.41, 1.24),
    ("Taiwan Semiconductor Manufacturing", "TW0002330008", "2330.TW", "Technology", _TW, 0.0, 1.12),
    ("JPMorgan Chase & Co", "US46625H1005", "JPM", "Financial Services", _US, 1.08, 0.95),
    ("Eli Lilly and Co", "US5324571083", "LLY", "Healthcare", _US, 0.97, 0.84),
    ("Berkshire Hathaway Inc Class B", "US0846707026", "BRK-B", "Financial Services", _US, 0.86, 0.76),
    ("Visa Inc Class A", "US92826C8394", "V", "Financial Services", _US, 0.79, 0.69),
    ("Exxon Mobil Corp", "US30231G1022", "XOM", "Energy", _US, 0.66, 0.58),
    ("Netflix Inc", "US64110L1061", "NFLX", "Communication Services", _US, 0.62, 0.54),
    ("SAP SE", "DE0007164600", "SAP.DE", "Technology", _DE, 0.38, 0.33),
    ("Nestlé SA", "CH0038863350", "NESN.SW", "Consumer Defensive", _CH, 0.34, 0.30),
    ("Allianz SE", "DE0008404005", "ALV.DE", "Financial Services", _DE, 0.19, 0.17),
    ("Münchener Rück AG", "DE0008430026", "MUV2.DE", "Financial Services", _DE, 0.10, 0.09),
    ("Realty Income Corp", "US7561091049", "O", "Real Estate", _US, 0.07, 0.06),
]


# --------------------------------------------------------------------------- demo helpers
def _month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def _day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _bday(d: date, forward: bool = True) -> date:
    step = timedelta(days=1 if forward else -1)
    while d.weekday() >= 5:
        d += step
    return d


def _random_walk(rng: np.random.Generator, days: pd.DatetimeIndex, anchor: float, drift: float, vol: float) -> np.ndarray:
    n = len(days)
    daily = rng.normal((drift - 0.5 * vol**2) / 252.0, vol / np.sqrt(252.0), size=n)
    daily[0] = 0.0
    path = np.exp(np.cumsum(daily))
    return path / path[-1] * anchor


class _Builder:
    """Collects demo transactions and tracks cash / share balances while generating."""

    def __init__(self, accounts: dict[str, Account], categories: dict[str, Category], today: date):
        self.acc = accounts
        self.cat = categories
        self.today = today
        self.txs: list[Transaction] = []
        self.cash: dict[int, Decimal] = {a.id: Decimal("0") for a in accounts.values()}
        self.shares: dict[tuple[int, int], Decimal] = {}
        self.lots: dict[tuple[int, int], list[list[Decimal]]] = {}

    def add(self, account: str, type_: TxType, when: date, amount: Decimal | float, *, category: str | None = None,
            counterparty: str | None = None, description: str | None = None, instrument: Instrument | None = None,
            quantity: Decimal | None = None, price: Decimal | None = None, fees: Decimal = Decimal("0"),
            taxes: Decimal = Decimal("0"), currency: str = "EUR", fx_rate: Decimal | None = None) -> Transaction | None:
        if when > self.today:
            return None
        acc = self.acc[account]
        amount = q2(amount)
        cash_acc_id = None
        if instrument is not None and acc.type == AccountType.brokerage and acc.settlement_account_id:
            cash_acc_id = acc.settlement_account_id
        tx = Transaction(
            account_id=acc.id, cash_account_id=cash_acc_id, instrument_id=instrument.id if instrument else None,
            type=type_, booking_date=when, value_date=when, quantity=quantity, price=price, amount=amount,
            fees=q2(fees), taxes=q2(taxes), currency=currency, fx_rate=fx_rate,
            category_id=self.cat[category].id if category else None, counterparty=counterparty,
            description=description, notes=DEMO_NOTE,
        )
        self.txs.append(tx)
        self.cash[cash_acc_id or acc.id] += amount
        return tx

    def transfer(self, src: str, dst: str, when: date, amount: float, description: str) -> None:
        if when > self.today:
            return
        out_tx = self.add(src, TxType.transfer_out, when, -Decimal(str(amount)), category="Umbuchung",
                          counterparty=dst, description=description)
        in_tx = self.add(dst, TxType.transfer_in, when, Decimal(str(amount)), category="Umbuchung",
                         counterparty=src, description=description)
        out_tx._peer = in_tx  # type: ignore[attr-defined]
        in_tx._peer = out_tx  # type: ignore[attr-defined]


def _dps(isin: str, year: int) -> float:
    spec = DIVIDENDS[isin]
    known = spec["dps"]
    if year in known:
        return known[year]
    last = max(known)
    if year > last:
        return round(known[last] * spec["growth"] ** (year - last), 4)
    first = min(known)
    return round(known[first] / spec["growth"] ** (first - year), 4)


def demo_exists(db: Session) -> bool:
    return db.get(Setting, DEMO_SETTING) is not None


# --------------------------------------------------------------------------- demo seed
def seed_demo(db: Session, today: date | None = None, years: float = 2.8) -> dict:
    """Insert the synthetic demo dataset (no-op if already present)."""
    if demo_exists(db):
        return {"skipped": True}
    today = today or date.today()
    base = seed_base(db)
    accounts, categories = base["accounts"], base["categories"]
    rng = np.random.default_rng(20240101)
    rnd = random.Random(20240101)
    start = date(today.year, today.month, 1) - timedelta(days=int(years * 365))
    start = date(start.year, start.month, 1)

    # ---- instruments
    created_instruments: list[int] = []
    instruments: dict[str, Instrument] = {}
    model_cols = {c.key for c in Instrument.__table__.columns}
    for spec in DEMO_INSTRUMENTS:
        inst = db.scalar(select(Instrument).where(Instrument.isin == spec["isin"]))
        if inst is None:
            inst = Instrument(**{k: v for k, v in spec.items() if k in model_cols})
            db.add(inst)
            db.flush()
            created_instruments.append(inst.id)
        else:
            for k, v in spec.items():
                if k in model_cols and getattr(inst, k) in (None, "") and v is not None:
                    setattr(inst, k, v)
        instruments[spec["isin"]] = inst
    db.flush()

    # ---- synthetic prices (business days up to the last business day before today)
    last_close_day = _bday(today - timedelta(days=1), forward=False)
    days = pd.bdate_range(start - timedelta(days=45), last_close_day)
    fx_path = 1.02 + 0.10 * (0.5 + 0.5 * np.sin(np.linspace(0.3, 5.2, len(days)))) + np.cumsum(rng.normal(0, 0.0012, len(days)))
    fx_path = fx_path / fx_path[-1] * 1.1240
    fx_series = pd.Series(fx_path, index=days)
    price_series: dict[str, pd.Series] = {}
    for spec in DEMO_INSTRUMENTS:
        price_series[spec["isin"]] = pd.Series(
            _random_walk(rng, days, spec["anchor"], spec["drift"], spec["vol"]), index=days)
    cal = pd.date_range(days[0], today, freq="D")
    fx_daily = fx_series.reindex(cal).ffill()
    price_daily = {isin: s.reindex(cal).ffill() for isin, s in price_series.items()}

    def fx_at(d: date) -> Decimal:
        return Decimal(str(round(float(fx_daily[pd.Timestamp(d)]), 6)))

    def price_eur(isin: str, d: date) -> Decimal:
        inst = instruments[isin]
        p = float(price_daily[isin][pd.Timestamp(d)])
        if inst.currency != "EUR":
            p /= float(fx_daily[pd.Timestamp(d)])
        return Decimal(str(round(p, 4)))

    price_hist_ids: list[int] = []
    for spec in DEMO_INSTRUMENTS:
        inst = instruments[spec["isin"]]
        has_history = db.scalar(select(func.count()).select_from(PriceHistory).where(PriceHistory.instrument_id == inst.id))
        if has_history:
            continue
        s = price_series[spec["isin"]]
        db.execute(pg_insert(PriceHistory).on_conflict_do_nothing(), [
            {"instrument_id": inst.id, "date": d.date(), "close": round(float(c), 4), "adj_close": round(float(c), 4),
             "volume": int(rng.integers(50_000, 5_000_000))}
            for d, c in s.items()
        ])
        price_hist_ids.append(inst.id)
    fx_seeded = False
    if not db.scalar(select(func.count()).select_from(FxRate).where(FxRate.currency == "USD")):
        db.execute(pg_insert(FxRate).on_conflict_do_nothing(), [
            {"date": d.date(), "currency": "USD", "rate": round(float(r), 6)} for d, r in fx_series.items()
        ])
        fx_seeded = True
    now = datetime.now(timezone.utc)
    for spec in DEMO_INSTRUMENTS:
        inst = instruments[spec["isin"]]
        if db.get(Quote, inst.id) is None:
            prev = float(price_series[spec["isin"]].iloc[-1])
            price = prev * float(np.exp(rng.normal(0.0005, spec["vol"] / np.sqrt(252.0))))
            db.add(Quote(instrument_id=inst.id, price=round(price, 4), previous_close=round(prev, 4),
                         currency=inst.currency, market_time=now, source=DEMO_SOURCE))

    # ---- transactions
    b = _Builder(accounts, categories, today)
    GIRO, ING, ING_DEPOT = "Postbank Giro", "ING Girokonto", "ING Depot"
    SC_DEPOT, SC_CASH = "Scalable Depot", "Scalable Tagesgeld"
    TR_DEPOT, TR_CASH = "Trade Republic Depot", "Trade Republic Tagesgeld"

    def buy(account: str, isin: str, when: date, *, shares: float | None = None, invest: float | None = None,
            fee: float = 0.0, label: str = "Kauf") -> None:
        when = _bday(when)
        if when > today:
            return
        inst = instruments[isin]
        price = price_eur(isin, when)
        qty = Decimal(str(shares)) if shares is not None else (Decimal(str(invest)) / price).quantize(Decimal("0.0001"))
        fees = Decimal(str(fee))
        amount = -(qty * price + fees)
        acc = accounts[account]
        b.add(account, TxType.buy, when, amount, instrument=inst, quantity=qty, price=price, fees=fees,
              counterparty=inst.name, description=f"{label} {inst.name}")
        key = (acc.id, inst.id)
        b.shares[key] = b.shares.get(key, Decimal("0")) + qty
        b.lots.setdefault(key, []).append([qty, (-amount) / qty])

    def sell(account: str, isin: str, when: date, shares: float, fee: float = 0.0) -> None:
        when = _bday(when)
        if when > today:
            return
        inst = instruments[isin]
        acc = accounts[account]
        key = (acc.id, inst.id)
        qty = min(Decimal(str(shares)), b.shares.get(key, Decimal("0")))
        if qty <= 0:
            return
        price = price_eur(isin, when)
        fees = Decimal(str(fee))
        cost, remaining = Decimal("0"), qty
        for lot in b.lots[key]:
            take = min(lot[0], remaining)
            cost += take * lot[1]
            lot[0] -= take
            remaining -= take
            if remaining <= 0:
                break
        gain = qty * price - fees - cost
        taxes = q2(max(gain, Decimal("0")) * TAX_RATE)
        b.add(account, TxType.sell, when, qty * price - fees - taxes, instrument=inst, quantity=qty, price=price,
              fees=fees, taxes=taxes, counterparty=inst.name, description=f"Verkauf {inst.name}")
        b.shares[key] -= qty

    def pay_dividends(account: str, isin: str) -> None:
        inst = instruments[isin]
        acc = accounts[account]
        spec = DIVIDENDS[isin]
        for year in range(start.year, today.year + 1):
            for ex_m, ex_d, pay_m, pay_d in spec["schedule"]:
                ex_date, pay_date = _day(year, ex_m, ex_d), _bday(_day(year, pay_m, pay_d))
                if pay_date > today or pay_date < start:
                    continue
                # shares held on the ex-date
                held = sum((t.quantity if t.type == TxType.buy else -t.quantity for t in b.txs
                            if t.instrument_id == inst.id and t.account_id == acc.id
                            and t.type in (TxType.buy, TxType.sell) and t.booking_date < ex_date), Decimal("0"))
                if held <= 0:
                    continue
                dps = Decimal(str(_dps(isin, year)))
                rate = fx_at(pay_date) if inst.currency != "EUR" else None
                gross = held * dps / (rate or Decimal("1"))
                taxes = q2(gross * TAX_RATE)
                b.add(account, TxType.dividend, pay_date, gross - taxes, instrument=inst, quantity=held, price=dps,
                      taxes=taxes, currency=inst.currency, fx_rate=rate, category="Dividenden",
                      counterparty=inst.name, description=f"Dividende {inst.name}")

    # opening balances (kind "transfer" -> not counted as household income)
    for name, amount in ((GIRO, 3850.0), (ING, 7900.0), (SC_CASH, 2500.0), (TR_CASH, 7200.0)):
        b.add(name, TxType.deposit, start, Decimal(str(amount)), category="Umbuchung", counterparty="Anfangsbestand",
              description="Anfangssaldo (Demo)")

    groceries = ["REWE Markt GmbH", "EDEKA Center", "ALDI SUED", "LIDL Dienstleistung", "REWE Markt GmbH", "Bäckerei Müller",
                 "PENNY Markt", "EDEKA Center"]
    restaurants = ["Lieferando.de", "Restaurant La Piazza", "Café Extrablatt", "Restaurant Sakura", "McDonalds", "Pizzeria Napoli"]
    shops = [("AMAZON EU S.A R.L.", "Shopping"), ("AMAZON EU S.A R.L.", "Shopping"), ("Zalando SE", "Kleidung"),
             ("H&M Hennes & Mauritz", "Kleidung"), ("MediaMarkt", "Elektronik"), ("IKEA Deutschland", "Einrichtung"),
             ("Thalia Bücher", "Bildung"), ("Decathlon", "Sport & Fitness")]
    salary = 3650.0
    months = _month_starts(start, today)
    for i, m in enumerate(months):
        y, mo = m.year, m.month
        if mo == 1 and i > 0:
            salary = round(salary * 1.035, 2)
        tag = f"{mo:02d}/{y}"
        # --- income
        b.add(GIRO, TxType.deposit, _bday(_day(y, mo, 27), False), salary, category="Gehalt",
              counterparty="Muster Maschinenbau GmbH", description=f"Lohn/Gehalt {tag}")
        if mo == 11:
            b.add(GIRO, TxType.deposit, _bday(_day(y, mo, 27), False), round(salary * 0.5, 2), category="Gehalt",
                  counterparty="Muster Maschinenbau GmbH", description=f"Weihnachtsgeld {y}")
        if mo == 6 and i > 3:
            b.add(GIRO, TxType.deposit, _bday(_day(y, mo, 18)), rnd.choice([512.34, 688.10, 743.92]),
                  category="Erstattungen", counterparty="Finanzamt München", description=f"Einkommensteuer {y - 1} Erstattung")
        # --- fixed costs
        rent = 1120.0 if y < start.year + 2 else 1165.0
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 1)), -rent, category="Miete",
              counterparty="Hausverwaltung Schmidt GmbH", description=f"Miete inkl. NK {tag}")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 5)), -68.0, category="Strom & Nebenkosten",
              counterparty="Stadtwerke München", description="Abschlag Strom")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 8)), -39.99, category="Internet & Telefon",
              counterparty="Vodafone GmbH", description="Internet & Festnetz")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 12)), -14.99, category="Internet & Telefon",
              counterparty="congstar", description="Mobilfunk")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 2)), -54.30, category="Versicherungen",
              counterparty="Allianz Lebensversicherungs-AG", description="Berufsunfähigkeitsversicherung")
        if mo in (1, 4, 7, 10):
            b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 3)), -148.20, category="Versicherungen",
                  counterparty="HUK-COBURG", description="Kfz-Versicherung Quartal")
            b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 15)), -55.08, category="Rundfunkbeitrag",
                  counterparty="Rundfunk ARD, ZDF, DRadio", description="Rundfunkbeitrag")
        if mo == 1:
            b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 4)), -72.40, category="Versicherungen",
                  counterparty="HUK-COBURG", description="Privathaftpflicht Jahresbeitrag")
        if mo == 3:
            b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 20)), -89.90, category="Abos",
                  counterparty="AMAZON PRIME", description="Amazon Prime Jahresabo")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 14)), -13.99, category="Abos", counterparty="NETFLIX.COM",
              description="Netflix Abo")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 19)), -10.99, category="Abos", counterparty="Spotify AB",
              description="Spotify Premium")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 1)), -24.99, category="Sport & Fitness",
              counterparty="FitX Deutschland", description="Mitgliedsbeitrag")
        b.add(GIRO, TxType.withdrawal, _bday(_day(y, mo, 1)), -58.0 if y >= 2025 else -49.0, category="ÖPNV & Bahn",
              counterparty="DB Vertrieb GmbH", description="Deutschlandticket")
        # --- variable costs
        for _ in range(rnd.randint(7, 10)):
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(9.5, 86.0), 2),
                  category="Lebensmittel", counterparty=rnd.choice(groceries), description="Kartenzahlung")
        for _ in range(rnd.randint(1, 2)):
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(42.0, 78.0), 2),
                  category="Tanken", counterparty=rnd.choice(["ARAL Station", "Shell Deutschland", "JET Tankstelle"]),
                  description="Kartenzahlung")
        for _ in range(rnd.randint(2, 4)):
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(14.0, 68.0), 2),
                  category="Restaurant & Café", counterparty=rnd.choice(restaurants), description="Kartenzahlung")
        for _ in range(rnd.randint(1, 3)):
            shop, cat = rnd.choice(shops)
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(12.0, 129.0), 2),
                  category=cat, counterparty=shop, description="Lastschrift / Kartenzahlung")
        b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(8.0, 34.0), 2),
              category="Drogerie", counterparty="dm-drogerie markt", description="Kartenzahlung")
        if rnd.random() < 0.45:
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -round(rnd.uniform(6.0, 38.0), 2),
                  category="Gesundheit", counterparty="Stern Apotheke", description="Kartenzahlung")
        if rnd.random() < 0.6:
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(1, 28)), -rnd.choice([50.0, 100.0, 100.0, 150.0]),
                  category="Bargeld", counterparty="Geldautomat Postbank", description="Bargeldauszahlung")
        if mo in (2, 8):
            b.add(GIRO, TxType.withdrawal, _day(y, mo, rnd.randint(3, 20)), -round(rnd.uniform(280.0, 760.0), 2),
                  category="Reisen", counterparty=rnd.choice(["Booking.com", "Lufthansa AG", "Airbnb Payments"]),
                  description="Urlaub")
        if mo == 12:
            b.add(GIRO, TxType.withdrawal, _day(y, mo, 10), -60.0, category="Spenden",
                  counterparty="Ärzte ohne Grenzen e.V.", description="Spende")
        if mo in (3, 6, 9, 12):
            b.add(GIRO, TxType.fee, _bday(_day(y, mo, 30), False), -17.70, category="Steuern & Gebühren",
                  counterparty="Postbank", description="Entgelt Kontoführung Quartal")
        # --- transfers to brokers
        b.transfer(GIRO, SC_CASH, _bday(_day(y, mo, 28)), 800.0, "Sparrate Scalable")
        b.transfer(GIRO, TR_CASH, _bday(_day(y, mo, 28)), 350.0, "Sparrate Trade Republic")
        if mo in (1, 4, 7, 10):
            b.transfer(GIRO, ING, _bday(_day(y, mo, 29)), 300.0, "Übertrag ING")
        # --- savings plans
        if i > 0:
            b_day = _bday(_day(y, mo, 2))
            buy(SC_DEPOT, "IE00B4L5Y983", b_day, invest=500.0, label="Sparplan")
            buy(SC_DEPOT, "IE00BK5BQT80", b_day, invest=300.0, label="Sparplan")
            buy(TR_DEPOT, "US7561091049", _bday(_day(y, mo, 16)), invest=75.0, fee=0.0, label="Sparplan")
        # --- single stock trades (relative to the start month)
        if i == 1:
            buy(TR_DEPOT, "US5949181045", _day(y, mo, 9), shares=6, fee=1.0)
            buy(TR_DEPOT, "US0378331005", _day(y, mo, 9), shares=14, fee=1.0)
            buy(ING_DEPOT, "DE0008404005", _day(y, mo, 12), shares=12, fee=11.65)
        if i == 2:
            buy(ING_DEPOT, "DE0007164600", _day(y, mo, 6), shares=15, fee=10.90)
        if i == 4:
            buy(ING_DEPOT, "DE0008430026", _day(y, mo, 15), shares=4, fee=9.40)
        if i == 9:
            buy(TR_DEPOT, "US5949181045", _day(y, mo, 21), shares=3, fee=1.0)
        if i == 14:
            buy(ING_DEPOT, "DE0008404005", _day(y, mo, 18), shares=5, fee=8.90)
            sell(TR_DEPOT, "US0378331005", _day(y, mo, 22), shares=5, fee=1.0)
        if i == 20:
            sell(ING_DEPOT, "DE0007164600", _day(y, mo, 11), shares=5, fee=8.20)
            buy(TR_DEPOT, "US0378331005", _day(y, mo, 25), shares=4, fee=1.0)
        if i == 27:
            buy(TR_DEPOT, "US5949181045", _day(y, mo, 4), shares=2, fee=1.0)
        if i == 30:
            sell(ING_DEPOT, "DE0008404005", _day(y, mo, 17), shares=3, fee=8.60)
        # --- interest on Tagesgeld (monthly, on the balance before booking)
        for cash_name, rate in ((TR_CASH, 0.0275 if y < 2025 else 0.02), (SC_CASH, 0.023 if y < 2025 else 0.02)):
            when = _bday(_day(y, mo, 1))
            if i == 0 or when > today:
                continue
            balance = b.cash[accounts[cash_name].id]
            gross = q2(max(balance, Decimal("0")) * Decimal(str(rate)) / 12)
            if gross <= 0:
                continue
            taxes = q2(gross * TAX_RATE)
            b.add(cash_name, TxType.interest, when, gross - taxes, taxes=taxes, category="Zinsen",
                  counterparty=accounts[cash_name].institution.replace("_", " ").title(),
                  description=f"Zinsgutschrift {tag}")

    pay_dividends(ING_DEPOT, "DE0008404005")
    pay_dividends(ING_DEPOT, "DE0008430026")
    pay_dividends(ING_DEPOT, "DE0007164600")
    pay_dividends(TR_DEPOT, "US5949181045")
    pay_dividends(TR_DEPOT, "US0378331005")
    pay_dividends(TR_DEPOT, "US7561091049")

    # dedupe hashes (unique within the batch and against existing rows)
    seen = set(db.scalars(select(Transaction.dedupe_hash).where(Transaction.dedupe_hash.is_not(None))))
    isin_by_id = {inst.id: inst.isin for inst in instruments.values()}
    b.txs.sort(key=lambda t: t.booking_date)
    for tx in b.txs:
        digest = dedupe_hash(tx.account_id, tx.type, tx.booking_date, isin_by_id.get(tx.instrument_id), tx.quantity, tx.amount)
        if digest not in seen:
            seen.add(digest)
            tx.dedupe_hash = digest
    db.add_all(b.txs)
    db.flush()
    for tx in b.txs:
        peer = getattr(tx, "_peer", None)
        if peer is not None:
            tx.transfer_peer_id = peer.id
    db.flush()

    # ---- upcoming events
    events: list[dict] = []
    horizon = today + timedelta(days=370)

    def upcoming(month: int, day: int) -> list[date]:
        return [d for d in (_day(yr, month, day) for yr in (today.year, today.year + 1)) if today <= d <= horizon]

    for isin, spec in DIVIDENDS.items():
        inst = instruments[isin]
        occurrences = []
        for ex_m, ex_d, pay_m, pay_d in spec["schedule"]:
            for yr in (today.year, today.year + 1):
                ex_date, pay_date = _day(yr, ex_m, ex_d), _bday(_day(yr, pay_m, pay_d))
                if today <= pay_date <= horizon:
                    occurrences.append((ex_date, pay_date, _dps(isin, yr)))
        occurrences.sort()
        for n, (ex_date, pay_date, dps) in enumerate(occurrences):
            for type_, d, title in ((EventType.ex_dividend, ex_date, "Ex-Dividende"),
                                    (EventType.dividend_payment, pay_date, "Dividendenzahlung")):
                events.append(dict(instrument_id=inst.id, type=type_, date=d, amount=dps, currency=inst.currency,
                                   title=f"{title} {inst.name}", url=inst.ir_url, is_estimate=n > 0, source=DEMO_SOURCE))
    for isin, dates in EARNINGS.items():
        inst = instruments[isin]
        for n, (m, d) in enumerate(dates):
            for when in upcoming(m, d):
                events.append(dict(instrument_id=inst.id, type=EventType.earnings, date=when, amount=None, currency=None,
                                   title=f"Quartalszahlen {inst.name}", url=inst.ir_url, is_estimate=True,
                                   source=DEMO_SOURCE))
    for isin, (m, d) in AGM.items():
        inst = instruments[isin]
        for when in upcoming(m, d):
            events.append(dict(instrument_id=inst.id, type=EventType.agm, date=when, amount=None, currency=None,
                               title=f"Hauptversammlung {inst.name}", url=inst.ir_url, is_estimate=False,
                               source=DEMO_SOURCE))
    if events:
        db.execute(pg_insert(InstrumentEvent).on_conflict_do_nothing(), events)

    # ---- fundamentals
    for isin, f in FUNDAMENTALS.items():
        inst = instruments[isin]
        if db.get(Fundamentals, inst.id) is not None:
            continue
        db.add(Fundamentals(instrument_id=inst.id, data=_demo_fundamentals(isin, f, price_series[isin], today, rng)))

    # ---- ETF holdings
    for isin, col in (("IE00B4L5Y983", 5), ("IE00BK5BQT80", 6)):
        inst = instruments[isin]
        if db.scalar(select(func.count()).select_from(EtfHolding).where(EtfHolding.etf_instrument_id == inst.id)):
            continue
        db.add_all([
            EtfHolding(etf_instrument_id=inst.id, holding_isin=h[1], holding_symbol=h[2], holding_name=h[0],
                       weight=Decimal(str(h[col])), sector=h[3], country=h[4], asset_class="Equity",
                       as_of=last_close_day, source=DEMO_SOURCE)
            for h in ETF_HOLDINGS if h[col] > 0
        ])

    db.add(Setting(key=DEMO_SETTING, value={
        "created_at": now.isoformat(), "start": start.isoformat(), "end": today.isoformat(),
        "instrument_ids": [i.id for i in instruments.values()], "created_instrument_ids": created_instruments,
        "price_history_instrument_ids": price_hist_ids, "fx_seeded": fx_seeded, "transactions": len(b.txs),
    }))
    db.flush()
    return {"skipped": False, "transactions": len(b.txs), "instruments": len(instruments), "events": len(events)}


def _demo_fundamentals(isin: str, f: dict, prices: pd.Series, today: date, rng: np.random.Generator) -> dict:
    """Synthetic fundamentals, assembled by the SAME builder as live data
    (app.market.fundamentals.build_fundamentals) so the layout of GET /market/fundamentals/{id} is identical."""
    from app.market.fundamentals import build_fundamentals
    from app.market.providers.base import AnnualStatement, FundamentalsRaw

    last_year = today.year - 1
    price = float(prices.iloc[-1])
    growth = f["growth"]
    eps_last = price / f["pe"] / (1 + growth)          # last full fiscal year; forward EPS ~ price / pe
    annual: list[AnnualStatement] = []
    for year in range(last_year - 9, last_year + 1):
        age = last_year - year
        eps = eps_last / (1 + growth) ** age * (1.0 + float(rng.normal(0, 0.04)))
        shares = f["shares"] * 1e9 * (1 + 0.01 * age)
        net_income = eps * shares
        revenue = net_income / f["margin"] * (1.0 + float(rng.normal(0, 0.03)))
        dps_year = _dps(isin, year) * len(DIVIDENDS[isin]["schedule"])
        annual.append(AnnualStatement(
            year=year, period_end=date(year, 12, 31), revenue=revenue, net_income=net_income, eps=round(eps, 2),
            shares=shares, fcf=net_income * f.get("fcf", 1.1), dividends_paid=dps_year * shares))
    # dividends per share (quote currency) for the last 12 years + the current year up to today
    dividends: list[tuple[date, float]] = []
    for year in range(last_year - 11, today.year + 1):
        for ex_month, ex_day, _pm, _pd in DIVIDENDS[isin]["schedule"]:
            ex = _day(year, ex_month, ex_day)
            if ex <= today:
                dividends.append((ex, _dps(isin, year)))
    # month-end closes: the simulated history, extended backwards so the P/E band covers ~10 years.
    # The synthetic past trades around the target P/E (±8 %), blended into the first simulated price.
    monthly = [(d.date(), float(p)) for d, p in prices.resample("ME").last().dropna().items()]
    if monthly:
        eps_by_year = {a.year: a.eps for a in annual}
        first_day, first_price = monthly[0]
        back, cursor = [], first_day
        while cursor.year > last_year - 9:
            cursor = cursor.replace(day=1) - timedelta(days=1)
            eps_known = eps_by_year.get(cursor.year - 1) or annual[0].eps
            eps_now = eps_known * (1 + growth) ** (cursor.month / 12)            # earnings grow during the year
            back.append((cursor, eps_now * f["pe"] * (1.0 + float(rng.normal(0, 0.08)))))
        if back:
            join = first_price / back[0][1]
            n = len(back)
            back = [(d, level * (1 + (join - 1) * (n - i) / n)) for i, (d, level) in enumerate(back)]
        monthly = back[::-1] + monthly
        monthly[-1] = (min(monthly[-1][0], today), monthly[-1][1])
    eps_forward = round(eps_last * (1 + growth), 2)
    dps_now = _dps(isin, today.year) * len(DIVIDENDS[isin]["schedule"])
    raw = FundamentalsRaw(
        annual=annual, currency=f["cur"], financial_currency=f["cur"], eps_ttm=round(eps_last * (1 + growth * 0.8), 2),
        eps_forward=eps_forward, pb=f["pb"], dividend_rate=round(dps_now, 4), dividend_yield=dps_now / price,
        payout_ratio=dps_now / eps_forward if eps_forward else None,
        market_cap=price * f["shares"] * 1e9, shares_outstanding=f["shares"] * 1e9, price=price)
    data = build_fundamentals(raw, monthly, dividends, today=today, price=price)
    data["source"] = DEMO_SOURCE
    return data


# --------------------------------------------------------------------------- reset
def reset_demo(db: Session) -> dict:
    """Remove everything the demo seed created (base accounts/categories stay)."""
    setting = db.get(Setting, DEMO_SETTING)
    info = dict(setting.value or {}) if setting is not None and isinstance(setting.value, dict) else {}
    demo_tx = select(Transaction.id).where(Transaction.notes == DEMO_NOTE)
    db.execute(update(Transaction).where(Transaction.transfer_peer_id.in_(demo_tx)).values(transfer_peer_id=None)
               .execution_options(synchronize_session=False))
    db.execute(text(
        "UPDATE extraction_drafts SET transaction_id = NULL WHERE transaction_id IN "
        "(SELECT id FROM transactions WHERE notes = :note)"), {"note": DEMO_NOTE})
    n_tx = db.execute(delete(Transaction).where(Transaction.notes == DEMO_NOTE)
                      .execution_options(synchronize_session=False)).rowcount
    for model in (EtfHolding, InstrumentEvent, Quote):
        db.execute(delete(model).where(model.source == DEMO_SOURCE).execution_options(synchronize_session=False))
    db.execute(text("DELETE FROM fundamentals WHERE data->>'source' = :src"), {"src": DEMO_SOURCE})
    ph_ids = info.get("price_history_instrument_ids") or []
    if ph_ids:
        db.execute(delete(PriceHistory).where(PriceHistory.instrument_id.in_(ph_ids))
                   .execution_options(synchronize_session=False))
    if info.get("fx_seeded") and info.get("end"):
        db.execute(delete(FxRate).where(FxRate.currency == "USD", FxRate.date <= date.fromisoformat(info["end"]))
                   .execution_options(synchronize_session=False))
    removed_instruments = 0
    for inst_id in info.get("created_instrument_ids") or []:
        inst = db.get(Instrument, inst_id)
        if inst is None or inst.is_watchlist:
            continue
        if db.scalar(select(func.count()).select_from(Transaction).where(Transaction.instrument_id == inst_id)):
            continue
        try:
            with db.begin_nested():
                for model, col in ((Quote, Quote.instrument_id), (PriceHistory, PriceHistory.instrument_id),
                                   (InstrumentEvent, InstrumentEvent.instrument_id),
                                   (EtfHolding, EtfHolding.etf_instrument_id), (Fundamentals, Fundamentals.instrument_id)):
                    db.execute(delete(model).where(col == inst_id).execution_options(synchronize_session=False))
                db.execute(delete(Instrument).where(Instrument.id == inst_id).execution_options(synchronize_session=False))
            removed_instruments += 1
        except Exception:  # noqa: BLE001 - still referenced elsewhere: keep the instrument
            pass
    if setting is not None:
        db.delete(setting)
    db.flush()
    db.expire_all()
    return {"transactions": n_tx, "instruments": removed_instruments}


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed accounts, categories and optional demo data")
    parser.add_argument("--demo", action="store_true", help="insert the synthetic demo dataset")
    parser.add_argument("--reset-demo", action="store_true", help="remove the demo dataset")
    args = parser.parse_args(argv)

    from app.core.db import SessionLocal

    with SessionLocal() as db:
        if args.reset_demo:
            result = reset_demo(db)
            db.commit()
            print(f"Demo-Daten entfernt: {result['transactions']} Transaktionen, {result['instruments']} Instrumente")
            if not args.demo:
                return 0
        base = seed_base(db)
        db.commit()
        print(f"Basisdaten: {len(base['accounts'])} Konten, {len(base['categories'])} Kategorien")
        if args.demo:
            result = seed_demo(db)
            db.commit()
            if result.get("skipped"):
                print("Demo-Daten sind bereits vorhanden (mit --reset-demo entfernen)")
            else:
                print(f"Demo-Daten: {result['transactions']} Transaktionen, {result['instruments']} Instrumente, "
                      f"{result['events']} Termine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
