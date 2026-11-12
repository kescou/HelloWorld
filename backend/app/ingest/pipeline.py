"""Orchestration: bytes -> Extraction (pure, no DB) -> persisted drafts (DB)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingest import classify as cls
from app.ingest import extract as ex
from app.ingest import llm
from app.ingest.dto import DocContext, Draft, Extraction
from app.ingest.parsers import run_parsers
from app.ingest.validate import assign_accounts, mark_duplicates, normalize_all, quality_ok
from app.models import Document, DocumentStatus, DraftStatus, ExtractionDraft

log = logging.getLogger(__name__)

NO_DATA_MSG = "Im Dokument wurden keine Buchungen erkannt."


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _doc_type_from(drafts: list[Draft], fallback: str | None) -> str | None:
    for d in drafts:
        dt = d.payload.get("_doc_type")
        if dt:
            return dt
    return fallback


def extract_document(data: bytes, kind: str, filename: str | None = None, *, llm_mode: str = "auto",
                     client: llm.OllamaClient | None = None, account_hint: str | None = None) -> Extraction:
    """Run tiers 2–6 without touching the database.

    llm_mode: 'auto' (LLM only when no parser matches or its self-check fails), 'force', 'off'.
    """
    out = Extraction()
    t0 = time.perf_counter()
    try:
        extracted = ex.extract(data, kind)
    except ValueError as exc:
        out.error = str(exc)
        return out
    out.timings["extract_ms"] = _ms(t0)
    out.raw_text, out.pages = extracted.text, extracted.pages
    out.warnings.extend(extracted.notes)
    text = extracted.text
    out.institution, out.doc_type = cls.classify(text, filename, "csv" if kind == "csv" else kind)

    # ---- tier 4: deterministic parsers
    parsed = None
    parser_drafts: list[Draft] = []
    t1 = time.perf_counter()
    if text.strip() and llm_mode != "force":
        ctx = DocContext(text=text, institution=out.institution, doc_type=out.doc_type, filename=filename,
                         source_kind=kind, raw_bytes=data)
        parsed = run_parsers(ctx)
        if parsed:
            doc_type = _doc_type_from(parsed.drafts, None)
            parser_drafts = normalize_all(parsed.drafts, source="parser")
            if extracted.ocr_used:
                for d in parser_drafts:
                    d.confidence = min(d.confidence, 0.85)
                    d.payload["warnings"].append("Text stammt aus OCR – Zahlen bitte mit dem Original vergleichen.")
            out.parser = parsed.parser
            out.drafts = parser_drafts
            out.warnings.extend(parsed.warnings)
            out.iban, out.account_number, out.settlement_iban = parsed.iban, parsed.account_number, parsed.settlement_iban
            out.doc_type = doc_type or out.doc_type
            if out.institution is None and parsed.parser.split(".")[0] in cls.INSTITUTIONS:
                out.institution = parsed.parser.split(".")[0]
    out.timings["parse_ms"] = _ms(t1)

    parser_ok = bool(parsed) and quality_ok(parser_drafts) and not any("erklärt die Saldoänderung" in w for w in out.warnings)
    need_llm = llm_mode == "force" or (llm_mode == "auto" and not parser_ok)
    if not need_llm:
        if not out.drafts:
            out.error = NO_DATA_MSG if text.strip() or not extracted.images else \
                "Bild/Scan ohne Text: Dafür wird das lokale Sprachmodell benötigt (Ollama starten)."
        return out

    # ---- tier 5: local LLM
    t2 = time.perf_counter()
    hint = account_hint or (f"Bank laut Schlagworten: {out.institution}" if out.institution else None)
    try:
        client = client or llm.get_client()
        if extracted.needs_vision and extracted.images:
            llm_drafts, inst, dtype = llm.extract_images(extracted.images, client, ocr_text=text or None, hint=hint)
            model = client.vision_model
        elif text.strip():
            llm_drafts, inst, dtype = llm.extract_text(text, client, hint=hint)
            model = client.text_model
        else:
            out.error = "Das Dokument enthält weder Text noch Bilder."
            return out
    except llm.LLMUnavailable as exc:
        out.timings["llm_ms"] = _ms(t2)
        if out.drafts:
            out.warnings.append(f"Selbstprüfung des Parsers nicht bestanden und Sprachmodell nicht verfügbar – bitte genau prüfen. ({exc})")
            for d in out.drafts:
                d.review_required = d.payload["review_required"] = True
        else:
            out.error = str(exc) if llm_mode == "force" or not text.strip() else \
                f"Kein Parser passt auf dieses Dokument. {exc}"
        return out
    except llm.LLMError as exc:
        out.timings["llm_ms"] = _ms(t2)
        if out.drafts:
            out.warnings.append(f"Sprachmodell lieferte kein verwertbares Ergebnis ({exc}) – Parser-Ergebnis bitte genau prüfen.")
        else:
            out.error = f"Das lokale Sprachmodell lieferte kein verwertbares Ergebnis: {exc}"
        return out
    out.timings["llm_ms"] = _ms(t2)
    out.used_llm = True
    llm_drafts = normalize_all(llm_drafts, source="llm")
    llm_ok = quality_ok(llm_drafts)
    # keep the parser result unless the LLM is clearly better (or was forced)
    take_llm = llm_mode == "force" or not out.drafts or (llm_ok and not parser_ok)
    if take_llm and llm_drafts:
        if out.drafts:
            out.warnings.append(f"Parser „{out.parser}“ bestand die Selbstprüfung nicht – Ergebnis des Sprachmodells übernommen.")
        out.drafts = llm_drafts
        out.parser = f"llm:{model}"
        out.institution = out.institution or (inst if inst in cls.INSTITUTIONS else None)
        if out.doc_type in (None, "unknown") and dtype:
            out.doc_type = dtype
    elif out.drafts:
        out.warnings.append("Parser und Sprachmodell sind sich nicht sicher – bitte genau prüfen.")
        out.parser = f"{out.parser}+llm-check"
    if not out.drafts:
        out.error = NO_DATA_MSG
    return out


# ----------------------------------------------------------------------------- DB part

def load_bytes(doc: Document) -> bytes:
    if doc.storage_path and Path(doc.storage_path).exists():
        return Path(doc.storage_path).read_bytes()
    if doc.raw_text is not None and doc.source_kind in ("paste", "text", "csv"):
        return doc.raw_text.encode("utf-8")
    raise FileNotFoundError("Originaldatei nicht mehr vorhanden.")


def refresh_status(doc: Document) -> None:
    """review while drafts are pending, imported when every draft is decided."""
    if doc.status in (DocumentStatus.failed, DocumentStatus.processing, DocumentStatus.uploaded):
        return
    pending = sum(1 for d in doc.drafts if d.status == DraftStatus.pending)
    doc.status = DocumentStatus.review if pending else DocumentStatus.imported


def process_document(db: Session, doc_id: int, *, force_llm: bool = False, client: llm.OllamaClient | None = None) -> Document | None:
    doc = db.get(Document, doc_id)
    if doc is None:
        return None
    t0 = time.perf_counter()
    doc.status = DocumentStatus.processing
    doc.error = None
    db.commit()
    try:
        data = load_bytes(doc)
        hint = None
        res = extract_document(data, doc.source_kind, doc.filename, llm_mode="force" if force_llm else "auto",
                               client=client, account_hint=hint)
        # replace undecided drafts, keep accepted ones (their transactions exist)
        for old in list(doc.drafts):
            if old.status != DraftStatus.accepted:
                doc.drafts.remove(old)
        db.flush()
        if res.raw_text or doc.source_kind != "paste":
            doc.raw_text = (res.raw_text or "").replace("\x00", "") or doc.raw_text
        doc.detected_institution = res.institution
        doc.detected_doc_type = res.doc_type
        doc.parser = res.parser
        meta = dict(doc.meta or {})
        meta.update({"warnings": res.warnings, "timings": res.timings, "pages": res.pages, "used_llm": res.used_llm})
        if res.error:
            doc.status = DocumentStatus.failed
            doc.error = res.error
        else:
            hint_id = meta.get("account_hint_id")
            doc_account = assign_accounts(db, res.drafts, hint_account_id=hint_id, institution=res.institution, iban=res.iban,
                                          account_number=res.account_number, settlement_iban=res.settlement_iban)
            mark_duplicates(db, res.drafts)
            if doc.account_id is None or not hint_id:
                doc.account_id = doc_account
            for d in res.drafts:
                doc.drafts.append(ExtractionDraft(kind=d.kind, payload=d.payload, confidence=d.confidence,
                                                  status=DraftStatus(d.status)))
            doc.status = DocumentStatus.review
            refresh_status(doc)
        doc.meta = meta
    except Exception as exc:  # noqa: BLE001 – always leave the document in a terminal state
        log.exception("processing document %s failed", doc_id)
        db.rollback()
        doc = db.get(Document, doc_id)
        if doc is None:
            return None
        doc.status = DocumentStatus.failed
        doc.error = f"Verarbeitung fehlgeschlagen: {exc}"
    doc.processing_ms = _ms(t0)
    doc.processed_at = datetime.now(timezone.utc)
    db.commit()
    return doc
