"""Tier 1 – intake: content-addressed storage + dedupe by sha256."""
from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ingest.extract import detect_kind
from app.models import Document, DocumentStatus

_EXT_BY_KIND = {"pdf": ".pdf", "image": ".png", "csv": ".csv", "text": ".txt"}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str | None, fallback: str = "upload") -> str:
    name = Path((name or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return (name or fallback)[:255]


def store_bytes(data: bytes, sha: str, ext: str) -> Path:
    root = Path(get_settings().upload_dir)
    ext = ext.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext or "") else ".bin"
    target = root / sha[:2] / f"{sha}{ext}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    return target


def intake_file(db: Session, data: bytes, filename: str | None, mime: str | None, account_id: int | None) -> tuple[Document, bool]:
    """Returns (document, created). Re-uploading identical bytes returns the existing document."""
    sha = sha256_hex(data)
    existing = db.scalar(select(Document).where(Document.sha256 == sha))
    if existing is not None:
        return existing, False
    filename = safe_filename(filename)
    kind = detect_kind(filename, mime, data[:4096])
    ext = Path(filename).suffix or _EXT_BY_KIND.get(kind, ".bin")
    path = store_bytes(data, sha, ext)
    doc = Document(filename=filename, mime_type=mime or mimetypes.guess_type(filename)[0], sha256=sha,
                   storage_path=str(path), source_kind=kind, status=DocumentStatus.uploaded, account_id=account_id,
                   meta={"account_hint_id": account_id, "size": len(data)})
    db.add(doc)
    db.flush()
    return doc, True


def intake_paste(db: Session, text: str, account_id: int | None) -> tuple[Document, bool]:
    data = text.encode("utf-8")
    sha = sha256_hex(data)
    existing = db.scalar(select(Document).where(Document.sha256 == sha))
    if existing is not None:
        return existing, False
    first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "Eingefügter Text")
    doc = Document(filename=f"Einfügen: {first[:60]}", mime_type="text/plain", sha256=sha, storage_path=None,
                   source_kind="paste", status=DocumentStatus.uploaded, account_id=account_id, raw_text=text.replace("\x00", ""),
                   meta={"account_hint_id": account_id, "size": len(data)})
    db.add(doc)
    db.flush()
    return doc, True


def delete_file(doc: Document) -> None:
    if not doc.storage_path:
        return
    root = Path(get_settings().upload_dir).resolve()
    p = Path(doc.storage_path).resolve()
    try:
        if root in p.parents and p.is_file():
            p.unlink()
    except OSError:
        pass
