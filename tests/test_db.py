"""
Regression tests for the data layer (db.py) — exercises the real SQLite
database with a throwaway user, including the detached-instance read path
that crashed with DetachedInstanceError before expire_on_commit=False.
"""

from datetime import date

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    add_expense, get_expenses, soft_delete_expense, restore_expense,
    get_audit_log, add_income, get_income,
)
from auth import hash_password

TEST_USERNAME = "db_test_user"
TEST_EMAIL    = "db_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        from db import get_user_by_username
        uid = get_user_by_username(TEST_USERNAME)["id"]
        delete_user_account(uid)
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "DB Tester")
    yield uid
    delete_user_account(uid)


def test_expense_roundtrip_after_session_close(test_user):
    """Rows must stay readable after the session is closed (regression)."""
    exp_id = add_expense(test_user, {
        "date": date(2025, 5, 1), "category": "Food & Dining",
        "subcategory": "Groceries", "description": "Lidl",
        "amount": 12.5, "currency": "EUR", "amount_eur": 12.5,
        "recurring": False, "notes": "",
    })
    df = get_expenses(test_user)
    assert len(df) == 1
    assert df.iloc[0]["id"] == exp_id
    assert df.iloc[0]["amount_eur"] == 12.5


def test_audit_log_reads_after_commits(test_user):
    """Audit rows (always non-empty after registration) must be readable."""
    df = get_audit_log(test_user, limit=10)
    assert not df.empty
    assert df.iloc[0]["action"] == "REGISTER"


def test_soft_delete_and_restore(test_user):
    add_expense(test_user, {
        "date": date(2025, 5, 2), "category": "Transport",
        "subcategory": "Fuel", "description": "Petrol",
        "amount": 40.0, "currency": "EUR", "amount_eur": 40.0,
        "recurring": False, "notes": "",
    })
    exp_id = get_expenses(test_user).iloc[0]["id"]
    assert soft_delete_expense(test_user, exp_id)
    assert get_expenses(test_user).empty
    assert len(get_expenses(test_user, include_deleted=True)) == 1
    assert restore_expense(test_user, exp_id)
    assert len(get_expenses(test_user)) == 1


def test_income_roundtrip(test_user):
    add_income(test_user, {
        "date": date(2025, 5, 1), "source": "Primary Salary",
        "budgeted": 1000.0, "actual": 1050.0, "currency": "EUR",
        "budgeted_eur": 1000.0, "actual_eur": 1050.0, "notes": "",
    })
    df = get_income(test_user)
    assert len(df) == 1
    assert df.iloc[0]["actual_eur"] == 1050.0
