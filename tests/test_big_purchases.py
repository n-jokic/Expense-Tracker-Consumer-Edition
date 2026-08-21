"""
Tests for the big-purchase priority matrix (utils.classify_quadrant).
"""

import pytest

from auth import hash_password
from db import (
    add_big_purchase,
    create_user,
    delete_user_account,
    get_big_purchases,
    get_user_by_username,
    init_db,
    update_big_purchase,
    username_exists,
)
from utils import classify_quadrant, validate_grouped_order


@pytest.fixture()
def purchase_user():
    init_db()
    username = "big_purchase_order_user"
    if username_exists(username):
        delete_user_account(get_user_by_username(username)["id"])
    uid = create_user(username, "big_purchase_order@example.com",
                      hash_password("test1234"), "Purchase Tester")
    yield uid
    delete_user_account(uid)


def test_quadrant_quick_win():
    # high usage, low work-hours -> buy soon
    assert classify_quadrant(work_hours=5, usage_hours=40,
                             median_work=20, median_usage=10) == "Quick wins"


def test_quadrant_plan_and_save():
    assert classify_quadrant(work_hours=60, usage_hours=40,
                             median_work=20, median_usage=10) == "Plan & save"


def test_quadrant_maybe_later():
    assert classify_quadrant(work_hours=5, usage_hours=2,
                             median_work=20, median_usage=10) == "Maybe later"


def test_quadrant_reconsider():
    assert classify_quadrant(work_hours=60, usage_hours=2,
                             median_work=20, median_usage=10) == "Reconsider"


def test_quadrant_boundary_values_fall_to_low_side():
    # exactly at the median counts as "not high"
    assert classify_quadrant(work_hours=20, usage_hours=10,
                             median_work=20, median_usage=10) == "Maybe later"


def test_drag_order_rejects_duplicate_unknown_and_missing_ids():
    expected = {"Other": ["a", "b"], "Travel": ["c"]}
    assert validate_grouped_order({"Other": ["b", "a"], "Travel": ["c"]}, expected) == {
        "Other": ["b", "a"], "Travel": ["c"]}
    assert validate_grouped_order({"Other": ["a", "a"], "Travel": ["c"]}, expected) is None
    assert validate_grouped_order({"Other": ["a", "x"], "Travel": ["c"]}, expected) is None
    assert validate_grouped_order({"Other": ["a"], "Travel": ["c"]}, expected) is None


def test_bought_purchase_is_retained_and_order_is_persisted(purchase_user):
    bought_id = add_big_purchase(purchase_user, {
        "name": "Bought item", "category": "Other", "price": 10.0,
        "currency": "EUR", "price_eur": 10.0, "usage_hours": 1.0,
        "importance": 3, "status": "bought", "sort_order": 4, "notes": "",
    })
    active_id = add_big_purchase(purchase_user, {
        "name": "Active item", "category": "Other", "price": 20.0,
        "currency": "EUR", "price_eur": 20.0, "usage_hours": 2.0,
        "importance": 3, "status": "wishlist", "sort_order": 1, "notes": "",
    })

    update_big_purchase(purchase_user, active_id, {"sort_order": 0})
    rows = get_big_purchases(purchase_user)

    assert set(rows["id"]) == {bought_id, active_id}
    assert rows.loc[rows["id"] == bought_id, "status"].iloc[0] == "bought"
    assert rows.loc[rows["id"] == active_id, "sort_order"].iloc[0] == 0


# ── FIN-06: funding-reference columns exist, migrate additively, round-trip ──

def test_funding_reference_round_trips_through_db_helpers(purchase_user):
    # add_big_purchase carries the optional funding link; get_big_purchases
    # exposes it (additive migration created the columns on the legacy table).
    ref = "anchor-row-uuid"
    item_id = add_big_purchase(purchase_user, {
        "name": "Linked item", "category": "Other", "price": 99.0,
        "currency": "EUR", "price_eur": 99.0, "usage_hours": 5.0,
        "importance": 2, "status": "wishlist", "notes": "",
        "funding_source": "savings_goal", "funding_goal_ref": ref,
    })
    row = get_big_purchases(purchase_user)
    row = row[row["id"] == item_id].iloc[0]
    assert row["funding_source"] == "savings_goal"
    assert row["funding_goal_ref"] == ref
    assert row["expense_id"] is None and row["pre_buy_status"] is None

    # edits of unrelated fields leave the reference untouched, and the
    # FIN-07 stamps can be written through update_big_purchase
    update_big_purchase(purchase_user, item_id, {"name": "Linked item v2"})
    update_big_purchase(purchase_user, item_id, {
        "expense_id": "exp-1", "pre_buy_status": "saving"})
    row = get_big_purchases(purchase_user)
    row = row[row["id"] == item_id].iloc[0]
    assert row["name"] == "Linked item v2"
    assert row["funding_goal_ref"] == ref
    assert row["expense_id"] == "exp-1"
    assert row["pre_buy_status"] == "saving"
