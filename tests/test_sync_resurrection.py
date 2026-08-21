"""
Tests for soft-delete resurrection prevention in device sync (sync_core.py).

Bug T4-003: a paired device could silently un-delete a server-tombstoned row
by pushing is_deleted=false (or omitting the field) through _apply_update,
because the lookup did not filter on is_deleted and the cursor-based
conflict check fired only when the device's cursor was behind the deletion
time. The resurrection gate now converts any deleted->live transition into a
sync_conflict for manual resolution; the web UI's restore buttons remain the
only supported way to un-delete a row.

See sync_core._apply_update "resurrection gate".
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, add_expense, get_expenses, get_sync_conflicts,
    update_expense, soft_delete_expense,
)
from auth import hash_password
import sync_core
from sync_core import apply_changes, snapshot, parse_since


TEST_USERNAME = "sync_res_user"
TEST_EMAIL    = "sync_res@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        uid = get_user_by_username(TEST_USERNAME)["id"]
        delete_user_account(uid)
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "Resurrection Tester")
    yield uid
    delete_user_account(uid)


def _make_expense_row():
    return {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "", "description": "Original", "amount": 5.0,
        "currency": "EUR", "amount_eur": 5.0, "recurring": False, "notes": "",
    }


def _get_db_row(test_user, rid):
    """Fetch the raw DB row including is_deleted / updated_at."""
    with sync_core.get_session() as s:
        rows = (s.query(sync_core.Expense)
                .filter(sync_core.Expense.id == rid,
                        sync_core.Expense.user_id == test_user).all())
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r.id, "is_deleted": r.is_deleted,
            "description": r.description,
            "updated_at": sync_core._norm_dt(r.updated_at),
        }


# ── (a) Resurrection with since AFTER deletion time → conflict ──────────────

def test_resurrection_after_deletion_conflict(test_user):
    rid = add_expense(test_user, _make_expense_row())
    soft_delete_expense(test_user, rid)
    deleted_at = _get_db_row(test_user, rid)["updated_at"]

    # Device cursor is AT/AFTER the deletion time — the old cursor-based
    # conflict check would NOT fire. The resurrection gate must still catch it.
    since = deleted_at + timedelta(seconds=1)

    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"is_deleted": False, "description": "Revived?"},
    }], since=parse_since(since.isoformat()))

    assert result["applied"] == []
    assert "conflicts" in result and len(result["conflicts"]) == 1
    assert result["conflicts"][0]["id"] == rid

    # The row must still be soft-deleted in the DB.
    db_row = _get_db_row(test_user, rid)
    assert db_row["is_deleted"] is True
    assert db_row["description"] == "Original"  # not mutated

    # A conflict row was recorded for manual resolution.
    conflicts = get_sync_conflicts(test_user, resolved=False)
    assert len(conflicts) == 1
    assert conflicts[0]["device_value"]["is_deleted"] is False


# ── (b) Resurrection with since BEFORE deletion time → conflict ─────────────

def test_resurrection_before_deletion_conflict(test_user):
    rid = add_expense(test_user, _make_expense_row())
    created_at = _get_db_row(test_user, rid)["updated_at"]
    soft_delete_expense(test_user, rid)

    # Device cursor is BEFORE the deletion time — the cursor check might fire,
    # but the resurrection gate must fire REGARDLESS of cursor position.
    since = created_at - timedelta(minutes=5)

    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"is_deleted": False, "description": "Revived?"},
    }], since=parse_since(since.isoformat()))

    assert result["applied"] == []
    assert len(result["conflicts"]) == 1
    db_row = _get_db_row(test_user, rid)
    assert db_row["is_deleted"] is True
    assert db_row["description"] == "Original"


# ── (c) Re-affirming is_deleted=true on a tombstoned row → applied ──────────

def test_reaffirm_delete_applies(test_user):
    rid = add_expense(test_user, _make_expense_row())
    soft_delete_expense(test_user, rid)
    deleted_at = _get_db_row(test_user, rid)["updated_at"]

    # A device that still knows the row is deleted pushes is_deleted=true.
    # This must apply as a normal update and stay deleted.
    since = deleted_at + timedelta(seconds=1)
    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"is_deleted": True, "notes": "cleaned up"},
    }], since=parse_since(since.isoformat()))

    assert result["applied"][0]["status"] == "updated"
    assert result["conflicts"] == []

    db_row = _get_db_row(test_user, rid)
    assert db_row["is_deleted"] is True
    assert db_row["description"] == "Original"  # untouched by this update


# ── (d) Live row normal UPDATE → unaffected ────────────────────────────────

def test_live_row_update_unaffected(test_user):
    rid = add_expense(test_user, _make_expense_row())
    result = apply_changes(test_user, [{
        "table": "expenses", "id": rid,
        "fields": {"description": "Updated on device", "amount": 12.0},
    }], since=None)

    assert result["applied"][0]["status"] == "updated"
    assert result["conflicts"] == []
    db_row = _get_db_row(test_user, rid)
    assert db_row["is_deleted"] is False
    assert db_row["description"] == "Updated on device"


# ── (e) snapshot() still includes tombstone rows (intended behavior) ─────────

def test_snapshot_includes_tombstone(test_user):
    """snapshot() ships tombstones by design (sync-and-household.md §260) so
    devices can reconcile deletions. This test guards that intended behavior
    against regression from the resurrection fix."""
    rid = add_expense(test_user, _make_expense_row())
    created_at = _get_db_row(test_user, rid)["updated_at"]
    soft_delete_expense(test_user, rid)

    # Snapshot from before the deletion must still emit the (now-tombstoned)
    # row so the device can pick up the deletion.
    snap, _ = snapshot(test_user, since=parse_since(created_at.isoformat()))
    ids = [r["id"] for r in snap.get("expenses", [])]
    assert rid in ids
    tomb = next(r for r in snap["expenses"] if r["id"] == rid)
    assert tomb["is_deleted"] is True
