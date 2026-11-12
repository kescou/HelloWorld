"""Yahoo Finance provider (free, unofficial) via `yfinance` 1.x.

Every remote call is funnelled through `_call()` (global throttle + retry/backoff) and every
parser is defensive: Yahoo changes field names now and then, a missing field must never raise.
Run `python -m app.market.selfcheck` to see which calls currently work.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

from app.market.providers.base import (
    AnnualStatement,
    CalendarData,
    DividendData,
    FundamentalsRaw,
    FundHolding,
    HistoryData,
    MarketDataProvider,
    PriceBar,
    ProfileData,
    ProviderError,
    ProviderRateLimited,
    QuoteData,
    SearchResult,
    SplitData,
)
from app.market.util import normalize_currency, to_date, to_float

log = logging.getLogger("market.yahoo")
T = TypeVar("T")

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
QUOTE_TYPE_MAP = {
    "EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "fund", "CRYPTOCURRENCY": "crypto", "FUTURE": "commodity",
    "INDEX": "other", "CURRENCY": "other", "BOND": "bond",
}


def _row(df: Any, *names: str) -> dict:
    """Return {column -> value} of the first existing row label in a yfinance statement frame."""
    if df is None or getattr(df, "empty", True):
        return {}
    for name in names:
        if name in df.index:
            return {col: df.at[name, col] for col in df.columns}
    return {}


class YahooProvider(MarketDataProvider):
    name = "yahoo"

    def __init__(self, min_interval: float = 0.35, retries: int = 2, ticker_ttl: float = 600.0):
        self.min_interval = min_interval
        self.retries = retries
        self.ticker_ttl = ticker_ttl
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._tickers: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ plumbing
    @staticmethod
    def _yf():
        import yfinance as yf  # imported lazily: keeps app start fast and tests offline

        return yf

    def _ticker(self, symbol: str):
        now = time.monotonic()
        hit = self._tickers.get(symbol)
        if hit and now - hit[0] < self.ticker_ttl:
            return hit[1]
        t = self._yf().Ticker(symbol)
        if len(self._tickers) > 300:
            self._tickers.clear()
        self._tickers[symbol] = (now, t)
        return t

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _call(self, what: str, fn: Callable[[], T]) -> T:
        """Run a yfinance call with throttle + retry/backoff. Raises ProviderError."""
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 – yfinance raises a zoo of exception types
                last = exc
                rate_limited = type(exc).__name__ == "YFRateLimitError" or "Too Many Requests" in str(exc)
                if attempt >= self.retries:
                    if rate_limited:
                        raise ProviderRateLimited(f"{what}: rate limited by Yahoo") from exc
                    break
                time.sleep((5.0 if rate_limited else 1.0) * (3 ** attempt))
        raise ProviderError(f"{what}: {type(last).__name__}: {last}")

    def _info(self, symbol: str) -> dict:
        t = self._ticker(symbol)
        info = self._call(f"info {symbol}", lambda: t.info)
        return info if isinstance(info, dict) else {}

    # ------------------------------------------------------------------ quotes
    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        symbols = [s for s in dict.fromkeys(symbols) if s]
        out: dict[str, QuoteData] = {}
        if not symbols:
            return out
        # 1) one HTTP request per 50 symbols via the quote endpoint (cookie/crumb handled by yfinance)
        for i in range(0, len(symbols), 50):
            chunk = symbols[i:i + 50]
            try:
                out.update(self._quotes_batch(chunk))
            except ProviderRateLimited:
                raise
            except ProviderError as exc:
                log.warning("batch quote failed (%s) – falling back to fast_info", exc)
        # 2) per-symbol fallback (chart endpoint) for whatever is missing
        for sym in symbols:
            if sym in out:
                continue
            try:
                q = self._quote_fast_info(sym)
            except ProviderRateLimited:
                raise
            except ProviderError as exc:
                log.warning("quote %s failed: %s", sym, exc)
                continue
            if q:
                out[sym] = q
        return out

    def _quotes_batch(self, symbols: list[str]) -> dict[str, QuoteData]:
        def fetch():
            from yfinance.data import YfData

            return YfData(session=None).get_raw_json(
                QUOTE_URL, params={"symbols": ",".join(symbols), "formatted": "false"}, timeout=15)

        raw = self._call("quote batch", fetch)
        return self.parse_quote_response(raw)

    @staticmethod
    def parse_quote_response(raw: Any) -> dict[str, QuoteData]:
        out: dict[str, QuoteData] = {}
        try:
            rows = raw["quoteResponse"]["result"] or []
        except (KeyError, TypeError):
            return out
        for r in rows:
            sym = r.get("symbol")
            price = to_float(r.get("regularMarketPrice"))
            if not sym or price is None:
                continue
            cur, div = normalize_currency(r.get("currency"))
            prev = to_float(r.get("regularMarketPreviousClose"))
            ts = to_float(r.get("regularMarketTime"))
            out[sym] = QuoteData(
                symbol=sym, price=price / div, currency=cur,
                previous_close=prev / div if prev is not None else None,
                market_time=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
            )
        return out

    def _quote_fast_info(self, symbol: str) -> QuoteData | None:
        t = self._ticker(symbol)

        def fetch():
            fi = t.fast_info
            return fi.last_price, fi.previous_close, fi.currency

        price, prev, currency = self._call(f"fast_info {symbol}", fetch)
        price = to_float(price)
        if price is None:
            return None
        cur, div = normalize_currency(currency)
        prev = to_float(prev)
        market_time = None
        try:
            ts = to_float((t.history_metadata or {}).get("regularMarketTime"))
            if ts:
                market_time = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            pass
        return QuoteData(symbol=symbol, price=price / div, currency=cur,
                         previous_close=prev / div if prev is not None else None, market_time=market_time)

    # ------------------------------------------------------------------ history / actions
    def get_history(self, symbol: str, start: date | None = None) -> HistoryData:
        t = self._ticker(symbol)
        kwargs: dict[str, Any] = {"interval": "1d", "auto_adjust": False, "actions": True}
        if start is None:
            kwargs["period"] = "max"
        else:
            kwargs["start"] = start.isoformat()
        df = self._call(f"history {symbol}", lambda: t.history(**kwargs))
        currency = None
        try:
            currency = (t.history_metadata or {}).get("currency")
        except Exception:  # noqa: BLE001
            pass
        return self.parse_history_frame(df, currency)

    @staticmethod
    def parse_history_frame(df: Any, currency: str | None) -> HistoryData:
        cur, div = normalize_currency(currency)
        out = HistoryData(currency=cur)
        if df is None or getattr(df, "empty", True):
            return out
        cols = set(df.columns)
        for idx, row in df.iterrows():
            d = to_date(idx)
            if d is None:
                continue
            close = to_float(row["Close"]) if "Close" in cols else None
            if close is not None and close > 0:
                adj = to_float(row["Adj Close"]) if "Adj Close" in cols else None
                vol = to_float(row["Volume"]) if "Volume" in cols else None
                out.bars.append(PriceBar(date=d, close=close / div, adj_close=adj / div if adj is not None else None,
                                         volume=int(vol) if vol is not None else None))
            dv = to_float(row["Dividends"]) if "Dividends" in cols else None
            if dv:
                out.dividends.append(DividendData(ex_date=d, amount=dv / div, currency=cur))
            sp = to_float(row["Stock Splits"]) if "Stock Splits" in cols else None
            if sp:
                out.splits.append(SplitData(date=d, ratio=sp))
        return out

    # ------------------------------------------------------------------ calendar
    def get_calendar(self, symbol: str) -> CalendarData:
        t = self._ticker(symbol)
        cal = self._call(f"calendar {symbol}", lambda: t.calendar)
        info: dict = {}
        try:
            info = self._info(symbol)
        except ProviderRateLimited:
            raise
        except ProviderError:
            pass
        out = self.parse_calendar(cal, info)
        # historical earnings dates (HTML scrape inside yfinance – best effort only)
        try:
            df = self._call(f"earnings_dates {symbol}", lambda: t.get_earnings_dates(limit=12))
            if df is not None and not df.empty:
                today = date.today()
                for idx in df.index:
                    d = to_date(idx)
                    if d and d < today:
                        out.past_earnings_dates.append(d)
        except ProviderRateLimited:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("earnings_dates %s unavailable: %s", symbol, exc)
        return out

    @staticmethod
    def parse_calendar(cal: Any, info: dict | None = None) -> CalendarData:
        info = info or {}
        out = CalendarData()
        cal = cal if isinstance(cal, dict) else {}
        ed = cal.get("Earnings Date") or []
        if not isinstance(ed, (list, tuple)):
            ed = [ed]
        out.earnings_dates = sorted({d for d in (to_date(x) for x in ed) if d})
        if not out.earnings_dates:
            for key in ("earningsTimestampStart", "earningsTimestamp"):
                d = to_date(info.get(key))
                if d:
                    out.earnings_dates = [d]
                    break
        out.earnings_is_estimate = bool(info.get("isEarningsDateEstimate")) or len(out.earnings_dates) > 1
        out.ex_dividend_date = to_date(cal.get("Ex-Dividend Date")) or to_date(info.get("exDividendDate"))
        out.dividend_pay_date = to_date(cal.get("Dividend Date")) or to_date(info.get("dividendDate"))
        cur, div = normalize_currency(info.get("currency"))
        out.currency = cur
        last = to_float(info.get("lastDividendValue"))
        out.dividend_amount = last / div if last else None
        return out

    # ------------------------------------------------------------------ profile
    def get_profile(self, symbol: str) -> ProfileData:
        return self.parse_profile(symbol, self._info(symbol))

    @staticmethod
    def parse_profile(symbol: str, info: dict) -> ProfileData:
        cur, _ = normalize_currency(info.get("currency"))
        qtype = QUOTE_TYPE_MAP.get(str(info.get("quoteType") or "").upper())
        website = info.get("website") or None
        logo = None
        if website:
            m = re.match(r"https?://([^/]+)", website)
            if m:
                logo = f"https://www.google.com/s2/favicons?domain={m[1].removeprefix('www.')}&sz=128"
        ter = to_float(info.get("netExpenseRatio"))
        if ter is None:
            frac = to_float(info.get("annualReportExpenseRatio"))
            ter = frac * 100 if frac is not None else None
        name = info.get("longName") or info.get("shortName") or None
        distribution = None
        if qtype in ("etf", "fund") and name:
            low = name.lower()
            if re.search(r"\bacc\b|accumulating|thesaurierend|\(acc\)|\b1c\b|\bc\)$", low):
                distribution = "accumulating"
            elif re.search(r"\bdist\b|distributing|ausschüttend|\(dist\)|\b1d\b", low):
                distribution = "distributing"
        return ProfileData(
            symbol=symbol, name=name, type=qtype, currency=cur,
            exchange=info.get("fullExchangeName") or info.get("exchange") or None,
            sector=info.get("sector") or None, industry=info.get("industry") or None,
            country=info.get("country") or None, website=website,
            ir_url=info.get("irWebsite") or None, logo_url=logo,
            fund_provider=info.get("fundFamily") or None, ter=ter, distribution=distribution,
        )

    # ------------------------------------------------------------------ fundamentals
    def get_fundamentals(self, symbol: str) -> FundamentalsRaw:
        t = self._ticker(symbol)
        info = self._info(symbol)
        income = cash = None
        try:
            income = self._call(f"income_stmt {symbol}", lambda: t.income_stmt)
        except ProviderRateLimited:
            raise
        except ProviderError as exc:
            log.warning("%s", exc)
        try:
            cash = self._call(f"cashflow {symbol}", lambda: t.cashflow)
        except ProviderRateLimited:
            raise
        except ProviderError as exc:
            log.warning("%s", exc)
        return self.parse_fundamentals(info, income, cash)

    @staticmethod
    def parse_fundamentals(info: dict, income: Any, cash: Any) -> FundamentalsRaw:
        cur, div = normalize_currency(info.get("currency"))
        fin_cur, _ = normalize_currency(info.get("financialCurrency"))
        price = to_float(info.get("currentPrice")) or to_float(info.get("regularMarketPrice"))
        price = price / div if price is not None else None
        rate = to_float(info.get("dividendRate"))
        # dividendRate: Yahoo reports it in the major currency even for pence-quoted listings
        dy = None
        if rate and price:
            dy = rate / price
        if dy is None or dy > 0.5:
            dy = to_float(info.get("trailingAnnualDividendYield"))
        if dy is None:
            pct = to_float(info.get("dividendYield"))   # yfinance >= 0.2.55: percent
            dy = pct / 100 if pct is not None else None
        raw = FundamentalsRaw(
            currency=cur, financial_currency=fin_cur or cur,
            eps_ttm=to_float(info.get("trailingEps")), eps_forward=to_float(info.get("forwardEps")),
            pe=to_float(info.get("trailingPE")), forward_pe=to_float(info.get("forwardPE")),
            pb=to_float(info.get("priceToBook")), dividend_rate=rate, dividend_yield=dy,
            payout_ratio=to_float(info.get("payoutRatio")), market_cap=to_float(info.get("marketCap")),
            shares_outstanding=to_float(info.get("sharesOutstanding")), price=price,
        )
        revenue = _row(income, "Total Revenue", "Operating Revenue")
        net = _row(income, "Net Income Common Stockholders", "Net Income")
        eps = _row(income, "Diluted EPS", "Basic EPS")
        shares = _row(income, "Diluted Average Shares", "Basic Average Shares")
        fcf = _row(cash, "Free Cash Flow")
        divs = _row(cash, "Cash Dividends Paid", "Common Stock Dividend Paid")
        by_year: dict[int, AnnualStatement] = {}
        for src, attr in ((revenue, "revenue"), (net, "net_income"), (eps, "eps"), (shares, "shares"),
                          (fcf, "fcf"), (divs, "dividends_paid")):
            for col, val in src.items():
                d = to_date(col)
                v = to_float(val)
                if d is None or v is None:
                    continue
                st = by_year.setdefault(d.year, AnnualStatement(year=d.year, period_end=d))
                setattr(st, attr, abs(v) if attr == "dividends_paid" else v)
        for st in by_year.values():
            if st.eps is None and st.net_income is not None and st.shares:
                st.eps = st.net_income / st.shares
        raw.annual = [by_year[y] for y in sorted(by_year)]
        return raw

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        yf = self._yf()

        def fetch():
            return yf.Search(query, max_results=limit, news_count=0, lists_count=0, timeout=15).quotes

        return self.parse_search(self._call(f"search {query}", fetch))

    @staticmethod
    def parse_search(quotes: Any) -> list[SearchResult]:
        out: list[SearchResult] = []
        for q in quotes or []:
            if not isinstance(q, dict) or not q.get("symbol"):
                continue
            out.append(SearchResult(
                symbol=q["symbol"], name=q.get("longname") or q.get("shortname"),
                exchange=q.get("exchange") or q.get("exchDisp"),
                type=QUOTE_TYPE_MAP.get(str(q.get("quoteType") or "").upper(), "other"),
                score=to_float(q.get("score")), source="yahoo",
            ))
        return out

    # ------------------------------------------------------------------ funds
    def get_fund_top_holdings(self, symbol: str) -> list[FundHolding]:
        t = self._ticker(symbol)
        df = self._call(f"funds_data {symbol}", lambda: t.funds_data.top_holdings)
        out: list[FundHolding] = []
        if df is None or getattr(df, "empty", True):
            return out
        for sym, row in df.iterrows():
            w = to_float(row.get("Holding Percent"))
            if w is None:
                continue
            out.append(FundHolding(name=str(row.get("Name") or sym), symbol=str(sym) if sym else None, weight=w * 100))
        return out

    # ------------------------------------------------------------------ FX fallback
    def get_fx_history(self, currency: str, start: date | None = None) -> list[tuple[date, float]]:
        hist = self.get_history(f"EUR{currency.upper()}=X", start or (date.today() - timedelta(days=3650)))
        return [(b.date, b.close) for b in hist.bars]
