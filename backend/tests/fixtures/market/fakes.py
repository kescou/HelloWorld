"""Test doubles for the market module: FakeProvider (no network) + small DB factories."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.market import http as market_http
from app.market.providers import set_provider
from app.market.providers.base import (
    CalendarData,
    FundamentalsRaw,
    FundHolding,
    HistoryData,
    MarketDataProvider,
    PriceBar,
    ProfileData,
    ProviderError,
    QuoteData,
    SearchResult,
)
from app.models.core import Account, AccountType, FxRate, Instrument, InstrumentType, Transaction, TxType

FIXTURES = Path(__file__).parent


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeProvider(MarketDataProvider):
    name = "fake"

    def __init__(self) -> None:
        self.quotes: dict[str, QuoteData] = {}
        self.history: dict[str, HistoryData] = {}
        self.calendars: dict[str, CalendarData] = {}
        self.profiles: dict[str, ProfileData] = {}
        self.fundamentals: dict[str, FundamentalsRaw] = {}
        self.searches: dict[str, list[SearchResult]] = {}
        self.top_holdings: dict[str, list[FundHolding]] = {}
        self.fx: dict[str, list[tuple[date, float]]] = {}
        self.fail: set[str] = set()          # symbols for which every call raises ProviderError
        self.offline = False                 # everything raises
        self.calls: list[tuple] = []

    def _check(self, what: str, symbol: str | None = None) -> None:
        self.calls.append((what, symbol))
        if self.offline:
            raise ProviderError(f"{what}: offline")
        if symbol is not None and symbol in self.fail:
            raise ProviderError(f"{what} {symbol}: boom")

    def get_quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        if self.offline:
            raise ProviderError("quotes: offline")
        return {s: self.quotes[s] for s in symbols if s in self.quotes and s not in self.fail}

    def get_history(self, symbol, start=None):
        self._check("history", symbol)
        self.calls[-1] = ("history", symbol, start)
        src = self.history.get(symbol, HistoryData())
        if start is None:
            return HistoryData(list(src.bars), list(src.dividends), list(src.splits), src.currency)
        return HistoryData([b for b in src.bars if b.date >= start],
                           [d for d in src.dividends if d.ex_date >= start],
                           [s for s in src.splits if s.date >= start], src.currency)

    def get_calendar(self, symbol):
        self._check("calendar", symbol)
        return self.calendars.get(symbol, CalendarData())

    def get_profile(self, symbol):
        self._check("profile", symbol)
        return self.profiles.get(symbol, ProfileData(symbol=symbol))

    def get_fundamentals(self, symbol):
        self._check("fundamentals", symbol)
        return self.fundamentals.get(symbol, FundamentalsRaw())

    def search(self, query, limit=10):
        self._check("search", query)
        return list(self.searches.get(query, []))[:limit]

    def get_fund_top_holdings(self, symbol):
        self._check("top_holdings", symbol)
        return list(self.top_holdings.get(symbol, []))

    def get_fx_history(self, currency, start=None):
        self._check("fx", currency)
        return list(self.fx.get(currency, []))


@pytest.fixture()
def provider():
    """FakeProvider installed as the global provider for the duration of a test."""
    fake = FakeProvider()
    set_provider(fake)
    yield fake
    set_provider(None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any attempt to use the real HTTP helper or the real Yahoo provider fails loudly."""
    calls: list[tuple[str, str]] = []

    def blocked(method, url, **kw):
        calls.append((method, url))
        raise market_http.RemoteError(f"network disabled in tests: {method} {url}")

    monkeypatch.setattr(market_http, "request", blocked)
    import app.market.providers as providers_pkg

    if providers_pkg._provider is None:
        set_provider(FakeProvider())
        yield calls
        set_provider(None)
    else:
        yield calls


# ---------------------------------------------------------------- DB factories
def bars(start: date, closes: list[float]) -> list[PriceBar]:
    """Consecutive calendar days (weekends not skipped – irrelevant for the tests)."""
    return [PriceBar(date=start + timedelta(days=i), close=c, adj_close=c, volume=1000 + i) for i, c in enumerate(closes)]


def make_account(db, name="Depot", type_=AccountType.brokerage) -> Account:
    acc = Account(name=name, institution="test", type=type_, currency="EUR")
    db.add(acc)
    db.flush()
    return acc


def make_instrument(db, name="Test AG", isin=None, symbol=None, type_=InstrumentType.stock, currency="EUR", **kw) -> Instrument:
    inst = Instrument(name=name, isin=isin, symbol=symbol, type=type_, currency=currency, **kw)
    db.add(inst)
    db.flush()
    return inst


def make_tx(db, account, instrument, type_=TxType.buy, quantity=1, price=100, on=None, currency="EUR") -> Transaction:
    qty, px = Decimal(str(quantity)), Decimal(str(price))
    amount = -(qty * px) if type_ == TxType.buy else (qty * px if type_ == TxType.sell else Decimal(0))
    tx = Transaction(account_id=account.id, instrument_id=instrument.id, type=type_,
                     booking_date=on or date.today() - timedelta(days=400), quantity=qty,
                     price=px if type_ in (TxType.buy, TxType.sell) else None,
                     amount=round(amount, 2), currency=currency)
    db.add(tx)
    db.flush()
    return tx


def make_fx(db, currency: str, rate: float, on: date | None = None) -> None:
    db.add(FxRate(date=on or date.today() - timedelta(days=1), currency=currency, rate=Decimal(str(rate))))
    db.flush()
