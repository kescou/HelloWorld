"""Document ingestion API (contract: docs/ARCHITECTURE.md → "Dokumente"). Mounted at /api/documents."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.ingest import commit as committer
from app.ingest import llm, runner, storage
from app.ingest.config import get_ingest_settings
from app.ingest.dto import Draft
from app.ingest.extract import ocr_available
from app.ingest.pipeline import refresh_status
from app.ingest.validate import assign_accounts, mark_duplicates, normalize_draft
from app.models import (
    Account, BalanceSnapshot, Document, DocumentStatus, DraftStatus, ExtractionDraft, Transaction,
)
from app.schemas.ingest import (
    AcceptAllOut, DocumentDetail, DocumentOut, DraftOut, DraftPatch, LlmStatus, PasteIn, ReprocessIn,
)

router = APIRouter()


# ----------------------------------------------------------------------------- serialisation

def _doc_out(doc: Document, *, duplicate_upload: bool = False) -> dict:
    meta = doc.meta or {}
    drafts = doc.drafts
    return {
        "id": doc.id, "filename": doc.filename, "mime_type": doc.mime_type, "source_kind": doc.source_kind,
        "status": doc.status.value, "account_id": doc.account_id,
        "detected_institution": doc.detected_institution, "detected_doc_type": doc.detected_doc_type, "parser": doc.parser,
        "draft_count": len(drafts),
        "pending_count": sum(1 for d in drafts if d.status == DraftStatus.pending),
        "duplicate_count": sum(1 for d in drafts if d.status == DraftStatus.duplicate),
        "accepted_count": sum(1 for d in drafts if d.status == DraftStatus.accepted),
        "error": doc.error, "warnings": list(meta.get("warnings") or []),
        "created_at": doc.created_at, "processed_at": doc.processed_at, "processing_ms": doc.processing_ms,
        "used_llm": bool(meta.get("used_llm")), "has_file": bool(doc.storage_path),
        "duplicate_upload": duplicate_upload,
    }


def _draft_out(d: ExtractionDraft) -> dict:
    return {"id": d.id, "document_id": d.document_id, "kind": d.kind, "payload": d.payload, "confidence": d.confidence,
            "status": d.status.value, "transaction_id": d.transaction_id, "balance_snapshot_id": d.balance_snapshot_id}


def _detail(doc: Document) -> dict:
    meta = doc.meta or {}
    out = _doc_out(doc)
    out.update({"drafts": [_draft_out(d) for d in doc.drafts], "raw_text": doc.raw_text,
                "timings": meta.get("timings") or {}, "pages": meta.get("pages")})
    return out


def _get_doc(db: Session, doc_id: int) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Dokument nicht gefunden.")
    return doc


def _get_draft(db: Session, draft_id: int) -> ExtractionDraft:
    draft = db.get(ExtractionDraft, draft_id)
    if draft is None:
        raise HTTPException(404, "Entwurf nicht gefunden.")
    return draft


def _check_account(db: Session, account_id: int | None) -> None:
    if account_id is not None and db.get(Account, account_id) is None:
        raise HTTPException(422, f"Konto {account_id} existiert nicht.")


# ----------------------------------------------------------------------------- LLM status (before /{id})

@router.get("/llm/status", response_model=LlmStatus)
def llm_status() -> dict:
    out = llm.get_client().status()
    out["ocr_available"] = ocr_available()
    return out


# ----------------------------------------------------------------------------- upload / paste

@router.post("", response_model=list[DocumentOut])
async def upload_documents(
    files: list[UploadFile] | None = File(default=None),
    files_bracket: list[UploadFile] | None = File(default=None, alias="files[]"),
    file: UploadFile | None = File(default=None),
    account_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    """multipart upload; field name `files` (or `files[]` / `file`), optional `account_id` hint.
    Identical files (sha256) are not stored twice – the existing document is returned."""
    uploads = [*(files or []), *(files_bracket or []), *([file] if file else [])]
    if not uploads:
        raise HTTPException(422, "Keine Datei übermittelt (Feldname `files`).")
    _check_account(db, account_id)
    limit = get_ingest_settings().max_upload_mb * 1024 * 1024
    results: list[tuple[Document, bool]] = []
    for up in uploads:
        data = await up.read()
        if not data:
            raise HTTPException(422, f"Datei „{up.filename}“ ist leer.")
        if len(data) > limit:
            raise HTTPException(413, f"Datei „{up.filename}“ ist größer als {get_ingest_settings().max_upload_mb} MB.")
        results.append(storage.intake_file(db, data, up.filename, up.content_type, account_id))
    db.commit()
    for doc, created in results:
        if created or doc.status == DocumentStatus.failed:
            runner.submit(doc.id)
    out = []
    for doc, created in results:
        db.refresh(doc)
        out.append(_doc_out(doc, duplicate_upload=not created))
    return out


@router.post("/paste", response_model=DocumentOut)
def paste_document(body: PasteIn, db: Session = Depends(get_db)) -> dict:
    if not body.text.strip():
        raise HTTPException(422, "Der eingefügte Text ist leer.")
    _check_account(db, body.account_id)
    doc, created = storage.intake_paste(db, body.text, body.account_id)
    db.commit()
    if created or doc.status == DocumentStatus.failed:
        runner.submit(doc.id)
    db.refresh(doc)
    return _doc_out(doc, duplicate_upload=not created)


# ----------------------------------------------------------------------------- read

@router.get("", response_model=list[DocumentOut])
def list_documents(status: str | None = Query(default=None, description="uploaded|processing|review|imported|failed (comma-separated)"),
                   limit: int = Query(default=200, ge=1, le=1000), offset: int = Query(default=0, ge=0),
                   db: Session = Depends(get_db)) -> list[dict]:
    runner.recover_stale(db)
    q = select(Document).options(selectinload(Document.drafts)).order_by(Document.created_at.desc(), Document.id.desc())
    if status:
        try:
            wanted = [DocumentStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError as exc:
            raise HTTPException(422, f"Unbekannter Status: {status}") from exc
        q = q.where(Document.status.in_(wanted))
    return [_doc_out(d) for d in db.scalars(q.limit(limit).offset(offset))]


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, db: Session = Depends(get_db)) -> dict:
    return _detail(_get_doc(db, doc_id))


@router.get("/{doc_id}/file")
def get_document_file(doc_id: int, db: Session = Depends(get_db)):
    doc = _get_doc(db, doc_id)
    if doc.storage_path:
        path = Path(doc.storage_path)
        if not path.is_file():
            raise HTTPException(404, "Originaldatei nicht mehr vorhanden.")
        return FileResponse(path, media_type=doc.mime_type or "application/octet-stream", filename=doc.filename,
                            content_disposition_type="inline")
    return PlainTextResponse(doc.raw_text or "")


# ----------------------------------------------------------------------------- drafts

@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def patch_draft(draft_id: int, body: DraftPatch, db: Session = Depends(get_db)) -> dict:
    """Merge corrections into the payload, re-run validation (warnings, arithmetic check) and
    duplicate detection. `status: "pending"` re-opens a rejected/duplicate draft."""
    draft = _get_draft(db, draft_id)
    if draft.status == DraftStatus.accepted:
        raise HTTPException(409, "Der Entwurf wurde bereits übernommen – bitte die Transaktion bearbeiten.")
    merged = {k: v for k, v in dict(draft.payload or {}).items() if k not in ("warnings", "review_required", "verified", "duplicate_of")}
    merged.update({k: v for k, v in body.payload.items() if k not in ("warnings", "verified", "duplicate_of", "dup_index")})
    if "account_id" in body.payload:
        _check_account(db, body.payload["account_id"])
    user_reviewed = body.payload.get("review_required") is False
    merged.pop("review_required", None)
    tmp = normalize_draft(Draft(kind=draft.kind, payload=merged, confidence=0.99), source="user")
    if tmp.payload.get("account_id") is None:
        assign_accounts(db, [tmp], institution=draft.document.detected_institution)
    if body.status not in (None, "pending", "rejected"):
        raise HTTPException(422, "status darf nur 'pending' oder 'rejected' sein.")
    base_index = merged.get("dup_index") if isinstance(merged.get("dup_index"), int) else 0
    mark_duplicates(db, [tmp], base_index=base_index)
    if user_reviewed and not tmp.payload["warnings"]:
        tmp.payload["review_required"] = False
    draft.payload = tmp.payload
    if not tmp.payload["warnings"]:
        draft.confidence = max(draft.confidence or 0.0, 0.9)
    if body.status == "rejected":
        draft.status = DraftStatus.rejected
    elif tmp.status == "duplicate" and body.status != "pending":
        draft.status = DraftStatus.duplicate
    elif body.status == "pending" or draft.status == DraftStatus.duplicate:
        draft.status = DraftStatus.pending
    refresh_status(draft.document)
    db.commit()
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/accept", response_model=DraftOut)
def accept_draft(draft_id: int, force: bool = Query(default=False, description="auch als Duplikat erkannte Entwürfe buchen"),
                 db: Session = Depends(get_db)) -> dict:
    draft = _get_draft(db, draft_id)
    try:
        committer.accept_draft(db, draft, force=force)
    except committer.DuplicateDraft as exc:
        db.commit()   # persists status=duplicate
        raise HTTPException(409, {"message": str(exc), "transaction_id": exc.transaction_id, "draft": _draft_out(draft)}) from exc
    except committer.CommitError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _draft_out(draft)


@router.post("/drafts/{draft_id}/reject", response_model=DraftOut)
def reject_draft(draft_id: int, db: Session = Depends(get_db)) -> dict:
    draft = _get_draft(db, draft_id)
    try:
        committer.reject_draft(db, draft)
    except committer.CommitError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _draft_out(draft)


@router.post("/{doc_id}/accept-all", response_model=AcceptAllOut)
def accept_all(doc_id: int, include_flagged: bool = Query(default=False, description="auch Entwürfe mit review_required übernehmen"),
               db: Session = Depends(get_db)) -> dict:
    """Accept every pending draft. Drafts flagged `review_required` (failed arithmetic check,
    missing account, securities lines on a giro statement ...) are skipped unless include_flagged."""
    doc = _get_doc(db, doc_id)
    accepted = skipped = duplicates = 0
    errors: list[dict] = []
    for draft in list(doc.drafts):
        if draft.status != DraftStatus.pending:
            continue
        if draft.payload.get("review_required") and not include_flagged:
            skipped += 1
            continue
        sp = db.begin_nested()
        try:
            committer.accept_draft(db, draft)
            sp.commit()
            accepted += 1
        except committer.DuplicateDraft:
            sp.commit()
            duplicates += 1
        except committer.CommitError as exc:
            sp.rollback()
            db.refresh(draft)
            errors.append({"draft_id": draft.id, "error": str(exc)})
    refresh_status(doc)
    db.commit()
    return {"accepted": accepted, "skipped": skipped, "duplicates": duplicates, "errors": errors, "document": _detail(doc)}


# ----------------------------------------------------------------------------- reprocess / delete

@router.post("/{doc_id}/reprocess", response_model=DocumentOut)
def reprocess(doc_id: int, body: ReprocessIn | None = None, db: Session = Depends(get_db)) -> dict:
    doc = _get_doc(db, doc_id)
    if doc.status == DocumentStatus.processing and not runner.is_inline():
        raise HTTPException(409, "Das Dokument wird gerade verarbeitet.")
    doc.status = DocumentStatus.uploaded
    doc.error = None
    db.commit()
    runner.submit(doc.id, force_llm=bool(body and body.force_llm))
    db.refresh(doc)
    return _doc_out(doc)


@router.delete("/{doc_id}")
def delete_document(doc_id: int, delete_transactions: bool = Query(default=False, description="importierte Transaktionen mitlöschen"),
                    db: Session = Depends(get_db)) -> dict:
    """Deletes document, drafts and the stored file. Imported transactions/balances are kept
    (their source link is cleared) unless delete_transactions=true."""
    doc = _get_doc(db, doc_id)
    db.execute(update(ExtractionDraft).where(ExtractionDraft.document_id == doc.id)
               .values(transaction_id=None, balance_snapshot_id=None))
    removed = 0
    if delete_transactions:
        txs = list(db.scalars(select(Transaction).where(Transaction.source_document_id == doc.id)))
        ids = [t.id for t in txs]
        if ids:
            db.execute(update(Transaction).where(Transaction.transfer_peer_id.in_(ids)).values(transfer_peer_id=None))
            db.execute(update(ExtractionDraft).where(ExtractionDraft.transaction_id.in_(ids)).values(transaction_id=None))
        for t in txs:
            db.delete(t)
        removed = len(txs)
        for s in db.scalars(select(BalanceSnapshot).where(BalanceSnapshot.source_document_id == doc.id)):
            db.delete(s)
    else:
        db.execute(update(Transaction).where(Transaction.source_document_id == doc.id).values(source_document_id=None))
        db.execute(update(BalanceSnapshot).where(BalanceSnapshot.source_document_id == doc.id).values(source_document_id=None))
    db.flush()
    storage.delete_file(doc)
    db.delete(doc)
    db.commit()
    return {"deleted": True, "id": doc_id, "transactions_deleted": removed}
