"""Debug a document without touching the database:

    python -m app.ingest.cli <file> [--llm | --no-llm] [--text] [--kind pdf|image|csv|text|paste]

Prints classification, parser, timings and the extracted drafts as JSON (account_id stays null –
accounts are assigned against the DB during a real import). ``--llm`` forces the local model,
``--no-llm`` never calls it, ``--text`` also prints the extracted text (what the parsers see).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.ingest.extract import detect_kind
from app.ingest.pipeline import extract_document


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ingest.cli", description=__doc__.split("\n")[0])
    ap.add_argument("file", help="PDF, Bild, CSV oder Textdatei ('-' = stdin als eingefügter Text)")
    ap.add_argument("--llm", action="store_true", help="lokales Sprachmodell erzwingen (Parser überspringen)")
    ap.add_argument("--no-llm", action="store_true", help="niemals das Sprachmodell aufrufen")
    ap.add_argument("--text", action="store_true", help="extrahierten Rohtext mit ausgeben")
    ap.add_argument("--kind", choices=["pdf", "image", "csv", "text", "paste"], help="Dokumentart überschreiben")
    args = ap.parse_args(argv)

    if args.file == "-":
        data, name, kind = sys.stdin.buffer.read(), "stdin.txt", "paste"
    else:
        path = Path(args.file)
        if not path.is_file():
            print(f"Datei nicht gefunden: {path}", file=sys.stderr)
            return 2
        data, name = path.read_bytes(), path.name
        kind = detect_kind(name, None, data[:4096])
    kind = args.kind or kind
    mode = "force" if args.llm else ("off" if args.no_llm else "auto")
    res = extract_document(data, kind, name, llm_mode=mode)
    out = {
        "file": name, "source_kind": kind, "institution": res.institution, "doc_type": res.doc_type, "parser": res.parser,
        "used_llm": res.used_llm, "pages": res.pages, "timings": res.timings, "error": res.error, "warnings": res.warnings,
        "iban": res.iban, "drafts": [d.to_json() for d in res.drafts],
    }
    if args.text:
        out["raw_text"] = res.raw_text
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 1 if res.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
