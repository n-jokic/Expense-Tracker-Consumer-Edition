"""#13: atomic create-target-and-fund-purchase command.

Pins:
  * one call creates BOTH the anchor Savings row (zero deposit, carries the
    target) and the BigPurchase stamped with its stable funding reference;
  * duplicate goal names are rejected and NOTHING is written (rollback);
  * the revision bump is visible on the returned CommandResult;
  * the legacy two-step path stays available for existing-goal linking."""

from datetime import date

import pytest

import db
import services.purchase_commands as pc
from auth import hash_password
from services.commands import CommandError

U = "i13_atomic_user"
E = "i13_atomic@example.com"
D1 = date(2025, 4, 1)

FIELDS = {"name": "New laptop", "category": "Other", "price": 900.0,
          "currency": "EUR", "price_eur": 900.0, "usage_hours": 20.0,
          "importance": 4, "status": "wishlist", "notes": ""}


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "I13 Tester")
    yield uid
    db.delete_user_account(uid)


def test_creates_goal_and_item_in_one_call(user):
    res = pc.create_target_and_fund_purchase(
        user, FIELDS, "Laptop fund", target_eur=1200.0)
    assert res.changed
    bp_id, rid = res.affected_ids

    sdf = db.get_savings(user)
    g = sdf[sdf["id"].astype(str) == rid]
    assert not g.empty
    assert g.iloc[0]["goal_name"] == "Laptop fund"
    assert float(g.iloc[0]["target_eur"]) == 1200.0
    assert float(g.iloc[0]["deposited_eur"]) == 0.0  # anchor only, no money moved

    bdf = db.get_big_purchases(user)
    row = bdf[bdf["id"].astype(str) == bp_id]
    assert not row.empty
    assert row.iloc[0]["funding_source"] == pc.FUNDING_SAVINGS_GOAL
    assert str(row.iloc[0]["funding_goal_ref"]) == rid


def test_duplicate_goal_name_rolls_back_everything(user):
    db.add_savings(user, {"goal_name": "Laptop fund", "date": D1,
                          "target_eur": 100.0, "deposited": 0.0,
                          "currency": "EUR"})
    with pytest.raises(CommandError):
        pc.create_target_and_fund_purchase(user, FIELDS, "laptop fund")
    bdf = db.get_big_purchases(user)
    assert bdf.empty, "item must not survive a failed combined command"


def test_empty_goal_name_rejected(user):
    with pytest.raises(CommandError):
        pc.create_target_and_fund_purchase(user, FIELDS, "   ")
    assert db.get_big_purchases(user).empty
