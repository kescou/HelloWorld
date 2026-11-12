"""Deterministic parsers on synthetic fixtures of every supported format (no DB, no LLM)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ingest import classify
from app.ingest.categorize import cash_tx_type, category_hint
from app.ingest.extract import decode_bytes, detect_kind
from app.ingest.util import (
    find_ibans, find_isin, isin_valid, line_amounts, parse_date, parse_decimal,
)
from test_ingest_support import balances, fx, run, tx


# ----------------------------------------------------------------------------- helpers

@pytest.mark.parametrize("raw,expected", [
    ("1.234,56", "1234.56"), ("-85,00", "-85.00"), ("85,00-", "-85.00"), ("+ 2.500,00", "2500.00"),
    ("1,234.56", "1234.56"), ("0,1234", "0.1234"), ("1.234", "1234"), ("12.5", "12.5"), ("85,00 S", "-85.00"),
    ("2.345,67 €", "2345.67"), ("EUR -3,00", "-3.00"), ("−12,00", "-12.00"), (3.5, "3.5"), ("", None), (None, None),
])
def test_parse_decimal(raw, expected):
    got = parse_decimal(raw)
    assert got == (Decimal(expected) if expected is not None else None)


@pytest.mark.parametrize("raw,expected", [
    ("02.01.2023", date(2023, 1, 2)), ("2.1.23", date(2023, 1, 2)), ("2023-01-02", date(2023, 1, 2)),
    ("02 Jan. 2024", date(2024, 1, 2)), ("2. März 2024", date(2024, 3, 2)), ("31 Okt. 2024", date(2024, 10, 31)),
    ("32.01.2024", None), ("kein Datum", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_without_year():
    assert parse_date("28.12.", default_year=2023) == date(2023, 12, 28)


def test_isin_checksum_and_iban():
    assert isin_valid("US0378331005") and isin_valid("IE00B4L5Y983") and isin_valid("DE0007164600")
    assert not isin_valid("US0378331006") and not isin_valid("DE12345")
    # an IBAN must not be mistaken for an ISIN
    assert find_isin("Konto DE61100123450123456789 ISIN: US88160R1014") == "US88160R1014"
    assert find_ibans("IBAN DE12 5001 0517 5400 1234 56 BIC INGDDEFFXXX") == ["DE12500105175400123456"]


def test_line_amounts_skip_percent_and_share_class_noise():
    assert line_amounts("Kapitalertragsteuer 25,00 % EUR 30,00") == [(Decimal("30.00"), "EUR")]
    assert line_amounts("Registered Shares DL-,001") == []
    assert line_amounts("Provision EUR 0,99 -") == [(Decimal("-0.99"), "EUR")]


def test_category_hints_do_not_match_inside_words():
    assert category_hint("Erika Musterfrau", "Überweisung Geschenk") is None       # 'rwe' inside 'Überweisung'
    assert category_hint("REWE SAGT DANKE") == "Lebensmittel"
    assert category_hint("Stadtwerke Musterstadt", "Abschlag Strom") == "Strom & Gas"
    assert category_hint("HUK-COBURG Haftpflichtversicherung") == "Versicherungen"
    assert cash_tx_type("Entgelt Kontoführung", Decimal("-5.30"), "Entgelt") == "fee"
    assert cash_tx_type("Zinsen Habenzinsen", Decimal("1.20"), "Zinsen") == "interest"
    assert cash_tx_type("Lastschrift Stadtwerke", Decimal("-85")) == "withdrawal"


def test_detect_kind_and_encoding():
    assert detect_kind("a.pdf", None, b"%PDF-1.7") == "pdf"
    assert detect_kind("shot.png", None, b"\x89PNG\r\n\x1a\n") == "image"
    assert detect_kind("umsatz.csv", None, b"a;b") == "csv"
    assert detect_kind("export.txt", "text/plain", fx("ing_umsatz.csv")[:4096]) == "csv"   # csv content wins
    assert detect_kind("notiz.txt", "text/plain", b"hallo welt") == "text"
    assert "Umsätze" in decode_bytes(fx("ing_umsatz.csv"))                                    # latin-1


@pytest.mark.parametrize("name,institution,doc_type", [
    ("tr_kauf.txt", "trade_republic", "trade_buy"), ("tr_verkauf.txt", "trade_republic", "trade_sell"),
    ("tr_sparplan.txt", "trade_republic", "savings_plan"), ("tr_dividende_usd.txt", "trade_republic", "dividend"),
    ("tr_zinsen.txt", "trade_republic", "interest"), ("tr_kontoauszug.txt", "trade_republic", "account_statement"),
    ("baader_kauf.txt", "scalable", "trade_buy"), ("baader_verkauf.txt", "scalable", "trade_sell"),
    ("baader_dividende_usd.txt", "scalable", "dividend"), ("scalable_bank_kauf.txt", "scalable", "trade_buy"),
    ("ing_kauf.txt", "ing", "trade_buy"), ("ing_verkauf.txt", "ing", "trade_sell"),
    ("ing_dividende_usd.txt", "ing", "dividend"), ("ing_kontoauszug.txt", "ing", "account_statement"),
    ("postbank_kontoauszug.txt", "postbank", "account_statement"),
])
def test_classification(name, institution, doc_type):
    text = fx(name).decode("utf-8")
    assert classify.classify(text, name) == (institution, doc_type)


# ----------------------------------------------------------------------------- Trade Republic

def test_tr_buy():
    res = run("tr_kauf.txt")
    assert res.parser == "trade_republic.trade" and res.error is None and not res.used_llm
    (p,) = tx(res)
    assert p["type"] == "buy" and p["isin"] == "US88160R1014"
    assert p["booking_date"] == "2024-01-02" and p["value_date"] == "2024-01-04"
    assert (p["quantity"], p["price"], p["amount"], p["fees"], p["taxes"]) == (3.0, 108.1, -325.3, 1.0, 0.0)
    assert p["name"].startswith("Tesla Inc.") and p["verified"] and not p["warnings"]
    assert res.drafts[0].confidence >= 0.95
    assert res.iban == "DE61100123450123456789"


def test_tr_sell_with_taxes():
    (p,) = tx(run("tr_verkauf.txt"))
    assert p["type"] == "sell" and p["isin"] == "US0378331005"
    assert (p["quantity"], p["price"], p["amount"]) == (5.0, 170.0, 822.63)
    assert p["fees"] == 1.0 and p["taxes"] == 26.37 and p["verified"]


def test_tr_savings_plan_fractional():
    res = run("tr_sparplan.txt")
    (p,) = tx(res)
    assert res.doc_type == "savings_plan"
    assert p["type"] == "buy" and p["quantity"] == 0.5432 and p["price"] == 92.05 and p["amount"] == -50.0
    assert "Sparplan" in p["description"] and p["verified"]


def test_tr_dividend_foreign_currency():
    (p,) = tx(run("tr_dividende_usd.txt"))
    assert p["type"] == "dividend" and p["isin"] == "US0378331005" and p["quantity"] == 10.0
    assert p["amount"] == 1.63 and p["currency"] == "EUR" and p["fx_rate"] == 1.085
    assert p["taxes"] == 0.58            # 0,36 USD / 1,085 + 0,24 + 0,01
    assert p["price"] == pytest.approx(0.24 / 1.085, abs=1e-6) and p["verified"]
    assert p["booking_date"] == "2024-10-15" and "Ex-Tag 11.10.2024" in p["description"]


def test_tr_interest():
    res = run("tr_zinsen.txt")
    (p,) = tx(res)
    assert res.doc_type == "interest"
    assert p["type"] == "interest" and p["amount"] == 17.2 and p["taxes"] == 6.16 and p["gross_amount"] == 23.36
    assert p["booking_date"] == "2024-10-31" and p["verified"] and p["category_hint"] == "Zinsen"


def test_tr_statement_blocks_direction_and_balance():
    res = run("tr_kontoauszug.txt")
    assert res.parser == "trade_republic.statement" and not res.warnings
    t = tx(res)
    assert [(p["type"], p["booking_date"], p["amount"]) for p in t] == [
        ("deposit", "2024-10-02", 500.0), ("buy", "2024-10-04", -50.0), ("withdrawal", "2024-10-07", -23.45),
        ("dividend", "2024-10-15", 1.63), ("interest", "2024-10-31", 17.2)]
    assert t[1]["isin"] == "IE00B4L5Y983" and t[1]["quantity"] == 0.5432
    assert t[2]["counterparty"] == "REWE Markt GmbH" and t[2]["category_hint"] == "Lebensmittel"
    assert t[3]["isin"] == "US0378331005"
    assert balances(res) == [{"account_id": None, "date": "2024-10-31", "balance": 1445.38, "warnings": [], "review_required": False}]
    assert res.iban == "DE61100123450123456789"


# ----------------------------------------------------------------------------- Scalable / Baader

def test_baader_buy():
    res = run("baader_kauf.txt")
    (p,) = tx(res)
    assert res.parser == "scalable.baader" and res.institution == "scalable"
    assert p["type"] == "buy" and p["isin"] == "IE00B4L5Y983" and p["wkn"] == "A0RPWH"
    assert (p["quantity"], p["price"], p["amount"], p["fees"]) == (10.0, 78.5, -785.99, 0.99)
    assert p["booking_date"] == "2022-01-03" and p["value_date"] == "2022-01-05" and p["verified"]


def test_baader_sell_taxes_ignore_bemessungsgrundlage():
    (p,) = tx(run("baader_verkauf.txt"))
    assert p["type"] == "sell" and p["isin"] == "DE0007164600"
    assert p["amount"] == 577.91 and p["fees"] == 0.99 and p["taxes"] == 21.1 and p["verified"]


def test_baader_dividend_usd():
    (p,) = tx(run("baader_dividende_usd.txt"))
    assert p["type"] == "dividend" and p["isin"] == "US5949181045" and p["quantity"] == 20.0
    assert p["amount"] == 10.35 and p["fx_rate"] == 1.08 and p["gross_amount"] == 13.89
    assert p["taxes"] == 3.54            # 2,25 USD / 1,08 + 1,39 + 0,07
    assert p["booking_date"] == "2022-03-10" and p["verified"]


def test_scalable_bank_format():
    res = run("scalable_bank_kauf.txt")
    (p,) = tx(res)
    assert res.parser == "scalable.bank"
    assert p["type"] == "buy" and p["isin"] == "IE00BK5BQT80" and p["quantity"] == 8.5 and p["price"] == 120.0
    assert p["amount"] == -1020.99 and p["fees"] == 0.99 and p["verified"]
    assert p["booking_date"] == "2025-03-03" and p["value_date"] == "2025-03-05"


def test_scalable_csv_export():
    res = run("scalable_transactions.csv")
    t = tx(res)
    assert res.parser == "scalable.csv" and res.doc_type == "csv_export"
    assert [p["type"] for p in t] == ["buy", "buy", "dividend", "sell", "deposit", "interest"]   # cancelled row skipped
    assert t[0]["amount"] == -1020.99 and t[0]["fees"] == 0.99 and t[0]["verified"]
    assert t[1]["quantity"] == 1.2345 and t[1]["amount"] == -100.0
    assert t[2]["amount"] == 9.13 and t[2]["taxes"] == 3.27
    assert t[3]["amount"] == 577.91 and t[3]["verified"]
    assert t[4]["amount"] == 1500.0 and t[5]["amount"] == 3.04
    assert t[0]["reference"] == "SCALabc001"


# ----------------------------------------------------------------------------- ING

def test_ing_buy():
    res = run("ing_kauf.txt")
    (p,) = tx(res)
    assert res.parser == "ing.trade"
    assert p["type"] == "buy" and p["isin"] == "IE00B4L5Y983" and p["wkn"] == "A0RPWH"
    assert (p["quantity"], p["price"], p["amount"], p["fees"]) == (10.0, 75.0, -754.9, 4.9)
    assert p["booking_date"] == "2023-03-15" and p["value_date"] == "2023-03-17" and p["verified"]
    assert p["name"].startswith("iShsIII-Core MSCI World")
    assert res.settlement_iban == "DE12500105175400123456"


def test_ing_sell_taxes():
    (p,) = tx(run("ing_verkauf.txt"))
    assert p["type"] == "sell" and p["amount"] == 962.2 and p["fees"] == 6.15 and p["taxes"] == 31.65
    assert p["verified"] and p["value_date"] == "2023-09-22"


def test_ing_dividend_usd():
    res = run("ing_dividende_usd.txt")
    (p,) = tx(res)
    assert res.parser == "ing.dividend"
    assert p["type"] == "dividend" and p["isin"] == "US0378331005" and p["wkn"] == "865985" and p["quantity"] == 10.0
    assert p["amount"] == 1.63 and p["taxes"] == 0.58 and p["fx_rate"] == 1.085 and p["verified"]
    assert p["booking_date"] == "2024-10-15"


def test_ing_statement():
    res = run("ing_kontoauszug.txt")
    t = tx(res)
    assert res.parser == "ing.statement" and not res.warnings      # old + Σ = new saldo
    assert [p["amount"] for p in t] == [2500.0, -85.0, -23.45, -23.45, -754.9, -20.0, -5.3]
    assert t[0]["type"] == "deposit" and t[0]["counterparty"] == "Muster Arbeitgeber GmbH" and t[0]["category_hint"] == "Gehalt"
    assert "Mandat: SW-000123" in t[1]["description"] and t[1]["value_date"] == "2023-03-02"
    assert t[4]["review_required"] and t[4]["category_hint"] == "Wertpapiere"
    assert t[5]["description"].endswith("Geschenk Rechnung 12,50")   # purpose ending in a number is not a booking
    assert t[6]["type"] == "fee"
    assert balances(res)[0]["balance"] == 2587.9 and balances(res)[0]["date"] == "2023-03-31"
    assert res.iban == "DE12500105175400123456"


def test_ing_csv_latin1_preamble():
    res = run("ing_umsatz.csv")
    t = tx(res)
    assert res.parser == "ing.csv" and res.institution == "ing" and len(t) == 7
    assert t[-1]["amount"] == 2500.0 and t[-1]["counterparty"] == "Muster Arbeitgeber GmbH"
    assert t[1]["description"] == "Überweisung | Geschenk Rechnung 12,50"
    assert sum(p["amount"] for p in t) == pytest.approx(1587.9)
    assert balances(res)[0] == {"account_id": None, "date": "2023-03-31", "balance": 2587.9, "warnings": [], "review_required": False}
    assert res.iban == "DE12500105175400123456"


# ----------------------------------------------------------------------------- Postbank

def test_postbank_statement_year_rollover():
    res = run("postbank_kontoauszug.txt")
    t = tx(res)
    assert res.parser == "postbank.statement" and not res.warnings
    assert [(p["booking_date"], p["amount"]) for p in t] == [
        ("2023-12-28", 2500.0), ("2023-12-29", -85.0), ("2024-01-02", -42.1), ("2024-01-03", -5.9), ("2024-01-04", -800.0)]
    assert t[3]["value_date"] == "2023-12-31" and t[3]["type"] == "fee"
    assert t[0]["counterparty"] == "Muster Arbeitgeber GmbH" and t[4]["category_hint"] == "Miete"
    assert balances(res)[0]["balance"] == 2767.0 and balances(res)[0]["date"] == "2024-01-05"
    assert res.iban == "DE89370400440532013000"


def test_postbank_csv_new_and_old():
    res = run("postbank_umsatz.csv")
    t = tx(res)
    assert res.parser == "postbank.csv" and len(t) == 5
    assert t[0]["amount"] == -800.0 and t[0]["counterparty_iban"] == "DE02120300000000202051"
    assert balances(res)[0] == {"account_id": None, "date": "2024-01-05", "balance": 2767.0, "warnings": [], "review_required": False}
    old = run("postbank_umsatz_alt.csv")
    t = tx(old)
    assert [(p["amount"], p["counterparty"]) for p in t] == [(-85.0, "Stadtwerke Musterstadt"), (1500.0, "Muster Arbeitgeber GmbH")]
    assert balances(old)[0]["balance"] == 1415.0


# ----------------------------------------------------------------------------- generic paste

def test_generic_pasted_table():
    res = run("paste_statement.txt", kind="paste")
    t = tx(res)
    assert res.parser == "generic.statement"
    assert [(p["booking_date"], p["amount"]) for p in t] == [
        ("2024-11-12", -34.56), ("2024-11-11", 2650.0), ("2024-11-08", -13.99), ("2024-11-08", -59.9), ("2024-11-07", -800.0)]
    assert t[0]["counterparty"] == "REWE Markt GmbH" and t[3]["counterparty"] == "Deutsche Bahn AG"
    assert t[1]["type"] == "deposit" and t[4]["type"] == "withdrawal"       # "800,00 S" = Soll
    assert balances(res)[0]["balance"] == 3210.45
    assert res.drafts[0].confidence < 0.9                                     # generic parser is less sure


def test_prose_is_not_a_statement():
    res = run("unknown_broker.txt")
    assert res.drafts == [] and res.error


def test_parser_selfcheck_flags_wrong_math():
    text = fx("baader_kauf.txt").decode().replace("EUR 785,99", "EUR 795,99")
    from app.ingest.pipeline import extract_document

    res = extract_document(text.encode(), "text", "x.txt", llm_mode="off")
    (d,) = res.drafts
    assert d.review_required and d.confidence < 0.7
    assert any(w.startswith("Rechenprobe") for w in d.payload["warnings"])
