"""ETF look-through: merge direct stock positions with ETF constituents per underlying company.

Public API (imported by backend-core):  compute_lookthrough(db, account_id=None) -> dict

Positions are computed here from `transactions` × latest quote × FX (no dependency on app.services).
Matching of an ETF constituent to an already known company: ISIN -> normalised name -> ticker.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market import positions
from app.market.fx import FxConverter
from app.market.util import SECTOR_LABELS_DE, normalize_country, normalize_name, normalize_sector, normalize_symbol
from app.models.core import Instrument, InstrumentType
from app.models.market import EtfHolding

OTHER_KEY = "_other"
CASH_KEY = "_cash"
OTHER_LABEL = "Sonstige/Unbekannt"
CASH_LABEL = "Cash/Derivate"
UNKNOWN = "Unbekannt"
FUND_TYPES = {InstrumentType.etf, InstrumentType.fund}


@dataclass
class _Item:
    key: str
    name: str
    isin: str | None = None
    symbol: str | None = None
    sector: str | None = None
    country: str | None = None
    direct_value: float = 0.0
    etf_value: float = 0.0
    kind: str = "company"            # company | other | cash | fund (ETF without holdings) | asset (bond, crypto, …)
    sources: list[dict] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.direct_value + self.etf_value


class _Index:
    """Finds the item of a company by ISIN, normalised name or ticker."""

    def __init__(self) -> None:
        self.items: dict[str, _Item] = {}
        self.by_isin: dict[str, _Item] = {}
        self.by_name: dict[str, _Item] = {}
        self.by_symbol: dict[str, list[_Item]] = defaultdict(list)

    def find(self, isin: str | None, symbol: str | None, name: str | None) -> _Item | None:
        if isin and isin in self.by_isin:
            return self.by_isin[isin]
        nkey = normalize_name(name)
        if len(nkey) >= 3 and nkey in self.by_name:
            return self.by_name[nkey]
        skey = normalize_symbol(symbol)
        if skey:
            first = nkey.split(" ")[0] if nkey else ""
            for cand in self.by_symbol.get(skey, []):
                cand_first = normalize_name(cand.name).split(" ")[0]
                # same ticker on another exchange can be a different company -> require a name hint
                if not first or not cand_first or first == cand_first or first.startswith(cand_first) or cand_first.startswith(first):
                    return cand
        return None

    def get_or_create(self, isin: str | None, symbol: str | None, name: str | None,
                      sector: str | None = None, country: str | None = None) -> _Item:
        item = self.find(isin, symbol, name)
        if item is None:
            nkey = normalize_name(name)
            key = isin or (f"name:{nkey}" if nkey else f"sym:{normalize_symbol(symbol)}")
            if key in self.items:           # extremely unlikely collision -> reuse
                item = self.items[key]
            else:
                item = _Item(key=key, name=(name or symbol or isin or "?").strip())
                self.items[key] = item
        self._register(item, isin, symbol, name)
        if not item.isin and isin:
            item.isin = isin
        if not item.symbol and symbol:
            item.symbol = symbol
        if not item.sector and sector:
            item.sector = sector
        if not item.country and country:
            item.country = country
        return item

    def _register(self, item: _Item, isin: str | None, symbol: str | None, name: str | None) -> None:
        if isin:
            self.by_isin.setdefault(isin, item)
        nkey = normalize_name(name)
        if len(nkey) >= 3:
            self.by_name.setdefault(nkey, item)
        skey = normalize_symbol(symbol)
        if skey and item not in self.by_symbol[skey]:
            self.by_symbol[skey].append(item)

    def special(self, key: str, label: str, kind: str) -> _Item:
        if key not in self.items:
            self.items[key] = _Item(key=key, name=label, kind=kind)
        return self.items[key]


def position_values(db: Session, account_id: int | None = None) -> list[tuple[Instrument, float, float | None]]:
    """[(instrument, quantity, value_eur | None)] for all positions with quantity > 0."""
    qty = {i: q for i, q in positions.compute_quantities(db, account_id).items() if q > positions.EPS}
    if not qty:
        return []
    instruments = {i.id: i for i in db.scalars(select(Instrument).where(Instrument.id.in_(list(qty))))}
    prices = positions.latest_prices(db, list(qty))
    fx = FxConverter(db)
    out = []
    for inst_id, q in qty.items():
        inst = instruments.get(inst_id)
        if inst is None:
            continue
        value = None
        if inst_id in prices:
            price, cur = prices[inst_id]
            value = fx.to_eur(price * float(q), cur or inst.currency)
        out.append((inst, float(q), value))
    out.sort(key=lambda t: -(t[2] or 0))
    return out


def _bucket(rows: dict[str, float], total: float, labels: dict[str, str] | None = None) -> list[dict]:
    out = []
    for key, value in sorted(rows.items(), key=lambda kv: -kv[1]):
        if abs(value) < 0.005:
            continue
        entry = {"key": key, "value": round(value, 2), "weight": round(value / total * 100, 4) if total else 0.0}
        if labels is not None:
            entry["label"] = labels.get(key, key)
        out.append(entry)
    return out


def compute_lookthrough(db: Session, account_id: int | None = None) -> dict:
    """Contract shape of GET /market/lookthrough.

    {total_value, coverage_pct, items:[{key,name,isin,symbol,direct_value,etf_value,total_value,weight,
      sector,country,kind,sources:[{instrument_id,name,value,weight_in_etf}]}], by_sector:[{key,value,weight}],
      by_country:[...], etfs:[{instrument_id,name,value,coverage_pct,source,as_of}], unpriced:[...]}
    Values in EUR, weights in percent of total_value. coverage_pct = share of the portfolio value that is
    resolved down to single companies/assets (ETF residuals and ETFs without holdings count as unresolved).
    """
    pos = position_values(db, account_id)
    index = _Index()
    etfs: list[dict] = []
    unpriced: list[dict] = []
    resolved_value = 0.0

    # 1) direct positions first so that ETF constituents attach to them
    for inst, qty, value in pos:
        if value is None:
            unpriced.append({"instrument_id": inst.id, "name": inst.name, "quantity": qty})
            continue
        if inst.type in FUND_TYPES:
            continue
        if inst.type == InstrumentType.stock:
            item = index.get_or_create(inst.isin, inst.symbol, inst.name, normalize_sector(inst.sector),
                                       normalize_country(inst.country))
            item.name = inst.name                     # prefer the user's instrument name over an ETF's spelling
            alias = ((inst.meta or {}).get("market") or {}).get("yahoo_name")
            if alias:
                index._register(item, None, None, alias)
        else:
            item = index.special(f"inst:{inst.id}", inst.name, "asset")
            item.isin, item.symbol = inst.isin, inst.symbol
            item.sector = normalize_sector(inst.sector) or inst.type.value.capitalize()
            item.country = normalize_country(inst.country)
        item.direct_value += value
        item.sources.append({"instrument_id": inst.id, "name": inst.name, "value": round(value, 2),
                             "weight_in_etf": None, "kind": "direct"})
        resolved_value += value

    # 2) ETFs / funds
    fund_pos = [(inst, value) for inst, _q, value in pos if value is not None and inst.type in FUND_TYPES]
    holdings_by_etf: dict[int, list[EtfHolding]] = defaultdict(list)
    if fund_pos:
        for h in db.scalars(select(EtfHolding).where(EtfHolding.etf_instrument_id.in_([i.id for i, _ in fund_pos]))
                            .order_by(EtfHolding.weight.desc(), EtfHolding.id)):
            holdings_by_etf[h.etf_instrument_id].append(h)
    for inst, value in fund_pos:
        rows = holdings_by_etf.get(inst.id, [])
        if not rows:
            item = index.special(f"etf:{inst.id}", f"{inst.name} (keine Durchschau)", "fund")
            item.isin, item.symbol = inst.isin, inst.symbol
            item.etf_value += value
            item.sources.append({"instrument_id": inst.id, "name": inst.name, "value": round(value, 2),
                                 "weight_in_etf": 100.0, "kind": "etf"})
            etfs.append({"instrument_id": inst.id, "name": inst.name, "value": round(value, 2), "coverage_pct": 0.0,
                         "source": None, "as_of": None})
            continue
        covered = 0.0
        for h in rows:
            w = float(h.weight)
            part = value * w / 100.0
            covered += w
            if h.asset_class in ("cash", "derivative"):
                item = index.special(CASH_KEY, CASH_LABEL, "cash")
                item.sector = CASH_LABEL
            else:
                item = index.get_or_create(h.holding_isin, h.holding_symbol, h.holding_name,
                                           normalize_sector(h.sector), normalize_country(h.country))
                resolved_value += part
            item.etf_value += part
            _add_source(item, inst, part, w)
        residual = 100.0 - covered
        if residual > 0.01:
            item = index.special(OTHER_KEY, OTHER_LABEL, "other")
            part = value * residual / 100.0
            item.etf_value += part
            _add_source(item, inst, part, residual)
        etfs.append({"instrument_id": inst.id, "name": inst.name, "value": round(value, 2),
                     "coverage_pct": round(max(0.0, min(100.0, covered)), 2), "source": rows[0].source,
                     "as_of": rows[0].as_of.isoformat() if rows[0].as_of else None})

    total = sum(i.total for i in index.items.values())
    by_sector: dict[str, float] = defaultdict(float)
    by_country: dict[str, float] = defaultdict(float)
    items = []
    for item in sorted(index.items.values(), key=lambda i: -i.total):
        if abs(item.total) < 0.005:
            continue
        if item.kind == "company" or item.kind == "asset":
            sector, country = item.sector or UNKNOWN, item.country or UNKNOWN
        elif item.kind == "cash":
            sector, country = CASH_LABEL, CASH_LABEL
        else:
            sector, country = UNKNOWN, UNKNOWN
        by_sector[sector] += item.total
        by_country[country] += item.total
        items.append({
            "key": item.key, "name": item.name, "isin": item.isin, "symbol": item.symbol,
            "direct_value": round(item.direct_value, 2), "etf_value": round(item.etf_value, 2),
            "total_value": round(item.total, 2), "weight": round(item.total / total * 100, 4) if total else 0.0,
            "sector": item.sector, "country": item.country, "kind": item.kind,
            "sources": sorted(item.sources, key=lambda s: -s["value"]),
        })
    return {
        "total_value": round(total, 2),
        "coverage_pct": round(resolved_value / total * 100, 2) if total else 0.0,
        "items": items,
        "by_sector": _bucket(by_sector, total, SECTOR_LABELS_DE),
        "by_country": _bucket(by_country, total),
        "etfs": etfs,
        "unpriced": unpriced,
    }


def _add_source(item: _Item, etf: Instrument, value: float, weight: float) -> None:
    for src in item.sources:                       # GOOGL + GOOG of the same ETF -> one source line
        if src.get("kind") == "etf" and src["instrument_id"] == etf.id:
            src["value"] = round(src["value"] + value, 2)
            src["weight_in_etf"] = round(src["weight_in_etf"] + weight, 6)
            return
    item.sources.append({"instrument_id": etf.id, "name": etf.name, "value": round(value, 2),
                         "weight_in_etf": round(weight, 6), "kind": "etf"})
