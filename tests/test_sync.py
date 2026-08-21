"""
Tests for the sync protocol (sync_core.py): pure logic (fields, dates,
validation), the DB-backed apply_changes/snapshot pipeline, and the v2
regressions (field validation, scoped record creation, atomic
compare-and-update) against throwaway users.
"""

from datetime import date, datetime, timezone, timedelta

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, add_expense, get_expenses, get_sync_conflicts,
    update_expense,
)
from auth import hash_password
from utils import INCOME_TYPES
import sync_core
from sync_core import (
    fields_differ, coerce_fields, parse_since, apply_changes, snapshot,
    validate_fields,
)

TEST_USERNAME = "sync_test_user"
TEST_EMAIL    = "sync_test@example.com"

U1 = "syncv2_user1"
U2 = "syncv2_user2"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        from db import get_user_by_username
        uid = get_user_by_username(TEST_USERNAME)["id"]
        delete_user_account(uid)
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "Sync Tester")
    yield uid
    delete_user_account(uid)


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


def _income_change(rid, **fields):
    base = {"date": "2025-06-01", "source": "Salary",
            "income_type": "Salary", "budgeted": 100.0, "actual": 100.0,
            "currency": "EUR", "budgeted_eur": 100.0, "actual_eur": 100.0}
    base.update(fields)
    return {"table": "income", "id": rid, "fields": base}


# ── Pure logic (v1) ───────────────────────────────────────────────────────────

def test_parse_since():
    assert parse_since(None) is None
    assert parse_since("2025-06-01T12:00:00Z") == datetime(2025, 6, 1, 12, 0, 0)
    assert parse_since("garbage") is None


def test_coerce_fields_dates():
    out = coerce_fields({"date": "2025-06-01", "amount_eur": 5})
    assert out["date"] == date(2025, 6, 1)
    assert out["amount_eur"] == 5
    assert coerce_fields({"date": "junk"}) == {}


def test_fields_differ():
    server = {"amount_eur": 5.0, "description": "Lidl", "date": "2025-06-01"}
    assert fields_differ(server, {"amount_eur": 10.0}) is True
    assert fields_differ(server, {"description": "Lidl"}) is False
    assert fields_differ(server, {"amount_eur": "5"}) is False
    assert fields_differ(server, {"id": "whatever"}) is False  # protected


def test_apply_changes_creates_new_record(test_user):
    result = apply_changes(test_user, [{
        "table": "expenses", "id": "e1",
        "fields": {"date": "2025-06-01", "category": "Food & Dining",
                   "description": "Offline entry", "amount": 5.0,
                   "currency": "EUR", "amount_eur": 5.0},
    }], since=None)
    assert result["applied"][0]["status"] == "created"
    df = get_expenses(test_user)
    assert len(df) == 1
    assert df.iloc[0]["description"] == "Offline entry"


def test_apply_changes_updates_unchanged_record(test_user):
    add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    })
    rid = get_expenses(test_user).iloc[0]["id"]
    before = get_expenses(test_user).iloc[0]["updated_at"]
    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"notes": "edited on phone"},
    }], since=None)
    assert result["applied"][0]["status"] == "updated"
    after = get_expenses(test_user).iloc[0]
    assert after["notes"] == "edited on phone"
    assert after["updated_at"] >= before


def test_snapshot_returns_newer_records(test_user):
    add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    })
    rid = get_expenses(test_user).iloc[0]["id"]
    updated = get_expenses(test_user).iloc[0]["updated_at"]

    since = (updated - timedelta(minutes=1))
    snap, truncated = snapshot(test_user, since)
    assert any(r["id"] == rid for r in snap["expenses"])
    assert truncated is False

    # a much newer `since` excludes it
    since2 = (updated + timedelta(minutes=1))
    snap2, _ = snapshot(test_user, since2)
    assert not any(r["id"] == rid for r in snap2["expenses"])


