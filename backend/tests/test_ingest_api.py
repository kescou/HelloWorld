"""HTTP API of the document ingestion: every endpoint + the review/commit flows."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.models import BalanceSnapshot, Document, Instrument, Transaction, TxType
from test_ingest_support import accounts, fx, ingest_env, make_png, make_text_pdf, ollama  # noqa: F401

API = "/api/documents"


def upload(client, name: str, data: bytes | None = None, mime: str = "text/plain", **form):
    r = client.post(API, files=[("files", (name, data if data is not None else fx(name), mime))], data=form)
    assert r.status_code == 200, r.text
    return r.json()[0]


def detail(client, doc_id: int) -> dict:
    r = client.get(f"{API}/{doc_id}")
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_review_accept_flow(client, db, ingest_env, accounts):
    doc = upload(client, "tr_kauf.txt")
    assert doc["status"] == "review" and doc["source_kind"] == "text" and doc["parser"] == "trade_republic.trade"
    assert doc["detected_institution"] == "trade_republic" and doc["detected_doc_type"] == "trade_buy"
    assert doc["draft_count"] == 1 and doc["pending_count"] == 1 and doc["error"] is None
    assert doc["processing_ms"] is not None and doc["duplicate_upload"] is False
    # stored content-addressed under upload_dir/<sha[:2]>/<sha>.<ext>
    row = db.get(Document, doc["id"])
    p = Path(row.storage_path)
    assert p.is_file() and p.parent.name == row.sha256[:2] and p.name == f"{row.sha256}.txt"
    assert Path(get_settings().upload_dir) in p.parents

    d = detail(client, doc["id"])
    assert "WERTPAPIERABRECHNUNG" in d["raw_text"] and d["timings"]["parse_ms"] >= 0
    (draft,) = d["drafts"]
    assert draft["kind"] == "transaction" and draft["status"] == "pending" and draft["confidence"] >= 0.95
    assert draft["payload"]["account_id"] == accounts["tr_depot"].id
    assert {"account_id", "type", "booking_date", "value_date", "isin", "wkn", "name", "symbol", "quantity", "price", "amount",
            "fees", "taxes", "currency", "fx_rate", "counterparty", "description", "category_hint"} <= set(draft["payload"])

    r = client.post(f"{API}/drafts/{draft['id']}/accept")
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    tx = db.get(Transaction, r.json()["transaction_id"])
    assert tx.type == TxType.buy and tx.amount == Decimal("-325.30") and tx.cash_account_id == accounts["tr_cash"].id
    assert tx.source_document_id == doc["id"] and tx.instrument.isin == "US88160R1014"
    assert detail(client, doc["id"])["status"] == "imported"
    # accepting twice is idempotent
    assert client.post(f"{API}/drafts/{draft['id']}/accept").json()["transaction_id"] == tx.id
    assert len(list(db.scalars(select(Transaction)))) == 1


def test_reupload_is_deduplicated_by_sha256(client, db, ingest_env, accounts):
    first = upload(client, "tr_kauf.txt")
    again = upload(client, "anderer_name.txt", fx("tr_kauf.txt"))
    assert again["id"] == first["id"] and again["duplicate_upload"] is True
    assert len(list(db.scalars(select(Document)))) == 1


def test_multi_upload_and_list_filter(client, ingest_env, accounts):
    r = client.post(API, files=[("files", ("a.txt", fx("tr_kauf.txt"), "text/plain")),
                                ("files", ("b.txt", fx("unknown_broker.txt"), "text/plain")),
                                ("files[]", ("c.csv", fx("ing_umsatz.csv"), "text/csv"))])
    assert r.status_code == 200 and [d["status"] for d in r.json()] == ["review", "failed", "review"]
    failed = r.json()[1]
    assert "Ollama" in failed["error"] and "starten" in failed["error"]         # clear German hint
    assert r.json()[2]["source_kind"] == "csv" and r.json()[2]["parser"] == "ing.csv"
    listed = client.get(API).json()
    assert len(listed) == 3 and {"id", "filename", "source_kind", "status", "detected_institution", "detected_doc_type", "parser",
                                 "draft_count", "pending_count", "error", "created_at", "processing_ms"} <= set(listed[0])
    assert [d["filename"] for d in client.get(API, params={"status": "failed"}).json()] == ["b.txt"]
    assert len(client.get(API, params={"status": "review,failed"}).json()) == 3
    assert client.get(API, params={"status": "quatsch"}).status_code == 422


def test_upload_validation_errors(client, ingest_env, accounts):
    assert client.post(API).status_code == 422
    assert client.post(API, files=[("files", ("leer.txt", b"", "text/plain"))]).status_code == 422
    assert client.post(API, files=[("files", ("a.txt", b"x", "text/plain"))], data={"account_id": 999999}).status_code == 422
    assert client.get(f"{API}/999999").status_code == 404
    assert client.post(f"{API}/drafts/999999/accept").status_code == 404


def test_csv_accept_all_skips_flagged_and_creates_balance(client, db, ingest_env, accounts):
    doc = upload(client, "ing_umsatz.csv", mime="text/csv")
    d = detail(client, doc["id"])
    assert doc["draft_count"] == 8 and all(x["payload"]["account_id"] == accounts["ing_giro"].id for x in d["drafts"])   # via IBAN
    r = client.post(f"{API}/{doc['id']}/accept-all")
    body = r.json()
    assert r.status_code == 200 and body["accepted"] == 7 and body["skipped"] == 1 and body["errors"] == []
    assert body["document"]["status"] == "review" and body["document"]["pending_count"] == 1   # Wertpapierkauf line waits
    txs = list(db.scalars(select(Transaction).order_by(Transaction.booking_date, Transaction.id)))
    assert len(txs) == 6 and sum(t.amount for t in txs) == Decimal("2342.80")
    assert sum(1 for t in txs if t.amount == Decimal("-23.45")) == 2            # identical lines both booked
    assert all(t.cash_account_id is None and t.account_id == accounts["ing_giro"].id for t in txs)
    snap = db.scalar(select(BalanceSnapshot))
    assert snap.balance == Decimal("2587.90") and snap.account_id == accounts["ing_giro"].id and snap.source_document_id == doc["id"]
    # the flagged one can be forced in bulk
    r = client.post(f"{API}/{doc['id']}/accept-all", params={"include_flagged": True})
    assert r.json()["accepted"] == 1 and r.json()["document"]["status"] == "imported"


def test_overlapping_statement_is_recognised_as_duplicates(client, db, ingest_env, accounts):
    csv_doc = upload(client, "ing_umsatz.csv", mime="text/csv")
    client.post(f"{API}/{csv_doc['id']}/accept-all", params={"include_flagged": True})
    pdf = upload(client, "Kontoauszug_2023_03.pdf", make_text_pdf(fx("ing_kontoauszug.txt").decode()), mime="application/pdf")
    assert pdf["source_kind"] == "pdf" and pdf["parser"] == "ing.statement"
    assert pdf["duplicate_count"] == 8 and pdf["pending_count"] == 0 and pdf["status"] == "imported"
    assert len(list(db.scalars(select(Transaction)))) == 7


def test_trade_then_giro_statement_cash_side_is_duplicate(client, db, ingest_env, accounts):
    trade = upload(client, "ing_kauf.txt")
    d = detail(client, trade["id"])
    assert d["drafts"][0]["payload"]["account_id"] == accounts["ing_depot"].id
    client.post(f"{API}/{trade['id']}/accept-all")
    tx = db.scalar(select(Transaction))
    assert tx.cash_account_id == accounts["ing_giro"].id
    stmt = detail(client, upload(client, "ing_kontoauszug.txt")["id"])
    wp = next(x for x in stmt["drafts"] if x["payload"].get("amount") == -754.9)
    assert wp["status"] == "duplicate" and wp["payload"]["duplicate_of"] == tx.id
    r = client.post(f"{API}/drafts/{wp['id']}/accept")
    assert r.status_code == 409 and r.json()["detail"]["transaction_id"] == tx.id


def test_tr_statement_after_single_documents(client, db, ingest_env, accounts):
    for name in ("tr_sparplan.txt", "tr_dividende_usd.txt", "tr_zinsen.txt"):
        doc = upload(client, name)
        assert client.post(f"{API}/{doc['id']}/accept-all").json()["accepted"] == 1, name
    interest = db.scalar(select(Transaction).where(Transaction.type == TxType.interest))
    assert interest.account_id == accounts["tr_cash"].id and interest.taxes == Decimal("6.16")
    stmt = detail(client, upload(client, "tr_kontoauszug.txt")["id"])
    by_type = {x["payload"].get("type", "balance"): x for x in stmt["drafts"]}
    assert by_type["buy"]["status"] == "duplicate" and by_type["dividend"]["status"] == "duplicate"
    assert by_type["interest"]["status"] == "duplicate"
    # the deposit came from the user's own Postbank IBAN -> recognised as an own-account transfer
    assert by_type["transfer_in"]["status"] == "pending" and by_type["withdrawal"]["status"] == "pending"
    assert by_type["transfer_in"]["payload"]["transfer_account_id"] == accounts["postbank"].id
    assert by_type["buy"]["payload"]["account_id"] == accounts["tr_depot"].id
    assert by_type["transfer_in"]["payload"]["account_id"] == accounts["tr_cash"].id
    body = client.post(f"{API}/{stmt['id']}/accept-all").json()
    assert body["accepted"] == 3 and body["document"]["status"] == "imported"     # deposit, card payment, balance
    assert len(list(db.scalars(select(Transaction)))) == 5


def test_account_hint_overrides_detection(client, ingest_env, accounts):
    doc = upload(client, "paste_statement.txt", account_id=str(accounts["postbank"].id))
    d = detail(client, doc["id"])
    assert doc["account_id"] == accounts["postbank"].id
    assert all(x["payload"]["account_id"] == accounts["postbank"].id for x in d["drafts"])


def test_paste_patch_reject_and_reopen(client, db, ingest_env, accounts):
    text = fx("paste_statement.txt").decode()
    r = client.post(f"{API}/paste", json={"text": text})
    assert r.status_code == 200
    doc = r.json()
    assert doc["source_kind"] == "paste" and doc["status"] == "review" and doc["parser"] == "generic.statement"
    assert doc["has_file"] is False and doc["account_id"] == accounts["postbank"].id        # IBAN in the pasted text
    assert client.post(f"{API}/paste", json={"text": text}).json()["duplicate_upload"] is True
    assert client.post(f"{API}/paste", json={"text": "   "}).status_code == 422
    assert client.get(f"{API}/{doc['id']}/file").text == text

    drafts = detail(client, doc["id"])["drafts"]
    rewe = drafts[0]
    # correct amount + account, German number string is accepted
    r = client.patch(f"{API}/drafts/{rewe['id']}", json={"payload": {"amount": "-35,56", "account_id": accounts["ing_giro"].id,
                                                                     "category_hint": "Lebensmittel"}})
    assert r.status_code == 200, r.text
    p = r.json()["payload"]
    assert p["amount"] == -35.56 and p["account_id"] == accounts["ing_giro"].id and p["counterparty"] == "REWE Markt GmbH"
    assert client.patch(f"{API}/drafts/{rewe['id']}", json={"payload": {"account_id": 424242}}).status_code == 422
    # turning a line into a securities purchase re-runs the arithmetic check
    r = client.patch(f"{API}/drafts/{drafts[1]['id']}", json={"payload": {"type": "buy", "isin": "US0378331005", "quantity": 10, "price": 100}})
    assert r.json()["payload"]["review_required"] and any(w.startswith("Rechenprobe") for w in r.json()["payload"]["warnings"])

    r = client.post(f"{API}/drafts/{drafts[2]['id']}/reject")
    assert r.json()["status"] == "rejected"
    r = client.patch(f"{API}/drafts/{drafts[2]['id']}", json={"payload": {}, "status": "pending"})
    assert r.json()["status"] == "pending"
    client.post(f"{API}/drafts/{drafts[2]['id']}/reject")

    r = client.post(f"{API}/drafts/{rewe['id']}/accept")
    tx = db.get(Transaction, r.json()["transaction_id"])
    assert tx.amount == Decimal("-35.56") and tx.account_id == accounts["ing_giro"].id
    assert client.patch(f"{API}/drafts/{rewe['id']}", json={"payload": {"amount": 1}}).status_code == 409
    assert client.post(f"{API}/drafts/{rewe['id']}/reject").status_code == 409
    body = client.post(f"{API}/{doc['id']}/accept-all").json()
    assert body["accepted"] == 3 and body["skipped"] == 1                    # the half-edited 'buy' stays for review
    assert body["document"]["status"] == "review"


def test_accept_without_account_gives_german_422(client, db, ingest_env):
    doc = upload(client, "tr_kauf.txt")                      # no accounts exist
    d = detail(client, doc["id"])["drafts"][0]
    assert d["payload"]["account_id"] is None and d["payload"]["review_required"]
    r = client.post(f"{API}/drafts/{d['id']}/accept")
    assert r.status_code == 422 and "Konto" in r.json()["detail"]
    body = client.post(f"{API}/{doc['id']}/accept-all", params={"include_flagged": True}).json()
    assert body["accepted"] == 0 and body["errors"][0]["draft_id"] == d["id"]
    assert db.scalar(select(Transaction)) is None and db.scalar(select(Instrument)) is None


def test_failed_then_reprocess_with_llm(client, db, ingest_env, accounts):
    doc = upload(client, "unknown_broker.txt")
    assert doc["status"] == "failed" and doc["draft_count"] == 0
    ingest_env.down = False
    ingest_env.queue({"institution": "other", "doc_type": "trade_buy", "balances": [], "transactions": [{
        "type": "buy", "booking_date": "2024-11-11", "isin": "DE0007236101", "name": "Siemens AG", "quantity": 12,
        "price": 180.0, "amount": -2169.90, "fees": 9.9}]})
    r = client.post(f"{API}/{doc['id']}/reprocess", json={"force_llm": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "review" and out["parser"] == "llm:qwen2.5:7b-instruct" and out["used_llm"] and out["error"] is None
    d = detail(client, doc["id"])["drafts"][0]
    assert d["payload"]["verified"] and d["payload"]["account_id"] is None      # unknown bank -> user must choose
    client.patch(f"{API}/drafts/{d['id']}", json={"payload": {"account_id": accounts["sc_depot"].id}})
    r = client.post(f"{API}/drafts/{d['id']}/accept")
    assert r.status_code == 200
    assert db.get(Transaction, r.json()["transaction_id"]).cash_account_id == accounts["sc_cash"].id
    # reprocess without body keeps the accepted draft and flags the re-extracted one as duplicate
    ingest_env.queue({"balances": [], "transactions": [{"type": "buy", "booking_date": "2024-11-11", "isin": "DE0007236101",
                                                        "name": "Siemens AG", "quantity": 12, "price": 180.0, "amount": -2169.90, "fees": 9.9}]})
    out = client.post(f"{API}/{doc['id']}/reprocess").json()
    assert out["accepted_count"] == 1 and out["draft_count"] == 2 and out["pending_count"] == 1
    assert client.post(f"{API}/999999/reprocess").status_code == 404


def test_reupload_of_failed_document_retries(client, ingest_env, accounts):
    first = upload(client, "unknown_broker.txt")
    assert first["status"] == "failed"
    ingest_env.down = False
    ingest_env.queue({"transactions": [{"type": "withdrawal", "booking_date": "2024-11-13", "amount": -2169.9}], "balances": []})
    again = upload(client, "unknown_broker.txt")
    assert again["id"] == first["id"] and again["status"] == "review"


def test_image_upload_uses_vision_model(client, ingest_env, accounts):
    ingest_env.down = False
    ingest_env.queue({"institution": "postbank", "doc_type": "account_statement", "balances": [{"date": "2024-11-12", "balance": 3210.45}],
                      "transactions": [{"type": "withdrawal", "booking_date": "2024-11-12", "amount": -34.56, "counterparty": "REWE"}]})
    doc = upload(client, "Bildschirmfoto.png", make_png(), mime="image/png")
    assert doc["source_kind"] == "image" and doc["parser"] == "llm:qwen2.5vl:7b" and doc["detected_institution"] == "postbank"
    d = detail(client, doc["id"])
    assert [x["kind"] for x in d["drafts"]] == ["transaction", "balance"]
    assert all(x["payload"]["account_id"] == accounts["postbank"].id for x in d["drafts"])
    r = client.get(f"{API}/{doc['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"


def test_file_download_and_delete(client, db, ingest_env, accounts):
    doc = upload(client, "tr_kauf.pdf", make_text_pdf(fx("tr_kauf.txt").decode()), mime="application/pdf")
    assert doc["parser"] == "trade_republic.trade" and doc["has_file"]
    r = client.get(f"{API}/{doc['id']}/file")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-" and r.headers["content-type"] == "application/pdf"
    path = Path(db.get(Document, doc["id"]).storage_path)
    client.post(f"{API}/{doc['id']}/accept-all")
    tx_id = db.scalar(select(Transaction.id))
    r = client.delete(f"{API}/{doc['id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert not path.exists() and client.get(f"{API}/{doc['id']}").status_code == 404
    db.expire_all()
    tx = db.get(Transaction, tx_id)
    assert tx is not None and tx.source_document_id is None           # imported data survives
    # same file can be uploaded again afterwards; its draft is now a duplicate of the kept transaction
    again = upload(client, "tr_kauf.pdf", make_text_pdf(fx("tr_kauf.txt").decode()), mime="application/pdf")
    assert again["duplicate_count"] == 1
    assert client.delete(f"{API}/{again['id']}").status_code == 200
    assert client.delete(f"{API}/{again['id']}").status_code == 404


def test_delete_with_transactions(client, db, ingest_env, accounts):
    doc = upload(client, "ing_umsatz.csv", mime="text/csv")
    client.post(f"{API}/{doc['id']}/accept-all")
    assert len(list(db.scalars(select(Transaction)))) == 6
    r = client.delete(f"{API}/{doc['id']}", params={"delete_transactions": True})
    assert r.json()["transactions_deleted"] == 6
    db.expire_all()
    assert db.scalar(select(Transaction)) is None and db.scalar(select(BalanceSnapshot)) is None


def test_llm_status_endpoint(client, ingest_env):
    r = client.get(f"{API}/llm/status")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["models"] == [] and body["text_model"] == "qwen2.5:7b-instruct"
    assert body["vision_model"] == "qwen2.5vl:7b" and "Ollama" in body["error"]
    ingest_env.down = False
    body = client.get(f"{API}/llm/status").json()
    assert body["available"] is True and body["text_model_available"] and body["vision_model_available"]
    assert set(body["models"]) == {"qwen2.5:7b-instruct", "qwen2.5vl:7b"} and body["hint"] is None


# ----------------------------------------------------------------------------- security (reviewer M4)
def test_uploaded_html_and_svg_are_never_served_as_active_content(client, db, ingest_env, accounts):
    """Stored XSS: an uploaded .html/.svg was served inline with its own type from the app's origin."""
    evil = b"<html><script>fetch('/api/accounts/1',{method:'DELETE'})</script></html>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
    for name, data, mime in (("x.html", evil, "text/html"), ("x.svg", svg, "image/svg+xml"),
                             ("fake.pdf", evil + b" 2", "application/pdf"), ("fake.png", svg + b" 2", "image/png")):
        doc = upload(client, name, data, mime)
        r = client.get(f"{API}/{doc['id']}/file")
        assert r.status_code == 200
        ctype = r.headers["content-type"]
        assert ctype.startswith("text/plain") or (ctype == "application/octet-stream"
                                                  and r.headers["content-disposition"].startswith("attachment")), (name, ctype)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
    # real PDFs and raster images are still shown inline
    pdf = upload(client, "echt.pdf", make_text_pdf("Kontoauszug"), "application/pdf")
    r = client.get(f"{API}/{pdf['id']}/file")
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    png = upload(client, "echt.png", make_png(), "text/html")           # the claimed type is ignored
    r = client.get(f"{API}/{png['id']}/file")
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"


def test_foreign_host_and_cross_site_origin_are_rejected(client):
    """DNS rebinding / CSRF: requests with a foreign Host were answered, any page could POST to the API."""
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/health", headers={"host": "evil.example"})
    assert r.status_code == 400 and "ALLOWED_HOSTS" in r.json()["detail"]
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "LOCALHOST"):
        assert client.get("/api/health", headers={"host": host}).status_code == 200, host
    body = {"name": "CSRF", "institution": "x", "type": "checking"}
    for origin in ("https://evil.example", "null", "http://localhost.evil.example:8000"):
        r = client.post("/api/accounts", json=body, headers={"origin": origin})
        assert r.status_code == 403, origin
    assert client.get("/api/accounts").json() == []
    # same-origin and the Vite dev server are fine; reads from anywhere are harmless (CORS keeps them unreadable)
    assert client.post("/api/accounts", json=body, headers={"host": "localhost:8000", "origin": "http://localhost:5173"}).status_code == 201
    assert client.post("/api/accounts", json=body | {"name": "B"}, headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"}).status_code == 201
    assert client.get("/api/accounts", headers={"origin": "https://evil.example"}).status_code == 200
