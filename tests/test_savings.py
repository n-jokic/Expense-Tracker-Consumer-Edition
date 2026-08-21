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


def test_tail_accrual_to_asof():
    """With an explicit `asof`, the last entry compounds forward from its
    date to `asof` (single-deposit goals earn interest too)."""
    df = pd.DataFrame([{
        "goal_name": "G", "date": "2025-01-01",
        "deposited_eur": 100.0, "interest_rate": 12.0, "balance_eur": 0.0,
    }])
    df["date"] = pd.to_datetime(df["date"])
    out = _recompute_savings_balances(df, asof=date(2025, 4, 1))
    assert out.iloc[0]["balance_eur"] == pytest.approx(100.0 * 1.01 ** 3, abs=0.01)
    # without asof the chain is unchanged (pure between-deposit semantics)
    out0 = _recompute_savings_balances(df)
    assert out0.iloc[0]["balance_eur"] == 100.0


def test_interest_compounds_over_elapsed_months():
    # 100 deposit on Jan 1 at 12% p.a. (1%/month), +100 on Mar 1
    # -> 100*1.01^2 + 100 = 202.01
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(202.01, abs=1e-3)


def test_two_deposits_in_same_month_get_no_interest():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-01-20", "deposited_eur": 50.0,  "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 150.0


def test_interest_uses_the_earlier_deposits_rate():
    # Growth between Jan and Feb uses the January rate (12%), not February's (0%).
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(201.0, abs=1e-3)


def test_goals_are_independent():
    df = _savings_df([
        {"goal_name": "A", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "B", "date": "2025-02-01", "deposited_eur": 500.0, "interest_rate": 0.0},
        {"goal_name": "A", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    a = df[df["goal_name"] == "A"].sort_values("date")
    b = df[df["goal_name"] == "B"]
    assert a.iloc[-1]["balance_eur"] == pytest.approx(202.01, abs=1e-3)
    assert b.iloc[0]["balance_eur"] == 500.0


def test_recompute_handles_missing_dates_gracefully():
    df = _savings_df([
        {"goal_name": "G", "date": None, "deposited_eur": 50.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 50.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 100.0


def test_withdrawal_reduces_balance_with_interest():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -30.0, "interest_rate": 12.0},
    ])
    # 100 * 1.01 - 30 = 71.0
    assert df.iloc[-1]["balance_eur"] == pytest.approx(71.0, abs=1e-3)


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
    assert df.iloc[-1]["balance_eur"] == 1000.0       # 0% rate -> flat

    n = update_savings_goal(test_user, "Car", {"target_eur": 5000.0,
                                               "interest_rate": 12.0})
    assert n == 2
    df = get_savings(test_user)
    assert set(df["target_eur"]) == {5000.0}
    assert set(df["interest_rate"]) == {12.0}
    # balance chain recomputed with the new rate AND tail-accrued to today:
    # 1000 * 1.01^2 (Jan->Mar) * 1.01^months (Mar->today)
    months = (date.today().year - 2025) * 12 + (date.today().month - 3)
    expected = 1000.0 * (1.01 ** 2) * (1.01 ** max(months, 0))
    assert df.iloc[-1]["balance_eur"] == pytest.approx(expected, abs=0.01)


def test_single_deposit_goal_accrues_to_today(test_user):
    """Regression: a goal with a single deposit used to show no interest
    until a second entry arrived — the tail now accrues to today."""
    _entry(test_user, date(2024, 1, 1), "Solo", 1000.0, rate=12.0)
    df = get_savings(test_user)
    months = (date.today().year - 2024) * 12 + (date.today().month - 1)
    assert df.iloc[-1]["balance_eur"] == pytest.approx(
        1000.0 * (1.01 ** max(months, 0)), abs=0.01)


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