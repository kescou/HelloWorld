"""Pure parsers: ECB XML, issuer files, OpenFIGI, Yahoo payloads, ICS, number/date helpers. No DB, no network."""
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from app.market.etf import best_effort, ishares, xtrackers
from app.market.etf.base import HoldingsUnavailable
from app.market.etf.tabular import parse_holdings_file
from app.market.events import classify_event_title, parse_ics
from app.market.fx import parse_ecb_xml
from app.market.http import RemoteError
from app.market.providers import openfigi
from app.market.providers.yahoo import YahooProvider
from app.market.util import normalize_name, normalize_symbol, parse_date_loose, parse_number
from app.models.market import EventType
from fixtures.market.fakes import fixture_bytes, no_network  # noqa: F401


# ---------------------------------------------------------------- helpers
@pytest.mark.parametrize("raw,expected", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("4,95", 4.95), ("4.95", 4.95), ("12,50 %", 12.5),
    ("5.954.312.345,12", 5954312345.12), ("5,954,312,345.12", 5954312345.12), ("1.234.567", 1234567.0),
    ("-", None), ("", None), (None, None), (0.0441, 0.0441), ("-0,25", -0.25),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("31.10.2026", date(2026, 10, 31)), ("30.Okt.2026", date(2026, 10, 30)), ("30/Oct/2026", date(2026, 10, 30)),
    ("Nov 08, 2024", date(2024, 11, 8)), ("2026-10-30T00:00:00Z", date(2026, 10, 30)),
    ("30/10/2026", date(2026, 10, 30)), ("20261030", date(2026, 10, 30)), ("garbage", None),
])
def test_parse_date_loose(raw, expected):
    assert parse_date_loose(raw) == expected


def test_normalize_name_and_symbol():
    assert normalize_name("MICROSOFT CORP") == normalize_name("Microsoft Corporation") == "MICROSOFT"
    assert normalize_name("Microsoft Corp. Registered Shares DL-,00000625") == "MICROSOFT"
    assert normalize_name("ALPHABET INC CLASS A") == normalize_name("Alphabet Inc-CL C") == "ALPHABET"
    assert normalize_name("Nestlé S.A.") == normalize_name("NESTLE SA") == "NESTLE"
    assert normalize_name("SAP SE") == normalize_name("SAP") == "SAP"
    assert normalize_symbol("SAP.DE") == "SAP"
    assert normalize_symbol("BRK.B") == normalize_symbol("BRK-B") == "BRKB"
    assert normalize_symbol("MSFT") == "MSFT"


# ---------------------------------------------------------------- ECB
def test_parse_ecb_xml():
    rows = parse_ecb_xml(fixture_bytes("ecb_hist_90d.xml"))
    assert len(rows) == 15                                   # 3 days x 5 currencies, the N/A rate is skipped
    by = {(d, c): r for d, c, r in rows}
    assert float(by[(date(2026, 11, 11), "USD")]) == 1.0850
    assert float(by[(date(2026, 11, 9), "GBP")]) == 0.837
    assert {c for _d, c, _r in rows} == {"USD", "JPY", "GBP", "CHF", "DKK"}


def test_parse_ecb_xml_garbage_raises_remote_error():
    with pytest.raises(RemoteError):
        parse_ecb_xml(b"<html>Service unavailable")


# ---------------------------------------------------------------- iShares
def test_ishares_csv_german():
    res = ishares.parse_csv(fixture_bytes("ishares_EUNL_holdings_de.csv"))
    assert res.source == "ishares"
    assert res.as_of == date(2026, 10, 30)
    assert len(res.rows) == 9                                # zero-weight future + footer dropped
    top = res.rows[0]
    assert (top.symbol, top.name, top.weight) == ("NVDA", "NVIDIA CORP", 5.62)
    assert top.sector == "Information Technology" and top.country == "United States"
    assert top.isin is None and top.asset_class == "equity"
    msft = next(r for r in res.rows if r.symbol == "MSFT")
    assert msft.weight == 4.40
    cash = next(r for r in res.rows if r.name == "USD CASH")
    assert cash.asset_class == "cash" and cash.sector == "Cash/Derivate"
    nestle = next(r for r in res.rows if r.symbol == "NESN")
    assert nestle.country == "Switzerland" and nestle.sector == "Consumer Staples"
    assert res.coverage_pct == pytest.approx(22.28)


def test_ishares_csv_english():
    res = ishares.parse_csv(fixture_bytes("ishares_SWDA_holdings_en.csv"))
    assert res.as_of == date(2026, 10, 30)
    assert [r.symbol for r in res.rows] == ["NVDA", "AAPL", "MSFT", "SAP", "USD"]
    assert res.rows[1].weight == 4.92 and res.rows[3].country == "Germany"