def test_conflict_with_date_field_is_json_safe(test_user):
    """Regression: conflicts whose fields contain dates must serialize into
    the JSON conflict storage (previously crashed with TypeError)."""
    add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    })
    rid = get_expenses(test_user).iloc[0]["id"]

    from db import update_expense
    update_expense(test_user, rid, {"notes": "server edit"})
    server_updated = get_expenses(test_user).iloc[0]["updated_at"]
    since_iso = (server_updated - timedelta(minutes=1)).isoformat()

    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"date": "2025-06-01", "notes": "phone edit"},
    }], since=parse_since(since_iso))

    assert result["conflicts"][0]["id"] == rid
    conflicts = get_sync_conflicts(test_user, resolved=False)
    assert len(conflicts) == 1
    assert conflicts[0]["device_value"]["date"] == "2025-06-01"  # ISO string, not date object


def test_pairing_flow_roundtrip(test_user):
    """Regression: pairing must not crash on naive-vs-aware datetime compare."""
    from db import create_pairing_device, complete_pairing, device_by_token
    dev_id, code = create_pairing_device(test_user)
    token = complete_pairing(code, "Test Phone")
    assert token is not None
    dev = device_by_token(token)
    assert dev["user_id"] == test_user
    assert dev["name"] == "Test Phone"
    # the code is single-use
    assert complete_pairing(code) is None


# ── Field validation (v2) ─────────────────────────────────────────────────────

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


def test_validate_rewrites_legacy_names():
    """Legacy category/subcategory names are remapped before validation."""
    clean, errors = validate_fields("expenses", {
        "category": "Food & Dining", "subcategory": "Groceries",
    })
    assert not errors
    assert clean["category"] == "Groceries"
    assert clean["subcategory"] == "Groceries"


def test_validate_rewrites_legacy_whole_category():
    clean, errors = validate_fields("expenses", {"category": "Housing"})
    assert not errors
    assert clean["category"] == "Housing & Utilities"
    assert clean["subcategory"] == ""


def test_apply_changes_accepts_legacy_names(two_users):
    uid_a, _ = two_users
    result = apply_changes(uid_a, [{
        "table": "expenses", "id": "legacy1",
        "fields": {"date": "2025-06-01", "category": "Entertainment",
                   "subcategory": "Vacation / Travel", "description": "Trip",
                   "amount": 5.0, "currency": "EUR", "amount_eur": 5.0},
    }])
    assert result["applied"][0]["status"] == "created"
    df = get_expenses(uid_a)
    assert df.iloc[0]["category"] == "Travel"
    assert df.iloc[0]["subcategory"] == "Tours & Activities"


# ── Cross-account isolation (v2) ──────────────────────────────────────────────

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


# ── Atomic conflict handling (v2) ─────────────────────────────────────────────

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


# ── income_type enum (v2) ──────────────────────────────────────────────────────

def test_validate_accepts_valid_income_type():
    """Every canonical INCOME_TYPES value passes validate_fields cleanly."""
    for itype in INCOME_TYPES:
        clean, errors = validate_fields("income", {"income_type": itype})
        assert not errors, "expected no errors for income_type=" + repr(itype)
        assert clean["income_type"] == itype


def test_validate_rejects_unknown_income_type():
    """A non-whitelisted income_type produces an 'unknown income_type' error."""
    clean, errors = validate_fields("income", {"income_type": "BOGUS"})
    assert any("unknown income_type" in e for e in errors)
    assert "income_type" not in clean

    # case-folded / lowercase labels are NOT silently accepted (strict equality,
    # matching MCP's validate_income_type contract)
    _, errors = validate_fields("income", {"income_type": "salary"})
    assert any("unknown income_type" in e for e in errors)


def test_apply_changes_bad_income_type_lands_in_failed(two_users):
    """A CREATE with a bad income_type is rejected and reported in failed[]."""
    uid_a, _ = two_users
    result = apply_changes(uid_a, [_income_change("inc-bad", income_type="BOGUS")])
    assert result["applied"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == "inc-bad"
    assert "unknown income_type" in result["failed"][0]["error"]
    from db import get_income
    df = get_income(uid_a)
    assert len(df) == 0