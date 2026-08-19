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