def test_ishares_screener_both_shapes():
    v31 = ishares.parse_screener(json.loads(fixture_bytes("ishares_screener_v31.json")))
    assert v31["IE00B4L5Y983"].endswith("/251882/ishares-msci-world-ucits-etf-acc-fund")
    assert v31["IE00B5BMR087"].endswith("/253743/ishares-sp-500-b-ucits-etf-acc-fund")
    v3 = ishares.parse_screener(json.loads(fixture_bytes("ishares_screener_v3.json")))
    assert set(v3) == {"IE00B4L5Y983", "DE0005933931"}
    assert ishares.parse_screener(["unexpected"]) == {}
    url = ishares.holdings_url(v31["IE00B4L5Y983"])
    assert url == ("https://www.ishares.com/uk/individual/en/products/251882/ishares-msci-world-ucits-etf-acc-fund/"
                   "1478358465952.ajax?fileType=csv&fileName=holdings&dataType=fund")


def test_ishares_html_instead_of_csv_is_rejected():
    with pytest.raises(HoldingsUnavailable):
        ishares.parse_csv(b"<!DOCTYPE html><html><body>Please accept cookies</body></html>")


# ---------------------------------------------------------------- Xtrackers
def test_xtrackers_xlsx():
    res = xtrackers.parse_export(fixture_bytes("xtrackers_constituent.xlsx"))
    assert res.source == "xtrackers" and res.as_of == date(2026, 10, 30)
    assert len(res.rows) == 7
    nvda = res.rows[0]
    assert nvda.isin == "US67066G1040" and nvda.weight == pytest.approx(5.61)   # fraction -> percent
    msft = next(r for r in res.rows if r.isin == "US5949181045")
    assert msft.weight == pytest.approx(4.41) and msft.sector == "Information Technology"
    cash = next(r for r in res.rows if r.name == "USD CASH")
    assert cash.isin is None and cash.asset_class == "cash" and cash.country is None
    assert res.coverage_pct == pytest.approx(19.25)


# ---------------------------------------------------------------- manual / generic tables
def test_manual_csv_german_semicolon():
    rows, as_of = parse_holdings_file(fixture_bytes("manual_holdings_de.csv"), "etf.csv")
    assert as_of == date(2026, 9, 30)
    assert [(r.name, r.isin, r.weight) for r in rows] == [
        ("Microsoft Corp.", "US5949181045", 12.5), ("Apple Inc.", "US0378331005", 10.0),
        ("SAP SE", "DE0007164600", 7.5), ("Nestlé S.A.", "CH0038863350", 5.0)]
    assert rows[0].country == "United States" and rows[2].country == "Germany"
    assert rows[0].sector == "Information Technology" and rows[3].sector == "Consumer Staples"


def test_manual_xlsx_market_value_only():
    rows, _ = parse_holdings_file(fixture_bytes("manual_marketvalue.xlsx"), "etf.xlsx")
    assert [(r.name, r.symbol, r.weight) for r in rows] == [
        ("Microsoft", "MSFT", 60.0), ("Allianz SE", "ALV", 30.0), ("Kasse", None, 10.0)]
    assert rows[1].sector == "Financials"


def test_table_without_header_gives_helpful_error():
    with pytest.raises(HoldingsUnavailable, match="Kopfzeile"):
        parse_holdings_file(b"foo;bar\n1;2\n", "x.csv")
    with pytest.raises(HoldingsUnavailable):
        parse_holdings_file(b"", "x.csv")


def test_best_effort_parsers():
    amundi = best_effort.amundi_parse(json.loads(fixture_bytes("amundi_products.json")))
    assert amundi.source == "amundi" and amundi.as_of == date(2026, 10, 30)
    assert {r.isin: round(r.weight, 2) for r in amundi.rows} == {
        "US0378331005": 4.92, "US5949181045": 4.41, "DE0007164600": 0.38}
    vg_payload = json.loads(fixture_bytes("vanguard_holdings.json"))
    vg = best_effort.vanguard_parse_holdings(vg_payload)
    assert vg.as_of == date(2026, 10, 31)
    assert [(r.isin, r.weight, r.symbol) for r in vg.rows] == [("US0378331005", 4.6, "AAPL"), ("US5949181045", 4.1, "MSFT")]
    assert vg.rows[0].country == "United States"
    with pytest.raises(HoldingsUnavailable):
        best_effort.vanguard_parse_holdings({"errors": ["nope"]})
    with pytest.raises(HoldingsUnavailable):
        best_effort.amundi_parse({"products": []})
    ports = {"data": {"funds": [{"profile": {"portId": "9679", "identifiers": [{"altId": "ISIN", "altIdValue": "IE00B3RBWM25"}]}}]}}
    assert best_effort.vanguard_find_port_id(ports, "IE00B3RBWM25") == "9679"
    assert best_effort.vanguard_find_port_id(ports, "IE0000000000") is None
    with pytest.raises(HoldingsUnavailable, match="anderen Fonds"):
        best_effort.spdr_parse(_spdr_file("IE00B6YX5C33"), expected_isin="IE00B44Z5B48")
    spdr = best_effort.spdr_parse(_spdr_file("IE00B6YX5C33"), expected_isin="IE00B6YX5C33")
    assert spdr.rows[0].isin == "US5949181045" and spdr.rows[0].weight == pytest.approx(6.5)


