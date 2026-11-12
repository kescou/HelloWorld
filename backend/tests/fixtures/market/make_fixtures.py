"""Regenerates the binary fixtures (run once: python tests/fixtures/market/make_fixtures.py)."""
from pathlib import Path

import openpyxl

HERE = Path(__file__).parent


def xtrackers() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Constituents"
    ws.append(["Xtrackers MSCI World UCITS ETF 1C"])
    ws.append(["IE00BJ0KDQ92"])
    ws.append(["Constituents as of", "30/10/2026"])
    ws.append([])
    ws.append(["Row", "Name", "ISIN", "Country", "Currency", "Exchange", "Type of Security", "Rating",
               "Primary Listing", "Industry Classification", "Weighting"])
    rows = [
        (1, "NVIDIA CORP", "US67066G1040", "United States", "USD", "NASDAQ", "Equity", "-", "NASDAQ", "Information Technology", 0.0561),
        (2, "APPLE INC", "US0378331005", "United States", "USD", "NASDAQ", "Equity", "-", "NASDAQ", "Information Technology", 0.0493),
        (3, "MICROSOFT CORP", "US5949181045", "United States", "USD", "NASDAQ", "Equity", "-", "NASDAQ", "Information Technology", 0.0441),
        (4, "ALPHABET INC-CL A", "US02079K3059", "United States", "USD", "NASDAQ", "Equity", "-", "NASDAQ", "Communication Services", 0.0210),
        (5, "ALPHABET INC-CL C", "US02079K1079", "United States", "USD", "NASDAQ", "Equity", "-", "NASDAQ", "Communication Services", 0.0178),
        (6, "SAP SE", "DE0007164600", "Germany", "EUR", "Xetra", "Equity", "-", "Xetra", "Information Technology", 0.0038),
        (7, "USD CASH", None, "-", "USD", "-", "Cash", "-", "-", "-", 0.0004),
    ]
    for r in rows:
        ws.append(list(r))
    wb.save(HERE / "xtrackers_constituent.xlsx")


def manual_xlsx() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Wertpapier", "Ticker", "Marktwert", "Sektor"])
    ws.append(["Microsoft", "MSFT", 6000, "Technology"])
    ws.append(["Allianz SE", "ALV", 3000, "Financial Services"])
    ws.append(["Kasse", None, 1000, None])
    wb.save(HERE / "manual_marketvalue.xlsx")


if __name__ == "__main__":
    xtrackers()
    manual_xlsx()
