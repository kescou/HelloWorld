"""LLM fallback against a fake Ollama (httpx.MockTransport) – no network."""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.ingest import extract as ex
from app.ingest import llm
from app.ingest.config import get_ingest_settings
from app.ingest.pipeline import extract_document
from test_ingest_support import FakeOllama, fx, make_png, make_scanned_pdf, make_text_pdf, ollama, tx  # noqa: F401

SIEMENS = {"institution": "other", "doc_type": "trade_buy", "transactions": [{
    "type": "buy", "booking_date": "2024-11-11", "value_date": "2024-11-13", "isin": "DE0007236101", "wkn": None,
    "name": "Siemens AG", "quantity": 12, "price": 180.0, "amount": 2169.90, "fees": 9.9, "taxes": 0,
    "currency": "EUR", "fx_rate": None, "counterparty": "Musterbank AG", "description": "Kauf Siemens AG"}], "balances": []}


def test_status_reports_models_and_missing_pull_hint(ollama):
    ollama.down = False
    ollama.models = ["qwen2.5:7b-instruct", "llama3.2:latest"]
    st = llm.get_client().status()
    assert st["available"] and st["text_model_available"] and not st["vision_model_available"]
    assert "ollama pull qwen2.5vl:7b" in st["hint"] and "llama3.2:latest" in st["models"]


def test_status_when_down(ollama):
    st = llm.get_client().status()
    assert st["available"] is False and st["models"] == [] and "Ollama" in st["error"] and "ollama serve" in st["hint"]


def test_has_model_latest_tag():
    assert llm.OllamaClient.has_model(["llama3.2:latest"], "llama3.2")
    assert not llm.OllamaClient.has_model(["qwen2.5:14b"], "qwen2.5:7b-instruct")


def test_text_fallback_request_shape_and_normalisation(ollama):
    ollama.down = False
    ollama.queue(SIEMENS)
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert res.used_llm and res.parser == "llm:qwen2.5:7b-instruct" and res.error is None
    (p,) = tx(res)
    assert p["amount"] == -2169.9                 # sign convention enforced (model said +)
    assert p["verified"] and p["isin"] == "DE0007236101" and p["booking_date"] == "2024-11-11"
    assert res.drafts[0].confidence == pytest.approx(0.85)    # 0.7 base + 0.15 for a passed arithmetic check
    body = ollama.chats[0]
    assert body["stream"] is False and body["options"]["temperature"] == 0
    assert body["options"]["num_ctx"] == get_ingest_settings().llm_num_ctx
    assert body["keep_alive"] == get_ingest_settings().llm_keep_alive
    assert body["format"]["required"] == ["transactions", "balances"]          # JSON schema, not just "json"
    assert body["messages"][0]["role"] == "system" and "Siemens" in body["messages"][-1]["content"]
    assert sum(1 for m in body["messages"] if m["role"] == "assistant") == 3    # few-shot examples


def test_malformed_json_is_retried_with_correction_prompt(ollama):
    ollama.down = False
    ollama.queue("Hier ist das Ergebnis: leider kaputt {", SIEMENS)
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert len(ollama.chats) == 2 and len(tx(res)) == 1
    assert "kein gültiges JSON" in ollama.chats[1]["messages"][-1]["content"]


def test_malformed_json_twice_fails_with_german_error(ollama):
    ollama.down = False
    ollama.queue("nope", "still nope")
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert res.drafts == [] and "Sprachmodell" in res.error


@pytest.mark.parametrize("raw", [
    '```json\n{"transactions": [], "balances": []}\n```',
    'Gerne! {"transactions": [], "balances": [],}',
    '<think>hmm</think>{"transactions": [], "balances": []}',
])
def test_repair_json_variants(raw):
    assert llm.repair_json(raw) == {"transactions": [], "balances": []}


def test_repair_json_truncated_keeps_complete_items():
    raw = '{"transactions": [{"type": "deposit", "booking_date": "2024-01-01", "amount": 5.0}, {"type": "with'
    obj = llm.repair_json(raw)
    assert obj["transactions"] == [{"type": "deposit", "booking_date": "2024-01-01", "amount": 5.0}]
    with pytest.raises(llm.LLMError):
        llm.repair_json("kein json")


