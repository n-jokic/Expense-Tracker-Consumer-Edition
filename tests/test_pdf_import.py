"""
Tests for PDF bank statement parsing (pdf_import.py). pdfplumber is mocked;
the line/table parsers and their pure helpers are exercised directly.
"""

from datetime import date

import pandas as pd
import pytest

import pdf_import
from pdf_import import (
    _classify_columns,
    _parse_amount_token,
    _parse_date_token,
    extract_transactions_from_pdf,
    parse_table_rows,
    parse_text_lines,
)


# --- Existing tests (kept passing) -------------------------------------------

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
    # Header uses a generic "Amount" column: a *Debit* column now correctly
    # negates its values, so sign-preserving extraction is covered here with a
    # generic amount column (see the debit/credit regression tests below).
    rows = [
        ["Date", "Description", "Amount"],
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

    monkeypatch.setattr(pdf_import.pdfplumber, "open", lambda _: FakePdf())
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


# --- Amount token parsing ----------------------------------------------------

def test_parse_amount_token_serbian_thousands():
    assert _parse_amount_token("1.200") == pytest.approx(1200.0)


def test_parse_amount_token_integer():
    assert _parse_amount_token("1200") == pytest.approx(1200.0)


def test_parse_amount_token_parenthesised_negative():
    assert _parse_amount_token("(45.00)") == pytest.approx(-45.0)


def test_parse_amount_token_trailing_minus():
    assert _parse_amount_token("45.00-") == pytest.approx(-45.0)


def test_parse_amount_token_both_separators():
    assert _parse_amount_token("1.234,56") == pytest.approx(1234.56)
    assert _parse_amount_token("1,234.56") == pytest.approx(1234.56)


def test_parse_amount_token_currency_symbols():
    assert _parse_amount_token("€1.200") == pytest.approx(1200.0)
    assert _parse_amount_token("1.200,00 €") == pytest.approx(1200.0)


def test_parse_amount_token_date_is_not_amount():
    assert _parse_amount_token("2025.01.02") is None
    assert _parse_amount_token("05.04.2025") is None


# --- Date parsing ------------------------------------------------------------

def test_parse_date_token_iso_dot_and_slash():
    assert _parse_date_token("2025.01.02") == date(2025, 1, 2)
    assert _parse_date_token("2025/01/02") == date(2025, 1, 2)
    assert _parse_date_token("2025-02-20") == date(2025, 2, 20)


def test_parse_date_token_day_first_heuristic():
    # first token > 12 -> day-first
    assert _parse_date_token("13.01.2025") == date(2025, 1, 13)
    # second token > 12 -> month-first
    assert _parse_date_token("01.13.2025") == date(2025, 1, 13)
    # default remains day-first
    assert _parse_date_token("01.02.2025") == date(2025, 2, 1)


# --- Column classification ---------------------------------------------------

def test_classify_columns_serbian_headers():
    rows = [["Datum", "Opis", "Duguje", "Potražuje", "Saldo"]]
    roles = _classify_columns(rows)
    assert roles == {0: "date", 1: "description", 2: "debit", 3: "credit", 4: "balance"}


def test_parse_table_rows_balance_column_ignored():
    rows = [
        ["Date", "Description", "Amount", "Balance"],
        ["01.02.2025", "LIDL", "-20.00", "980.00"],
        ["02.02.2025", "PAY", "100.00", "1080.00"],
    ]
    out = parse_table_rows(rows)
    assert len(out) == 2
    assert out[0]["amount"] == pytest.approx(-20.0)
    assert out[1]["amount"] == pytest.approx(100.0)


def test_parse_table_rows_debit_negative_credit_positive():
    rows = [
        ["Datum", "Opis", "Duguje", "Potražuje", "Saldo"],
        ["01.02.2025", "LIDL", "45.00", "", "955.00"],
        ["02.02.2025", "SALARY", "", "1200.00", "2155.00"],
    ]
    out = parse_table_rows(rows)
    assert len(out) == 2
    assert out[0]["amount"] == pytest.approx(-45.0)
    assert out[1]["amount"] == pytest.approx(1200.0)
    assert out[1]["description"] == "SALARY"


def test_parse_table_rows_headerless_balance_heuristic():
    rows = [
        ["01.02.2025", "LIDL", "-20.00", "980.00"],
        ["02.02.2025", "ATM", "-50.00", "930.00"],
        ["03.02.2025", "SHOP", "-30.00", "900.00"],
        ["04.02.2025", "CAFE", "-10.00", "890.00"],
    ]
    out = parse_table_rows(rows)
    assert [r["amount"] for r in out] == pytest.approx([-20.0, -50.0, -30.0, -10.0])
    assert out[0]["description"] == "LIDL"


def test_parse_table_rows_filters_zero_and_huge_amounts():
    rows = [
        ["01.02.2025", "ZERO", "0.00"],
        ["02.02.2025", "HUGE", "2000000.00"],
        ["03.02.2025", "OK", "-50.00"],
    ]
    out = parse_table_rows(rows)
    assert [r["amount"] for r in out] == pytest.approx([-50.0])


# --- Text fallback -----------------------------------------------------------

def test_parse_text_lines_trailing_balance_uses_first_amount():
    rows = parse_text_lines("01.02.2025 LIDL -20.00 980.00")
    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(-20.0)
    assert rows[0]["description"] == "LIDL"


def test_parse_text_lines_wrapped_description_continuation():
    text = "01.02.2025 LIDL -20.00 980.00\nPURCHASE GROCERIES\n"
    rows = parse_text_lines(text)
    assert len(rows) == 1
    assert rows[0]["description"] == "LIDL PURCHASE GROCERIES"


def test_parse_text_lines_yyyy_mm_dd_date():
    rows = parse_text_lines("2025.01.02 TRANSFER 50.00")
    assert len(rows) == 1
    assert rows[0]["date"] == date(2025, 1, 2)


def test_parse_text_lines_skips_noise_lines():
    text = "01.02.2025 LIDL -20.00\nClosing balance\n"
    rows = parse_text_lines(text)
    assert len(rows) == 1
    assert rows[0]["description"] == "LIDL"


# --- Borderless table fallback -----------------------------------------------

def test_extract_transactions_from_pdf_borderless_table_fallback(monkeypatch):
    class FakePage:
        def __init__(self):
            self.calls = []

        def extract_tables(self, settings=None):
            self.calls.append(settings)
            if settings and settings.get("vertical_strategy") == "text":
                return [[["05.04.2025", "SHOP", "-30.00"]]]
            return []

        def extract_text(self):
            return ""

    page = FakePage()

    class FakePdf:
        pages = [page]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pdf_import.pdfplumber, "open", lambda _: FakePdf())
    df = extract_transactions_from_pdf(b"fake")
    assert len(df) == 1
    assert df.iloc[0]["amount"] == pytest.approx(-30.0)
    assert page.calls[0]["vertical_strategy"] == "lines"
    assert page.calls[1]["vertical_strategy"] == "text"


