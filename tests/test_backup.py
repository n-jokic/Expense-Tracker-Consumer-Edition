"""
Regression tests for backup_db (db.py): same-day force backups must capture
later changes, writes must be atomic (no stray .tmp files), and the daily
marker must keep non-forced calls to one backup per day.
"""

import os
import sqlite3
from datetime import date

import pytest

import db as db_module
from db import (
    init_db, create_user, delete_user_account, backup_db,
    add_expense, username_exists, get_user_by_username,
)
from auth import hash_password

TEST_USERNAME = "backup_test_user"
TEST_EMAIL    = "backup_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Backup Tester")
    yield uid
    delete_user_account(uid)


def _count_rows(db_path: str, table: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_force_backup_captures_same_day_changes(test_user):
    first = backup_db(force=True)
    assert first and os.path.exists(first)

    # Non-forced call on the same day is a no-op (daily marker).
    assert backup_db() is None

    # A change made AFTER the morning backup...
    add_expense(test_user, {
        "date": date(2025, 6, 1), "category": "Food & Dining",
        "subcategory": "Groceries", "description": "Second run",
        "amount": 9.99, "currency": "EUR", "amount_eur": 9.99,
        "recurring": False, "notes": "",
    })

    # ...must be captured by a forced backup later the same day.
    second = backup_db(force=True)
    assert second and second != first and os.path.exists(second)
    assert _count_rows(second, "expenses") == _count_rows(first, "expenses") + 1


def test_backup_is_atomic_no_tmp_files(test_user):
    backup_db(force=True)
    leftovers = [f for f in os.listdir(db_module.BACKUP_DIR) if f.endswith(".tmp")]
    assert leftovers == []


def test_backup_prunes_old_files(test_user):
    # Plant a file older than retention; a forced backup should remove it.
    old = os.path.join(db_module.BACKUP_DIR, "expense_tracker_2000-01-01_000000_x.db")
    os.makedirs(db_module.BACKUP_DIR, exist_ok=True)
    with open(old, "wb") as f:
        f.write(b"")
    backup_db(force=True)
    assert not os.path.exists(old)
