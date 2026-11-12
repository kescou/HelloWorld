"""Validation/normalisation, account assignment, duplicate detection and commit (DB, no HTTP)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ingest import commit
from app.ingest.dto import Draft
from app.ingest.validate import assign_accounts, mark_duplicates, normalize_draft, quality_ok
from app.models import (
    BalanceSnapshot, Category, CategoryKind, Document, DocumentStatus, DraftStatus, ExtractionDraft, Instrument,
    InstrumentType, Transaction, TxType,
)
from test_ingest_support import IBAN_ING, accounts  # noqa: F401


def _tx(**kw) -> Draft:
    base = {"type": "buy", "booking_date": "2024-01-02", "isin": "US88160R1014", "name": "Tesla", "quantity": 3,
            "price": 108.10, "amount": -325.30, "fees": 1.0, "taxes": 0}
    base.update(kw)
    return Draft(kind="transaction", payload=base, confidence=0.95)


def n(**kw) -> Draft:
    return normalize_draft(_tx(**kw))


# ----------------------------------------------------------------------------- normalisation

def test_arithmetic_ok_marks_verified():
    d = n()
    assert d.payload["verified"] and not d.payload["warnings"] and not d.review_required and d.confidence == 0.95
    assert d.payload["amount"] == -325.3 and d.payload["booking_date"] == "2024-01-02"


@pytest.mark.parametrize("kw", [{"amount": -335.30}, {"quantity": 4}, {"fees": 11.0}])
def test_arithmetic_mismatch_lowers_confidence_and_flags(kw):
    d = n(**kw)
    assert not d.payload["verified"] and d.review_required and d.confidence <= 0.65
    assert any(w.startswith("Rechenprobe") for w in d.payload["warnings"])
    assert not quality_ok([d])


def test_tolerance_allows_rounding_of_fractional_shares():
    d = n(quantity="0,5432", price="92,05", amount="-50,00", fees=0)
    assert d.payload["verified"]


def test_sell_and_dividend_math():
    assert n(type="sell", quantity=5, price=170, amount=822.63, fees=1, taxes=26.37).payload["verified"]
    assert n(type="dividend", quantity=10, price=0.2212, amount=1.63, fees=0, taxes=0.58).payload["verified"]
    bad = n(type="dividend", quantity=10, price=0.2212, amount=2.21, fees=0, taxes=0.58)
    assert bad.review_required and not bad.payload["verified"]


def test_sign_conventions_enforced():
    assert n(amount=325.30).payload["amount"] == -325.3                          # buy is always negative
    assert n(type="sell", amount=-323.30, fees=1).payload["amount"] == 323.3
    assert n(type="withdrawal", isin=None, name=None, quantity=None, price=None, amount=85).payload["amount"] == -85.0
    assert n(type="deposit", isin=None, name=None, quantity=None, price=None, amount=-85).payload["amount"] == 85.0


def test_german_strings_and_type_aliases_are_coerced():
    d = n(type="Kauf", booking_date="02.01.2024", value_date="04.01.2024", quantity="3", price="108,10", amount="325,30-", fees="1,00")
    p = d.payload
    assert p["type"] == "buy" and p["booking_date"] == "2024-01-02" and p["value_date"] == "2024-01-04" and p["verified"]


def test_invalid_isin_missing_fields_and_date_sanity():
    d = n(isin="US88160R1015")
    assert d.review_required and any("Prüfziffer" in w for w in d.payload["warnings"])
    d = n(booking_date=None, value_date=None)
    assert d.review_required and d.confidence <= 0.3 and not quality_ok([d])
    d = n(booking_date=(date.today() + timedelta(days=60)).isoformat())
    assert d.review_required and any("Zukunft" in w for w in d.payload["warnings"])
    d = n(quantity=None)
    assert d.review_required and "Stückzahl fehlt." in d.payload["warnings"]
    d = n(amount=None)
    assert d.payload["amount"] == -325.3 and any("berechnet" in w for w in d.payload["warnings"])
    d = n(type="quatsch", amount=-5, isin=None, name=None, quantity=None, price=None)
    assert d.payload["type"] == "withdrawal" and d.review_required


def test_missing_price_is_derived():
    d = n(price=None)
    assert d.payload["price"] == pytest.approx(108.1) and any("Kurs fehlte" in w for w in d.payload["warnings"])


def test_balance_draft_normalisation():
    d = normalize_draft(Draft(kind="balance", payload={"date": "31.03.2023", "balance": "2.587,90"}))
    assert d.payload["date"] == "2023-03-31" and d.payload["balance"] == 2587.9 and not d.review_required
    d = normalize_draft(Draft(kind="balance", payload={"date": None, "balance": "1,00"}))
    assert d.review_required


# ----------------------------------------------------------------------------- accounts

def _cash(**kw) -> Draft:
    base = {"type": "withdrawal", "booking_date": "2024-03-05", "amount": -23.45, "counterparty": "REWE"}
    base.update(kw)
    return normalize_draft(Draft(kind="transaction", payload=base, confidence=0.95))


def test_assign_by_institution_and_role(db, accounts):
    trade, cash, bal = n(), _cash(), normalize_draft(Draft(kind="balance", payload={"date": "2024-03-31", "balance": 1}, role="cash"))
    assign_accounts(db, [trade, cash, bal], institution="trade_republic")
    assert trade.payload["account_id"] == accounts["tr_depot"].id        # trades go to the depot
    assert cash.payload["account_id"] == accounts["tr_cash"].id == bal.payload["account_id"]
    d = _cash()
    assign_accounts(db, [d], institution="ing")
    assert d.payload["account_id"] == accounts["ing_giro"].id
    d = _cash()
    assign_accounts(db, [d], institution="postbank")
    assert d.payload["account_id"] == accounts["postbank"].id


def test_user_hint_wins_but_trades_still_go_to_the_depot(db, accounts):
    trade, cash = n(), _cash()
    doc_acc = assign_accounts(db, [trade, cash], hint_account_id=accounts["ing_giro"].id, institution="trade_republic")
    assert trade.payload["account_id"] == accounts["ing_depot"].id       # depot settled via the hinted giro
    assert cash.payload["account_id"] == accounts["ing_giro"].id and doc_acc == accounts["ing_giro"].id
    cash2 = _cash()
    assign_accounts(db, [cash2], hint_account_id=accounts["sc_depot"].id)
    assert cash2.payload["account_id"] == accounts["sc_cash"].id          # cash line + depot hint -> settlement account


def test_iban_on_document_beats_institution(db, accounts):
    d = _cash()
    assign_accounts(db, [d], institution="postbank", iban="DE12 5001 0517 5400 1234 56")
    assert d.payload["account_id"] == accounts["ing_giro"].id
    t = n()
    assign_accounts(db, [t], institution=None, settlement_iban=IBAN_ING)
    assert t.payload["account_id"] == accounts["ing_depot"].id


def test_unknown_account_is_flagged(db, accounts):
    d = _cash()
    assign_accounts(db, [d], institution=None)
    assert d.payload["account_id"] is None and d.review_required
    assert "Konto nicht erkannt – bitte Konto auswählen." in d.payload["warnings"]


def test_own_account_transfer_detected_via_iban(db, accounts):
    d = _cash(counterparty="Max Mustermann", description=f"Sparen {IBAN_ING}", amount=-500)
    assign_accounts(db, [d], institution="postbank")
    assert d.payload["type"] == "transfer_out" and d.payload["transfer_account_id"] == accounts["ing_giro"].id
    assert d.payload["category_hint"] == "Umbuchung"


# ----------------------------------------------------------------------------- duplicates + commit

def _doc(db) -> Document:
    doc = Document(filename="t.txt", sha256=f"{id(object()):064x}"[:64], source_kind="text", status=DocumentStatus.review)
    db.add(doc)
    db.flush()
    return doc


def _persist(db, doc, d: Draft) -> ExtractionDraft:
    row = ExtractionDraft(kind=d.kind, payload=d.payload, confidence=d.confidence, status=DraftStatus(d.status))
    doc.drafts.append(row)
    db.flush()
    return row


def test_commit_creates_transaction_instrument_and_settlement_link(db, accounts):
    doc = _doc(db)
    d = n(wkn="A1CX3T")
    assign_accounts(db, [d], institution="trade_republic")
    mark_duplicates(db, [d])
    row = _persist(db, doc, d)
    commit.accept_draft(db, row)
    tx = db.get(Transaction, row.transaction_id)
    assert row.status == DraftStatus.accepted and doc.status == DocumentStatus.imported
    assert tx.account_id == accounts["tr_depot"].id and tx.cash_account_id == accounts["tr_cash"].id
    assert tx.type == TxType.buy and tx.amount == Decimal("-325.30") and tx.quantity == Decimal("3") and tx.fees == Decimal("1.00")
    assert tx.source_document_id == doc.id and tx.booking_date == date(2024, 1, 2)
    inst = tx.instrument
    assert inst.isin == "US88160R1014" and inst.name == "Tesla" and inst.wkn == "A1CX3T" and inst.type == InstrumentType.stock
    assert tx.dedupe_hash == commit.dedupe_hash(tx.account_id, "buy", date(2024, 1, 2), "US88160R1014", Decimal("3"), Decimal("-325.30"))


def test_dedupe_hash_matches_ledger_service_formula():
    ledger = pytest.importorskip("app.services.ledger")
    args = (7, TxType.buy, date(2024, 1, 2), "us88160r1014", Decimal("3"), Decimal("-325.3"))
    assert commit.dedupe_hash(*args) == ledger.dedupe_hash(*args)


def test_existing_instrument_is_reused_and_etf_type_guessed(db, accounts):
    db.add(Instrument(isin="US88160R1014", name="Tesla Inc.", type=InstrumentType.stock))
    db.flush()
    doc = _doc(db)
    a, b = n(), n(isin="IE00B4L5Y983", name="iShares Core MSCI World UCITS ETF", booking_date="2024-01-03")
    assign_accounts(db, [a, b], institution="ing")
    ra, rb = _persist(db, doc, a), _persist(db, doc, b)
    commit.accept_draft(db, ra)
    commit.accept_draft(db, rb)
    assert db.scalar(select(Instrument).where(Instrument.isin == "US88160R1014")).name == "Tesla Inc."
    assert len(list(db.scalars(select(Instrument)))) == 2
    assert db.get(Transaction, rb.transaction_id).instrument.type == InstrumentType.etf


def test_duplicate_detection_same_account_amount_isin_within_window(db, accounts):
    doc = _doc(db)
    first = n()
    assign_accounts(db, [first], institution="trade_republic")
    commit.accept_draft(db, _persist(db, doc, first))
    # same trade seen again in a statement two days later (valuta) -> duplicate
    again = n(booking_date="2024-01-04", price=None)
    other_amount = n(amount=-433.40, quantity=4)
    other_isin = n(isin="US0378331005", name="Apple")
    far = n(booking_date="2024-02-20")
    drafts = [again, other_amount, other_isin, far]
    assign_accounts(db, drafts, institution="trade_republic")
    mark_duplicates(db, drafts)
    assert [d.status for d in drafts] == ["duplicate", "pending", "pending", "pending"]
    assert again.payload["duplicate_of"] == db.scalar(select(Transaction.id))


def test_identical_lines_in_one_statement_are_not_collapsed(db, accounts):
    doc = _doc(db)
    a, b = _cash(), _cash()
    assign_accounts(db, [a, b], institution="ing")
    mark_duplicates(db, [a, b])
    assert (a.status, b.status) == ("pending", "pending") and (a.payload["dup_index"], b.payload["dup_index"]) == (0, 1)
    ra, rb = _persist(db, doc, a), _persist(db, doc, b)
    commit.accept_draft(db, ra)
    commit.accept_draft(db, rb)
    assert ra.transaction_id != rb.transaction_id
    # importing the same statement again: both are duplicates now, a third identical line would not be
    c, d, e = _cash(), _cash(), _cash()
    assign_accounts(db, [c, d, e], institution="ing")
    mark_duplicates(db, [c, d, e])
    assert [x.status for x in (c, d, e)] == ["duplicate", "duplicate", "pending"]


def test_commit_refuses_hash_duplicate_and_force_overrides(db, accounts):
    doc = _doc(db)
    a, b = _cash(), _cash()
    assign_accounts(db, [a, b], institution="ing")
    ra, rb = _persist(db, doc, a), _persist(db, doc, b)      # no mark_duplicates -> both dup_index 0
    commit.accept_draft(db, ra)
    with pytest.raises(commit.DuplicateDraft) as exc:
        commit.accept_draft(db, rb)
    assert exc.value.transaction_id == ra.transaction_id and rb.status == DraftStatus.duplicate
    commit.accept_draft(db, rb, force=True)
    assert rb.status == DraftStatus.accepted and rb.transaction_id != ra.transaction_id


def test_giro_line_is_duplicate_of_cash_side_of_depot_trade(db, accounts):
    doc = _doc(db)
    trade = n(booking_date="2023-03-15", isin="IE00B4L5Y983", quantity=10, price=75, amount=-754.90, fees=4.90)
    assign_accounts(db, [trade], institution="ing")
    commit.accept_draft(db, _persist(db, doc, trade))
    giro = _cash(booking_date="2023-03-17", amount=-754.90, counterparty="WP-ABRECHNUNG")
    assign_accounts(db, [giro], institution="ing")
    mark_duplicates(db, [giro])
    assert giro.status == "duplicate" and any("Cash-Seite" in w for w in giro.payload["warnings"])


def test_trade_warns_when_cash_side_was_already_imported_from_giro(db, accounts):
    doc = _doc(db)
    giro = _cash(booking_date="2023-03-17", amount=-754.90)
    assign_accounts(db, [giro], institution="ing")
    commit.accept_draft(db, _persist(db, doc, giro))
    trade = n(booking_date="2023-03-15", isin="IE00B4L5Y983", quantity=10, price=75, amount=-754.90, fees=4.90)
    assign_accounts(db, [trade], institution="ing")
    mark_duplicates(db, [trade])
    assert trade.status == "pending" and trade.review_required
    assert any("Verrechnungskonto" in w for w in trade.payload["warnings"])


def test_balance_commit_upserts_snapshot_and_detects_duplicate(db, accounts):
    doc = _doc(db)
    b = normalize_draft(Draft(kind="balance", payload={"date": "2023-03-31", "balance": 2587.90}, role="cash"))
    assign_accounts(db, [b], institution="ing")
    row = _persist(db, doc, b)
    commit.accept_draft(db, row)
    snap = db.get(BalanceSnapshot, row.balance_snapshot_id)
    assert snap.account_id == accounts["ing_giro"].id and snap.balance == Decimal("2587.90") and snap.date == date(2023, 3, 31)
    same = normalize_draft(Draft(kind="balance", payload={"date": "2023-03-31", "balance": 2587.90}, role="cash"))
    changed = normalize_draft(Draft(kind="balance", payload={"date": "2023-03-31", "balance": 2600}, role="cash"))
    assign_accounts(db, [same, changed], institution="ing")
    mark_duplicates(db, [same, changed])
    assert same.status == "duplicate" and changed.status == "pending" and any("überschreibt" in w for w in changed.payload["warnings"])
    commit.accept_draft(db, _persist(db, doc, changed))
    assert db.scalar(select(BalanceSnapshot.balance).where(BalanceSnapshot.account_id == accounts["ing_giro"].id)) == Decimal("2600.00")


def test_commit_errors_are_german_and_category_is_resolved(db, accounts):
    doc = _doc(db)
    cat = Category(name="Lebensmittel", kind=CategoryKind.expense)
    rule = Category(name="Wohnen", kind=CategoryKind.expense, match_rules=["hausverwaltung"])
    db.add_all([cat, rule])
    db.flush()
    no_acc = _cash()
    row = _persist(db, doc, no_acc)
    with pytest.raises(commit.CommitError, match="Konto"):
        commit.accept_draft(db, row)
    rewe = _cash(counterparty="REWE SAGT DANKE")
    miete = _cash(counterparty="Hausverwaltung Schmidt", amount=-800, booking_date="2024-03-01", category_hint="Gibtsnicht")
    assign_accounts(db, [rewe, miete], institution="postbank")
    r1, r2 = _persist(db, doc, rewe), _persist(db, doc, miete)
    commit.accept_draft(db, r1)
    commit.accept_draft(db, r2)
    assert db.get(Transaction, r1.transaction_id).category_id == cat.id
    assert db.get(Transaction, r2.transaction_id).category_id == rule.id
    assert db.get(Transaction, r1.transaction_id).cash_account_id is None
    with pytest.raises(commit.CommitError, match="abgelehnt"):
        commit.reject_draft(db, r1)
    assert doc.status == DocumentStatus.review            # `row` (no account) is still pending
    commit.reject_draft(db, row)
    assert doc.status == DocumentStatus.imported


# ----------------------------------------------------------------------------- reviewer findings (sprint 4)
def test_second_purchase_with_same_amount_but_other_quantity_is_not_a_duplicate(db, accounts):
    """M8: the duplicate check ignored the quantity – savings plan + one-off purchase of 325.30 € within 4 days."""
    doc = _doc(db)
    first = n()
    assign_accounts(db, [first], institution="trade_republic")
    commit.accept_draft(db, _persist(db, doc, first))
    second = n(booking_date="2024-01-04", quantity=2.9, price=111.827586)      # same 325.30 €, different shares
    same = n(booking_date="2024-01-04")                                        # really the same trade again
    no_qty = n(booking_date="2024-01-04", quantity=None, price=None)           # cannot be compared -> stays duplicate
    drafts = [second, same, no_qty]
    assign_accounts(db, drafts, institution="trade_republic")
    for d in drafts:
        mark_duplicates(db, [d])
    assert [d.status for d in drafts] == ["pending", "duplicate", "duplicate"]


def test_arithmetic_check_converts_foreign_currency_price(db):
    """L: qty × price was compared with the EUR amount without the exchange rate -> false alarms on USD trades."""
    usd = n(quantity=10, price=200, currency="USD", fx_rate=1.25, fees=1, amount=-1601)
    assert usd.payload["verified"] and not usd.payload["warnings"] and not usd.review_required
    wrong = n(quantity=10, price=200, currency="USD", fx_rate=1.25, fees=1, amount=-2001)
    assert wrong.review_required and any("Rechenprobe" in w for w in wrong.payload["warnings"])
    # no exchange rate on the document: nothing to verify, but no false alarm either
    no_fx = n(quantity=10, price=200, currency="USD", fees=1, amount=-1601)
    assert not no_fx.payload["verified"] and not any("Rechenprobe" in w for w in no_fx.payload["warnings"])
    # a missing amount / price is derived through the rate as well
    assert n(quantity=10, price=200, currency="USD", fx_rate=1.25, fees=1, amount=None).payload["amount"] == -1601.0
    assert n(quantity=10, price=None, currency="USD", fx_rate=1.25, fees=1, amount=-1601).payload["price"] == 200.0


def test_soll_marker_after_currency_is_negative():
    """L: "5,00 EUR S" was parsed as +5 (S = Soll)."""
    from app.ingest.util import parse_decimal

    assert parse_decimal("5,00 EUR S") == Decimal("-5.00")
    assert parse_decimal("85,00 S") == Decimal("-85.00")
    assert parse_decimal("1.234,56 € S") == Decimal("-1234.56")
    assert parse_decimal("5,00 EUR H") == Decimal("5.00")
    assert parse_decimal("5,00 EUR") == Decimal("5.00")
    assert parse_decimal("12 USD") == Decimal("12")
