"""Issuer detection + provider registry."""
from __future__ import annotations

import re

from app.market.etf.base import EtfHoldingsProvider
from app.models.core import Instrument

_ISSUER_PATTERNS: list[tuple[str, str]] = [
    (r"ishares|blackrock", "ishares"),
    (r"xtrackers|x-trackers|\bdws\b|db x", "xtrackers"),
    (r"vanguard", "vanguard"),
    (r"amundi|lyxor|comstage", "amundi"),
    (r"spdr|state street|ssga", "spdr"),
    (r"invesco|powershares", "invesco"),
]

_providers: dict[str, EtfHoldingsProvider] | None = None


def detect_issuer(text: str | None) -> str | None:
    low = (text or "").lower()
    for pattern, issuer in _ISSUER_PATTERNS:
        if re.search(pattern, low):
            return issuer
    return None


def issuer_of(inst: Instrument) -> str | None:
    return detect_issuer(inst.fund_provider) or detect_issuer(inst.name)


def providers() -> dict[str, EtfHoldingsProvider]:
    global _providers
    if _providers is None:
        from app.market.etf.best_effort import AmundiProvider, InvescoProvider, SpdrProvider, VanguardProvider
        from app.market.etf.ishares import ISharesProvider
        from app.market.etf.xtrackers import XtrackersProvider

        _providers = {p.issuer: p for p in (
            ISharesProvider(), XtrackersProvider(), VanguardProvider(), AmundiProvider(), SpdrProvider(),
            InvescoProvider())}
    return _providers


def register(provider: EtfHoldingsProvider) -> None:
    """Plug in another issuer (or replace one, e.g. in tests)."""
    providers()[provider.issuer] = provider
