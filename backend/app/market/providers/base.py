"""Provider abstraction: everything the app needs from a (free) market-data source.

Providers return plain dataclasses and never touch the DB. They may raise `ProviderError`;
callers (app.market.service / jobs) catch per instrument and carry on.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime


class ProviderError(RuntimeError):
    pass


class ProviderRateLimited(ProviderError):
    """The source throttles us (HTTP 429) – callers stop the current batch and retry on the next run."""


@dataclass
class QuoteData:
    symbol: str
    price: float
    currency: str | None = None
    previous_close: float | None = None
    market_time: datetime | None = None


@dataclass
class PriceBar:
    date: date
    close: float
    adj_close: float | None = None
    volume: int | None = None


@dataclass
class DividendData:
    ex_date: date
    amount: float
    currency: str | None = None


@dataclass
class SplitData:
    date: date
    ratio: float            # 4.0 for a 4:1 split, 0.1 for a 1:10 reverse split


@dataclass
class HistoryData:
    bars: list[PriceBar] = field(default_factory=list)
    dividends: list[DividendData] = field(default_factory=list)
    splits: list[SplitData] = field(default_factory=list)
    currency: str | None = None
    full: bool = False                       # True when it covers the max range (not an incremental window)


@dataclass
class CalendarData:
    earnings_dates: list[date] = field(default_factory=list)
    earnings_is_estimate: bool = False
    past_earnings_dates: list[date] = field(default_factory=list)
    ex_dividend_date: date | None = None
    dividend_pay_date: date | None = None
    dividend_amount: float | None = None     # next/last declared amount per share, if known
    currency: str | None = None


@dataclass
class ProfileData:
    symbol: str
    name: str | None = None
    type: str | None = None                  # stock|etf|fund|crypto|bond|commodity|other
    currency: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    website: str | None = None
    ir_url: str | None = None
    logo_url: str | None = None
    fund_provider: str | None = None
    ter: float | None = None                 # percent p.a.
    distribution: str | None = None          # distributing|accumulating
    isin: str | None = None


@dataclass
class AnnualStatement:
    year: int
    period_end: date | None = None
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None                 # diluted
    shares: float | None = None              # diluted average shares
    fcf: float | None = None
    dividends_paid: float | None = None      # positive number


@dataclass
class FundamentalsRaw:
    annual: list[AnnualStatement] = field(default_factory=list)
    currency: str | None = None              # quote currency
    financial_currency: str | None = None    # statement currency
    eps_ttm: float | None = None
    eps_forward: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    pb: float | None = None
    dividend_rate: float | None = None       # forward annual dividend per share (quote currency)
    dividend_yield: float | None = None      # fraction, 0.021 = 2.1 %
    payout_ratio: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    price: float | None = None


@dataclass
class SearchResult:
    symbol: str
    name: str | None = None
    exchange: str | None = None
    type: str | None = None
    currency: str | None = None
    isin: str | None = None
    score: float | None = None
    source: str = "yahoo"


@dataclass
class FundHolding:
    name: str
    weight: float                            # percent
    symbol: str | None = None


class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        """Latest quote per symbol. Missing/failed symbols are simply absent."""

    @abstractmethod
    def get_history(self, symbol: str, start: date | None = None) -> HistoryData:
        """Daily bars (+ dividends/splits in the same window). start=None -> max range."""

    @abstractmethod
    def get_calendar(self, symbol: str) -> CalendarData: ...

    @abstractmethod
    def get_profile(self, symbol: str) -> ProfileData: ...

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> FundamentalsRaw: ...

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...

    def get_fund_top_holdings(self, symbol: str) -> list[FundHolding]:
        return []

    def get_fx_history(self, currency: str, start: date | None = None) -> list[tuple[date, float]]:
        """Units of `currency` per 1 EUR, daily."""
        return []
