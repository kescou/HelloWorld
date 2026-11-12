"""Live self-check of every free data source – run it on YOUR machine (the build sandbox had no access):

    cd backend && python -m app.market.selfcheck                      # default set
    python -m app.market.selfcheck US0378331005 DE0008404005          # your own ISINs (stocks)
    python -m app.market.selfcheck --etf IE00B4L5Y983 --etf IE00BJ0KDQ92
    python -m app.market.selfcheck --json                             # machine readable

Prints a ✅/❌ table. No database needed, nothing is written. Exit code 1 if a core source
(ECB, Yahoo quotes/history/search) fails, else 0.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

DEFAULT_STOCKS = ["US5949181045", "DE0007164600"]                      # Microsoft, SAP
FALLBACK_SYMBOLS = {"US5949181045": "MSF.DE", "DE0007164600": "SAP.DE"}  # used when the search itself is broken
# (ISIN, issuer, fallback Yahoo symbol)
DEFAULT_ETFS = [
    ("IE00B4L5Y983", "ishares", "EUNL.DE"),      # iShares Core MSCI World
    ("IE00BJ0KDQ92", "xtrackers", "XDWD.DE"),    # Xtrackers MSCI World 1C
    ("IE00B3RBWM25", "vanguard", "VGWL.DE"),     # Vanguard FTSE All-World (Dist)
    ("LU1681043599", "amundi", "AMEW.DE"),       # Amundi MSCI World
    ("IE00B6YX5C33", "spdr", "SPY5.DE"),         # SPDR S&P 500
    ("IE00B60SX394", "invesco", "SC0J.DE"),      # Invesco MSCI World
]


@dataclass
class Check:
    group: str
    name: str
    ok: bool
    detail: str
    ms: int
    core: bool = False
    best_effort: bool = False


class Runner:
    def __init__(self) -> None:
        self.results: list[Check] = []

    def run(self, group: str, name: str, fn: Callable[[], str], core: bool = False, best_effort: bool = False):
        start = time.monotonic()
        try:
            detail, ok = fn(), True
        except Exception as exc:  # noqa: BLE001 – the whole point is to show what breaks
            detail, ok = f"{type(exc).__name__}: {exc}", False
        check = Check(group, name, ok, str(detail)[:150], int((time.monotonic() - start) * 1000), core, best_effort)
        self.results.append(check)
        mark = "✅" if ok else ("⚠️ " if best_effort else "❌")
        print(f"{mark} {group:<10} {name:<34} {check.ms:>6} ms  {check.detail}", flush=True)
        return ok


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live check of all market-data sources")
    parser.add_argument("isins", nargs="*", help="stock ISINs to test (default: Microsoft, SAP)")
    parser.add_argument("--etf", action="append", default=[], help="ETF ISIN (issuer is detected via Yahoo name)")
    parser.add_argument("--json", action="store_true", help="print results as JSON at the end")
    args = parser.parse_args(argv)

    from app.market import fx, http, service
    from app.market.etf import registry
    from app.market.etf import service as etf_service
    from app.market.providers import openfigi
    from app.market.providers.yahoo import YahooProvider
    from app.models.core import Instrument, InstrumentType

    yahoo = YahooProvider()
    runner = Runner()
    stocks = args.isins or DEFAULT_STOCKS
    print(f"Marktdaten-Selfcheck {date.today().isoformat()}  (alle Quellen kostenlos, ohne API-Key)\n")

    # ---------------------------------------------------------------- ECB
    def ecb_90d() -> str:
        rows = fx.parse_ecb_xml(http.get_bytes(fx.ECB_90D, timeout=20))
        _require(len(rows) > 500, f"nur {len(rows)} Kurse")
        latest = max(r[0] for r in rows)
        usd = next(r[2] for r in rows if r[0] == latest and r[1] == "USD")
        return f"{len(rows)} Kurse, letzter Tag {latest}, 1 EUR = {usd} USD"

    def ecb_hist() -> str:
        rows = fx.parse_ecb_xml(http.get_bytes(fx.ECB_HIST, timeout=60))
        _require(len(rows) > 100_000, f"nur {len(rows)} Kurse")
        return f"{len(rows)} Kurse seit {min(r[0] for r in rows)}"

    runner.run("EZB", "eurofxref-hist-90d.xml", ecb_90d, core=True)
    runner.run("EZB", "eurofxref-hist.xml (Vollhistorie)", ecb_hist)

    # ---------------------------------------------------------------- Yahoo per stock
    symbols: dict[str, str] = {}
    for isin in stocks:
        def search(isin=isin) -> str:
            results = service.search_symbols(yahoo, isin)
            best = service.pick_listing(results)
            _require(best is not None, "kein Treffer")
            symbols[isin] = best.symbol
            return f"{best.symbol} ({best.name}, {best.exchange}) – {len(results)} Treffer, Quelle {best.source}"

        if not runner.run("Yahoo", f"Suche ISIN {isin}", search, core=True) and isin in FALLBACK_SYMBOLS:
            symbols[isin] = FALLBACK_SYMBOLS[isin]

    def figi() -> str:
        res = openfigi.lookup_isin(stocks[0])
        _require(bool(res), "keine Zuordnung")
        return f"{len(res)} Listings, bevorzugt {res[0].symbol}"

    runner.run("OpenFIGI", f"Mapping {stocks[0]}", figi)

    syms = list(symbols.values())
    if syms:
        def batch() -> str:
            quotes = yahoo._quotes_batch(syms)
            _require(len(quotes) == len(syms), f"{len(quotes)}/{len(syms)} Kurse")
            return ", ".join(f"{s}={q.price:.2f} {q.currency}" for s, q in quotes.items())

        def fast() -> str:
            q = yahoo._quote_fast_info(syms[0])
            _require(q is not None, "kein Kurs")
            return f"{syms[0]}={q.price:.2f} {q.currency}, Vortag {q.previous_close}, Zeit {q.market_time}"

        runner.run("Yahoo", "Batch-Kurse (v7/quote)", batch, core=True)
        runner.run("Yahoo", "Kurs via fast_info (Fallback)", fast, core=True)

    for isin, sym in symbols.items():
        def hist_max(sym=sym) -> str:
            h = yahoo.get_history(sym)
            _require(len(h.bars) > 200, f"nur {len(h.bars)} Tage")
            return (f"{len(h.bars)} Tage seit {h.bars[0].date}, {len(h.dividends)} Dividenden, "
                    f"{len(h.splits)} Splits, Währung {h.currency}")

        def hist_inc(sym=sym) -> str:
            h = yahoo.get_history(sym, date.today() - timedelta(days=14))
            _require(len(h.bars) >= 3, f"nur {len(h.bars)} Tage")
            return f"{len(h.bars)} Tage, letzter Schluss {h.bars[-1].close:.2f} am {h.bars[-1].date}"

        def calendar(sym=sym) -> str:
            c = yahoo.get_calendar(sym)
            _require(bool(c.earnings_dates or c.ex_dividend_date), "weder Earnings- noch Ex-Termin")
            return (f"Earnings {[d.isoformat() for d in c.earnings_dates]}, Ex-Tag {c.ex_dividend_date}, "
                    f"Zahltag {c.dividend_pay_date}, vergangene Earnings {len(c.past_earnings_dates)}")

        def profile(sym=sym) -> str:
            p = yahoo.get_profile(sym)
            _require(bool(p.name and p.type), "Name/Typ fehlt")
            return f"{p.name} | {p.type} | {p.sector} | {p.country} | {p.exchange} | IR {p.ir_url or '–'}"

        def fundamentals(sym=sym) -> str:
            f = yahoo.get_fundamentals(sym)
            _require(len(f.annual) >= 3, f"nur {len(f.annual)} Geschäftsjahre")
            _require(any(a.eps is not None for a in f.annual), "kein EPS")
            last = f.annual[-1]
            return (f"{len(f.annual)} Jahre ({f.annual[0].year}–{last.year}), EPS {last.eps}, FCF {last.fcf}, "
                    f"fwd EPS {f.eps_forward}, KGV {f.pe}, Währung {f.financial_currency}")

        runner.run("Yahoo", f"Historie max {sym}", hist_max, core=True)
        runner.run("Yahoo", f"Historie inkrementell {sym}", hist_inc, core=True)
        runner.run("Yahoo", f"Kalender {sym}", calendar)
        runner.run("Yahoo", f"Stammdaten {sym}", profile)
        runner.run("Yahoo", f"Fundamentaldaten {sym}", fundamentals)

    def fx_yahoo() -> str:
        rows = yahoo.get_fx_history("USD", date.today() - timedelta(days=14))
        _require(len(rows) >= 3, "keine Kurse")
        return f"EURUSD=X {rows[-1][1]:.4f} am {rows[-1][0]}"

    runner.run("Yahoo", "FX-Fallback EURUSD=X", fx_yahoo)

    # ---------------------------------------------------------------- ETF holdings
    etfs = list(DEFAULT_ETFS)
    if args.etf:
        etfs = []
        for isin in args.etf:
            sym, name = None, isin
            try:
                best = service.pick_listing(service.search_symbols(yahoo, isin), ("etf", "fund", "stock"))
                if best:
                    sym, name = best.symbol, best.name or isin
            except Exception:  # noqa: BLE001
                pass
            etfs.append((isin, registry.detect_issuer(name) or "?", sym))
    providers = registry.providers()
    for isin, issuer, sym in etfs:
        inst = Instrument(name=f"{issuer} {isin}", isin=isin, symbol=sym, type=InstrumentType.etf, currency="EUR")
        prov = providers.get(issuer)
        if prov is not None:
            def issuer_fetch(prov=prov, inst=inst) -> str:
                res = prov.fetch(inst)
                _require(len(res.rows) >= 10, f"nur {len(res.rows)} Positionen")
                with_isin = sum(1 for r in res.rows if r.isin)
                return (f"{len(res.rows)} Positionen, Abdeckung {res.coverage_pct:.1f} %, Stand {res.as_of}, "
                        f"{with_isin} mit ISIN, Top: {res.rows[0].name} {res.rows[0].weight:.2f} %")

            runner.run("ETF", f"{issuer} {isin} [{prov.status}]", issuer_fetch, best_effort=prov.status != "implemented")
        if sym:
            def top10(inst=inst) -> str:
                res = etf_service.fetch_yahoo_top10(inst, yahoo)
                return f"{len(res.rows)} Positionen, Abdeckung {res.coverage_pct:.1f} %, Top: {res.rows[0].name}"

            runner.run("ETF", f"Yahoo Top-10 {sym}", top10)

    # ---------------------------------------------------------------- summary
    failed = [c for c in runner.results if not c.ok]
    core_failed = [c for c in failed if c.core]
    print(f"\n{len(runner.results) - len(failed)}/{len(runner.results)} Prüfungen ok"
          + (f" – {len(core_failed)} Kernquelle(n) fehlgeschlagen" if core_failed else ""))
    if failed:
        print("Fehlgeschlagen: " + ", ".join(f"{c.group}/{c.name}" for c in failed))
        print("Hinweis: ⚠️ = Best-Effort-Quelle (Fallback: Yahoo Top-10 oder manueller CSV-Upload). "
              "Details siehe app/market/README.md, Abschnitt „Grenzen“.")
    if args.json:
        print(json.dumps([c.__dict__ for c in runner.results], ensure_ascii=False, indent=2))
    return 1 if core_failed else 0


if __name__ == "__main__":
    sys.exit(main())
