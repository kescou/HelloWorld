from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models.core import CategoryKind, Instrument, InstrumentType, Transaction, TxType
from app.schemas.core import TransactionUpdate
from app.services import ledger
from test_core_helpers import make_accounts, make_category, make_instrument, tx

TODAY = date.today()


def test_cash_balance_rule(db):
    acc = make_accounts(db)
    tx(db, acc["giro"], "deposit", TODAY, amount=D("2500.00"), counterparty="Arbeitgeber")
    tx(db, acc["giro"], "withdrawal", TODAY, amount=D("-320.55"), counterparty="REWE")
    tx(db, acc["cash"], "deposit", TODAY, amount=D("5000"))
    buy = tx(db, acc["depot"], "buy", TODAY, isin="DE0007164600", name="SAP SE", quantity=D("10"), price=D("100"),
             fees=D("1"))
    assert buy.cash_account_id == acc["cash"].id       # defaults to the settlement account
    assert buy.amount == D("-1001.00")
    balances = ledger.cash_balances(db)
    assert balances[acc["giro"].id] == D("3179.45")     # opening 1000 + 2500 - 320.55
    assert balances[acc["cash"].id] == D("3999.00")     # 5000 - 1001 (buy booked on the depot)
    assert balances[acc["depot"].id] == D("0.00")
    assert ledger.cash_balance(db, acc["cash"].id) == D("3999.00")
    assert ledger.cash_balance(db, acc["giro"].id, as_of=TODAY - timedelta(days=1)) == D("1000.00")


def test_amount_computation_per_type(db):
    acc = make_accounts(db)
    inst = make_instrument(db)
    sell_base = dict(instrument_id=inst.id, quantity=D("4"), price=D("50"))
    buy = tx(db, acc["depot"], "buy", TODAY, **sell_base, fees=D("2.50"), taxes=D("0.50"))
    assert buy.amount == D("-203.00")
    sell = tx(db, acc["depot"], "sell", TODAY, **sell_base, fees=D("2.50"), taxes=D("10"))
    assert sell.amount == D("187.50")
    div = tx(db, acc["depot"], "dividend", TODAY, instrument_id=inst.id, quantity=D("4"), price=D("2.20"), taxes=D("2.32"))
    assert div.amount == D("6.48")
    split = tx(db, acc["depot"], "split", TODAY, instrument_id=inst.id, quantity=D("2"), amount=D("99"))
    assert split.amount == D("0")
    # explicit amount wins, buy sign is normalised, price is derived
    buy2 = tx(db, acc["depot"], "buy", TODAY - timedelta(days=1), instrument_id=inst.id, quantity=D("2"),
              amount=D("101.00"), fees=D("1"))
    assert buy2.amount == D("-101.00")
    assert buy2.price == D("50")
    with pytest.raises(Exception) as exc:
        tx(db, acc["giro"], "deposit", TODAY)
    assert exc.value.status_code == 422
    # foreign currency: price in USD, cash effect in EUR via fx_rate (1 EUR = x USD)
    usd = tx(db, acc["depot"], "buy", TODAY - timedelta(days=2), instrument_id=inst.id, quantity=D("2"),
             price=D("125"), currency="USD", fx_rate=D("1.25"), fees=D("1"))
    assert usd.amount == D("-201.00")
    # negative "deposit" becomes a withdrawal
    w = tx(db, acc["giro"], "deposit", TODAY, amount=D("-12.34"))
    assert w.type == TxType.withdrawal


def test_instrument_resolved_or_created_by_isin_and_symbol(db):
    acc = make_accounts(db)
    t1 = tx(db, acc["depot"], "buy", TODAY, isin="ie00b4l5y983", name="iShares Core MSCI World UCITS ETF",
            quantity=D("1"), price=D("100"))
    inst = db.get(Instrument, t1.instrument_id)
    assert inst.isin == "IE00B4L5Y983" and inst.type == InstrumentType.etf
    t2 = tx(db, acc["depot"], "buy", TODAY, isin="IE00B4L5Y983", quantity=D("2"), price=D("100"))
    assert t2.instrument_id == inst.id
    t3 = tx(db, acc["depot"], "buy", TODAY, symbol="msft", quantity=D("1"), price=D("400"))
    t4 = tx(db, acc["depot"], "sell", TODAY, symbol="MSFT", quantity=D("1"), price=D("410"))
    assert t3.instrument_id == t4.instrument_id != inst.id
    assert db.scalar(select(Instrument).where(Instrument.symbol == "MSFT")).type == InstrumentType.stock
    with pytest.raises(Exception) as exc:
        tx(db, acc["depot"], "buy", TODAY, quantity=D("1"), price=D("1"))
    assert exc.value.status_code == 422


