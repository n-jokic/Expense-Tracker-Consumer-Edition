"""#16 part 2 — effective taxonomy (queries cache, validation variants,
import fallback chain) and the set_budget ripple onto custom categories."""
from datetime import date

import pytest

import db
from auth import hash_password
from db import (TAXONOMY_RESERVED_CATEGORY, add_expense,
                create_user, delete_user_account, ensure_user_taxonomy_seeded,
                get_user_by_username, init_db, rename_user_category,
                soft_delete_user_category, upsert_user_category,
                username_exists)
from domain.taxonomy import CATEGORIES, CAT_LIST
from domain.validation import (map_unknown_category,
                               validate_category_in,
                               validate_category_subcategory_in)
from services.commands import CommandError, set_budget

U = "eff_tax_user"
E = "eff_tax@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Eff Tax Tester")
    yield uid
    delete_user_account(uid)


def _effective(uid):
    """Uncached read path (queries wrapper adds only caching)."""
    return db.effective_taxonomy(uid)


def test_defaults_match_static_taxonomy(user):
    ensure_user_taxonomy_seeded(user)
    cats, cats_dict = _effective(user)
    assert set(cats) == set(CAT_LIST) | {TAXONOMY_RESERVED_CATEGORY}
    for cat in CAT_LIST:
        assert sorted(cats_dict[cat]) == sorted(CATEGORIES[cat])


def test_custom_category_flows_through(user):
    ensure_user_taxonomy_seeded(user)
    upsert_user_category(user, "Coffee fund", ["Beans", "Cafe"], 500)
    cats, cats_dict = _effective(user)
    assert "Coffee fund" in cats
    assert cats_dict["Coffee fund"] == ["Beans", "Cafe"]
    validate_category_in("Coffee fund", cats_dict)
    validate_category_subcategory_in("Coffee fund", "Beans", cats_dict)
    with pytest.raises(ValueError):
        validate_category_in("Nope", cats_dict)


def test_rename_and_softdelete_reflect_effective(user):
    ensure_user_taxonomy_seeded(user)
    rename_user_category(user, "Groceries", "Food")
    _, cats_dict = _effective(user)
    assert "Groceries" not in cats_dict and "Food" in cats_dict
    soft_delete_user_category(user, "Transport")
    cats, _ = _effective(user)
    assert "Transport" not in cats


def test_map_unknown_fallback_chain(user, monkeypatch):
    ensure_user_taxonomy_seeded(user)
    _, cats_dict = _effective(user)
    # exact match passes through untouched
    assert map_unknown_category("Groceries", cats_dict)[0] == "Groceries"
    # keyword fallback lands in a live category from the static ruleset
    cat, _sub = map_unknown_category("lidl weekly shop", cats_dict)
    assert cat == "Groceries"

    import bank_import as bi

    # keyword rules know nothing -> reserved catch-all
    monkeypatch.setattr(bi, "categorize_expense", lambda t: (None, ""))
    assert map_unknown_category("zzz unmatchable", cats_dict) == ("Uncategorized", "")
    # keywords suggest a category the user has deleted -> catch-all again
    monkeypatch.setattr(bi, "categorize_expense", lambda t: ("Transport", "—"))
    soft_delete_user_category(user, "Transport")
    _, live = _effective(user)
    assert map_unknown_category("whatever", live) == ("Uncategorized", "")
    # ...but a live suggestion keeps its subcategory when valid
    upsert_user_category(user, "Coffee fund", ["Beans"], 77)
    _, live = _effective(user)
    monkeypatch.setattr(bi, "categorize_expense",
                        lambda t: ("Coffee fund", "Beans"))
    assert map_unknown_category("beans order", live) == ("Coffee fund", "Beans")


def test_set_budget_accepts_custom_category(user):
    ensure_user_taxonomy_seeded(user)
    upsert_user_category(user, "Coffee fund", [], 501)
    res = set_budget(user, "Coffee fund", 40.0, year=2026, month=3)
    assert res.changed
    bud = db.get_budgets(user)
    row = bud[(bud["year"] == 2026) & (bud["month"] == 3)
              & (bud["category"] == "Coffee fund")]
    assert len(row) == 1 and float(row.iloc[0]["budgeted_eur"]) == 40.0
    with pytest.raises(CommandError):
        set_budget(user, "Not a category", 10.0, year=2026, month=3)
