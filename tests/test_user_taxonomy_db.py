"""#16 part 1 — user_taxonomy registry: seeding, CRUD, remap, guards."""
from datetime import date

import pytest

from auth import hash_password
from db import (TAXONOMY_RESERVED_CATEGORY, add_budget, add_expense,
                add_recurring, can_delete_user_category,
                ensure_user_taxonomy_seeded, get_user_taxonomy,
                get_expenses, get_recurring, remap_user_category,
                rename_user_category, soft_delete_user_category,
                upsert_user_category, create_user, delete_user_account,
                get_user_by_username, init_db, username_exists)

U = "tax_user"
E = "tax@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Tax Tester")
    yield uid
    delete_user_account(uid)


def _expense(uid, cat, desc="x"):
    add_expense(uid, {"date": date(2026, 1, 5), "category": cat,
                      "description": desc, "amount": 5.0,
                      "currency": "EUR", "amount_eur": 5.0, "notes": ""})


def _recurring(uid, cat):
    add_recurring(uid, {"category": cat, "subcategory": "",
                        "description": "rent", "amount": 500.0,
                        "currency": "EUR", "amount_eur": 500.0,
                        "notes": "", "active": True})


def test_seed_is_idempotent_and_shapes_registry(user):
    assert ensure_user_taxonomy_seeded(user) is True
    assert ensure_user_taxonomy_seeded(user) is False   # second call no-op
    df = get_user_taxonomy(user)
    cats = set(df["category"])
    assert "Groceries" in cats and TAXONOMY_RESERVED_CATEGORY in cats
    # every seeded category has at least one row; pairs are unique
    assert not df.duplicated(subset=["category", "subcategory"]).any()
    assert df["sort_order"].is_monotonic_increasing


def test_upsert_creates_and_replaces_subcats(user):
    ensure_user_taxonomy_seeded(user)
    upsert_user_category(user, "Coffee fund", ["Beans", "Cafe"], 99)
    df = get_user_taxonomy(user)
    got = sorted(df[df["category"] == "Coffee fund"]["subcategory"])
    assert got == ["Beans", "Cafe"]
    upsert_user_category(user, "Coffee fund", ["Beans"], 99)
    df = get_user_taxonomy(user)
    got = sorted(df[df["category"] == "Coffee fund"]["subcategory"])
    assert got == ["Beans"]
    with pytest.raises(ValueError):
        upsert_user_category(user, "", ["x"])
    with pytest.raises(ValueError):
        upsert_user_category(user, TAXONOMY_RESERVED_CATEGORY, [])


def test_rename_moves_registry_and_data(user):
    ensure_user_taxonomy_seeded(user)
    _expense(user, "Groceries")
    _recurring(user, "Groceries")
    add_budget(user, {"year": 2026, "month": 1, "category": "Groceries",
                      "subcategory": "", "budgeted_eur": 120.0})
    rename_user_category(user, "Groceries", "Food")
    assert "Groceries" not in set(get_user_taxonomy(user)["category"])
    assert set(get_expenses(user)["category"]) == {"Food"}
    assert set(get_recurring(user)["category"]) == {"Food"}
    bud = get_budgets(user)
    assert set(bud[bud["month"] == 1]["category"]) == {"Food"}
    with pytest.raises(ValueError):
        rename_user_category(user, TAXONOMY_RESERVED_CATEGORY, "Other")
    with pytest.raises(ValueError):
        rename_user_category(user, "Food", TAXONOMY_RESERVED_CATEGORY)
    with pytest.raises(ValueError):
        rename_user_category(user, "Food", "Transport")   # exists -> remap


def get_budgets(uid):
    from db import get_budgets as _gb
    return _gb(uid)


def test_soft_delete_marks_rows(user):
    ensure_user_taxonomy_seeded(user)
    assert soft_delete_user_category(user, "Transport") is True
    assert soft_delete_user_category(user, "Transport") is False  # already gone
    live = set(get_user_taxonomy(user)["category"])
    assert "Transport" not in live
    allrows = get_user_taxonomy(user, include_deleted=True)
    assert "Transport" in set(allrows["category"])
    assert allrows[allrows["category"] == "Transport"]["is_deleted"].all()
    with pytest.raises(ValueError):
        soft_delete_user_category(user, TAXONOMY_RESERVED_CATEGORY)


def test_can_delete_counts_and_reserved_guard(user):
    ensure_user_taxonomy_seeded(user)
    _expense(user, "Groceries")
    _expense(user, "Groceries")
    _recurring(user, "Groceries")
    add_budget(user, {"year": 2026, "month": 2, "category": "Groceries",
                      "subcategory": "", "budgeted_eur": 10.0})
    info = can_delete_user_category(user, "Groceries")
    assert info == {"deletable": True, "expense_count": 2,
                    "recurring_count": 1, "budget_count": 1}
    reserved = can_delete_user_category(user, TAXONOMY_RESERVED_CATEGORY)
    assert reserved["deletable"] is False


def test_remap_moves_counts_and_drops_source(user):
    ensure_user_taxonomy_seeded(user)
    _expense(user, "Groceries")
    _recurring(user, "Groceries")
    moved = remap_user_category(user, "Groceries", "Food")
    assert moved == 2
    assert set(get_expenses(user)["category"]) == {"Food"}
    assert "Groceries" not in set(
        get_user_taxonomy(user, include_deleted=True)["category"])
    # remap onto a brand-new category creates its registry row
    _expense(user, "Food")
    remap_user_category(user, "Food", "Nosh")
    assert "Nosh" in set(get_user_taxonomy(user)["category"])
    with pytest.raises(ValueError):
        remap_user_category(user, TAXONOMY_RESERVED_CATEGORY, "Other")
