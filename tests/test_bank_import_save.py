"""
Regression tests for the bank-import save path (bank_import._save_edited_row):
the EUR value must be recalculated from the EDITED amount/currency (never the
stale pre-editor amount_eur), NaN rows must be rejected, and rows duplicated
within a single upload must be deduped.
"""

from datetime import date

import pandas as pd
import pytest

from bank_import import _save_edited_row, _to_eur_amount
from db import (
    init_db, create_user, delete_user_account, get_expenses,
    username_exists, get_user_by_username,
)
from auth import hash_password

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


def test_unknown_currency_assumes_1_to_1(test_user):
    assert _to_eur_amount(50.0, "XYZ", RATES) == 50.0
    assert _save_edited_row(test_user, _row(currency="XYZ", amount=50.0),
                            RATES, set()) == "imported"
    assert get_expenses(test_user).iloc[0]["amount_eur"] == 50.0


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
