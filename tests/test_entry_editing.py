"""
Regression tests for editing existing entries (income, savings, loans, big
purchases): edits change ONLY the edited row (+ derived recomputations), never
other stored history.

Extended with non-finite amount poisoning regression tests for the shared
expense ledger: services.commands.bulk_update_expenses must reject inf/NaN/
oversized numerics, and the log_expense.py batch-editor guard (_valid_amount)
must reject the same before they reach the service layer.
"""
import importlib
import math
import os
import sys
from datetime import date

import pandas as pd
import pytest

from db import (
    init_db, create_user, delete_user_account,
    add_income, get_income, update_income,
    add_savings, get_savings, update_savings,
    add_loan, get_loans, update_loan,
    add_big_purchase, get_big_purchases, update_big_purchase,
    add_expense, get_expenses,
    username_exists, get_user_by_username,
)
from auth import hash_password
from utils import MAX_AMOUNT

TEST_USERNAME = "edit_entry_user"
TEST_EMAIL    = "edit_entry@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Edit Tester")
    yield uid
    delete_user_account(uid)


def _add_expense(test_user, amount=10.0, currency="EUR", amount_eur=None,
                 description="test exp"):
    if amount_eur is None:
        amount_eur = amount
    return add_expense(test_user, {
        "date": date(2025, 6, 5), "category": "Groceries",
        "subcategory": "Groceries", "description": description,
        "amount": amount, "currency": currency, "amount_eur": amount_eur,
    })


# ── Existing tests (unchanged) ─────────────────────────────────────────────────


def test_edit_income_updates_only_that_row(test_user):
    i1 = add_income(test_user, {"date": date(2025, 5, 1), "source": "Primary Salary",
                                "budgeted": 1000.0, "actual": 1000.0, "currency": "EUR",
                                "budgeted_eur": 1000.0, "actual_eur": 1000.0, "notes": ""})
    i2 = add_income(test_user, {"date": date(2025, 6, 1), "source": "Bonus",
                                "budgeted": 0.0, "actual": 200.0, "currency": "EUR",
                                "budgeted_eur": 0.0, "actual_eur": 200.0, "notes": ""})

    assert update_income(test_user, i1, {
        "source": "Primary Salary (edited)", "actual": 1100.0,
        "actual_eur": 1100.0, "notes": "raise",
    })
    df = get_income(test_user).set_index("id")
    assert df.loc[i1, "source"] == "Primary Salary (edited)"
    assert df.loc[i1, "actual_eur"] == 1100.0
    assert df.loc[i2, "source"] == "Bonus"      # other row untouched
    assert df.loc[i2, "actual_eur"] == 200.0


def test_edit_savings_recomputes_chain_forward_only(test_user):
    s1 = add_savings(test_user, {"date": date(2025, 1, 1), "goal_name": "Emergency Fund",
                                 "target_eur": 1000.0, "deposited": 100.0, "currency": "EUR",
                                 "deposited_eur": 100.0, "interest_rate": 0.0, "notes": ""})
    s2 = add_savings(test_user, {"date": date(2025, 2, 1), "goal_name": "Emergency Fund",
                                 "target_eur": 1000.0, "deposited": 100.0, "currency": "EUR",
                                 "deposited_eur": 100.0, "interest_rate": 0.0, "notes": ""})

    assert update_savings(test_user, s1, {"deposited": 200.0, "deposited_eur": 200.0})
    df = get_savings(test_user).set_index("id")
    assert df.loc[s1, "balance_eur"] == 200.0     # edited entry recomputed
    assert df.loc[s2, "balance_eur"] == 300.0     # chain forward recomputed
    assert df.loc[s2, "deposited_eur"] == 100.0   # stored row itself untouched


def test_edit_loan_terms_do_not_touch_payments(test_user):
    loan_id = add_loan(test_user, {
        "name": "Car", "principal": 12000.0, "currency": "EUR",
        "principal_eur": 12000.0, "annual_rate": 5.0, "start_date": date(2025, 1, 1),
        "term_months": 36, "payment_day": 1, "status": "active", "notes": "",
    })
    assert update_loan(test_user, loan_id, {
        "annual_rate": 3.5, "term_months": 48, "name": "Car (refinanced)",
    })
    row = get_loans(test_user).iloc[0]
    assert row["annual_rate"] == 3.5
    assert row["term_months"] == 48
    assert row["name"] == "Car (refinanced)"
    assert row["principal_eur"] == 12000.0  # untouched


def test_edit_big_purchase_updates_fields(test_user):
    bp_id = add_big_purchase(test_user, {
        "name": "Laptop", "category": "Other", "price": 900.0, "currency": "EUR",
        "price_eur": 900.0, "usage_hours": 40.0, "importance": 4,
        "status": "wishlist", "notes": "",
    })
    assert update_big_purchase(test_user, bp_id, {
        "name": 'Laptop 14"', "price": 850.0, "price_eur": 850.0,
        "importance": 5,
    })
    row = get_big_purchases(test_user).iloc[0]
    assert row["name"] == 'Laptop 14"'
    assert row["price_eur"] == 850.0
    assert row["importance"] == 5