def test_dedupe_raises_409(db):
    acc = make_accounts(db)
    data = dict(isin="DE0007164600", quantity=D("3"), price=D("120.5"))
    first = tx(db, acc["depot"], "buy", TODAY, **data)
    assert len(first.dedupe_hash) == 64
    with pytest.raises(ledger.DuplicateTransactionError) as exc:
        tx(db, acc["depot"], "buy", TODAY, **data)
    assert exc.value.status_code == 409 and exc.value.existing_id == first.id
    # differing quantity / date / explicit opt-out are fine
    tx(db, acc["depot"], "buy", TODAY, **{**data, "quantity": D("4")})
    tx(db, acc["depot"], "buy", TODAY - timedelta(days=1), **data)
    dup = tx(db, acc["depot"], "buy", TODAY, **data, allow_duplicate=True)
    assert dup.dedupe_hash is None
    assert first.dedupe_hash == ledger.dedupe_hash(acc["depot"].id, "buy", TODAY, "DE0007164600", D("3.0"), D("-361.50"))


def test_transfer_creates_two_linked_transactions(db):
    acc = make_accounts(db)
    out_tx, in_tx = ledger.create_transfer(db, {
        "from_account_id": acc["giro"].id, "to_account_id": acc["cash"].id, "amount": D("400"), "date": TODAY,
    })
    assert (out_tx.type, in_tx.type) == (TxType.transfer_out, TxType.transfer_in)
    assert (out_tx.amount, in_tx.amount) == (D("-400.00"), D("400.00"))
    assert out_tx.transfer_peer_id == in_tx.id and in_tx.transfer_peer_id == out_tx.id
    balances = ledger.cash_balances(db)
    assert balances[acc["giro"].id] == D("600.00") and balances[acc["cash"].id] == D("400.00")
    with pytest.raises(ledger.DuplicateTransactionError):
        ledger.create_transfer(db, {"from_account_id": acc["giro"].id, "to_account_id": acc["cash"].id,
                                    "amount": D("400"), "date": TODAY})
    # editing one leg keeps the peer in sync, deleting removes both
    ledger.update_transaction(db, out_tx, TransactionUpdate(amount=D("-250")))
    assert db.get(Transaction, in_tx.id).amount == D("250.00")
    deleted = ledger.delete_transaction(db, out_tx)
    assert sorted(deleted) == sorted([out_tx.id, in_tx.id])
    assert db.scalar(select(Transaction).where(Transaction.id.in_(deleted))) is None
    with pytest.raises(Exception) as exc:
        ledger.create_transfer(db, {"from_account_id": acc["giro"].id, "to_account_id": acc["giro"].id,
                                    "amount": D("1"), "date": TODAY})
    assert exc.value.status_code == 422


def test_category_auto_assign(db):
    acc = make_accounts(db)
    food = make_category(db, "Lebensmittel", rules=["REWE", "edeka", "ALDI", "LIDL"])
    shop = make_category(db, "Shopping", rules=["Amazon"])
    abo = make_category(db, "Abos", rules=["Amazon Prime"])
    salary = make_category(db, "Gehalt", CategoryKind.income, rules=["Gehalt"])
    t1 = tx(db, acc["giro"], "withdrawal", TODAY, amount=D("-23.10"), counterparty="Rewe Markt GmbH")
    t2 = tx(db, acc["giro"], "withdrawal", TODAY, amount=D("-89.90"), description="AMAZON PRIME Jahresabo")
    t3 = tx(db, acc["giro"], "deposit", TODAY, amount=D("3000"), description="Lohn/Gehalt 05/2025")
    t4 = tx(db, acc["giro"], "withdrawal", TODAY, amount=D("-5"), counterparty="Unbekannt")
    t5 = tx(db, acc["giro"], "withdrawal", TODAY, amount=D("-7"), counterparty="x", category_hint="shopping")
    assert (t1.category_id, t2.category_id, t3.category_id) == (food.id, abo.id, salary.id)  # longest rule wins
    assert t4.category_id is None and t5.category_id == shop.id
    # rules added later are applied by auto-assign to uncategorised rows only
    misc = make_category(db, "Sonstiges", rules=["unbekannt"])
    t1.category_id = None
    db.flush()
    assert ledger.auto_assign_categories(db) == 2
    assert t4.category_id == misc.id and t1.category_id == food.id
    assert ledger.auto_assign_categories(db) == 0
    # short rules (<= 4 chars) only match whole words
    taxi = make_category(db, "Taxi", rules=["Uber", "TIER"])
    cats = [food, shop, abo, salary, misc, taxi]
    assert ledger.match_category(cats, "Bäckerei Huber") is None
    assert ledger.match_category(cats, "Tierarztpraxis") is None
    assert ledger.match_category(cats, "UBER *TRIP Amsterdam") is taxi
    assert ledger.match_category(cats, "ALDI-SUED sagt Danke") is food
