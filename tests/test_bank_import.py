"""
Tests for bank statement parsing and categorisation (bank_import.py).
"""

import pandas as pd
import pytest

from bank_import import detect_bank_format, normalize_bank_csv, categorize_expense


def test_detect_revolut():
    df = pd.DataFrame({"Started Date": ["2025-01-01"], "Description": ["Lidl"], "Amount": [-10]})
    assert detect_bank_format(df) == "revolut"


def test_detect_n26():
    df = pd.DataFrame({"Date": ["2025-01-01"], "Payee": ["Lidl"], "Amount (EUR)": [-10]})
    assert detect_bank_format(df) == "n26"


def test_detect_wise():
    df = pd.DataFrame({"Date": ["2025-01-01"], "Description": ["x"],
                       "Source amount (after fees)": [-10], "Source currency": ["EUR"]})
    assert detect_bank_format(df) == "wise"


def test_detect_generic():
    df = pd.DataFrame({"A": [1], "B": [2], "C": [3]})
    assert detect_bank_format(df) == "generic"


def test_normalize_revolut():
    df = pd.DataFrame({
        "Started Date": ["2025-01-15"], "Description": ["Lidl shop"],
        "Amount": ["-23.50"], "Currency": ["EUR"],
    })
    out = normalize_bank_csv(df, "revolut")
    assert list(out.columns) == ["date", "description", "amount", "currency"]
    assert out.iloc[0]["amount"] == -23.50
    assert out.iloc[0]["currency"] == "EUR"


def test_normalize_generic_drops_invalid_rows():
    df = pd.DataFrame({
        "Date": ["2025-01-15", "not-a-date"],
        "Description": ["ok", "bad"],
        "Amount": ["-5", "-6"],
    })
    out = normalize_bank_csv(df, "generic")
    assert len(out) == 1


def test_normalize_generic_day_first_dates():
    """Regression: '05/02/2025' was silently read as May 2 (month-first);
    ambiguous dates must follow the PDF parser's day-first heuristic."""
    df = pd.DataFrame({
        "Date": ["05/02/2025", "13/02/2025"],
        "Description": ["a", "b"],
        "Amount": ["-5", "-6"],
    })
    out = normalize_bank_csv(df, "generic")
    assert str(out.iloc[0]["date"])[:10] == "2025-02-05"
    assert str(out.iloc[1]["date"])[:10] == "2025-02-13"


def test_normalize_wise_dash_dates_day_first():
    """Regression: Wise's DD-MM-YYYY dates were parsed month-first."""
    df = pd.DataFrame({
        "Date": ["05-02-2025"],
        "Description": ["LIDL"],
        "Source amount (after fees)": ["-5.00"],
        "Source currency": ["EUR"],
    })
    out = normalize_bank_csv(df, "wise")
    assert str(out.iloc[0]["date"])[:10] == "2025-02-05"


def test_normalize_generic_serbian_dot_thousands():
    """Regression: '1.234' was read as 1.234; pure 3-digit dot groups are
    thousands separators (Serbian) and must parse as 1234."""
    df = pd.DataFrame({
        "Date": ["2025-01-15"], "Description": ["a"],
        "Amount": ["1.234"], "Currency": ["RSD"],
    })
    out = normalize_bank_csv(df, "generic")
    assert out.iloc[0]["amount"] == 1234.0


def test_categorize_known_keyword():
    assert categorize_expense("LIDL 1234 BERLIN") == ("Groceries", "Groceries")
    assert categorize_expense("Netflix.com") == ("Entertainment", "Streaming Services")
    assert categorize_expense("SHELL station") == ("Transport", "Fuel")
    assert categorize_expense("mcdonald's") == ("Dining Out", "Restaurants & Takeaway")
    assert categorize_expense("Adobe subscription") == ("Subscriptions & Software", "Subscriptions & Software")
    assert categorize_expense("tax payment") == ("Fees & Taxes", "Taxes & Fees")
    assert categorize_expense("zara") == ("Shopping", "Clothing & Accessories")
    assert categorize_expense("rent") == ("Housing & Utilities", "Rent / Mortgage")


def test_categorize_unknown_falls_back_to_other():
    assert categorize_expense("XYZ unknown merchant") == ("Other", "Miscellaneous")
