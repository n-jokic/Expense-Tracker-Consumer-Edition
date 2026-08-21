"""
Tests for savings balance recomputation (db._recompute_savings_balances),
savings-goal editing, goal-wide updates, and term-deposit accounts
(saved_accounts table + sync support).
"""

from datetime import date

import pandas as pd
import pytest

from db import (
    _recompute_savings_balances,
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, add_savings, get_savings,
    update_savings_goal, rename_savings_goal, soft_delete_savings_goal,
    add_savings_account, get_savings_accounts, update_savings_account,
    soft_delete_savings_account, restore_savings_account,
)
from auth import hash_password
from sync_core import apply_changes, snapshot


# ── Balance recomputation (db._recompute_savings_balances) ────────────────────

def _savings_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return _recompute_savings_balances(df)


def test_first_deposit_is_the_balance():
    df = _savings_df([{
        "goal_name": "Emergency Fund", "date": "2025-01-05",
        "deposited_eur": 100.0, "interest_rate": 12.0, "balance_eur": 999.0,
    }])
    assert df.iloc[0]["balance_eur"] == 100.0


def test_pending_accrual_to_asof():
    """FIN-04 semantic change: balances are POSTED principal; unrealized
    accrual is reported separately as pending_interest_eur (daily ACT/365,
    final segment including `asof`)."""
    df = pd.DataFrame([{
        "goal_name": "G", "date": "2025-01-01",
        "deposited_eur": 100.0, "interest_rate": 12.0, "balance_eur": 0.0,
    }])
    df["date"] = pd.to_datetime(df["date"])
    out = _recompute_savings_balances(df, asof=date(2025, 4, 1))
    assert out.iloc[0]["balance_eur"] == pytest.approx(100.0)
    # Jan 1 .. Apr 1 inclusive = 91 earning days
    assert out.iloc[0]["pending_interest_eur"] == pytest.approx(
        100.0 * 0.12 / 365 * 91, abs=0.01)
    # without asof no accrual window is opened
    out0 = _recompute_savings_balances(df)
    assert out0.iloc[0]["balance_eur"] == 100.0
    assert out0.iloc[0]["pending_interest_eur"] == 0.0


