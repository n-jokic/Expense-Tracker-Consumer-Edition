"""
Tests for bank-import currency handling: CSV currency columns that are
present-but-empty must normalise to "" (so the user's "Statement currency"
selection fills them — never NaN, never a silently hardcoded EUR), and
existing per-row currencies must be preserved/uppercased.
"""

import pandas as pd

from bank_import import normalize_bank_csv, _clean_currency


def test_revolut_empty_currency_column_yields_blank():
    df = pd.DataFrame({
        "Started Date": ["2025-01-15"],
        "Description": ["Lidl shop"],
        "Amount": ["-23.50"],
        "Currency": [None],
    })
    out = normalize_bank_csv(df, "revolut")
    assert list(out.columns) == ["date", "description", "amount", "currency"]
    assert out.iloc[0]["currency"] == ""
    assert not out["currency"].isna().any()


def test_wise_empty_source_currency_column_yields_blank():
    df = pd.DataFrame({
        "Date": ["2025-01-15"],
        "Description": ["Transfer"],
        "Source amount (after fees)": ["-100.00"],
        "Source currency": [None],
    })
    out = normalize_bank_csv(df, "wise")
    assert out.iloc[0]["currency"] == ""


def test_missing_currency_column_yields_blank():
    """A statement with no currency column at all must leave the currency
    blank so the bulk Statement-currency selector decides it."""
    df = pd.DataFrame({
        "Date": ["2025-01-15"],
        "Description": ["Transfer"],
        "Amount": ["-100.00"],
    })
    out = normalize_bank_csv(df, "generic")
    assert out.iloc[0]["currency"] == ""


def test_revolut_currency_values_uppercased_and_preserved():
    df = pd.DataFrame({
        "Started Date": ["2025-01-15", "2025-01-16"],
        "Description": ["Lidl", "Shell"],
        "Amount": ["-23.50", "-50"],
        "Currency": ["eur", "USD"],
    })
    out = normalize_bank_csv(df, "revolut")
    assert list(out["currency"]) == ["EUR", "USD"]


def test_clean_currency_never_produces_nan():
    assert list(_clean_currency(pd.Series([None, "", " rSd "])) ) == ["", "", "RSD"]
