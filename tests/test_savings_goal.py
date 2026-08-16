"""
Tests for savings-goal editing, goal-wide updates, and term-deposit accounts
(saved_accounts table + sync support).
"""

from datetime import date

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, add_savings, get_savings,
    update_savings_goal, rename_savings_goal, soft_delete_savings_goal,
    add_savings_account, get_savings_accounts, update_savings_account,
    soft_delete_savings_account, restore_savings_account,
)
from auth import hash_password
from sync_core import apply_changes, snapshot

TEST_USERNAME = "savings_goal_user"
TEST_EMAIL    = "savings_goal@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "SG Tester")
    yield uid
    delete_user_account(uid)


def _entry(uid, d, goal, dep_eur, rate=0.0, target=0.0):
    add_savings(uid, {
        "date": d, "goal_name": goal, "target_eur": target,
        "deposited": dep_eur, "currency": "EUR", "deposited_eur": dep_eur,
        "interest_rate": rate, "balance_eur": 0.0, "notes": "",
    })


def test_goal_wide_update_applies_to_all_entries_and_recomputes(test_user):
    _entry(test_user, date(2025, 1, 1), "Car", 1000.0)
    _entry(test_user, date(2025, 3, 1), "Car", 0.0)   # 2 months later, no deposit

    df = get_savings(test_user)
    assert df.iloc[-1]["balance_eur"] == 1000.0       # 0% rate -> flat

    n = update_savings_goal(test_user, "Car", {"target_eur": 5000.0,
                                               "interest_rate": 12.0})
    assert n == 2
    df = get_savings(test_user)
    assert set(df["target_eur"]) == {5000.0}
    assert set(df["interest_rate"]) == {12.0}
    # balance chain recomputed with the new rate: 1000 * 1.01^2
    assert df.iloc[-1]["balance_eur"] == pytest.approx(1020.10, abs=0.01)


def test_rename_savings_goal_renames_entries_and_accounts(test_user):
    _entry(test_user, date(2025, 1, 1), "Old name", 500.0)
    add_savings_account(test_user, {
        "goal_name": "Old name", "name": "CD", "amount": 100.0, "currency": "EUR",
        "amount_eur": 100.0, "annual_rate": 4.0,
        "start_date": date(2025, 1, 1), "maturity_date": date(2026, 1, 1),
        "status": "active", "notes": "",
    })

    n = rename_savings_goal(test_user, "Old name", "New name")
    assert n == 2
    df = get_savings(test_user)
    assert df.iloc[0]["goal_name"] == "New name"
    accs = get_savings_accounts(test_user)
    assert accs.iloc[0]["goal_name"] == "New name"


def test_soft_delete_savings_goal_trashes_entries_and_accounts(test_user):
    _entry(test_user, date(2025, 1, 1), "Doomed", 500.0)
    add_savings_account(test_user, {
        "goal_name": "Doomed", "name": "CD", "amount": 100.0, "currency": "EUR",
        "amount_eur": 100.0, "annual_rate": 4.0,
        "start_date": date(2025, 1, 1), "maturity_date": date(2026, 1, 1),
        "status": "active", "notes": "",
    })

    n = soft_delete_savings_goal(test_user, "Doomed")
    assert n == 1
    assert get_savings(test_user).empty
    assert get_savings_accounts(test_user).empty
    deleted = get_savings(test_user, include_deleted=True)
    assert len(deleted) == 1 and deleted.iloc[0]["is_deleted"] == True
    deleted_accs = get_savings_accounts(test_user, include_deleted=True)
    assert len(deleted_accs) == 1 and deleted_accs.iloc[0]["is_deleted"] == True


def test_savings_account_crud_update_and_restore(test_user):
    acc_id = add_savings_account(test_user, {
        "goal_name": "House", "name": "6m CD", "amount": 2000.0, "currency": "EUR",
        "amount_eur": 2000.0, "annual_rate": 3.0,
        "start_date": date(2025, 1, 1), "maturity_date": date(2025, 7, 1),
        "status": "active", "notes": "",
    })
    accs = get_savings_accounts(test_user)
    assert len(accs) == 1
    assert accs.iloc[0]["amount_eur"] == 2000.0

    assert update_savings_account(test_user, acc_id, {"annual_rate": 3.5,
                                                      "status": "closed"})
    accs = get_savings_accounts(test_user)
    assert accs.iloc[0]["annual_rate"] == 3.5
    assert accs.iloc[0]["status"] == "closed"

    assert soft_delete_savings_account(test_user, acc_id)
    assert get_savings_accounts(test_user).empty
    assert restore_savings_account(test_user, acc_id)
    assert len(get_savings_accounts(test_user)) == 1


def test_sync_supports_savings_accounts(test_user):
    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-sync-1",
        "fields": {
            "goal_name": "House", "name": "12m CD", "amount": 500.0,
            "currency": "EUR", "amount_eur": 500.0, "annual_rate": 4.0,
            "start_date": "2025-06-01", "maturity_date": "2026-06-01",
            "status": "active", "notes": "",
        },
    }])
    assert res["applied"][0]["status"] == "created"

    snap, truncated = snapshot(test_user)
    assert "savings_accounts" in snap
    rows = snap["savings_accounts"]
    assert len(rows) == 1
    assert rows[0]["goal_name"] == "House"
    assert rows[0]["maturity_date"] == "2026-06-01"

    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-sync-1",
        "fields": {"annual_rate": 4.25},
    }])
    assert res["applied"][0]["status"] == "updated"
    accs = get_savings_accounts(test_user)
    assert accs.iloc[0]["annual_rate"] == 4.25

    # unknown fields are rejected, not dropped
    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-sync-1",
        "fields": {"hacker_field": 1},
    }])
    assert res["failed"] and "unknown field" in res["failed"][0]["error"]
