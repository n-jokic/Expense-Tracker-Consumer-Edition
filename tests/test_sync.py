"""
Tests for the sync protocol (sync_core.py) — pure logic plus DB-backed
apply_changes/snapshot against a throwaway user.
"""

from datetime import date, datetime, timezone, timedelta

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    add_expense, get_expenses, get_sync_conflicts,
)
from auth import hash_password
import sync_core
from sync_core import fields_differ, coerce_fields, parse_since, apply_changes, snapshot

TEST_USERNAME = "sync_test_user"
TEST_EMAIL    = "sync_test@example.com"


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


def test_apply_changes_records_conflict(test_user):
    add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Lidl", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "old",
    })
    rid = get_expenses(test_user).iloc[0]["id"]

    # Server edited the record (newer than the device's base timestamp)
    from db import update_expense
    update_expense(test_user, rid, {"notes": "server edit"})
    server_updated = get_expenses(test_user).iloc[0]["updated_at"]

    # Device syncs with an old `since` and a different value
    since_iso = (server_updated - timedelta(minutes=1)).isoformat()
    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"notes": "phone edit"},
    }], since=parse_since(since_iso))

    assert result["conflicts"][0]["id"] == rid
    conflicts = get_sync_conflicts(test_user, resolved=False)
    assert len(conflicts) == 1
    assert conflicts[0]["device_value"] == {"notes": "phone edit"}
    assert conflicts[0]["server_value"]["notes"] == "server edit"
    # the device value was NOT applied
    assert get_expenses(test_user).iloc[0]["notes"] == "server edit"


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
