"""Provider registry. Tests swap the provider via `set_provider(FakeProvider())`."""
from __future__ import annotations

from app.market.providers.base import MarketDataProvider

_provider: MarketDataProvider | None = None


def get_provider() -> MarketDataProvider:
    global _provider
    if _provider is None:
        from app.market.providers.yahoo import YahooProvider

        _provider = YahooProvider()
    return _provider


def set_provider(provider: MarketDataProvider | None) -> None:
    global _provider
    _provider = provider
