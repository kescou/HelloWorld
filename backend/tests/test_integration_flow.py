"""Sprint-2 integration: document import -> ledger service -> transfers -> portfolio -> market data,
plus export, startup helpers and the background refresh. No network (FakeProvider, fake Ollama)."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import seed
from app.ingest import runner
from app.market import background
from app.market.providers.base import HistoryData, QuoteData, SearchResult
from app.models import (
    Account, BalanceSnapshot, Category, CategoryKind, Document, DocumentStatus, Instrument, Transaction, TxType,
)
from app.services import ledger, transfers
from fixtures.market.fakes import bars, no_network, provider  # noqa: F401
from test_ingest_support import IBAN_POSTBANK, IBAN_TR, accounts, fx, ingest_env, ollama  # noqa: F401

DOCS = "/api/documents"


def upload(client, name: str, mime: str = "text/plain", **form) -> dict:
    r = client.post(DOCS, files=[("files", (name, fx(name), mime))], data=form)
    assert r.status_code == 200, r.text
    return r.json()[0]


def paste(client, text: str, account_id: int | None = None) -> dict:
    r = client.post(f"{DOCS}/paste", json={"text": text, "account_id": account_id})
    assert r.status_code == 200, r.text
    return r.json()


def accept_all(client, doc_id: int, **params) -> dict:
    r = client.post(f"{DOCS}/{doc_id}/accept-all", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def balance(client, account_id: int) -> float:
    return next(a["balance"] for a in client.get("/api/accounts").json() if a["id"] == account_id)


@pytest.fixture()
def categories(db) -> dict[str, Category]:
    cats = {
        "Lebensmittel": Category(name="Lebensmittel", kind=CategoryKind.expense, match_rules=["rewe", "edeka"]),
        "Energie": Category(name="Energie", kind=CategoryKind.expense, match_rules=["stadtwerke"]),
        "Gehalt": Category(name="Gehalt", kind=CategoryKind.income, match_rules=["lohn", "gehalt"]),
        "Umbuchung": Category(name="Umbuchung", kind=CategoryKind.transfer, match_rules=[]),
        "Dividenden": Category(name="Dividenden", kind=CategoryKind.income, match_rules=[]),
    }
    db.add_all(cats.values())
    db.flush()
    return cats


# ----------------------------------------------------------------------------- import -> ledger service
def test_import_goes_through_ledger_service(client, db, ingest_env, accounts, categories, monkeypatch):
    calls = []
    original = ledger.create_transaction

    def spy(db_, data, **kw):
        calls.append(kw)
        return original(db_, data, **kw)

    monkeypatch.setattr(ledger, "create_transaction", spy)
    giro = accounts["ing_giro"]
    doc = upload(client, "ing_kontoauszug.txt")
    assert doc["status"] == "review"
    res = accept_all(client, doc["id"], include_flagged=True)
    assert res["errors"] == [] and res["accepted"] == doc["draft_count"] - doc["duplicate_count"]
    assert calls and all("occurrence" in kw for kw in calls)          # every booking went through the ledger service

    txs = list(db.scalars(select(Transaction).where(Transaction.account_id == giro.id).order_by(Transaction.id)))
    # auto-categorisation via the user's match_rules (not only the parser's keyword hint)
    by_cat = {t.id: t.category_id for t in txs}
    rewe = [t for t in txs if "REWE" in (t.counterparty or "") + (t.description or "")]
    assert len(rewe) == 2 and all(by_cat[t.id] == categories["Lebensmittel"].id for t in rewe)
    assert any(t.category_id == categories["Energie"].id for t in txs)
    assert any(t.category_id == categories["Gehalt"].id and t.type == TxType.deposit for t in txs)
    # two identical lines of ONE statement are two bookings with different hashes (|n suffix)
    assert rewe[0].amount == rewe[1].amount and rewe[0].booking_date == rewe[1].booking_date
    assert rewe[0].dedupe_hash != rewe[1].dedupe_hash
    assert rewe[0].dedupe_hash == ledger.dedupe_hash(giro.id, rewe[0].type, rewe[0].booking_date, None, None, rewe[0].amount)
    assert rewe[1].dedupe_hash == ledger.dedupe_hash(giro.id, rewe[1].type, rewe[1].booking_date, None, None, rewe[1].amount, 1)
    # the balance draft became a snapshot and matches the ledger
    snap = db.scalar(select(BalanceSnapshot).where(BalanceSnapshot.account_id == giro.id))
    assert snap is not None and snap.source_document_id == doc["id"]
    detail = client.get(f"/api/accounts/{giro.id}").json()
    (s,) = detail["snapshots"]
    assert s["date"] == snap.date.isoformat() and s["balance"] == float(snap.balance) and isinstance(s["computed_balance"], float)
    assert s["difference"] == pytest.approx(s["balance"] - s["computed_balance"])
    assert "opening_balance" in detail and "opening_date" in detail
    assert client.post("/api/categories/auto-assign").json()["assigned"] == 0
    # editing the description of the second twin must not trip the dedupe check
    r = client.patch(f"/api/transactions/{rewe[1].id}", json={"description": "zweiter Einkauf"})
    assert r.status_code == 200, r.text
    assert db.get(Transaction, rewe[1].id).dedupe_hash == rewe[1].dedupe_hash

    # the same data again (CSV export of the same month): everything is recognised as duplicate
    again = upload(client, "ing_umsatz.csv", mime="text/csv")
    assert again["pending_count"] == 0 and again["duplicate_count"] == again["draft_count"] > 0
    draft = client.get(f"{DOCS}/{again['id']}").json()["drafts"][0]
    assert client.post(f"{DOCS}/drafts/{draft['id']}/accept").status_code == 409
    n = len(txs)
    forced = client.post(f"{DOCS}/drafts/{draft['id']}/accept", params={"force": True})
    assert forced.status_code == 200 and forced.json()["status"] == "accepted"
    assert db.scalar(select(Transaction.dedupe_hash).where(Transaction.id == forced.json()["transaction_id"])) is not None
    assert len(list(db.scalars(select(Transaction).where(Transaction.account_id == giro.id)))) == n + 1


def test_upload_changes_positions_and_balances(client, db, ingest_env, accounts, categories):
    depot, cash = accounts["tr_depot"], accounts["tr_cash"]
    assert client.get("/api/portfolio/holdings").json() == []
    doc = upload(client, "tr_kauf.txt")
    accept_all(client, doc["id"])
    holdings = client.get("/api/portfolio/holdings").json()
    assert len(holdings) == 1 and holdings[0]["isin"] == "US88160R1014" and holdings[0]["quantity"] == 3.0
    assert holdings[0]["accounts"][0]["account_id"] == depot.id and holdings[0]["cost_basis"] == 325.3
    assert balance(client, cash.id) == -325.3                       # cash side on the settlement account
    summary = client.get("/api/portfolio/summary").json()
    assert summary["invested_value"] > 0 and round(summary["net_worth"], 2) == round(summary["cash_total"] + summary["invested_value"], 2)

    div = upload(client, "tr_dividende_usd.txt")
    accept_all(client, div["id"], include_flagged=True)
    dividend = db.scalar(select(Transaction).where(Transaction.type == TxType.dividend))
    assert dividend is not None and dividend.category_id == categories["Dividenden"].id
    assert balance(client, cash.id) == pytest.approx(-325.3 + float(dividend.amount))

    sale = upload(client, "tr_verkauf.txt")
    accept_all(client, sale["id"], include_flagged=True)
    sold = db.scalar(select(Transaction).where(Transaction.type == TxType.sell))
    assert sold is not None and sold.account_id == depot.id and sold.cash_account_id == cash.id
    # a failed commit leaves no half-created instrument behind
    bad = paste(client, "Wertpapierabrechnung Kauf\nISIN: DE0007164600\nStück 0\n", account_id=depot.id)
    assert bad["status"] in ("failed", "review")


# ----------------------------------------------------------------------------- transfers
def test_imported_transfer_is_linked_on_accept(client, db, ingest_env, accounts, categories):
    giro, tr = accounts["postbank"], accounts["tr_cash"]
    # 1) Trade Republic statement: "Einzahlung akzeptiert: <Postbank IBAN> auf <TR IBAN>" -> one-sided transfer
    doc = upload(client, "tr_kontoauszug.txt")
    accept_all(client, doc["id"], include_flagged=True)
    incoming = db.scalar(select(Transaction).where(Transaction.account_id == tr.id, Transaction.amount == Decimal("500.00")))
    assert incoming.type == TxType.transfer_in and incoming.transfer_peer_id is None
    assert incoming.category_id == categories["Umbuchung"].id
    # 2) the giro side arrives two days earlier, text only names the bank
    doc2 = paste(client, "30.09.2024  Dauerauftrag Sparen Trade Republic  -500,00\n01.10.2024  REWE Markt  -31,20\n", giro.id)
    accept_all(client, doc2["id"], include_flagged=True)
    outgoing = db.scalar(select(Transaction).where(Transaction.account_id == giro.id, Transaction.amount == Decimal("-500.00")))
    db.refresh(incoming)
    assert outgoing.type == TxType.transfer_out
    assert outgoing.transfer_peer_id == incoming.id and incoming.transfer_peer_id == outgoing.id
    # cashflow: neither side is income/expense
    months = {m["month"]: m for m in client.get("/api/analytics/cashflow", params={"months": 60}).json()}
    assert months["2024-09"]["expenses"] == 0.0
    assert months["2024-10"]["income"] < 100 and months["2024-10"]["expenses"] > 0       # REWE etc. still count
    # deleting one imported side keeps the other statement line (unlinked)
    assert client.delete(f"/api/transactions/{outgoing.id}").json() == {"deleted": [outgoing.id]}
    db.refresh(incoming)
    assert incoming.transfer_peer_id is None and incoming.type == TxType.transfer_in


def _cash_tx(db, account: Account, amount: str, on: date, text: str | None = None, type_: TxType | None = None,
             category: Category | None = None) -> Transaction:
    amt = Decimal(amount)
    tx = Transaction(account_id=account.id, type=type_ or (TxType.deposit if amt > 0 else TxType.withdrawal), booking_date=on,
                     amount=amt, counterparty=text, category_id=category.id if category else None)
    db.add(tx)
    db.flush()
    return tx


def test_link_transfers_endpoint_rules(client, db, accounts, categories):
    giro, ing, tr, sc = accounts["postbank"], accounts["ing_giro"], accounts["tr_cash"], accounts["sc_cash"]
    d = date(2025, 3, 10)
    # IBAN evidence (dates 3 days apart)
    a_out = _cash_tx(db, giro, "-1000", d, f"Übertrag {IBAN_TR[:4]} {IBAN_TR[4:]}")
    a_in = _cash_tx(db, tr, "1000", d + timedelta(days=3), "Einzahlung")
    # institution name evidence, although categorised as expense by a rule
    b_out = _cash_tx(db, giro, "-250", d, "Scalable Capital Sparrate", category=categories["Lebensmittel"])
    b_in = _cash_tx(db, sc, "250", d + timedelta(days=1), "Gutschrift")
    # amount only, unambiguous -> only SUGGESTED, linked after the user confirms
    c_out = _cash_tx(db, giro, "-77.77", d, "Überweisung")
    c_in = _cash_tx(db, ing, "77.77", d + timedelta(days=2), "Gutschrift")
    # amount only but ambiguous (two possible counterparts) -> not linked
    e_out = _cash_tx(db, giro, "-40", d, "Auszahlung")
    e_in1 = _cash_tx(db, ing, "40", d, "Gutschrift")
    e_in2 = _cash_tx(db, tr, "40", d + timedelta(days=1), "Gutschrift")
    # amount only but one side is a categorised expense -> not linked
    f_out = _cash_tx(db, giro, "-19.99", d, "REWE", category=categories["Lebensmittel"])
    f_in = _cash_tx(db, ing, "19.99", d, "Erstattung")
    # too far apart
    g_out = _cash_tx(db, giro, "-300", d, "Scalable")
    g_in = _cash_tx(db, sc, "300", d + timedelta(days=4), "Gutschrift")
    # one-sided: names another own account by IBAN, counterpart not imported yet
    h_out = _cash_tx(db, ing, "-600", d, f"Sparen {IBAN_POSTBANK}")

    r = client.post("/api/transactions/link-transfers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["linked"] == 2 and body["retyped"] == 1
    assert {(p["out_id"], p["in_id"], p["reason"]) for p in body["pairs"]} == {
        (a_out.id, a_in.id, "iban"), (b_out.id, b_in.id, "name")}
    sugg = {(p["out_id"], p["in_id"]): p for p in body["suggestions"]}
    assert set(sugg) == {(c_out.id, c_in.id), (e_out.id, e_in1.id), (e_out.id, e_in2.id)}
    assert sugg[(c_out.id, c_in.id)]["ambiguous"] is False and sugg[(e_out.id, e_in1.id)]["ambiguous"] is True
    db.refresh(c_out)
    assert c_out.transfer_peer_id is None and c_out.type == TxType.withdrawal      # nothing automatic
    # the read-only endpoint lists the same suggestions and changes nothing
    listed = client.get("/api/transactions/transfer-suggestions").json()
    assert {(p["out_id"], p["in_id"]) for p in listed} == set(sugg)
    db.refresh(c_out)
    assert c_out.transfer_peer_id is None and c_out.type == TxType.withdrawal
    r = client.post("/api/transactions/link-transfers/confirm", json={"out_id": c_out.id, "in_id": c_in.id})
    assert r.status_code == 200 and r.json()["reason"] == "confirmed"
    assert client.post("/api/transactions/link-transfers/confirm",
                       json={"out_id": c_out.id, "in_id": c_in.id}).status_code == 422      # already linked
    assert client.post("/api/transactions/link-transfers/confirm",
                       json={"out_id": f_out.id, "in_id": e_in1.id}).status_code == 422     # amounts differ
    assert client.post("/api/transactions/link-transfers/confirm", json={"out_id": 0, "in_id": 1}).status_code == 404
    for tx in (a_out, a_in, b_out, b_in, c_out, c_in, e_out, e_in1, e_in2, f_out, f_in, g_out, g_in, h_out):
        db.refresh(tx)
    assert (a_out.type, a_in.type) == (TxType.transfer_out, TxType.transfer_in) and a_out.transfer_peer_id == a_in.id
    assert b_out.category_id == categories["Umbuchung"].id and c_in.category_id == categories["Umbuchung"].id
    assert e_out.transfer_peer_id is None and e_out.type == TxType.withdrawal
    assert f_out.transfer_peer_id is None and f_in.type == TxType.deposit
    assert g_out.transfer_peer_id is None
    assert h_out.type == TxType.transfer_out and h_out.transfer_peer_id is None
    # idempotent
    assert client.post("/api/transactions/link-transfers").json()["linked"] == 0
    # the counterpart of the one-sided line arrives -> linked on the next run
    h_in = _cash_tx(db, giro, "600", d + timedelta(days=1), "Gutschrift ING")
    assert client.post("/api/transactions/link-transfers").json()["pairs"][0]["in_id"] == h_in.id

    # the user says "this is not a transfer": re-typing dissolves the link and the matcher leaves it alone
    r = client.patch(f"/api/transactions/{c_out.id}", json={"type": "withdrawal"})
    assert r.status_code == 200 and r.json()["transfer_peer_id"] is None
    db.refresh(c_in)
    assert c_in.transfer_peer_id is None
    assert {c_out.id, c_in.id} <= transfers.get_exclusions(db)
    assert client.post("/api/transactions/link-transfers").json()["linked"] == 0
    # editing a linked imported-style pair keeps both; manual transfers are still deleted as a pair
    out_tx, in_tx = (t["id"] for t in client.post("/api/transactions/transfer", json={
        "from_account_id": giro.id, "to_account_id": ing.id, "amount": 12.5, "date": d.isoformat()}).json())
    assert sorted(client.delete(f"/api/transactions/{out_tx}").json()["deleted"]) == sorted([out_tx, in_tx])


# ----------------------------------------------------------------------------- import -> market data
def test_imported_instrument_gets_market_data_on_refresh(client, db, ingest_env, accounts, provider):
    doc = upload(client, "tr_kauf.txt")
    accept_all(client, doc["id"])
    inst = db.scalar(select(Instrument).where(Instrument.isin == "US88160R1014"))
    assert inst is not None and inst.symbol is None
    # offline: degrades gracefully, the status says why
    provider.offline = True
    r = client.post("/api/market/refresh", json={"scope": "quotes"})
    assert r.status_code == 200 and r.json()["status"] == "error"
    status = client.get("/api/market/status").json()
    assert status["counts"]["without_symbol"] == 1 and status["online"] is False and status["source"] == "fake"
    assert status["errors"] and status["errors"][0].startswith("quotes: ") and status["error_log"][0]["job"] == "quotes"
    holding = client.get("/api/portfolio/holdings").json()[0]
    assert holding["price_source"] == "transaction" and holding["market_value"] > 0      # valued at the last trade price
    # online: symbol is resolved from the ISIN, quote + history + events are loaded
    provider.offline = False
    provider.searches["US88160R1014"] = [SearchResult(symbol="TL0.DE", name="Tesla Inc", exchange="GER", type="stock", currency="EUR")]
    provider.quotes["TL0.DE"] = QuoteData(symbol="TL0.DE", price=250.0, currency="EUR", previous_close=245.0)
    provider.history["TL0.DE"] = HistoryData(bars=bars(date.today() - timedelta(days=30), [200.0 + i for i in range(30)]), currency="EUR")
    r = client.post("/api/market/refresh", json={"scope": "quotes", "force": True})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    db.refresh(inst)
    assert inst.symbol == "TL0.DE"
    holding = client.get("/api/portfolio/holdings").json()[0]
    assert holding["price"] == 250.0 and holding["price_source"] == "quote" and holding["market_value"] == 750.0
    assert len(client.get(f"/api/market/history/{inst.id}", params={"range": "1M"}).json()) >= 20
    status = client.get("/api/market/status").json()
    assert status["counts"]["without_symbol"] == 0 and status["online"] is True and status["errors"] == []
    assert status["last_quote_update"] is not None


def test_refresh_all_runs_in_background(client, db, provider, monkeypatch):
    background.reset()
    pending = []
    monkeypatch.setattr(background, "spawn", pending.append)
    seen = {}

    def fake_run_job(scope, instrument_ids=None, force=False, on_progress=None, **kw):
        on_progress(3, 7, "history")
        seen.update(scope=scope, ids=instrument_ids, force=force, db=kw.get("db"))
        return {"scope": scope, "status": "partial", "jobs": {}, "duration_ms": 5}

    import app.market.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "run_job", fake_run_job)

    r = client.post("/api/market/refresh", json={"scope": "all", "force": True})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "running" and job["background"] is True and job["scope"] == "all" and job["job_id"]
    assert job["progress"] == {"done": 0, "total": 7, "current": None} and job["result"] is None
    status = client.get("/api/market/status").json()["refresh"]
    assert status["running"] is True and status["current"]["job_id"] == job["job_id"] and status["last"] is None
    # a second request while running does not queue another job
    again = client.post("/api/market/refresh", json={"scope": "all"}).json()
    assert again["job_id"] == job["job_id"] and again["already_running"] is True and len(pending) == 1

    pending.pop()()                                    # the worker thread's body
    assert seen == {"scope": "all", "ids": None, "force": True, "db": None}       # own session, not the request's
    status = client.get("/api/market/status").json()["refresh"]
    assert status["running"] is False and status["current"] is None
    assert status["last"]["status"] == "partial" and status["last"]["finished_at"] and status["last"]["result"]["scope"] == "all"
    done = client.get(f"/api/market/refresh/{job['job_id']}").json()
    assert done["status"] == "partial" and done["progress"]["done"] == 7
    assert client.get("/api/market/refresh/nope").status_code == 404
    # small scopes stay synchronous unless wait=false; `all` can be forced synchronous
    assert client.post("/api/market/refresh", params={"wait": "false"}, json={"scope": "fx"}).json()["background"] is True
    pending.pop()()
    background.reset()


# ----------------------------------------------------------------------------- export / startup / misc
def test_export_csv_and_backup(client, db):
    seed.seed_base(db)
    seed.seed_demo(db, today=date(2026, 6, 30), years=0.4)
    db.flush()
    n = client.get("/api/transactions", params={"limit": 1}).json()["total"]
    r = client.get("/api/export/transactions.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == n and {"booking_date", "account", "type", "isin", "amount", "category"} <= set(rows[0])
    total_de = sum(Decimal(row["amount"].replace(",", ".")) for row in rows)
    en = list(csv.DictReader(io.StringIO(client.get("/api/export/transactions.csv", params={"format": "en"}).text)))
    assert sum(Decimal(row["amount"]) for row in en) == total_de == db.scalar(select(__import__("sqlalchemy").func.sum(Transaction.amount)))
    acc = client.get("/api/accounts").json()[0]
    some = client.get("/api/export/transactions.csv", params={"account_id": acc["id"], "date_from": "2026-06-01", "date_to": "2026-06-30"})
    assert 0 < len(some.text.splitlines()) - 1 < n

    r = client.get("/api/export/backup.json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    dump = r.json()
    tables = dump["tables"]
    assert {"accounts", "instruments", "categories", "transactions", "balance_snapshots", "settings", "documents",
            "extraction_drafts", "fx_rates", "quotes", "instrument_events", "etf_holdings", "fundamentals"} <= set(tables)
    assert "price_history" not in tables and dump["meta"]["tables"]["transactions"] == len(tables["transactions"]) == n
    assert isinstance(tables["transactions"][0]["amount"], str) and tables["accounts"][0]["type"] in ("checking", "savings", "brokerage")
    assert "NaN" not in r.text and "Infinity" not in r.text
    full = client.get("/api/export/backup.json", params={"include_market_history": True}).json()
    assert len(full["tables"]["price_history"]) > 0


def test_demo_fundamentals_use_the_market_layout(client, db):
    seed.seed_base(db)
    seed.seed_demo(db)
    db.flush()
    stocks = [i for i in client.get("/api/instruments").json() if i["type"] == "stock"]
    assert len(stocks) >= 6
    for inst in stocks:
        f = client.get(f"/api/market/fundamentals/{inst['id']}").json()
        assert f["available"] is True and f["source"] == "demo" and len(f["years"]) == 10
        fv = f["fair_value"]
        assert fv["value"] and fv["price"] and fv["verdict"] in ("undervalued", "fair", "overvalued")
        assert fv["upside_pct"] == pytest.approx((fv["value"] / fv["price"] - 1) * 100, abs=0.05)
        assert 0.3 < fv["value"] / fv["price"] < 3, (inst["name"], fv)                     # plausible, not nonsense
        assert len(f["pe_history"]) >= 60 and len(f["fair_value_series"]) >= 60 and len(f["dividends_by_year"]) >= 10
        assert f["valuation"]["dividend_yield"] is not None and 0 < f["valuation"]["dividend_yield"] < 10
        assert f["dividend_growth"]["cagr_5y"] is not None and f["earnings_quality"]["score"] is not None


def test_unknown_api_path_is_json_404_and_health(client):
    r = client.get("/api/does/not/exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert client.post("/api/nope").status_code == 404
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"] is True


def test_startup_requeues_stuck_documents(db, monkeypatch):
    doc = Document(filename="x.txt", sha256="0" * 64, source_kind="paste", status=DocumentStatus.processing, raw_text="x")
    done = Document(filename="y.txt", sha256="1" * 64, source_kind="paste", status=DocumentStatus.imported, raw_text="y")
    db.add_all([doc, done])
    db.flush()
    submitted = []
    monkeypatch.setattr(runner, "submit", lambda doc_id, **kw: submitted.append(doc_id))
    runner.reset_recovery()
    assert runner.recover_stale(db) == 1 and submitted == [doc.id]
    assert runner.recover_stale(db) == 0                    # once per process
    runner.reset_recovery()


def test_app_starts_without_database_or_services(monkeypatch):
    """Lifespan must survive an unreachable database (and never touch Ollama/Yahoo)."""
    import app.core.db as core_db
    import app.main as main
    from fastapi.testclient import TestClient

    def boom(*a, **kw):
        raise RuntimeError("database is down")

    monkeypatch.setattr(core_db, "SessionLocal", boom)
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
    runner.reset_recovery()
