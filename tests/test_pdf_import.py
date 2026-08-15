"""
Tests for PDF bank statement parsing (pdf_import.py). pdfplumber is mocked;
the line/table parsers are exercised directly.
"""

from datetime import date

import pandas as pd
import pytest

import pdf_import
from pdf_import import parse_text_lines, parse_table_rows, extract_transactions_from_pdf


def test_parse_text_lines_eu_dates_and_amounts():
    text = ("01.02.2025 MAXI SUPERMARKET BEOGRAD -1.234,56\n"
            "15/02/2025 KAFETERIJA -3,50\n"
            "2025-02-20 NETFLIX.COM -12.99\n"
            "Random line without amounts\n")
    rows = parse_text_lines(text)
    assert len(rows) == 3
    assert rows[0]["date"] == date(2025, 2, 1)
    assert rows[0]["amount"] == pytest.approx(-1234.56)
    assert rows[1]["date"] == date(2025, 2, 15)
    assert rows[2]["date"] == date(2025, 2, 20)
    assert rows[2]["amount"] == pytest.approx(-12.99)


def test_parse_text_lines_comma_decimal_without_thousands():
    """Regression: '1234,56' must parse as 1234.56, not 234.56."""
    rows = parse_text_lines("05.04.2025 SOMETHING -1234,56")
    assert rows[0]["amount"] == pytest.approx(-1234.56)


def test_parse_text_lines_skips_lines_without_date_or_amount():
    rows = parse_text_lines("hello world\n02.03.2025 no amount here\n")
    assert rows == []


def test_parse_table_rows():
    rows = [
        ["Date", "Description", "Debit"],
        ["01.03.2025", "ELECTRICITY BILL", "-45.00"],
        ["02.03.2025", "SALARY", "1200.00"],
    ]
    out = parse_table_rows(rows)
    assert len(out) == 2
    assert out[0]["description"] == "ELECTRICITY BILL"
    assert out[0]["amount"] == pytest.approx(-45.0)
    assert out[1]["amount"] == pytest.approx(1200.0)


def test_extract_transactions_from_pdf_tables_path(monkeypatch):
    class FakePage:
        def extract_tables(self):
            return [[["01.04.2025", "LIDL", "-20.00"]]]

        def extract_text(self):
            return ""

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pdf_import.pdfplumber, "open",
                        lambda _: FakePdf())
    df = extract_transactions_from_pdf(b"fake-pdf-bytes")
    assert list(df.columns) == ["date", "description", "amount", "currency"]
    assert len(df) == 1
    assert df.iloc[0]["description"] == "LIDL"
    assert df.iloc[0]["amount"] == pytest.approx(-20.0)


def test_extract_transactions_from_pdf_text_fallback(monkeypatch):
    class FakePage:
        def extract_tables(self):
            return []

        def extract_text(self):
            return "05.04.2025 GYM MEMBERSHIP -25.00"

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pdf_import.pdfplumber, "open", lambda _: FakePdf())
    df = extract_transactions_from_pdf(b"fake")
    assert len(df) == 1
    assert df.iloc[0]["description"] == "GYM MEMBERSHIP"
