"""Shared helpers/fixtures for the ingestion tests (imported by the other test_ingest_* modules).
No network: Ollama is replaced by an httpx.MockTransport."""
from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.ingest import llm, runner
from app.ingest.pipeline import extract_document
from app.models import Account, AccountType

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"

IBAN_ING = "DE12500105175400123456"
IBAN_POSTBANK = "DE89370400440532013000"
IBAN_TR = "DE61100123450123456789"


def fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def run(name: str, kind: str | None = None, **kw):
    """Run the pure pipeline (no DB, no LLM) on a fixture."""
    if kind is None:
        kind = "csv" if name.endswith(".csv") else "text"
    return extract_document(fx(name), kind, name, llm_mode=kw.pop("llm_mode", "off"), **kw)


def tx(res) -> list[dict]:
    return [d.payload for d in res.drafts if d.kind == "transaction"]


def balances(res) -> list[dict]:
    return [d.payload for d in res.drafts if d.kind == "balance"]


class FakeOllama:
    """Scriptable Ollama: queue chat answers (dict -> JSON content, str -> raw content,
    Exception -> raised, callable(body) -> any of those)."""

    def __init__(self, models=("qwen2.5:7b-instruct", "qwen2.5vl:7b"), down: bool = False):
        self.models = list(models)
        self.down = down
        self.answers: list = []
        self.default = {"transactions": [], "balances": []}
        self.chats: list[dict] = []
        self.transport = httpx.MockTransport(self._handle)

    def queue(self, *answers):
        self.answers.extend(answers)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            self.chats.append(body)
            if body["model"] not in self.models:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found, try pulling it first"})
            ans = self.answers.pop(0) if self.answers else self.default
            if callable(ans):
                ans = ans(body)
            if isinstance(ans, Exception):
                raise ans
            content = ans if isinstance(ans, str) else json.dumps(ans)
            return httpx.Response(200, json={"model": body["model"], "message": {"role": "assistant", "content": content}, "done": True})
        return httpx.Response(404)


@pytest.fixture()
def ollama(monkeypatch):
    """Ollama is DOWN unless the test flips `ollama.down = False`."""
    fake = FakeOllama(down=True)
    monkeypatch.setattr(llm, "TRANSPORT", fake.transport)
    return fake


@pytest.fixture()
def ingest_env(db, tmp_path, monkeypatch, ollama):
    """tmp upload dir + synchronous processing inside the test transaction + fake Ollama."""
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    runner.set_inline(db)
    yield ollama
    runner.set_inline(None)


@pytest.fixture()
def accounts(db) -> dict[str, Account]:
    def mk(name, inst, typ, **kw):
        a = Account(name=name, institution=inst, type=typ, currency="EUR", opening_balance=Decimal("0"), **kw)
        db.add(a)
        db.flush()
        return a

    acc = {
        "postbank": mk("Postbank Giro", "postbank", AccountType.checking, iban=IBAN_POSTBANK),
        "ing_giro": mk("ING Girokonto", "ing", AccountType.checking, iban=IBAN_ING),
        "sc_cash": mk("Scalable Tagesgeld", "scalable", AccountType.savings),
        "tr_cash": mk("Trade Republic Tagesgeld", "trade_republic", AccountType.savings, iban=IBAN_TR),
    }
    acc["ing_depot"] = mk("ING Depot", "ing", AccountType.brokerage, settlement_account_id=acc["ing_giro"].id)
    acc["sc_depot"] = mk("Scalable Depot", "scalable", AccountType.brokerage, settlement_account_id=acc["sc_cash"].id)
    acc["tr_depot"] = mk("Trade Republic Depot", "trade_republic", AccountType.brokerage, settlement_account_id=acc["tr_cash"].id)
    db.commit()
    return acc


def make_text_pdf(text: str) -> bytes:
    """Real PDF with a text layer (reportlab), one line per source line."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    c.setFont("Helvetica", 9)
    for line in text.split("\n"):
        if y < 50:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = 800
        c.drawString(40, y, line)
        y -= 13
    c.save()
    return buf.getvalue()


def make_png(text: str = "Kontoauszug") -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 300), "white")
    ImageDraw.Draw(img).text((20, 20), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_scanned_pdf() -> bytes:
    """PDF that only contains an image (no text layer)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(io.BytesIO(make_png("Scan"))), 40, 400, width=400, height=200)
    c.save()
    return buf.getvalue()
