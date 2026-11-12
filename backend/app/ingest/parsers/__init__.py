"""Tier 4 – deterministic parsers. ``run_parsers`` picks the first parser that produces drafts.

Adding a parser: create a module with ``matches(ctx) -> bool`` and ``parse(ctx) -> ParseResult |
None``, register it in ``INSTITUTION_PARSERS`` and add a fixture + test (see docs/INGESTION.md).
"""
from __future__ import annotations

import logging
import re

from app.ingest.dto import DocContext, ParseResult
from app.ingest.parsers import generic, ing, postbank, scalable, trade_republic
from app.ingest.parsers import generic_security as gs

log = logging.getLogger(__name__)

INSTITUTION_PARSERS = {
    "trade_republic": trade_republic,
    "scalable": scalable,
    "ing": ing,
    "postbank": postbank,
}


def _try(fn, ctx: DocContext) -> ParseResult | None:
    try:
        res = fn(ctx)
    except Exception:  # noqa: BLE001 – a parser bug must never kill the pipeline; the LLM takes over
        log.exception("parser %s crashed", getattr(fn, "__module__", fn))
        return None
    return res if res and res.drafts else None


def run_parsers(ctx: DocContext) -> ParseResult | None:
    ordered = []
    if ctx.institution in INSTITUTION_PARSERS:
        ordered.append(INSTITUTION_PARSERS[ctx.institution])
    ordered += [m for m in INSTITUTION_PARSERS.values() if m not in ordered]
    for mod in ordered:
        if mod.matches(ctx):
            res = _try(mod.parse, ctx)
            if res:
                return res
    if ctx.source_kind == "csv":
        return _try(generic.parse_csv, ctx) or _try(lambda c: scalable.parse_csv(c.text), ctx)
    head = ctx.text[:4000]
    if (gs.TRADE_RX.search(head) or gs.DIVIDEND_RX.search(head)) and re.search(r"ISIN|WKN", head):
        res = _try(generic.parse_security, ctx)
        if res:
            return res
    res = _try(generic.parse_statement, ctx)
    if res:
        tx = [d for d in res.drafts if d.kind == "transaction"]
        # free text that merely contains a date and a number is not a statement
        if ctx.source_kind in ("paste", "text") or ctx.doc_type == "account_statement":
            if len(tx) >= 2 or (len(tx) == 1 and (res.coverage or 0) >= 0.3):
                return res
    return None
