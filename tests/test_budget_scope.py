"""
Regression tests for budget scoping (db.add_budget upsert, the dedupe/unique
migration, and utils.effective_category_budgets): one row per scope and
subcategory rows authoritative — overlaps are never summed.
"""

import pandas as pd
import pytest
from sqlalchemy import text

from db import (
    init_db, create_user, delete_user_account, add_budget, get_budgets,
    get_engine, username_exists, get_user_by_username,
)
from auth import hash_password
from utils import effective_category_budgets

TEST_USERNAME = "budget_test_user"
TEST_EMAIL    = "budget_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Budget Tester")
    yield uid
    delete_user_account(uid)


def _scope(uid, year=2025, month=6, category="Food & Dining",
           subcategory="", value=100.0):
    return {"user_id": uid, "year": year, "month": month,
            "category": category, "subcategory": subcategory,
            "budgeted_eur": value}


def test_add_budget_upserts_same_scope(test_user):
    add_budget(test_user, _scope(test_user, value=100.0))
    add_budget(test_user, _scope(test_user, value=250.0))  # same scope
    df = get_budgets(test_user)
    assert len(df) == 1
    assert df.iloc[0]["budgeted_eur"] == 250.0


def test_migration_dedupes_existing_overlaps(test_user):
    # Simulate a legacy install: rebuild the budgets table in its PRE-migration
    # shape (no unique scope constraint), insert overlapping rows behind the
    # ORM's back, then run the migration — it must dedupe (newest kept) and
    # enforce the unique index.
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS budgets"))
        conn.execute(text(
            "CREATE TABLE budgets ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id INTEGER NOT NULL,"
            " year INTEGER, month INTEGER, category VARCHAR,"
            " subcategory VARCHAR DEFAULT '', budgeted_eur FLOAT DEFAULT 0)"))
        for sub, val in (("", 100.0), ("Groceries", 60.0), ("", 777.0)):
            conn.execute(text(
                "INSERT INTO budgets (user_id, year, month, category,"
                " subcategory, budgeted_eur)"
                " VALUES (:u, 2025, 6, 'Food & Dining', :s, :v)"),
                {"u": test_user, "s": sub, "v": val})

    init_db()  # migration dedupes (newest kept) + unique index

    df = get_budgets(test_user)
    assert len(df) == 2
    values = {row["subcategory"]: row["budgeted_eur"]
              for _, row in df.iterrows()}
    assert values[""] == 777.0      # newest duplicate kept
    assert values["Groceries"] == 60.0

    # The unique index must reject new duplicates now.
    with pytest.raises(Exception):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO budgets (user_id, year, month, category,"
                " subcategory, budgeted_eur)"
                " VALUES (:u, 2025, 6, 'Food & Dining', '', 1)"),
                {"u": test_user})


def test_subcategory_rows_authoritative_over_category_row():
    m_bud = pd.DataFrame([
        {"category": "Food & Dining", "subcategory": "", "budgeted_eur": 100.0},
        {"category": "Food & Dining", "subcategory": "Groceries", "budgeted_eur": 30.0},
        {"category": "Food & Dining", "subcategory": "Coffee & Snacks", "budgeted_eur": 20.0},
        {"category": "Transport", "subcategory": "", "budgeted_eur": 80.0},
    ])
    eff = effective_category_budgets(m_bud)
    # Subcategory rows win: 30 + 20, NOT 100 + 30 + 20.
    assert eff["Food & Dining"] == 50.0
    # No subcategory rows: the entire-category row applies.
    assert eff["Transport"] == 80.0


def test_effective_budgets_empty_and_null_subcategory():
    assert effective_category_budgets(pd.DataFrame()) == {}
    assert effective_category_budgets(None) == {}
    m_bud = pd.DataFrame([
        {"category": "Health", "subcategory": None, "budgeted_eur": 40.0},
        {"category": "Health", "subcategory": "  ", "budgeted_eur": 5.0},
    ])
    # Whitespace/None subcategories normalize to "" (entire category).
    assert effective_category_budgets(m_bud)["Health"] == 45.0
