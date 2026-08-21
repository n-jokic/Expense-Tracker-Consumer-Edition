"""
Tests for bank statement parsing, categorisation, currency handling, and the
save path (bank_import.py): the EUR value must be recalculated from the EDITED
amount/currency (never a stale pre-editor amount_eur), NaN rows must be
rejected, rows duplicated within a single upload must be deduped, and
empty-but-present currency columns must normalise to "" (never NaN).
"""

from datetime import date
import math

import pandas as pd
import pytest

from bank_import import (
    detect_bank_format, normalize_bank_csv, categorize_expense,
    _clean_currency, _save_edited_row, _to_eur_amount, _to_numeric_locale,
)
from pdf_import import _parse_amount_token
from db import (
    init_db, create_user, delete_user_account, get_expenses,
    username_exists, get_user_by_username,
)
from auth import hash_password


# ── Format detection & normalization ──────────────────────────────────────────

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


# ── Categorisation ────────────────────────────────────────────────────────────

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


# ── Currency handling ─────────────────────────────────────────────────────────

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


# ── Save path (bank_import._save_edited_row) ──────────────────────────────────

TEST_USERNAME = "bankimport_test_user"
TEST_EMAIL    = "bankimport_test@example.com"

RATES = {"EUR": 1.0, "RSD": 117.0}


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Bank Import Tester")
    yield uid
    delete_user_account(uid)


def _row(**overrides):
    base = {
        "date": date(2025, 6, 3),
        "description": "Lidl",
        "amount": 1000.0,
        "currency": "RSD",
        "amount_eur": 999.0,  # stale pre-editor value on purpose
        "category": "Food & Dining",
        "subcategory": "Groceries",
    }
    base.update(overrides)
    return pd.Series(base)


def test_edited_amount_and_currency_drive_eur(test_user):
    """The saved EUR must come from the edited amount/currency — the
    pre-editor amount_eur (999.0) must be ignored."""
    assert _save_edited_row(test_user, _row(), RATES, set()) == "imported"
    df = get_expenses(test_user)
    assert len(df) == 1
    assert df.iloc[0]["amount_eur"] == pytest.approx(1000 / 117, abs=1e-4)
    assert df.iloc[0]["amount"] == 1000.0
    assert df.iloc[0]["currency"] == "RSD"


def test_duplicate_row_within_upload_skipped(test_user):
    keys = set()
    row = _row()
    assert _save_edited_row(test_user, row, RATES, keys) == "imported"
    assert _save_edited_row(test_user, row, RATES, keys) == "skipped"
    assert len(get_expenses(test_user)) == 1


def test_nan_amount_skipped(test_user):
    row = _row(amount=float("nan"))
    assert _save_edited_row(test_user, row, RATES, set()) == "skipped"
    assert get_expenses(test_user).empty


def test_empty_currency_treated_as_eur(test_user):
    for cur in (None, float("nan"), ""):
        status = _save_edited_row(test_user, _row(currency=cur, amount=12.5),
                                  RATES, set())
        assert status == "imported"
    df = get_expenses(test_user)
    assert len(df) == 3
    assert set(df["currency"]) == {"EUR"}
    assert set(df["amount_eur"]) == {12.5}


def test_unknown_currency_returns_nan_and_is_skipped(test_user):
    """An unknown non-empty currency must not convert 1:1 — it yields NaN so
    the save path rejects the row."""
    assert math.isnan(_to_eur_amount(50.0, "XYZ", RATES))
    assert _save_edited_row(test_user, _row(currency="XYZ", amount=50.0),
                            RATES, set()) == "skipped"
    assert get_expenses(test_user).empty


def test_to_eur_amount_nan_currency_treated_as_eur():
    """NaN/blank currency is treated as EUR, never stringified to 'NAN'."""
    for cur in (None, float("nan"), "", " ", "EUR", "eur"):
        assert _to_eur_amount(12.5, cur, RATES) == pytest.approx(12.5)


def test_to_eur_amount_converts_known_currency():
    assert _to_eur_amount(1170.0, "RSD", RATES) == pytest.approx(10.0)
    assert _to_eur_amount(50.0, "EUR", RATES) == pytest.approx(50.0)


def test_suggestion_telemetry_recorded(test_user):
    """Measurement-first ML: the import must record the suggestion source,
    confidence, model version, normalized merchant, and acceptance."""
    row = _row()
    row["_suggest_source"] = "classifier"
    row["_suggest_conf"] = 0.87
    row["_suggest_cat"] = "Food & Dining"  # matches final category
    assert _save_edited_row(test_user, row, RATES, set()) == "imported"
    saved = get_expenses(test_user).iloc[0]
    assert saved["suggest_source"] == "classifier"
    assert saved["suggest_confidence"] == pytest.approx(0.87)
    assert saved["suggest_model_version"] is not None
    assert saved["suggest_merchant"] == "lidl"
    assert saved["suggest_accepted"] == True  # noqa: E712 (numpy bool)


