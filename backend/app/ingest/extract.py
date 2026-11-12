"""Tier 2 – turn a stored upload into text (and page images when there is no text layer)."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest.config import get_ingest_settings
from app.ingest.util import normalize_text

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic"}
TEXT_EXT = {".txt", ".text", ".md", ".eml", ".html", ".htm", ".json", ".xml"}
CSV_EXT = {".csv", ".tsv"}


@dataclass
class Extracted:
    text: str = ""
    images: list[bytes] = field(default_factory=list)   # PNG bytes, one per page/screenshot
    pages: int | None = None
    ocr_used: bool = False
    needs_vision: bool = False
    notes: list[str] = field(default_factory=list)


def detect_kind(filename: str | None, mime: str | None, head: bytes) -> str:
    """pdf | image | csv | text  (pasted text is tagged 'paste' by the API)."""
    ext = Path(filename or "").suffix.lower()
    mime = (mime or "").lower()
    if head[:5] == b"%PDF-" or ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if (head[:8] == b"\x89PNG\r\n\x1a\n" or head[:3] == b"\xff\xd8\xff" or head[:4] in (b"GIF8", b"RIFF", b"II*\x00", b"MM\x00*")
            or ext in IMAGE_EXT or mime.startswith("image/")):
        return "image"
    if ext in CSV_EXT or mime in ("text/csv", "application/csv"):
        return "csv"
    if ext in TEXT_EXT or mime.startswith("text/"):
        return "csv" if _looks_like_csv(head) else "text"
    return "csv" if _looks_like_csv(head) else "text"


def _looks_like_csv(head: bytes) -> bool:
    lines = [ln for ln in decode_bytes(head[:4000]).split("\n") if ln.strip()][:25]
    if len(lines) < 3:
        return False
    for sep in (";", "\t"):
        hits = sum(1 for ln in lines if ln.count(sep) >= 4)
        if hits >= max(3, len(lines) // 2):
            return True
    return False


def decode_bytes(data: bytes) -> str:
    """utf-8 (with BOM) -> cp1252 -> latin-1. German bank CSVs are usually cp1252/latin-1."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# ----------------------------------------------------------------------------- PDF

def pdf_text(data: bytes) -> tuple[str, int]:
    """Text layer via pdfplumber (rows stay on one line), pypdf as fallback. Pages joined by \\f."""
    pages: list[str] = []
    max_pages = get_ingest_settings().pdf_max_pages
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            n = len(pdf.pages)
            for page in pdf.pages[:max_pages]:
                try:
                    txt = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                except Exception:  # noqa: BLE001 – one broken page must not kill the document
                    txt = ""
                pages.append(txt)
                page.flush_cache()
    except Exception as exc:  # noqa: BLE001
        log.warning("pdfplumber failed (%s) – trying pypdf", exc)
        pages = []
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            n = len(reader.pages)
            for page in reader.pages[:max_pages]:
                pages.append(page.extract_text() or "")
        except Exception as exc2:  # noqa: BLE001
            raise ValueError(f"PDF konnte nicht gelesen werden: {exc2}") from exc2
    return "\n\f\n".join(pages), n


def pdf_page_images(data: bytes) -> list[bytes]:
    """Render pages to PNG with pypdfium2 (for scanned PDFs)."""
    import pypdfium2 as pdfium

    s = get_ingest_settings()
    out: list[bytes] = []
    pdf = pdfium.PdfDocument(data)
    try:
        for i in range(min(len(pdf), s.pdf_max_pages)):
            page = pdf[i]
            pil = page.render(scale=s.pdf_render_scale).to_pil()
            out.append(_png_bytes(_shrink(pil)))
            page.close()
    finally:
        pdf.close()
    return out


# ----------------------------------------------------------------------------- images / OCR

def _shrink(img):
    max_side = get_ingest_settings().image_max_side
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
    return img


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def prepare_image(data: bytes) -> bytes:
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return _png_bytes(_shrink(img))


def ocr_available() -> bool:
    if not get_ingest_settings().ocr_enabled:
        return False
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001 – not installed / binary missing
        return False


def ocr_image(png: bytes) -> str:
    """Tesseract OCR (German). Returns '' when Tesseract is not installed."""
    if not ocr_available():
        return ""
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(png))
        lang = get_ingest_settings().ocr_lang
        try:
            return pytesseract.image_to_string(img, lang=lang, config="--psm 6")
        except pytesseract.TesseractError:
            return pytesseract.image_to_string(img, config="--psm 6")  # language pack missing
    except Exception as exc:  # noqa: BLE001
        log.warning("OCR failed: %s", exc)
        return ""


# ----------------------------------------------------------------------------- entry

def extract(data: bytes, kind: str) -> Extracted:
    s = get_ingest_settings()
    if kind in ("text", "paste", "csv"):
        return Extracted(text=normalize_text(decode_bytes(data)))
    if kind == "pdf":
        text, n = pdf_text(data)
        text = normalize_text(text)
        res = Extracted(text=text, pages=n)
        if len(text.replace("\f", "").strip()) < s.pdf_min_chars_per_page * max(1, min(n, s.pdf_max_pages)):
            res.notes.append("PDF ohne Textebene (Scan) – Seiten werden als Bild verarbeitet.")
            try:
                res.images = pdf_page_images(data)
            except Exception as exc:  # noqa: BLE001
                res.notes.append(f"Seiten konnten nicht gerendert werden: {exc}")
            _ocr_into(res)
        return res
    if kind == "image":
        try:
            res = Extracted(images=[prepare_image(data)], pages=1)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Bild konnte nicht gelesen werden: {exc}") from exc
        _ocr_into(res)
        return res
    raise ValueError(f"Unbekannter Dokumenttyp: {kind}")


def _ocr_into(res: Extracted) -> None:
    res.needs_vision = bool(res.images)
    if not res.images:
        return
    texts = [ocr_image(img) for img in res.images]
    if any(t.strip() for t in texts):
        res.text = normalize_text("\n\f\n".join(texts))
        res.ocr_used = True
