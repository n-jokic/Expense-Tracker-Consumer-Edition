"""
Tests for the automatic taxonomy migration (db._migrate_taxonomy /
_migrate_settings_taxonomy): old (category, subcategory) pairs rewrite to the
new taxonomy exactly once, re-runs are no-ops, and user_settings fun/travel
pools are remapped.
"""

from datetime import date

import pytest

from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, add_expense, get_expenses, add_recurring,
    get_recurring, add_big_purchase, get_big_purchases,
    save_settings, get_settings,
)
from auth import hash_password

TEST_USERNAME = "taxonomy_test_user"
TEST_EMAIL    = "taxonomy_test@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Taxonomy Tester")
    yield uid
    delete_user_account(uid)


# (old_cat, old_sub, expected_cat, expected_sub)
CASES = [
    ("Housing", "Rent / Mortgage", "Housing & Utilities", "Rent / Mortgage"),
    ("Housing", "", "Housing & Utilities", ""),
    ("Food & Dining", "Groceries", "Groceries", "Groceries"),
    ("Food & Dining", "", "Groceries", "Groceries"),  # documented default
    ("Food & Dining", "Restaurants & Takeaway", "Dining Out", "Restaurants & Takeaway"),
    ("Food & Dining", "Coffee & Snacks", "Dining Out", "Coffee & Snacks"),
    ("Food & Dining", "Food Delivery", "Dining Out", "Food Delivery"),
    ("Food & Dining", "Work Lunch", "Dining Out", "Work Lunch"),
    ("Transport", "Flights & Trains", "Travel", "Flights & Trains"),
    ("Transport", "Fuel", "Transport", "Fuel"),
    ("Entertainment", "Vacation / Travel", "Travel", "Tours & Activities"),
    ("Entertainment", "Hotels & Lodging", "Travel", "Hotels & Lodging"),
    ("Entertainment", "Streaming Services", "Entertainment", "Streaming Services"),
    ("Personal", "Gifts", "Shopping", "Gifts"),
    ("Personal", "", "Shopping", ""),
    ("Other", "Subscriptions & Software", "Subscriptions & Software", "Subscriptions & Software"),
    ("Other", "Taxes & Fees", "Fees & Taxes", "Taxes & Fees"),
    ("Other", "", "Other", "Miscellaneous"),
    ("Loans & Debt", "Loan Repayment", "Loans & Debt", "Loan Repayment"),
]


def _add_old_expense(uid, desc, category, subcategory):
    add_expense(uid, {
        "date": date(2025, 1, 1), "category": category, "subcategory": subcategory,
        "description": desc, "amount": 1.0, "currency": "EUR",
        "amount_eur": 1.0, "recurring": False, "notes": "",
    })


def test_migration_maps_pairs_exactly(user):
    for i, (oc, os_, _ec, _es) in enumerate(CASES):
        _add_old_expense(user, f"old-{i}", oc, os_)

    init_db()  # run the taxonomy migration

    df = get_expenses(user)
    by_desc = {r["description"]: (r["category"], r["subcategory"])
               for _, r in df.iterrows()}
    for i, (oc, os_, ec, es) in enumerate(CASES):
        assert by_desc[f"old-{i}"] == (ec, es), f"case {(oc, os_)!r}"


def test_migration_is_idempotent(user):
    _add_old_expense(user, "grocery-run", "Food & Dining", "Groceries")
    init_db()
    df1 = get_expenses(user)
    assert len(df1) == 1
    assert df1.iloc[0]["category"] == "Groceries"
    assert df1.iloc[0]["subcategory"] == "Groceries"

    init_db()  # second run must be a no-op
    df2 = get_expenses(user)
    assert len(df2) == 1
    assert df2.iloc[0]["category"] == "Groceries"
    assert df2.iloc[0]["subcategory"] == "Groceries"
    assert df2.iloc[0]["id"] == df1.iloc[0]["id"]


def test_migration_rewrites_settings_fun_travel_categories(user):
    save_settings(user, {
        "fun_categories": ["Entertainment", "Food & Dining", "Groceries", "Housing"],
        "travel_categories": [
            "Entertainment › Vacation / Travel",
            "Transport › Flights & Trains",
            "Entertainment",
            "Transport › Fuel",
        ],
    })
    init_db()
    s = get_settings(user)
    assert s["fun_categories"] == ["Entertainment", "Dining Out", "Housing"]
    assert s["travel_categories"] == ["Travel", "Transport › Fuel"]


def test_migration_rewrites_recurring_and_big_purchases(user):
    add_recurring(user, {
        "category": "Food & Dining", "subcategory": "Coffee & Snacks",
        "description": "cafe", "amount": 3.0, "currency": "EUR",
        "amount_eur": 3.0, "notes": "", "active": True,
    })
    add_big_purchase(user, {
        "name": "Sofa", "category": "Personal", "price": 500.0, "currency": "EUR",
        "price_eur": 500.0, "usage_hours": 0.0, "importance": 3,
        "status": "wishlist", "notes": "",
    })

    init_db()

    rec = get_recurring(user)
    assert rec.iloc[0]["category"] == "Dining Out"
    assert rec.iloc[0]["subcategory"] == "Coffee & Snacks"

    bp = get_big_purchases(user)
    assert bp.iloc[0]["category"] == "Shopping"