# ── Non-finite amount poisoning regression ────────────────────────────────────


def test_bulk_update_expenses_applies_valid_amounts(test_user):
    """Valid finite amounts within range are persisted and counted as affected."""
    from services.commands import bulk_update_expenses
    eid = _add_expense(test_user, amount=10.0, amount_eur=10.0)

    res = bulk_update_expenses(test_user, [
        {"id": eid, "fields": {"amount": 42.5, "amount_eur": 42.5}},
    ])
    assert res.changed
    assert eid in res.affected_ids
    assert res.rejected == 0
    df = get_expenses(test_user).set_index("id")
    assert df.loc[eid, "amount"] == 42.5
    assert df.loc[eid, "amount_eur"] == 42.5


@pytest.mark.parametrize("bad_amount", [
    float("inf"),
    float("-inf"),
    float("nan"),
    MAX_AMOUNT + 1,   # oversized
    1e999,            # parses to inf
])
def test_bulk_update_expenses_rejects_nonfinite_amounts(test_user, bad_amount):
    """inf / -inf / nan / oversized amounts are rejected, not persisted."""
    from services.commands import bulk_update_expenses
    eid = _add_expense(test_user, amount=10.0, amount_eur=10.0)
    original = 10.0

    res = bulk_update_expenses(test_user, [
        {"id": eid, "fields": {"amount": bad_amount, "amount_eur": 42.0}},
    ])
    # The bad amount field is rejected → not committed to the DB.
    assert res.rejected >= 1
    df = get_expenses(test_user).set_index("id")
    # amount was bad so it must remain unchanged from the original;
    # amount_eur was valid so it should be applied.
    assert df.loc[eid, "amount"] == original
    assert df.loc[eid, "amount_eur"] == 42.0


def test_bulk_update_expenses_rejects_nonfinite_amount_eur(test_user):
    """amount_eur (also a REAL column) is guarded the same way."""
    from services.commands import bulk_update_expenses
    eid = _add_expense(test_user, amount=10.0, amount_eur=10.0)

    res = bulk_update_expenses(test_user, [
        {"id": eid, "fields": {"amount_eur": float("inf")}},
    ])
    assert res.rejected >= 1
    df = get_expenses(test_user).set_index("id")
    assert df.loc[eid, "amount_eur"] == 10.0  # unchanged, not poisoned


def test_bulk_update_expenses_mixed_valid_and_invalid(test_user):
    """A row whose amount is bad but description is valid: description saved,
    amount rejected, both counted correctly."""
    from services.commands import bulk_update_expenses
    eid = _add_expense(test_user, amount=10.0, amount_eur=10.0,
                       description="old desc")

    res = bulk_update_expenses(test_user, [
        {"id": eid, "fields": {"amount": float("inf"), "description": "new desc"}},
    ])
    assert res.rejected == 1  # only the bad amount field rejected
    df = get_expenses(test_user).set_index("id")
    assert df.loc[eid, "description"] == "new desc"   # valid co-edit applied
    assert df.loc[eid, "amount"] == 10.0               # bad amount NOT persisted


def test_bulk_update_expenses_no_poisoning_in_sum(test_user):
    """End-to-end: after attempting an inf update, aggregate sums stay finite."""
    from services.commands import bulk_update_expenses
    e1 = _add_expense(test_user, amount=10.0, amount_eur=10.0, description="e1")
    e2 = _add_expense(test_user, amount=20.0, amount_eur=20.0, description="e2")

    bulk_update_expenses(test_user, [
        {"id": e1, "fields": {"amount": float("inf"), "amount_eur": float("inf")}},
        {"id": e2, "fields": {"amount": 99.0, "amount_eur": 99.0}},
    ])
    df = get_expenses(test_user)
    total = df["amount_eur"].sum()
    assert math.isfinite(total)
    assert total == 10.0 + 99.0  # e1 unchanged (10.0), e2 updated to 99.0

    total_amount = df["amount"].sum()
    assert math.isfinite(total_amount)


# ── Batch-editor amount guard (canonical: domain.validation.is_valid_amount) ──
#
# The guard used to live on the page and was tested by importing
# app_pages/log_expense.py in bare mode. A bare page import executes the
# whole Streamlit script WITHOUT a ScriptRunContext; its top-level
# st.form("exp_form") then leaks an open form context that made the NEXT
# AppTest run fail with "Forms cannot be nested" (the recorded order-
# dependent ocr_review flake). The logic now lives in domain/validation.py,
# which is Streamlit-free, so these tests import it directly.


@pytest.fixture()
def valid_amount():
    from domain.validation import is_valid_amount
    return is_valid_amount


@pytest.mark.parametrize("bad_amount", [
    float("inf"),
    float("-inf"),
    float("nan"),
    MAX_AMOUNT + 1,   # oversized
    0.0,              # zero — not positive
    -5.0,             # negative
])
def test_valid_amount_guard_rejects_bad(valid_amount, bad_amount):
    assert valid_amount(bad_amount) is False


def test_valid_amount_guard_accepts_good(valid_amount):
    assert valid_amount(0.01) is True
    assert valid_amount(MAX_AMOUNT) is True
    assert valid_amount(42.5) is True