def test_unavailable_gives_german_error_but_parsers_still_work(ollama):
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert res.drafts == [] and "Ollama" in res.error and "starten" in res.error
    ok = extract_document(fx("tr_kauf.txt"), "text", "tr.txt")
    assert ok.error is None and len(ok.drafts) == 1 and not ollama.chats


def test_missing_model_tells_user_to_pull(ollama):
    ollama.down = False
    ollama.models = ["llama3.2:latest"]
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert "ollama pull qwen2.5:7b-instruct" in res.error


def test_timeout_is_reported_as_unavailable(ollama):
    ollama.down = False
    ollama.queue(lambda body: httpx.ReadTimeout("slow"))
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    assert "nicht innerhalb" in res.error


def test_failed_selfcheck_escalates_to_llm_and_takes_verified_answer(ollama):
    """Parser reads a wrong total -> arithmetic check fails -> LLM is asked -> its verified result wins."""
    ollama.down = False
    good = {"transactions": [{"type": "buy", "booking_date": "2022-01-03", "isin": "IE00B4L5Y983", "name": "iShares Core MSCI World",
                              "quantity": 10, "price": 78.5, "amount": -785.99, "fees": 0.99, "taxes": 0}], "balances": []}
    ollama.queue(good)
    broken = fx("baader_kauf.txt").decode().replace("STK 10 iShs", "STK 11 iShs").encode()
    res = extract_document(broken, "text", "baader.txt")
    assert res.used_llm and res.parser.startswith("llm:")
    assert tx(res)[0]["quantity"] == 10.0 and any("Selbstprüfung" in w for w in res.warnings)


def test_failed_selfcheck_keeps_parser_result_when_llm_is_down(ollama):
    broken = fx("baader_kauf.txt").decode().replace("STK 10 iShs", "STK 11 iShs").encode()
    res = extract_document(broken, "text", "baader.txt")
    assert res.parser == "scalable.baader" and res.error is None
    assert res.drafts[0].review_required and any("Sprachmodell nicht verfügbar" in w for w in res.warnings)


def test_force_llm_skips_parsers(ollama):
    ollama.down = False
    ollama.queue({"transactions": [{"type": "buy", "booking_date": "2024-01-02", "isin": "US88160R1014", "quantity": 3,
                                    "price": 108.1, "amount": -325.3, "fees": 1.0}], "balances": []})
    res = extract_document(fx("tr_kauf.txt"), "text", "tr.txt", llm_mode="force")
    assert res.parser.startswith("llm:") and len(ollama.chats) == 1
    assert res.institution == "trade_republic"     # keyword classification still applies


def test_invalid_isin_and_bad_math_from_llm_are_flagged(ollama):
    ollama.down = False
    ollama.queue({"transactions": [{"type": "buy", "booking_date": "2024-11-11", "isin": "DE0007236102", "name": "Siemens",
                                    "quantity": 12, "price": 180, "amount": -2500, "fees": 9.9}], "balances": []})
    res = extract_document(fx("unknown_broker.txt"), "text", "order.txt")
    d = res.drafts[0]
    assert d.review_required and d.confidence < 0.5
    assert any("Prüfziffer" in w for w in d.payload["warnings"]) and any(w.startswith("Rechenprobe") for w in d.payload["warnings"])


