"""
Regression tests for editing existing entries (income, savings, loans, big
purchases): edits change ONLY the edited row (+ derived recomputations), never
other stored history.
"""

from datetime import date

import pytest

from db import (
    init_db, create_user, delete_user_account,
    add_income, get_income, update_income,
    add_savings, get_savings, update_savings,
    add_loan, get_loans, update_loan,
    add_big_purchase, get_big_purchases, update_big_purchase,
    username_exists, get_user_by_username,
)
from auth import hash_password

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
        "name": "Laptop 14\"", "price": 850.0, "price_eur": 850.0,
        "importance": 5,
    })
    row = get_big_purchases(test_user).iloc[0]
    assert row["name"] == 'Laptop 14"'
    assert row["price_eur"] == 850.0
    assert row["importance"] == 5