# --- Q&A regression tests -----------------------------------------------------

def test_description_cell_with_number_is_not_an_amount():
    """Regression: 'PAYMENT REF 1234' in a description column used to be
    misread as amount 1234."""
    rows = [
        ["Date", "Description", "Amount"],
        ["01.02.2025", "PAYMENT REF 1234", "-20.00"],
    ]
    out = parse_table_rows(rows)
    assert len(out) == 1
    assert out[0]["amount"] == pytest.approx(-20.0)
    assert out[0]["description"] == "PAYMENT REF 1234"


def test_headerless_description_cell_with_number_is_not_an_amount():
    rows = [
        ["01.02.2025", "PAYMENT REF 1234", "-20.00"],
        ["02.02.2025", "SHOP", "-5.00"],
    ]
    out = parse_table_rows(rows)
    assert len(out) == 2
    assert out[0]["amount"] == pytest.approx(-20.0)
    assert out[1]["amount"] == pytest.approx(-5.0)


def test_iso_date_is_not_parsed_as_amount():
    assert _parse_amount_token("2025-01-02") is None
    rows = parse_text_lines("03.02.2025 TRANSFER 2025-01-02 -10.00")
    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(-10.0)


def test_two_digit_year_pivot():
    assert _parse_date_token("01.02.25") == date(2025, 2, 1)
    assert _parse_date_token("01.02.99") == date(1999, 2, 1)


def test_transaction_split_across_lines_is_joined():
    """Regression: 'date+description' on one line and the bare amount on the
    next used to drop the transaction entirely."""
    rows = parse_text_lines("01.02.2025 LIDL BEOGRAD\n-20.00\n02.02.2025 SHOP -5.00\n")
    assert len(rows) == 2
    assert rows[0]["date"] == date(2025, 2, 1)
    assert rows[0]["amount"] == pytest.approx(-20.0)
    assert "LIDL" in rows[0]["description"]
    assert rows[1]["amount"] == pytest.approx(-5.0)