def _spdr_file(isin: str) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in (["Fund Name:", "SPDR S&P 500 UCITS ETF"], ["ISIN:", isin], ["Ticker Symbol:", "SPY5"],
                ["Holdings As Of:", "30-Oct-2026"], [],
                ["ISIN", "SEDOL", "Security Name", "Currency", "Number of Shares", "Percent of Fund",
                 "Trade Country Name", "Sector Classification"],
                ["US5949181045", "2588173", "Microsoft Corporation", "USD", 1000, 6.5, "United States", "Information Technology"],
                ["US0378331005", "2046251", "Apple Inc.", "USD", 900, 6.1, "United States", "Information Technology"]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- OpenFIGI
def test_openfigi_mapping_prefers_xetra():
    res = openfigi.parse_mapping(json.loads(fixture_bytes("openfigi_mapping.json")), "US5949181045")
    assert [r.symbol for r in res] == ["MSF.DE", "MSF.F", "MSFT"]      # unknown exchange ZZ dropped, US de-duplicated
    assert res[0].isin == "US5949181045" and res[0].type == "stock" and res[0].source == "openfigi"
    assert openfigi.to_yahoo_symbol("BRK/B", "US") == "BRK-B"
    assert openfigi.parse_mapping([{"warning": "No identifier found."}], "XX") == []
    assert openfigi.parse_mapping({"error": "x"}, "XX") == []


# ---------------------------------------------------------------- Yahoo payload parsers (no network)
def test_yahoo_parse_quote_response_handles_pence_and_garbage():
    quotes = YahooProvider.parse_quote_response(json.loads(fixture_bytes("yahoo_quote_response.json")))
    assert set(quotes) == {"MSF.DE", "SHEL.L"}
    assert quotes["MSF.DE"].price == 441.15 and quotes["MSF.DE"].currency == "EUR"
    assert quotes["MSF.DE"].market_time == datetime.fromtimestamp(1794412800, tz=timezone.utc)
    assert quotes["SHEL.L"].currency == "GBP" and quotes["SHEL.L"].price == pytest.approx(28.505)
    assert quotes["SHEL.L"].previous_close == pytest.approx(28.0)
    assert YahooProvider.parse_quote_response({"finance": {"error": "x"}}) == {}


def test_yahoo_parse_history_frame():
    idx = pd.DatetimeIndex(["2026-11-09", "2026-11-10", "2026-11-11"], tz="Europe/Berlin")
    df = pd.DataFrame({
        "Open": [1, 2, 3], "Close": [100.0, float("nan"), 104.0], "Adj Close": [99.0, 100.0, 103.0],
        "Volume": [10, 0, 30], "Dividends": [0.0, 0.0, 1.25], "Stock Splits": [0.0, 4.0, 0.0],
    }, index=idx)
    hist = YahooProvider.parse_history_frame(df, "EUR")
    assert [(b.date, b.close) for b in hist.bars] == [(date(2026, 11, 9), 100.0), (date(2026, 11, 11), 104.0)]
    assert hist.bars[0].adj_close == 99.0 and hist.bars[0].volume == 10
    assert [(d.ex_date, d.amount, d.currency) for d in hist.dividends] == [(date(2026, 11, 11), 1.25, "EUR")]
    assert [(s.date, s.ratio) for s in hist.splits] == [(date(2026, 11, 10), 4.0)]
    assert YahooProvider.parse_history_frame(pd.DataFrame(), "EUR").bars == []
    pence = YahooProvider.parse_history_frame(df, "GBp")
    assert pence.currency == "GBP" and pence.bars[0].close == 1.0 and pence.dividends[0].amount == 0.0125


def test_yahoo_parse_profile_calendar_fundamentals_search():
    info = {"longName": "SAP SE", "quoteType": "EQUITY", "currency": "EUR", "fullExchangeName": "XETRA",
            "sector": "Technology", "industry": "Software - Application", "country": "Germany",
            "website": "https://www.sap.com", "irWebsite": "https://www.sap.com/investors",
            "trailingEps": 5.0, "forwardEps": 6.0, "trailingPE": 40.0, "forwardPE": 33.3, "priceToBook": 5.5,
            "dividendRate": 2.35, "currentPrice": 200.0, "marketCap": 2.4e11, "sharesOutstanding": 1.2e9,
            "financialCurrency": "EUR", "exDividendDate": 1810252800, "isEarningsDateEstimate": True,
            "lastDividendValue": 2.35}
    p = YahooProvider.parse_profile("SAP.DE", info)
    assert (p.name, p.type, p.currency, p.exchange, p.country) == ("SAP SE", "stock", "EUR", "XETRA", "Germany")
    assert p.ir_url == "https://www.sap.com/investors" and "domain=sap.com" in p.logo_url
    etf = YahooProvider.parse_profile("EUNL.DE", {"longName": "iShares Core MSCI World UCITS ETF USD (Acc)",
                                                  "quoteType": "ETF", "fundFamily": "BlackRock Asset Management",
                                                  "netExpenseRatio": 0.2, "currency": "EUR"})
    assert etf.type == "etf" and etf.distribution == "accumulating" and etf.ter == 0.2

    cal = YahooProvider.parse_calendar({"Earnings Date": [date(2027, 1, 21), date(2027, 1, 25)],
                                        "Ex-Dividend Date": date(2027, 5, 14), "Dividend Date": date(2027, 5, 18)}, info)
    assert cal.earnings_dates == [date(2027, 1, 21), date(2027, 1, 25)] and cal.earnings_is_estimate
    assert cal.ex_dividend_date == date(2027, 5, 14) and cal.dividend_pay_date == date(2027, 5, 18)
    assert cal.dividend_amount == 2.35
    cal2 = YahooProvider.parse_calendar(None, info)          # calendar missing -> fall back to info timestamps
    assert cal2.ex_dividend_date == date(2027, 5, 14)

    cols = pd.to_datetime(["2025-12-31", "2024-12-31"])
    income = pd.DataFrame({cols[0]: [36e9, 6e9, 5.0, 1.2e9], cols[1]: [34e9, 3.6e9, float("nan"), 1.2e9]},
                          index=["Total Revenue", "Net Income", "Diluted EPS", "Diluted Average Shares"])
    cash = pd.DataFrame({cols[0]: [7e9, -2.8e9], cols[1]: [5e9, -2.6e9]}, index=["Free Cash Flow", "Cash Dividends Paid"])
    raw = YahooProvider.parse_fundamentals(info, income, cash)
    assert [a.year for a in raw.annual] == [2024, 2025]
    assert raw.annual[1].revenue == 36e9 and raw.annual[1].eps == 5.0 and raw.annual[1].dividends_paid == 2.8e9
    assert raw.annual[0].eps == pytest.approx(3.0)           # NaN EPS -> net income / shares
    assert raw.dividend_yield == pytest.approx(2.35 / 200) and raw.eps_forward == 6.0 and raw.pe == 40.0
    empty = YahooProvider.parse_fundamentals({}, None, pd.DataFrame())
    assert empty.annual == [] and empty.pe is None

    res = YahooProvider.parse_search([{"symbol": "SAP.DE", "longname": "SAP SE", "exchange": "GER", "quoteType": "EQUITY", "score": 2e4},
                                      {"shortname": "no symbol"}, "junk"])
    assert len(res) == 1 and res[0].type == "stock" and res[0].exchange == "GER"


def test_yahoo_provider_retries_then_raises_provider_error(monkeypatch):
    from app.market.providers.base import ProviderError, ProviderRateLimited

    monkeypatch.setattr("time.sleep", lambda s: None)
    yp = YahooProvider(min_interval=0, retries=2)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("offline")
        return "ok"

    assert yp._call("x", flaky) == "ok" and len(attempts) == 3
    with pytest.raises(ProviderError):
        yp._call("x", lambda: (_ for _ in ()).throw(ConnectionError("offline")))
    with pytest.raises(ProviderRateLimited):
        yp._call("x", lambda: (_ for _ in ()).throw(RuntimeError("Too Many Requests. Rate limited.")))


# ---------------------------------------------------------------- ICS
def test_parse_ics_and_classification():
    events = parse_ics(fixture_bytes("ir_calendar.ics"))
    assert [(e["date"], e["title"]) for e in events] == [
        (date(2027, 5, 12), "Annual General Meeting of Shareholders"),
        (date(2027, 1, 21), "Q4 and Full-Year 2026 Results, Preliminary"),
        (date(2027, 6, 9), "Capital Markets Day")]
    assert events[0]["url"].endswith("agm.html")
    assert events[1]["url"] == "https://www.sap.com/investors/q4-2026.html"      # unfolded + extracted from description
    assert classify_event_title(events[0]["title"]) == EventType.agm
    assert classify_event_title(events[1]["title"]) == EventType.earnings
    assert classify_event_title(events[2]["title"]) == EventType.investor_day
    assert classify_event_title("Ordentliche Hauptversammlung 2027") == EventType.agm
    assert classify_event_title("Sommerfest") == EventType.other
    assert parse_ics("not a calendar") == []
