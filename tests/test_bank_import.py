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


def test_categorize_known_keyword():
    assert categorize_expense("LIDL 1234 BERLIN") == ("Food & Dining", "Groceries")
    assert categorize_expense("Netflix.com") == ("Entertainment", "Streaming Services")
    assert categorize_expense("SHELL station") == ("Transport", "Fuel")


def test_categorize_unknown_falls_back_to_other():
    assert categorize_expense("XYZ unknown merchant") == ("Other", "Miscellaneous")
