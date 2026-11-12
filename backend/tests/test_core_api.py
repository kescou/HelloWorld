"""API smoke tests for every Backend-Core endpoint (shapes per docs/ARCHITECTURE.md)."""
from datetime import date, timedelta

import pytest

from app import seed

TODAY = date.today()
ISO = TODAY.isoformat()


@pytest.fixture()
def base(client, db):
    seed.seed_base(db)
    db.flush()
    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    return accounts


def _post_tx(client, **payload):
    return client.post("/api/transactions", json=payload)


def test_accounts_crud_and_detail(client, base):
    assert len(base) == 7
    giro = base["Postbank Giro"]
    assert {"id", "name", "institution", "type", "currency", "iban", "color", "balance", "market_value",
            "total_value", "settlement_account_id", "is_active", "interest_rate", "sort_order"} <= set(giro)
    assert base["ING Depot"]["settlement_account_id"] == base["ING Girokonto"]["id"]

    r = client.post("/api/accounts", json={"name": "Kreditkarte", "institution": "ing", "type": "credit_card",
                                           "opening_balance": 150.5, "iban": "DE00123"})
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["balance"] == 150.5 and card["total_value"] == 150.5 and card["market_value"] == 0
    r = client.patch(f"/api/accounts/{card['id']}", json={"name": "Visa", "color": "#112233", "iban": None})
    assert r.status_code == 200 and r.json()["name"] == "Visa" and r.json()["iban"] is None
    assert client.post("/api/accounts", json={"name": "x", "type": "nope"}).status_code == 422
    assert client.post("/api/accounts", json={"name": "x", "settlement_account_id": 999999}).status_code == 404

    _post_tx(client, account_id=giro["id"], type="deposit", booking_date=(TODAY - timedelta(days=3)).isoformat(),
             amount=500)
    detail = client.get(f"/api/accounts/{giro['id']}").json()
    assert detail["balance"] == 500.0
    assert detail["history"][-1] == {"date": ISO, "value": 500.0}
    assert detail["history"][0]["value"] == 0.0 or len(detail["history"]) <= 5
    assert client.get("/api/accounts/999999").status_code == 404

    # delete: without transactions -> removed, with transactions -> deactivated
    assert client.delete(f"/api/accounts/{card['id']}").json()["deleted"] is True
    assert client.get(f"/api/accounts/{card['id']}").status_code == 404
    r = client.delete(f"/api/accounts/{giro['id']}").json()
    assert r == {"deleted": False, "deactivated": True, "transaction_count": 1}
    assert client.get(f"/api/accounts/{giro['id']}").json()["is_active"] is False
    assert len(client.get("/api/accounts?include_inactive=false").json()) == 6


