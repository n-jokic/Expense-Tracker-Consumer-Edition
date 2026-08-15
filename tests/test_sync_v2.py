"""
Regression tests for the sync protocol v2 (sync_core.validate_fields, scoped
record creation, atomic compare-and-update).
"""

import math
from datetime import date, datetime, timedelta

import pytest

from db import (
    init_db, create_user, delete_user_account, add_expense, get_expenses,
    get_sync_conflicts, username_exists, get_user_by_username, update_expense,
)
from auth import hash_password
from sync_core import validate_fields, apply_changes, snapshot

U1 = "syncv2_user1"
U2 = "syncv2_user2"


@pytest.fixture()
def two_users():
    init_db()
    ids = []
    for name, email in ((U1, "sv2a@example.com"), (U2, "sv2b@example.com")):
        if username_exists(name):
            delete_user_account(get_user_by_username(name)["id"])
        ids.append(create_user(name, email, hash_password("test1234"), name))
    yield ids
    for uid in ids:
        delete_user_account(uid)


def _expense_change(rid, **fields):
    base = {"date": "2025-06-01", "category": "Food & Dining",
            "description": "Offline", "amount": 5.0, "currency": "EUR",
            "amount_eur": 5.0}
    base.update(fields)
    return {"table": "expenses", "id": rid, "fields": base}


# ── Field validation ──────────────────────────────────────────────────────────

def test_validate_rejects_unknown_fields():
    clean, errors = validate_fields("expenses", {"hacker_column": 1})
    assert errors == ["unknown field hacker_column"]
    assert clean == {}


def test_validate_rejects_protected_fields():
    clean, errors = validate_fields("expenses", {"updated_at": "2020-01-01"})
    assert "updated_at is server-managed" in errors


def test_validate_rejects_bad_types_and_values():
    _, errors = validate_fields("expenses", {"amount_eur": float("nan")})
    assert any("must be finite" in e for e in errors)
    _, errors = validate_fields("expenses", {"amount_eur": "junk"})
    assert any("invalid type" in e for e in errors)
    _, errors = validate_fields("expenses", {"date": "not-a-date"})
    assert any("invalid type" in e for e in errors)


def test_validate_rejects_unknown_category_and_subcategory():
    _, errors = validate_fields("expenses", {"category": "Not A Category"})
    assert any("unknown category" in e for e in errors)
    _, errors = validate_fields("expenses", {"subcategory": "Not A Subcat"})
    assert any("unknown subcategory" in e for e in errors)


def test_validate_rejects_oversized_strings():
    _, errors = validate_fields("expenses", {"description": "x" * 501})
    assert any("too long" in e for e in errors)


def test_validate_coerces_valid_values():
    clean, errors = validate_fields("expenses", {
        "date": "2025-06-01", "amount": "12.50", "amount_eur": 12.5,
        "recurring": 1, "category": "Food & Dining", "subcategory": "Groceries",
    })
    assert not errors
    assert clean["date"] == date(2025, 6, 1)
    assert clean["amount"] == 12.5
    assert clean["recurring"] is True


# ── Cross-account isolation ───────────────────────────────────────────────────

def test_cross_user_ids_do_not_block_or_leak(two_users):
    uid_a, uid_b = two_users
    rid = add_expense(uid_a, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "A's secret", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    })
    # B creates a record with the same id: it must succeed (remapped to a
    # fresh id), never crash, and never touch A's row.
    result = apply_changes(uid_b, [_expense_change(rid, description="B's row")])
    entry = result["applied"][0]
    assert entry["status"] == "created"
    assert "new_id" in entry and entry["new_id"] != rid
    df_a = get_expenses(uid_a)
    df_b = get_expenses(uid_b)
    assert df_a.iloc[0]["description"] == "A's secret"
    assert df_b.iloc[0]["description"] == "B's row"
    assert df_b.iloc[0]["id"] == entry["new_id"]


def test_update_is_scoped_to_owner(two_users):
    uid_a, uid_b = two_users
    rid = add_expense(uid_a, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "A row", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    })
    # B's update for A's id remaps into B's own new record instead of
    # editing A's row.
    result = apply_changes(uid_b, [_expense_change(rid, description="B attempt",
                                                   notes="x")])
    assert result["applied"][0]["status"] == "created"
    assert result["applied"][0].get("new_id") != rid
    assert get_expenses(uid_a).iloc[0]["description"] == "A row"
    assert len(get_expenses(uid_b)) == 1


# ── Atomic conflict handling ──────────────────────────────────────────────────

def test_conflict_not_applied_and_recorded(two_users):
    uid_a, _ = two_users
    rid = add_expense(uid_a, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False,
        "notes": "old",
    })
    update_expense(uid_a, rid, {"notes": "server edit"})
    server_updated = get_expenses(uid_a).iloc[0]["updated_at"]
    since = server_updated - timedelta(minutes=1)

    result = apply_changes(uid_a, [{
        "table": "expenses", "id": rid, "fields": {"notes": "phone edit"},
    }], since=since)
    assert result["conflicts"][0]["id"] == rid
    assert result["applied"] == []
    assert get_expenses(uid_a).iloc[0]["notes"] == "server edit"
    conflicts = get_sync_conflicts(uid_a, resolved=False)
    assert conflicts[0]["device_value"] == {"notes": "phone edit"}


def test_failed_changes_reported(two_users):
    uid_a, _ = two_users
    result = apply_changes(uid_a, [
        {"table": "expenses", "id": "x1", "fields": {"evil": 1}},
        {"table": "notatable", "id": "x2", "fields": {}},
    ])
    assert len(result["failed"]) == 2
    assert result["applied"] == [] and result["conflicts"] == []


def test_snapshot_truncation_flag(two_users):
    uid_a, _ = two_users
    for i in range(5):
        add_expense(uid_a, {
            "date": date(2025, 6, 1), "category": "Other",
            "subcategory": "Miscellaneous", "description": f"e{i}",
            "amount": 1.0, "currency": "EUR", "amount_eur": 1.0,
            "recurring": False, "notes": "",
        })
    snap, truncated = snapshot(uid_a, limit=3)
    assert truncated is True
    assert len(snap["expenses"]) == 3
