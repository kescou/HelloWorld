"""API smoke tests for every /api/market endpoint (FakeProvider, no network)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.market import fx
from app.market.providers.base import (
    AnnualStatement,
    CalendarData,
    DividendData,
    FundamentalsRaw,
    FundHolding,
    HistoryData,
    ProfileData,
    QuoteData,
    SearchResult,
)
from app.models.core import InstrumentType, TxType
from fixtures.market.fakes import (  # noqa: F401
    bars,
    fixture_bytes,
    make_account,
    make_fx,
    make_instrument,
    make_tx,
    no_network,
    provider,
)

TODAY = date.today()
API = "/api/market"


@pytest.fixture()
def portfolio(db, provider):
    acc = make_account(db)
    msft = make_instrument(db, "Microsoft Corp.", isin="US5949181045", symbol="MSF.DE", sector="Technology", country="United States")
    world = make_instrument(db, "iShares Core MSCI World UCITS ETF", isin="IE00B4L5Y983", symbol="EUNL.DE", type_=InstrumentType.etf)
    watch = make_instrument(db, "SAP SE", isin="DE0007164600", symbol="SAP.DE", is_watchlist=True)
    make_tx(db, acc, msft, TxType.buy, 10, 300)
    make_tx(db, acc, world, TxType.buy, 100, 80)
    for sym, price in (("MSF.DE", 400.0), ("EUNL.DE", 100.0), ("SAP.DE", 200.0)):
        provider.quotes[sym] = QuoteData(sym, price, "EUR", price * 0.98, datetime.now(timezone.utc))
        provider.history[sym] = HistoryData(bars(TODAY - timedelta(days=499), [price] * 500), currency="EUR")
        provider.calendars[sym] = CalendarData(earnings_dates=[TODAY + timedelta(days=25)])
        provider.profiles[sym] = ProfileData(symbol=sym, name=sym)
    provider.history["MSF.DE"].dividends = [DividendData(TODAY - timedelta(days=30 + 91 * i), 0.75, "EUR") for i in range(8)]
    provider.fundamentals["MSF.DE"] = FundamentalsRaw(
        annual=[AnnualStatement(TODAY.year - 2, date(TODAY.year - 2, 6, 30), revenue=2e11, net_income=7e10, eps=10.0, shares=7.4e9, fcf=6e10),
                AnnualStatement(TODAY.year - 1, date(TODAY.year - 1, 6, 30), revenue=2.4e11, net_income=8.8e10, eps=12.0, shares=7.4e9, fcf=7e10)],
        currency="EUR", eps_ttm=13.0, eps_forward=15.0, pb=10.0, market_cap=3e12, price=400.0)
    provider.top_holdings["EUNL.DE"] = [FundHolding("Microsoft Corp", 4.4, "MSFT"), FundHolding("NVIDIA Corp", 5.6, "NVDA")]
    db.commit()
    return {"acc": acc, "msft": msft, "world": world, "watch": watch}


def test_refresh_all_then_read_everything(client, db, provider, portfolio, monkeypatch):
    monkeypatch.setattr(fx, "fetch_ecb", lambda full=False: fx.parse_ecb_xml(fixture_bytes("ecb_hist_90d.xml")))
    msft, world, watch = portfolio["msft"], portfolio["world"], portfolio["watch"]

    r = client.post(f"{API}/refresh", json={"scope": "all"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "all" and set(body["jobs"]) == {"fx", "quotes", "profile", "history", "events", "fundamentals", "etf"}
    assert body["status"] == "ok", body
    assert body["jobs"]["quotes"]["updated"] == 3 and len(body["jobs"]["quotes"]["backfilled"]) == 3

    # --- quotes
    r = client.get(f"{API}/quotes", params={"instrument_ids": f"{msft.id},{world.id}"})
    quotes = {q["instrument_id"]: q for q in r.json()}
    assert r.status_code == 200 and set(quotes) == {msft.id, world.id}
    q = quotes[msft.id]
    assert q["price"] == 400.0 and q["previous_close"] == 392.0 and q["currency"] == "EUR"
    assert q["change"] == pytest.approx(8.0) and q["change_pct"] == pytest.approx(2.0408, abs=1e-3)
    assert q["market_time"] and q["updated_at"]
    assert {"instrument_id", "price", "previous_close", "change", "change_pct", "currency", "market_time", "updated_at"} <= set(q)
    assert len(client.get(f"{API}/quotes").json()) == 3                       # default: held + watchlist
    assert client.get(f"{API}/quotes", params={"instrument_ids": "abc"}).status_code == 422

    # --- history
    full = client.get(f"{API}/history/{msft.id}", params={"range": "MAX"}).json()
    assert len(full) == 500 and full[0] == {"date": (TODAY - timedelta(days=499)).isoformat(), "close": 400.0}
    assert len(client.get(f"{API}/history/{msft.id}", params={"range": "1M"}).json()) == 32
    assert 360 <= len(client.get(f"{API}/history/{msft.id}").json()) <= 367                   # default 1Y
    ytd = client.get(f"{API}/history/{msft.id}", params={"range": "ytd"}).json()
    assert ytd[0]["date"] >= f"{TODAY.year}-01-01"
    assert client.get(f"{API}/history/{msft.id}", params={"range": "7X"}).status_code == 422
    assert client.get(f"{API}/history/999999").status_code == 404

    # --- events (held + watchlist), enrichment
    r = client.get(f"{API}/events", params={"from": (TODAY - timedelta(days=40)).isoformat(),
                                             "to": (TODAY + timedelta(days=370)).isoformat()})
    events = r.json()
    assert r.status_code == 200 and {e["instrument_id"] for e in events} == {msft.id, world.id, watch.id}
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)
    past_div = next(e for e in events if e["type"] == "ex_dividend" and not e["is_estimate"])
    assert past_div["amount"] == 0.75 and past_div["position_quantity"] == 10 and past_div["expected_payment"] == 7.5
    est = [e for e in events if e["type"] == "ex_dividend" and e["is_estimate"]]
    assert len(est) == 4 and all(e["expected_payment"] == 7.5 and e["instrument_name"] == "Microsoft Corp." for e in est)
    assert any(e["type"] == "dividend_payment" and e["is_estimate"] for e in events)
    assert {"id", "instrument_id", "instrument_name", "symbol", "type", "date", "amount", "currency", "title", "url",
            "is_estimate", "position_quantity", "expected_payment"} <= set(events[0])
    only = client.get(f"{API}/events", params={"types": "earnings"}).json()
    assert len(only) == 3 and {e["type"] for e in only} == {"earnings"}
    assert client.get(f"{API}/events", params={"types": "bogus"}).status_code == 422

    # --- fundamentals
    f = client.get(f"{API}/fundamentals/{msft.id}").json()
    assert f["available"] and f["instrument_id"] == msft.id and len(f["years"]) == 2
    assert f["fair_value"]["method"] == "pe_avg_x_forward_eps" and f["fair_value"]["eps_forward"] == 15.0
    assert f["fair_value"]["value"] == pytest.approx(f["fair_value"]["pe_avg"] * 15.0, abs=0.1)
    assert f["pe_history"] and f["valuation"]["pb"] == 10.0 and f["valuation"]["market_cap"] == 3e12
    assert {"years", "pe_history", "fair_value", "dividend_growth", "valuation", "earnings_quality"} <= set(f)
    assert {"year", "revenue", "net_income", "eps", "dps", "fcf", "payout_ratio", "shares"} <= set(f["years"][0])
    assert client.get(f"{API}/fundamentals/999999").status_code == 404

    # --- ETF holdings (issuer download blocked -> Yahoo top-10 fallback)
    h = client.get(f"{API}/etf/{world.id}/holdings").json()
    assert h["source"] == "yahoo_top10" and h["coverage_pct"] == 10.0 and len(h["holdings"]) == 2
    assert {"as_of", "source", "coverage_pct", "holdings"} <= set(h)
    assert {"name", "isin", "symbol", "weight", "sector", "country"} <= set(h["holdings"][0])
    assert h["attempts"][0]["provider"] == "ishares" and h["attempts"][0]["ok"] is False

    # --- look-through: MSFT direct (4000) + 4.4 % of 10 000 in the ETF
    lt = client.get(f"{API}/lookthrough").json()
    assert lt["total_value"] == 14000.0
    m = next(i for i in lt["items"] if i["isin"] == "US5949181045")
    assert (m["direct_value"], m["etf_value"], m["total_value"]) == (4000.0, 440.0, 4440.0)
    assert any(i["key"] == "_other" and i["etf_value"] == 9000.0 for i in lt["items"])
    assert lt["by_sector"] and lt["by_country"]
    lt_acc = client.get(f"{API}/lookthrough", params={"account_id": portfolio["acc"].id}).json()
    assert lt_acc["total_value"] == 14000.0
    assert client.get(f"{API}/lookthrough", params={"account_id": 987654}).json()["items"] == []

    # --- status / fx
    st = client.get(f"{API}/status").json()
    assert st["provider"] == "fake" and st["scheduler"]["running"] is False
    assert {j["job"]: j["status"] for j in st["jobs"]} == {k: "ok" for k in body["jobs"]}
    assert st["counts"]["tracked_instruments"] == 3 and st["counts"]["quotes"] == 3
    assert st["counts"]["price_history_rows"] == 1500 and st["counts"]["etfs_with_holdings"] == 1
    assert st["fx_latest_date"] == "2026-11-11" and st["errors"] == []
    assert st["sources"]["etf_holdings"]["ishares"] == "implemented"
    assert st["sources"]["etf_holdings"]["vanguard"] == "best_effort"
    rates = client.get(f"{API}/fx").json()
    assert rates["date"] == "2026-11-11" and rates["rates"]["USD"] == 1.085 and rates["base"] == "EUR"


def test_refresh_scopes_and_offline(client, db, provider, portfolio):
    msft = portfolio["msft"]
    r = client.post(f"{API}/refresh", json={"scope": "quotes", "instrument_ids": [msft.id]})
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["updated"] == 1
    assert client.post(f"{API}/refresh").json()["scope"] == "quotes"            # body optional
    assert client.post(f"{API}/refresh", json={"scope": "history"}).json()["status"] == "ok"
    assert client.post(f"{API}/refresh", json={"scope": "events"}).json()["status"] == "ok"
    assert client.post(f"{API}/refresh", json={"scope": "etf"}).json()["status"] == "ok"
    assert client.post(f"{API}/refresh", json={"scope": "bogus"}).status_code == 422
    provider.offline = True
    r = client.post(f"{API}/refresh", json={"scope": "all"})
    assert r.status_code == 200 and r.json()["status"] in ("error", "partial")  # offline is a result, not a 500
    st = client.get(f"{API}/status").json()
    assert st["errors"] and any(e["job"] == "fx" for e in st["errors"])
    assert client.get(f"{API}/quotes").status_code == 200                       # cached data still served
    f = client.get(f"{API}/fundamentals/{portfolio['watch'].id}").json()        # never fetched + offline -> skeleton
    assert f["available"] is False and f["years"] == [] and f["fair_value"]["value"] is None


def test_manual_events_crud_and_ics(client, db, provider, portfolio):
    watch, msft = portfolio["watch"], portfolio["msft"]
    day = (TODAY + timedelta(days=60)).isoformat()
    r = client.post(f"{API}/events", json={"instrument_id": watch.id, "type": "agm", "date": day,
                                           "title": "Hauptversammlung", "url": "https://www.sap.com/investors"})
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["type"] == "agm" and ev["source"] == "manual" and ev["instrument_name"] == "SAP SE" and ev["id"]
    assert client.post(f"{API}/events", json={"instrument_id": 999999, "type": "agm", "date": day}).status_code == 404
    assert client.post(f"{API}/events", json={"instrument_id": watch.id, "type": "nope", "date": day}).status_code == 422
    # same instrument/type/date again -> replaced, not duplicated
    again = client.post(f"{API}/events", json={"instrument_id": watch.id, "type": "agm", "date": day, "title": "HV 2027"}).json()
    assert again["id"] == ev["id"] and again["title"] == "HV 2027"

    new_day = (TODAY + timedelta(days=61)).isoformat()
    r = client.patch(f"{API}/events/{ev['id']}", json={"date": new_day, "title": "HV (verschoben)"})
    assert r.status_code == 200 and r.json()["date"] == new_day and r.json()["title"] == "HV (verschoben)"
    assert client.patch(f"{API}/events/999999", json={"title": "x"}).status_code == 404
    other = client.post(f"{API}/events", json={"instrument_id": watch.id, "type": "agm", "date": day}).json()
    assert client.patch(f"{API}/events/{other['id']}", json={"date": new_day}).status_code == 409    # unique clash

    manual_div = client.post(f"{API}/events", json={"instrument_id": msft.id, "type": "dividend_payment", "date": day,
                                                    "amount": 0.83}).json()
    assert manual_div["currency"] == "EUR"
    listed = client.get(f"{API}/events", params={"types": "agm,dividend_payment"}).json()
    assert {e["id"] for e in listed} == {ev["id"], other["id"], manual_div["id"]}
    assert next(e for e in listed if e["id"] == manual_div["id"])["expected_payment"] == 8.3          # 10 shares

    assert client.delete(f"{API}/events/{other['id']}").status_code == 204
    assert client.delete(f"{API}/events/{other['id']}").status_code == 404

    # ICS: file upload, pasted text, errors
    ics = fixture_bytes("ir_calendar.ics")
    r = client.post(f"{API}/events/import-ics", params={"instrument_id": watch.id},
                    files={"file": ("sap.ics", ics, "text/calendar")})
    assert r.status_code == 200 and r.json() == {"parsed": 3, "imported": 3}
    r = client.post(f"{API}/events/import-ics", data={"instrument_id": str(msft.id), "text": ics.decode()})
    assert r.status_code == 200 and r.json()["imported"] == 3
    types = {e["type"] for e in client.get(f"{API}/events", params={"to": "2027-12-31", "instrument_id": watch.id}).json()}
    assert {"agm", "earnings", "investor_day"} <= types
    assert client.post(f"{API}/events/import-ics", data={"instrument_id": str(msft.id), "text": "nope"}).status_code == 422
    assert client.post(f"{API}/events/import-ics", data={"text": "x"}).status_code == 422
    assert client.post(f"{API}/events/import-ics", data={"instrument_id": str(msft.id)}).status_code == 422
    r = client.post(f"{API}/events/import-ics", data={"instrument_id": str(msft.id), "url": "https://ir.example.com/cal.ics"})
    assert r.status_code == 502                                                    # network blocked in tests
    assert client.post(f"{API}/events/import-ics", data={"instrument_id": str(msft.id), "url": "file:///etc/passwd"}).status_code == 422


def test_etf_upload_refresh_delete(client, db, provider, portfolio):
    world = portfolio["world"]
    assert client.get(f"{API}/etf/{world.id}/holdings").json()["holdings"] == []
    assert client.get(f"{API}/etf/999999/holdings").status_code == 404

    r = client.post(f"{API}/etf/{world.id}/holdings/upload",
                    files={"file": ("holdings.csv", fixture_bytes("manual_holdings_de.csv"), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "manual" and body["rows"] == 4 and body["coverage_pct"] == 35.0
    assert body["as_of"] == "2026-09-30" and body["holdings"][0]["isin"] == "US5949181045"
    r = client.post(f"{API}/etf/{world.id}/holdings/upload", data={"as_of": "2026-11-01"},
                    files={"file": ("x.xlsx", fixture_bytes("xtrackers_constituent.xlsx"), "application/octet-stream")})
    assert r.status_code == 200 and r.json()["rows"] == 7 and r.json()["as_of"] == "2026-11-01"
    r = client.post(f"{API}/etf/{world.id}/holdings/upload", files={"file": ("x.csv", b"a;b\n1;2\n", "text/csv")})
    assert r.status_code == 422 and "Kopfzeile" in r.json()["detail"]
    assert client.get(f"{API}/etf/{world.id}/holdings").json()["count"] == 7        # failed upload changed nothing

    assert client.post(f"{API}/etf/{world.id}/holdings/refresh").json()["status"] == "kept"
    forced = client.post(f"{API}/etf/{world.id}/holdings/refresh", params={"force": True}).json()
    assert forced["status"] == "ok" and forced["source"] == "yahoo_top10"
    assert client.delete(f"{API}/etf/{world.id}/holdings").status_code == 204
    assert client.get(f"{API}/etf/{world.id}/holdings").json()["count"] == 0


def test_search(client, provider):
    provider.searches["US5949181045"] = [SearchResult("MSFT", "Microsoft Corporation", "NMS", "stock", score=3e4),
                                         SearchResult("MSF.DE", "Microsoft Corporation", "GER", "stock", score=2e4)]
    r = client.get(f"{API}/search", params={"q": "US5949181045"})
    assert r.status_code == 200
    res = r.json()
    assert [x["symbol"] for x in res] == ["MSFT", "MSF.DE"]
    assert [x["preferred"] for x in res] == [False, True] and all(x["isin"] == "US5949181045" for x in res)
    assert client.get(f"{API}/search", params={"q": "unbekannt"}).json() == []
    assert client.get(f"{API}/search").status_code == 422
    provider.offline = True
    assert client.get(f"{API}/search", params={"q": "sap"}).json() == []            # offline -> empty, no 500


def test_fundamentals_on_demand_refresh(client, db, provider, portfolio):
    msft = portfolio["msft"]
    f = client.get(f"{API}/fundamentals/{msft.id}").json()                         # first GET fetches on demand
    assert f["available"] and f["valuation"]["pb"] == 10.0
    provider.fundamentals["MSF.DE"].pb = 11.0
    assert client.get(f"{API}/fundamentals/{msft.id}").json()["valuation"]["pb"] == 10.0     # cached
    assert client.get(f"{API}/fundamentals/{msft.id}", params={"refresh": True}).json()["valuation"]["pb"] == 11.0
    provider.offline = True
    assert client.get(f"{API}/fundamentals/{msft.id}", params={"refresh": True}).json()["valuation"]["pb"] == 11.0
    assert client.get(f"{API}/fx").json() == {"date": None, "base": "EUR", "rates": {}}
