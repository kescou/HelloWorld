"""Import all models so Base.metadata is complete."""
from app.models.core import (  # noqa: F401
    Account, AccountType, BalanceSnapshot, Category, CategoryKind, FxRate, Instrument,
    InstrumentType, Setting, Transaction, TxType,
)
from app.models.ingest import Document, DocumentStatus, DraftStatus, ExtractionDraft  # noqa: F401
from app.models.market import (  # noqa: F401
    EtfHolding, EventType, Fundamentals, InstrumentEvent, MarketDataLog, PriceHistory, Quote,
)
