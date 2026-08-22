"""
B3: auto-archive of a drained big-purchase funding goal (and its refund
mirror). Pins:
  * buying the ONLY linked item down to a zero principal soft-deletes the
    goal in the SAME transaction and audits AUTO_ARCHIVE;
  * another still-unbought linked item blocks the archive;
  * an active term account under the goal blocks the archive;
  * refunding that purchase restores exactly the auto-archived goal;
  * a goal the user deleted manually stays deleted on refund.
"""

from datetime import date

import pytest

import db
import services.purchase_commands as pc
from auth import hash_password
from services.commands import deposit_to_goal

U = "b3_autoarchive_user"
E = "b3_autoarchive@example.com"
D1 = date(2025, 4, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "B3 Tester")
    yield uid
    db.delete_user_account(uid)


def _income(uid, amount):
    db.add_income(uid, {
        "date": D1, "source": "Salary", "income_type": "Salary",
        "budgeted": amount, "budgeted_eur": amount,
        "actual": amount, "actual_eur": amount, "currency": "EUR", "notes": "",
    })


def _fund_goal(uid, goal, amount):
    return deposit_to_goal(uid, goal_name=goal, amount_eur=amount, entry_date=D1)


def _anchor(uid, goal):
    sdf = db.get_savings(uid)
    g = sdf[sdf["goal_name"] == goal]
    assert not g.empty, f"goal {goal} missing"
    return str(g.sort_values("date").iloc[0]["id"])


def _add_linked_item(uid, name, price, ref):
    return db.add_big_purchase(uid, {
        "name": name, "category": "Other",
        "price": price, "currency": "EUR", "price_eur": price,
        "usage_hours": 5.0, "importance": 3, "status": "wishlist",
        "notes": "", "funding_source": pc.FUNDING_SAVINGS_GOAL,
        "funding_goal_ref": ref,
    })


def _goal_alive(uid, goal):
    df = db.get_savings(uid)
    return not df[df["goal_name"] == goal].empty


def _principal_left(uid, goal):
    df = db.get_savings(uid)
    g = df[df["goal_name"] == goal]
    return float(g["deposited_eur"].fillna(0).sum()) if not g.empty else 0.0


def _audit_actions(uid, action, table="savings_goal"):
    import queries as q
    # Outside a Streamlit runtime, queries.db_version() falls back to a
    # session-local counter that commands' _bump() never advances, so the
    # ttl-cached _audit reader can serve stale frames mid-test.
    try:
        q._audit.clear()
    except Exception:
        pass
    adf = q.audit(user_id=uid)
    if adf.empty:
        return []
    m = adf[(adf["action"] == action) & (adf["table_name"] == table)]
    return list(m["record_id"])


def test_buy_draining_single_linked_goal_archives_it(user):
    uid = user
    _income(uid, 500)
    _fund_goal(uid, "Laptop fund", 250)
    ref = _anchor(uid, "Laptop fund")
    item_id = _add_linked_item(uid, "Laptop", 250.0, ref)
    pc.buy_wishlist_item(uid, item_id)
    bdf = db.get_big_purchases(uid)
    assert str(bdf[bdf["id"] == item_id].iloc[0]["status"]) == "bought"
    assert not _goal_alive(uid, "Laptop fund"), (
        "drained single-linked goal must be auto-archived")
    assert "Laptop fund" in _audit_actions(uid, "AUTO_ARCHIVE")


def test_other_linked_item_blocks_archive(user):
    uid = user
    _income(uid, 1000)
    _fund_goal(uid, "Shared fund", 400)
    ref = _anchor(uid, "Shared fund")
    i1 = _add_linked_item(uid, "Thing A", 250.0, ref)
    _add_linked_item(uid, "Thing B", 150.0, ref)
    pc.buy_wishlist_item(uid, i1)
    assert _goal_alive(uid, "Shared fund"), (
        "another unbought linked item must block auto-archive")
    assert round(_principal_left(uid, "Shared fund"), 2) == 150.0


def test_active_term_blocks_archive(user):
    uid = user
    _income(uid, 600)
    _fund_goal(uid, "Term fund", 300)
    ref = _anchor(uid, "Term fund")
    db.add_savings_account(uid, {
        "goal_name": "Term fund", "name": "locked",
        "amount": 50.0, "currency": "EUR", "amount_eur": 50.0,
        "annual_rate": 1.0, "start_date": D1,
        "maturity_date": date(2026, 4, 1), "status": "active", "notes": "",
    })
    item = _add_linked_item(uid, "Spendy", 250.0, ref)
    # The buy validates against posted principal only; the locked term is
    # separate money and must still block archiving afterwards.
    pc.buy_wishlist_item(uid, item)
    assert _goal_alive(uid, "Term fund"), (
        "active term account must block auto-archive")


def test_refund_restores_auto_archived_goal(user):
    uid = user
    _income(uid, 500)
    _fund_goal(uid, "Refund fund", 200)
    ref = _anchor(uid, "Refund fund")
    item = _add_linked_item(uid, "Gadget", 200.0, ref)
    pc.buy_wishlist_item(uid, item)
    assert not _goal_alive(uid, "Refund fund")
    pc.refund_wishlist_item(uid, item)
    assert _goal_alive(uid, "Refund fund"), (
        "refund must restore the auto-archived goal")
    assert "Refund fund" in _audit_actions(uid, "AUTO_RESTORE")


def test_manually_deleted_goal_stays_deleted_on_refund(user):
    uid = user
    _income(uid, 500)
    _fund_goal(uid, "Manual fund", 120)
    ref = _anchor(uid, "Manual fund")
    item = _add_linked_item(uid, "Cheap thing", 100.0, ref)
    pc.buy_wishlist_item(uid, item)
    # User deletes what remains of the goal by hand (guarded soft delete).
    db.soft_delete_savings_goal(uid, "Manual fund")
    assert not _goal_alive(uid, "Manual fund")
    pc.refund_wishlist_item(uid, item)
    assert not _goal_alive(uid, "Manual fund"), (
        "manually deleted goals must NOT come back on refund")