def test_balance_is_posted_principal_and_accrual_accumulates():
    # FIN-04 semantic change: balance = cumulative deposits (200), while the
    # accrual accumulates across BOTH segments (pre- and post-second-deposit).
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(200.0)
    df2 = pd.DataFrame([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    df2["date"] = pd.to_datetime(df2["date"])
    out = _recompute_savings_balances(df2, asof=date(2025, 3, 15))
    expected = (100.0 * 0.12 / 365 * 59      # Jan 1 -> Mar 1 (exclusive)
                + 200.0 * 0.12 / 365 * 15)   # Mar 1 .. Mar 15 (inclusive)
    assert out.iloc[0]["pending_interest_eur"] == pytest.approx(expected, abs=0.01)


def test_two_deposits_in_same_month_get_no_interest():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-01-20", "deposited_eur": 50.0,  "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 150.0


def test_accrual_uses_the_depositing_rows_rate():
    # FIN-04 semantic change: the segment before Feb earns the January row's
    # rate (12%); from Feb 1 the 0% row takes over the rate configuration.
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(200.0)
    df2 = pd.DataFrame([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    df2["date"] = pd.to_datetime(df2["date"])
    out = _recompute_savings_balances(df2, asof=date(2025, 2, 10))
    expected = 100.0 * 0.12 / 365 * 31     # only January earns 12%
    assert out.iloc[0]["pending_interest_eur"] == pytest.approx(expected, abs=0.01)


def test_goals_are_independent():
    df = _savings_df([
        {"goal_name": "A", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "B", "date": "2025-02-01", "deposited_eur": 500.0, "interest_rate": 0.0},
        {"goal_name": "A", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    a = df[df["goal_name"] == "A"].sort_values("date")
    b = df[df["goal_name"] == "B"]
    # FIN-04 semantic change: posted principal, no compounding between rows
    assert a.iloc[-1]["balance_eur"] == pytest.approx(200.0)
    assert b.iloc[0]["balance_eur"] == 500.0


def test_recompute_handles_missing_dates_gracefully():
    df = _savings_df([
        {"goal_name": "G", "date": None, "deposited_eur": 50.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 50.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 100.0


def test_withdrawal_reduces_posted_balance_rate_carried():
    # FIN-04 semantic change: the withdrawal reduces POSTED principal; the
    # earning rate carries across withdrawals (established by deposit rows).
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -30.0, "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(70.0)
    df2 = pd.DataFrame([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -30.0, "interest_rate": 12.0},
    ])
    df2["date"] = pd.to_datetime(df2["date"])
    out = _recompute_savings_balances(df2, asof=date(2025, 2, 28))
    expected = (100.0 * 0.12 / 365 * 31     # Jan: full balance earns
                + 70.0 * 0.12 / 365 * 28)   # Feb (incl. asof): reduced balance
    assert out.iloc[0]["pending_interest_eur"] == pytest.approx(expected, abs=0.01)


def test_withdrawal_below_zero_is_preserved_inspectable():
    """FIN-01 semantic change: the read chain no longer clamps at 0 — a legacy
    overdrawn goal stays visible as a negative balance (new overdrafts are
    prevented by service validation, not by masking)."""
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -250.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(-150.0)


def test_negative_first_deposit_preserved_inspectable():
    """FIN-01 semantic change: a negative opening entry stays negative."""
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": -50.0, "interest_rate": 0.0},
    ])
    assert df.iloc[0]["balance_eur"] == pytest.approx(-50.0)


# ── Goal editing & term-deposit accounts (db.py + sync) ───────────────────────

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
    assert df.iloc[-1]["balance_eur"] == 1000.0       # 0% rate -> no accrual

    n = update_savings_goal(test_user, "Car", {"target_eur": 5000.0,
                                               "interest_rate": 12.0})
    assert n == 2
    df = get_savings(test_user)
    assert set(df["target_eur"]) == {5000.0}
    assert set(df["interest_rate"]) == {12.0}
    # FIN-04 semantic change: balance stays POSTED principal (1000); the new
    # rate flows into the pending accrual — every day from the first deposit
    # to today (inclusive) earns 1000 * 12% / 365.
    total_days = (date.today() - date(2025, 1, 1)).days + 1
    expected_pending = 1000.0 * 0.12 / 365 * max(total_days, 1)
    assert df.iloc[-1]["balance_eur"] == pytest.approx(1000.0)
    assert df.iloc[-1]["pending_interest_eur"] == pytest.approx(
        expected_pending, abs=0.01)


def test_single_deposit_goal_accrues_to_today(test_user):
    """FIN-04 semantic change: a single-deposit goal shows its accrued
    interest as pending_interest_eur (balance stays posted principal)."""
    _entry(test_user, date(2024, 1, 1), "Solo", 1000.0, rate=12.0)
    df = get_savings(test_user)
    assert df.iloc[-1]["balance_eur"] == pytest.approx(1000.0)
    total_days = (date.today() - date(2024, 1, 1)).days + 1
    assert df.iloc[-1]["pending_interest_eur"] == pytest.approx(
        1000.0 * 0.12 / 365 * max(total_days, 1), abs=0.01)


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


def test_rename_savings_goal_rejects_existing_name(test_user):
    """Regression: renaming a goal into another goal's name used to merge the
    two histories silently."""
    _entry(test_user, date(2025, 1, 1), "A", 500.0)
    _entry(test_user, date(2025, 1, 1), "B", 500.0)
    assert rename_savings_goal(test_user, "A", "B") == 0
    assert set(get_savings(test_user)["goal_name"]) == {"A", "B"}
    # case-insensitive clash is rejected too
    assert rename_savings_goal(test_user, "A", "b") == 0


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


def test_sync_create_without_goal_name_fails_cleanly(test_user):
    """Regression: a savings_accounts create without goal_name used to raise
    an IntegrityError that crashed the whole sync call after partial apply."""
    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-no-goal",
        "fields": {"name": "CD", "amount": 100.0, "amount_eur": 100.0},
    }])
    assert res["applied"][0]["status"] == "failed"
    assert get_savings_accounts(test_user).empty


def test_sync_rejects_blank_goal_name_and_bad_status(test_user):
    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-blank",
        "fields": {"goal_name": "   "},
    }])
    assert res["failed"] and "goal_name must not be blank" in res["failed"][0]["error"]

    res = apply_changes(test_user, [{
        "table": "savings_accounts", "id": "acc-status",
        "fields": {"goal_name": "House", "status": "suspended"},
    }])
    assert res["failed"] and "unknown status" in res["failed"][0]["error"]


def test_keep_device_value_supports_savings_accounts(test_user):
    """Regression: db._SYNC_MODELS must include savings_accounts so the
    Settings → Sync 'keep device value' resolution can apply it."""
    from db import apply_record_fields
    acc_id = add_savings_account(test_user, {
        "goal_name": "House", "name": "CD", "amount": 100.0, "currency": "EUR",
        "amount_eur": 100.0, "annual_rate": 3.0,
        "start_date": date(2025, 1, 1), "maturity_date": date(2026, 1, 1),
        "status": "active", "notes": "",
    })
    assert apply_record_fields(test_user, "savings_accounts", acc_id,
                               {"annual_rate": 9.9})
    accs = get_savings_accounts(test_user)
    assert accs.iloc[0]["annual_rate"] == 9.9