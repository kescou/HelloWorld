"""ETF holdings providers: common types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.models.core import Instrument


class HoldingsUnavailable(RuntimeError):
    """This provider cannot deliver holdings for the ETF (unknown product, download failed, unparsable)."""


@dataclass
class HoldingRow:
    name: str
    weight: float                       # percent of fund, 4.5123 = 4.5123 %
    isin: str | None = None
    symbol: str | None = None
    sector: str | None = None
    country: str | None = None
    asset_class: str | None = None      # equity|bond|cash|derivative|other


@dataclass
class HoldingsResult:
    rows: list[HoldingRow] = field(default_factory=list)
    as_of: date | None = None
    source: str = "manual"

    @property
    def coverage_pct(self) -> float:
        return round(sum(r.weight for r in self.rows), 4)


class EtfHoldingsProvider(ABC):
    issuer: str = "base"
    #: "implemented" = written against a well-known, stable download; "best_effort" = endpoint/format
    #: less certain or known to change – failures fall through to the next provider.
    status: str = "best_effort"

    @abstractmethod
    def fetch(self, inst: Instrument) -> HoldingsResult:
        """Download + parse full holdings. Raise HoldingsUnavailable on any problem."""