def test_long_statement_is_chunked_and_merged_in_order(ollama, monkeypatch):
    monkeypatch.setattr(get_ingest_settings(), "llm_chunk_chars", 600)
    monkeypatch.setattr(get_ingest_settings(), "llm_concurrency", 3)
    lines = [f"{d:02d}.05.2024 Händler Nummer {d} irgendein langer Verwendungszweck ohne Betrag am Ende" for d in range(1, 29)]
    text = "Meine Liste aus dem Mai\n" + "\n".join(lines)
    chunks = llm.split_chunks(text, 600)
    assert len(chunks) >= 3 and all(len(c) <= 900 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")       # nothing lost, nothing duplicated

    def answer(body):
        doc = body["messages"][-1]["content"].split("Dokument", 2)[-1]
        days = [ln[:2] for ln in doc.split("\n") if ln[:2].isdigit() and ln[2:3] == "."]
        return {"transactions": [{"type": "withdrawal", "booking_date": f"2024-05-{d}", "amount": -float(d)} for d in days],
                "balances": [{"date": f"2024-05-{days[-1]}", "balance": 100.0 + int(days[-1])}]}

    ollama.down = False
    ollama.default = answer
    res = extract_document(text.encode(), "text", "notes.txt", llm_mode="force")
    assert len(ollama.chats) == len(chunks)
    assert [p["booking_date"] for p in tx(res)] == [f"2024-05-{d:02d}" for d in range(1, 29)]
    bal = [d.payload for d in res.drafts if d.kind == "balance"]
    assert len(bal) == 1 and bal[0]["date"] == "2024-05-28"                      # only the latest balance survives
    assert "Kopf des Dokuments" in ollama.chats[-1]["messages"][-1]["content"] or "Kopf des Dokuments" in ollama.chats[1]["messages"][-1]["content"]


def test_image_goes_to_vision_model_with_base64(ollama):
    ollama.down = False
    ollama.queue({"transactions": [{"type": "withdrawal", "booking_date": "2024-11-08", "amount": -13.99,
                                    "counterparty": "Netflix"}], "balances": []})
    res = extract_document(make_png(), "image", "screenshot.png")
    body = ollama.chats[0]
    assert body["model"] == "qwen2.5vl:7b"
    img = base64.b64decode(body["messages"][-1]["images"][0])
    assert img[:8] == b"\x89PNG\r\n\x1a\n"
    assert res.parser == "llm:qwen2.5vl:7b" and tx(res)[0]["category_hint"] == "Abos & Streaming"
    assert res.drafts[0].confidence < 0.7


def test_image_without_ollama_fails_clearly(ollama):
    res = extract_document(make_png(), "image", "screenshot.png")
    assert res.drafts == [] and "Ollama" in res.error


def test_ocr_prepass_lets_parser_handle_screenshot_without_llm(ollama, monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda png: fx("tr_kauf.txt").decode())
    res = extract_document(make_png(), "image", "tr.png")
    assert res.parser == "trade_republic.trade" and not ollama.chats and not res.used_llm
    assert res.drafts[0].confidence <= 0.85 and any("OCR" in w for w in res.drafts[0].payload["warnings"])


def test_ocr_text_is_passed_as_context_to_vision_model(ollama, monkeypatch):
    monkeypatch.setattr(ex, "ocr_image", lambda png: "Irgendein Text 12,00")
    ollama.down = False
    extract_document(make_png(), "image", "x.png")
    assert "OCR-Text" in ollama.chats[0]["messages"][-1]["content"] and ollama.chats[0]["model"] == "qwen2.5vl:7b"


def test_real_pdf_text_layer_via_pdfplumber(ollama):
    pdf = make_text_pdf(fx("ing_kontoauszug.txt").decode())
    assert pdf[:5] == b"%PDF-"
    res = extract_document(pdf, "pdf", "Girokonto_5400123456_Kontoauszug_20230331.pdf")
    assert res.parser == "ing.statement" and res.pages == 1 and not res.warnings
    assert len(tx(res)) == 7 and res.iban == "DE12500105175400123456"
    assert "Neuer Saldo 2.587,90 Euro" in res.raw_text
    pdf = make_text_pdf(fx("tr_dividende_usd.txt").decode())
    res = extract_document(pdf, "pdf", "tr.pdf")
    assert res.parser == "trade_republic.dividend" and tx(res)[0]["amount"] == 1.63


def test_scanned_pdf_is_rendered_and_sent_to_vision(ollama):
    ollama.down = False
    ollama.queue({"transactions": [{"type": "deposit", "booking_date": "2024-01-05", "amount": 10.0}], "balances": []})
    res = extract_document(make_scanned_pdf(), "pdf", "scan.pdf")
    assert ollama.chats[0]["model"] == "qwen2.5vl:7b" and len(ollama.chats[0]["messages"][-1]["images"]) == 1
    assert any("ohne Textebene" in w for w in res.warnings) and len(tx(res)) == 1


def test_broken_pdf_fails_gracefully(ollama):
    res = extract_document(b"%PDF-1.4 this is not a pdf", "pdf", "kaputt.pdf")
    assert res.drafts == [] and res.error