def test_transactions_flow(client, base):
    giro, depot, cash = base["Postbank Giro"], base["Scalable Depot"], base["Scalable Tagesgeld"]
    r = _post_tx(client, account_id=giro["id"], type="withdrawal", booking_date=ISO, amount=-45.9,
                 counterparty="REWE Markt GmbH", description="Kartenzahlung")
    assert r.status_code == 201, r.text
    grocery = r.json()
    assert grocery["category_name"] == "Lebensmittel" and grocery["account_name"] == "Postbank Giro"

    # securities buy by ISIN without amount: instrument is created, amount computed, settlement account used
    payload = dict(account_id=depot["id"], type="buy", booking_date=ISO, isin="IE00B4L5Y983",
                   name="iShares Core MSCI World UCITS ETF", quantity=2.5, price=100.0, fees=1.0)
    r = _post_tx(client, **payload)
    assert r.status_code == 201, r.text
    buy = r.json()
    assert buy["amount"] == -251.0 and buy["cash_account_id"] == cash["id"]
    assert buy["instrument_isin"] == "IE00B4L5Y983" and buy["instrument_name"].startswith("iShares")
    assert buy["cash_account_name"] == "Scalable Tagesgeld"
    dup = _post_tx(client, **payload)
    assert dup.status_code == 409 and dup.json()["detail"]["existing_id"] == buy["id"]
    assert _post_tx(client, account_id=999999, type="deposit", booking_date=ISO, amount=1).status_code == 404
    assert _post_tx(client, account_id=giro["id"], type="deposit", booking_date=ISO).status_code == 422

    # transfer
    r = client.post("/api/transactions/transfer", json={
        "from_account_id": giro["id"], "to_account_id": cash["id"], "amount": 300, "date": ISO,
        "description": "Sparrate"})
    assert r.status_code == 201, r.text
    out_tx, in_tx = r.json()
    assert out_tx["type"] == "transfer_out" and in_tx["type"] == "transfer_in"
    assert out_tx["transfer_peer_id"] == in_tx["id"] and in_tx["transfer_peer_id"] == out_tx["id"]
    assert out_tx["amount"] == -300.0 and in_tx["amount"] == 300.0

    # list + filters
    listing = client.get("/api/transactions").json()
    assert listing["total"] == 4 and len(listing["items"]) == 4
    assert {"account_name", "instrument_name", "instrument_isin", "category_name", "dedupe_hash"} <= set(listing["items"][0])

    def total(**params):
        return client.get("/api/transactions", params=params).json()["total"]

    assert total(account_id=cash["id"]) == 2                # transfer_in + buy via settlement
    assert total(instrument_id=buy["instrument_id"]) == 1
    assert total(type="buy") == 1 and total(type="buy,withdrawal") == 2
    assert total(category_id=grocery["category_id"]) == 1
    assert total(q="rewe") == 1 and total(q="msci world") == 1 and total(q="zzz") == 0
    assert total(date_from=ISO, date_to=ISO) == 4
    assert total(date_to=(TODAY - timedelta(days=1)).isoformat()) == 0
    page = client.get("/api/transactions", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 4 and len(page["items"]) == 1
    assert client.get("/api/transactions", params={"type": "bogus"}).status_code == 422

    balances = {a["name"]: a for a in client.get("/api/accounts").json()}
    assert balances["Postbank Giro"]["balance"] == -345.9
    assert balances["Scalable Tagesgeld"]["balance"] == 49.0
    assert balances["Scalable Depot"]["market_value"] == 250.0 and balances["Scalable Depot"]["total_value"] == 250.0

    # patch + delete
    r = client.patch(f"/api/transactions/{buy['id']}", json={"quantity": 3, "notes": "korrigiert"})
    assert r.status_code == 200 and r.json()["amount"] == -301.0 and r.json()["notes"] == "korrigiert"
    r = client.patch(f"/api/transactions/{grocery['id']}", json={"category_id": None, "amount": -50})
    assert r.json()["category_id"] is None and r.json()["amount"] == -50.0
    assert client.get(f"/api/transactions/{grocery['id']}").json()["amount"] == -50.0
    assert client.patch("/api/transactions/999999", json={"notes": "x"}).status_code == 404
    assert sorted(client.delete(f"/api/transactions/{out_tx['id']}").json()["deleted"]) == sorted([out_tx["id"], in_tx["id"]])
    assert client.get(f"/api/transactions/{in_tx['id']}").status_code == 404
    assert client.delete(f"/api/transactions/{buy['id']}").json() == {"deleted": [buy["id"]]}
    assert client.delete("/api/transactions/999999").status_code == 404
    assert client.get("/api/transactions").json()["total"] == 1


def test_categories_crud_and_auto_assign(client, base):
    cats = client.get("/api/categories").json()
    assert {"id", "name", "kind", "parent_id", "color", "match_rules"} <= set(cats[0])
    giro = base["Postbank Giro"]
    tx = _post_tx(client, account_id=giro["id"], type="withdrawal", booking_date=ISO, amount=-9.99,
                  counterparty="Bäckerladen Huber XY").json()
    r = client.post("/api/categories", json={"name": "Haustier", "kind": "expense", "match_rules": ["Fressnapf", " "]})
    assert r.status_code == 201 and r.json()["match_rules"] == ["Fressnapf"]
    cat = r.json()
    assert client.post("/api/categories", json={"name": "haustier"}).status_code == 409
    child = client.post("/api/categories", json={"name": "Tierarzt", "parent_id": cat["id"]}).json()
    assert child["parent_id"] == cat["id"]
    r = client.patch(f"/api/categories/{cat['id']}", json={"match_rules": ["Fressnapf", "huber xy"], "color": "#abcdef"})
    assert r.status_code == 200 and r.json()["color"] == "#abcdef"
    assert tx["category_id"] is None
    r = client.post("/api/categories/auto-assign").json()
    assert r["updated"] == 1 and r["uncategorized"] == 0
    assert client.get(f"/api/transactions/{tx['id']}").json()["category_name"] == "Haustier"
    listed = {c["id"]: c for c in client.get("/api/categories").json()}
    assert listed[cat["id"]]["transaction_count"] == 1
    assert client.patch(f"/api/categories/{cat['id']}", json={"parent_id": cat["id"]}).status_code == 422
    r = client.delete(f"/api/categories/{cat['id']}").json()
    assert r == {"deleted": True, "uncategorized_transactions": 1}
    listed = {c["id"]: c for c in client.get("/api/categories").json()}
    assert cat["id"] not in listed and listed[child["id"]]["parent_id"] is None
    assert client.get(f"/api/transactions/{tx['id']}").json()["category_id"] is None
    assert client.delete("/api/categories/999999").status_code == 404


def test_instruments_endpoints(client, base, db):
    r = client.post("/api/instruments", json={"isin": "de0007164600", "symbol": "SAP.DE", "name": "SAP SE",
                                              "type": "stock", "sector": "Technology", "country": "Germany"})
    assert r.status_code == 201, r.text
    sap = r.json()
    assert sap["isin"] == "DE0007164600" and sap["currency"] == "EUR" and sap["is_watchlist"] is False
    again = client.post("/api/instruments", json={"isin": "DE0007164600", "is_watchlist": True})
    assert again.status_code == 200 and again.json()["id"] == sap["id"] and again.json()["is_watchlist"] is True
    watch = client.post("/api/instruments", json={"symbol": "nvda", "is_watchlist": True}).json()
    assert watch["symbol"] == "NVDA" and watch["name"] == "NVDA"
    assert client.post("/api/instruments", json={"name": "nix"}).status_code == 422
    assert client.post("/api/instruments", json={"isin": "SHORT"}).status_code == 422
    r = client.patch(f"/api/instruments/{watch['id']}", json={"name": "NVIDIA Corp", "currency": "usd", "is_watchlist": False})
    assert r.json()["name"] == "NVIDIA Corp" and r.json()["currency"] == "USD" and r.json()["is_watchlist"] is False
    assert client.patch(f"/api/instruments/{watch['id']}", json={"isin": "DE0007164600"}).status_code == 409
    assert client.patch("/api/instruments/999999", json={"name": "x"}).status_code == 404

    depot = base["ING Depot"]
    _post_tx(client, account_id=depot["id"], type="buy", booking_date=ISO, instrument_id=sap["id"], quantity=4, price=200)
    from test_core_helpers import add_quote
    from app.models.core import Instrument
    add_quote(db, db.get(Instrument, sap["id"]), 210, previous_close=200)

    assert [i["symbol"] for i in client.get("/api/instruments", params={"q": "sap"}).json()] == ["SAP.DE"]
    assert [i["id"] for i in client.get("/api/instruments", params={"watchlist": "true"}).json()] == [sap["id"]]
    assert len(client.get("/api/instruments").json()) == 2
    held = client.get("/api/instruments", params={"held": "true"}).json()
    assert len(held) == 1 and held[0]["quantity"] == 4.0 and held[0]["price"] == 210.0 and held[0]["day_change_pct"] == 5.0

    detail = client.get(f"/api/instruments/{sap['id']}").json()
    assert detail["name"] == "SAP SE"
    assert detail["quote"]["price"] == 210.0 and detail["quote"]["change"] == 10.0 and detail["quote"]["change_pct"] == 5.0
    assert detail["position"]["quantity"] == 4.0 and detail["position"]["market_value"] == 840.0
    assert len(detail["transactions"]) == 1 and detail["transactions"][0]["type"] == "buy"
    empty = client.get(f"/api/instruments/{watch['id']}").json()
    assert empty["quote"] is None and empty["position"] is None and empty["transactions"] == []
    assert client.get("/api/instruments/999999").status_code == 404


def test_portfolio_and_analytics_endpoints_on_demo_data(client, db):
    seed.seed_demo(db)
    db.flush()

    s = client.get("/api/portfolio/summary").json()
    assert {"net_worth", "cash_total", "invested_value", "cost_basis", "unrealized_gain", "unrealized_gain_pct",
            "realized_gain_ytd", "dividends_ytd", "interest_ytd", "day_change", "day_change_pct", "by_account",
            "updated_at"} <= set(s)
    assert s["net_worth"] == pytest.approx(s["cash_total"] + s["invested_value"], abs=0.02)
    assert {"account_id", "name", "value"} <= set(s["by_account"][0]) and len(s["by_account"]) == 7
    assert sum(a["value"] for a in s["by_account"]) == pytest.approx(s["net_worth"], abs=0.1)

    holdings = client.get("/api/portfolio/holdings").json()
    assert len(holdings) == 8
    assert {"instrument_id", "isin", "symbol", "name", "type", "quantity", "avg_cost", "cost_basis", "price",
            "price_currency", "market_value", "day_change", "day_change_pct", "unrealized_gain",
            "unrealized_gain_pct", "weight", "dividends_received", "sector", "country", "accounts"} <= set(holdings[0])
    assert {"account_id", "quantity"} <= set(holdings[0]["accounts"][0])
    assert sum(h["market_value"] for h in holdings) == pytest.approx(s["invested_value"], abs=0.1)
    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    tr = client.get("/api/portfolio/holdings", params={"account_id": accounts["Trade Republic Depot"]["id"]}).json()
    assert {h["symbol"] for h in tr} == {"MSFT", "AAPL", "O"}
    assert sum(h["market_value"] for h in tr) == pytest.approx(accounts["Trade Republic Depot"]["market_value"], abs=0.1)

    for rng in ("1M", "3M", "YTD", "1Y", "3Y", "5Y", "MAX"):
        hist = client.get("/api/portfolio/history", params={"range": rng}).json()
        assert hist and set(hist[0]) == {"date", "net_worth", "invested", "cost_basis", "cash"}, rng
        assert hist[-1]["date"] == ISO and hist[-1]["net_worth"] == pytest.approx(s["net_worth"], abs=0.1)
    assert len(client.get("/api/portfolio/history", params={"range": "1M"}).json()) == 31
    depot_hist = client.get("/api/portfolio/history", params={"range": "1Y", "account_id": accounts["ING Depot"]["id"]}).json()
    assert depot_hist[-1]["cash"] == 0.0 and depot_hist[-1]["invested"] == pytest.approx(accounts["ING Depot"]["market_value"], abs=0.1)
    assert client.get("/api/portfolio/history", params={"range": "7X"}).status_code == 422
    detail = client.get(f"/api/accounts/{accounts['ING Depot']['id']}").json()
    assert 700 <= len(detail["history"]) <= 732 and detail["history"][-1]["value"] == detail["total_value"]

    for rng in ("1M", "YTD", "1Y", "MAX"):
        perf = client.get("/api/portfolio/performance", params={"range": rng}).json()
        assert {"twr", "xirr", "absolute_gain", "series"} <= set(perf), rng
        assert {"date", "twr_index"} <= set(perf["series"][0]) and perf["series"][-1]["date"] == ISO
    inv = client.get("/api/portfolio/performance", params={"range": "MAX", "scope": "investments"}).json()
    assert inv["scope"] == "investments" and inv["end_value"] == pytest.approx(s["invested_value"], abs=0.1)

    for by in ("type", "sector", "country", "account", "currency"):
        for lookthrough in ("false", "true"):
            alloc = client.get("/api/portfolio/allocation", params={"by": by, "lookthrough": lookthrough}).json()
            assert alloc and set(alloc[0]) == {"key", "value", "weight"}, by
            assert sum(a["weight"] for a in alloc) == pytest.approx(100.0, abs=0.05), (by, lookthrough)
    by_type = {a["key"]: a["value"] for a in client.get("/api/portfolio/allocation").json()}
    assert by_type["cash"] == pytest.approx(s["cash_total"], abs=0.1)
    assert client.get("/api/portfolio/allocation", params={"by": "color"}).status_code == 422

    div = client.get("/api/analytics/dividends").json()
    assert {"received_by_month", "received_by_year", "forecast_12m", "yield_on_cost", "portfolio_yield"} <= set(div)
    assert set(div["received_by_month"][0]) >= {"month", "amount"} and len(div["received_by_month"]) == 12
    assert set(div["received_by_year"][0]) >= {"year", "amount"}
    month = next(m for m in div["forecast_12m"] if m["items"])
    assert {"instrument_id", "name", "amount", "ex_date", "pay_date"} <= set(month["items"][0])
    assert client.get("/api/analytics/dividends", params={"year": TODAY.year - 1}).json()["year"] == TODAY.year - 1

    flow = client.get("/api/analytics/cashflow").json()
    assert len(flow) == 12 and set(flow[0]) == {"month", "income", "expenses", "savings", "savings_rate"}
    assert len(client.get("/api/analytics/cashflow", params={"months": 6}).json()) == 6

    exp = client.get("/api/analytics/expenses-by-category").json()
    assert {"category_id", "name", "amount", "share"} <= set(exp[0]) and exp[0]["amount"] >= exp[-1]["amount"]
    ranged = client.get("/api/analytics/expenses-by-category", params={
        "date_from": (TODAY - timedelta(days=30)).isoformat(), "date_to": ISO}).json()
    assert 0 < sum(e["amount"] for e in ranged) < sum(e["amount"] for e in exp)

    fc = client.get("/api/analytics/forecast").json()
    assert len(fc["series"]) == 11 and set(fc["series"][0]) == {"year", "p10", "p50", "p90", "contributions"}
    assert fc["series"][0]["p50"] == pytest.approx(s["net_worth"], abs=0.1)
    fc2 = client.get("/api/analytics/forecast", params={"years": 5, "monthly_savings": 250, "expected_return": 4}).json()
    assert len(fc2["series"]) == 6 and fc2["monthly_savings"] == 250.0 and fc2["expected_return"] == 4.0

    gains = client.get("/api/analytics/realized-gains").json()
    assert gains and {"instrument", "date", "quantity", "proceeds", "cost", "gain", "taxes"} <= set(gains[0])
    year = int(gains[0]["date"][:4])
    assert all(g["date"].startswith(str(year)) for g in client.get("/api/analytics/realized-gains", params={"year": year}).json())


def test_endpoints_on_empty_database(client):
    assert client.get("/api/accounts").json() == []
    assert client.get("/api/transactions").json() == {"items": [], "total": 0}
    assert client.get("/api/categories").json() == []
    assert client.get("/api/instruments").json() == []
    s = client.get("/api/portfolio/summary").json()
    assert s["net_worth"] == 0 and s["by_account"] == []
    assert client.get("/api/portfolio/holdings").json() == []
    assert client.get("/api/portfolio/history").json() == []
    perf = client.get("/api/portfolio/performance").json()
    assert perf["twr"] == 0 and perf["xirr"] is None and perf["series"] == []
    assert client.get("/api/portfolio/allocation").json() == []
    div = client.get("/api/analytics/dividends").json()
    assert div["forecast_12m"] and all(m["amount"] == 0 for m in div["forecast_12m"])
    assert len(client.get("/api/analytics/cashflow").json()) == 12
    assert client.get("/api/analytics/expenses-by-category").json() == []
    assert client.get("/api/analytics/forecast").json()["series"][-1]["p50"] == 0
    assert client.get("/api/analytics/realized-gains").json() == []
    assert client.post("/api/categories/auto-assign").json() == {"assigned": 0, "updated": 0, "uncategorized": 0}