def test_corrected_suggestion_recorded_as_not_accepted(test_user):
    row = _row()
    row["_suggest_source"] = "keywords"
    row["_suggest_conf"] = None
    row["_suggest_cat"] = "Transport"  # user corrected it to Food & Dining
    assert _save_edited_row(test_user, row, RATES, set()) == "imported"
    saved = get_expenses(test_user).iloc[0]
    assert saved["suggest_source"] == "keywords"
    assert saved["suggest_accepted"] == False  # noqa: E712 (numpy bool)
    assert saved["suggest_model_version"] is None


# ── FIX 1: NaN description cleared in the editor must not persist as 'nan' ───────

def test_cleared_description_cell_skipped(test_user):
    """A description cleared in st.data_editor arrives as float NaN; that must
    be coerced to empty and the row skipped (not stored as literal 'nan')."""
    row = _row(description=float("nan"))
    assert _save_edited_row(test_user, row, RATES, set()) == "skipped"
    assert get_expenses(test_user).empty


def test_literal_nan_description_skipped(test_user):
    """The string 'nan'/'NaN' (case-insensitive) must also be treated as empty."""
    row = _row(description="NaN")
    assert _save_edited_row(test_user, row, RATES, set()) == "skipped"
    assert get_expenses(test_user).empty


# ── FIX 2: signed thousands separators must match pdf_import parity ─────────────

_PARITY_TOKENS = ["1.234", "-1.234", "1,234", "-1,234", "+1,234", "12.345.678"]


@pytest.mark.parametrize("tok", _PARITY_TOKENS)
def test_signed_thousands_parity_with_pdf_import(tok):
    """Every locale-aware amount token must parse identically through
    bank_import._to_numeric_locale and pdf_import._parse_amount_token."""
    series = pd.Series([tok])
    bank_val = float(_to_numeric_locale(series).iloc[0])
    pdf_val = _parse_amount_token(tok)
    assert pdf_val is not None
    assert bank_val == pytest.approx(pdf_val)


def test_signed_dot_thousands_pure_value():
    assert float(_to_numeric_locale(pd.Series(["-1.234"])).iloc[0]) == -1234.0
    assert float(_to_numeric_locale(pd.Series(["+1.234"])).iloc[0]) == 1234.0


def test_signed_comma_thousands_pure_value():
    assert float(_to_numeric_locale(pd.Series(["-1,234"])).iloc[0]) == -1234.0
    assert float(_to_numeric_locale(pd.Series(["1,234"])).iloc[0]) == 1234.0


def test_comma_decimal_unaffected_by_sign_fix():
    """'1,5' is a decimal (not thousands) and must stay 1.5; sign preserved."""
    assert float(_to_numeric_locale(pd.Series(["1,5"])).iloc[0]) == 1.5
    assert float(_to_numeric_locale(pd.Series(["-1,5"])).iloc[0]) == -1.5


# ── FIX 3: generic CSV picks the right amount column ─────────────────────────────

def test_generic_csv_finds_amount_by_alias():
    """A generic CSV with {Date, Payee, Value, Currency} must use the 'Value'
    column (not 'Currency') so amounts are parsed and rows are retained."""
    df = pd.DataFrame({
        "Date": ["2025-01-01", "2025-01-02"],
        "Payee": ["Lidl", "Shell"],
        "Value": ["-10.00", "-20.00"],
        "Currency": ["USD", "USD"],
    })
    out = normalize_bank_csv(df, "generic")
    assert list(out.columns) == ["date", "description", "amount", "currency"]
    assert len(out) == 2
    assert out.iloc[0]["amount"] == -10.00
    assert out.iloc[1]["amount"] == -20.00


def test_generic_csv_currency_not_selected_as_amount():
    """Regression guard: 'Currency' must never be mistaken for the amount
    column (which previously dropped every row via dropna on NaN amounts)."""
    df = pd.DataFrame({
        "Date": ["2025-01-01", "2025-01-02"],
        "Payee": ["Lidl", "Shell"],
        "Value": ["10.00", "20.00"],
        "Currency": ["EUR", "EUR"],
    })
    out = normalize_bank_csv(df, "generic")
    assert len(out) == 2
    assert out.iloc[0]["amount"] == 10.00


def test_generic_csv_falls_back_to_last_column_for_unknown_schema():
    """Truly unknown schemas still fall back to the last column (unchanged)."""
    df = pd.DataFrame({
        "Date": ["2025-01-01", "2025-01-02"],
        "Payee": ["Lidl", "Shell"],
        "Stuff": ["10.00", "20.00"],
    })
    out = normalize_bank_csv(df, "generic")
    assert len(out) == 2
    assert out.iloc[0]["amount"] == 10.00
