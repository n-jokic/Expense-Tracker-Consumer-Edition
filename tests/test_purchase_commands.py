"""
FIN-06 / FIN-07 regression tests: wishlist↔savings linkage and atomic
purchases via services/purchase_commands.py.

Pins the locked financial-model acceptances with exact numbers:
  * the stable funding reference survives reload, edit, rename and
    missing-goal cases;
  * buying writes ONE expense + the funding debit + the bought stamp in ONE
    transaction (injected failure -> nothing written);
  * retries cannot duplicate the expense; double-buy is rejected;
  * refund reverses exactly (soft-deletes the linked legs, restores the
    pre-buy status) atomically;
  * arbitrary status changes cannot reach "bought" outside the command.
"""

from datetime import date

import pytest

import db
import services.commands as cmd
import services.finance_queries as fq
import services.purchase_commands as pc
from auth import hash_password

U = "fin06_fin07_purchase_user"
E = "fin06_fin07@example.com"

D1 = date(2025, 3, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "FIN-06/07 Tester")
    yield uid
    db.delete_user_account(uid)


# ── helpers ───────────────────────────────────────────────────────────────────

def _income(uid, amount, d=D1, source="Salary"):
    db.add_income(uid, {
        "date": d, "source": source, "income_type": source,
        "budgeted": amount, "budgeted_eur": amount,
        "actual": amount, "actual_eur": amount, "currency": "EUR", "notes": "",
    })


def _unalloc(uid) -> float:
    return fq.unallocated_funds_eur(uid)


def _principal(uid, goal) -> float:
    df = db.get_savings(uid)
    if df.empty:
        return 0.0
    g = df[df["goal_name"] == goal]
    return round(float(g["deposited_eur"].fillna(0).sum()), 2) if not g.empty else 0.0


def _add_item(uid, name="Laptop", price=250.0, **kw) -> str:
    return db.add_big_purchase(uid, {
        "name": name, "category": kw.pop("category", "Other"),
        "price": price, "currency": "EUR", "price_eur": price,
        "usage_hours": kw.pop("usage_hours", 20.0),
        "importance": 4, "status": kw.pop("status", "wishlist"),
        "notes": "", **kw,
    })


def _item(uid, item_id):
    df = db.get_big_purchases(uid)
    return df[df["id"] == item_id].iloc[0]


def _expenses(uid, include_deleted=False):
    return db.get_expenses(uid, include_deleted=include_deleted)


def _debit_rows(uid, include_deleted=False):
    df = db.get_savings(uid, include_deleted=include_deleted)
    if df.empty:
        return df
    return df[df["deposited_eur"] < 0]


def _anchor_ref(uid, goal) -> str | None:
    df = db.get_savings(uid)
    if df.empty:
        return None
    g = df[df["goal_name"] == goal]
    if g.empty:
        return None
    return str(g.sort_values(["date", "created_at"]).iloc[0]["id"])


def _rev(uid) -> int:
    from db import User, get_session
    with get_session() as s:
        u = s.query(User).filter(User.id == uid).first()
        return int(u.data_revision or 0)


# ── FIN-06: create-a-target / link-existing / stable reference ────────────────

def test_create_wishlist_target_makes_empty_linkable_goal(user):
    before = _unalloc(user)
    res = pc.create_wishlist_target(user, "Gadget Fund", target_eur=500.0)
    assert res.changed and len(res.affected_ids) == 1
    df = db.get_savings(user)
    row = df.iloc[0]
    assert row["goal_name"] == "Gadget Fund"
    assert float(row["deposited_eur"]) == 0.0          # not a money movement
    assert float(row["target_eur"]) == 500.0
    assert _unalloc(user) == pytest.approx(before, abs=0.01)
    # duplicate goal names are rejected (case-insensitive)
    with pytest.raises(cmd.CommandError):
        pc.create_wishlist_target(user, "gadget fund")
    with pytest.raises(cmd.CommandError):
        pc.create_wishlist_target(user, "")


def test_funding_link_survives_reload_edit_and_rename(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "Trip", 200.0, entry_date=D1)
    ref = _anchor_ref(user, "Trip")
    item_id = _add_item(user, funding_source="savings_goal", funding_goal_ref=ref)

    # survives reload
    row = _item(user, item_id)
    assert row["funding_source"] == "savings_goal"
    assert str(row["funding_goal_ref"]) == ref

    # survives an item edit (rename/price change never touches the link)
    db.update_big_purchase(user, item_id, {"name": "Laptop Pro", "price": 300.0})
    row = _item(user, item_id)
    assert str(row["funding_goal_ref"]) == ref

    # survives a goal RENAME: the anchor row keeps its id, only goal_name moves
    assert db.rename_savings_goal(user, "Trip", "Voyage") > 0
    assert pc.resolve_linked_goal_name(user, ref) == "Voyage"
    row = _item(user, item_id)
    assert str(row["funding_goal_ref"]) == ref


def test_missing_goal_resolves_gracefully_and_blocks_buy(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "Trip", 200.0, entry_date=D1)
    ref = _anchor_ref(user, "Trip")
    item_id = _add_item(user, funding_source="savings_goal", funding_goal_ref=ref)

    # goal emptied and deleted -> the reference must degrade, not crash
    cmd.withdraw_from_goal(user, "Trip", 200.0, entry_date=D1)
    cmd.soft_delete_goal_checked(user, "Trip")
    assert pc.resolve_linked_goal_name(user, ref) is None

    with pytest.raises(cmd.CommandError, match="no longer exists"):
        pc.buy_wishlist_item(user, item_id)
    assert _item(user, item_id)["status"] == "wishlist"
    assert _expenses(user).empty


def test_funding_summary_variants_without_duplicating_savings_math(user):
    # no target -> ''
    assert pc.funding_summary({"funding_source": None}, []) == ""
    assert pc.funding_summary({}, []) == ""
    # explicit unallocated
    assert pc.funding_summary(
        {"funding_source": "unallocated"}, []) == "Funding: unallocated funds"
    # linked goal: canonical timeline math, exact display numbers
    rows = [{"id": "A", "goal_name": "G", "date": D1,
             "deposited_eur": 300.0, "interest_rate": 0.0, "target_eur": 500.0}]
    assert pc.funding_summary(
        {"funding_source": "savings_goal", "funding_goal_ref": "A"}, rows) == \
        "Funding: G — €300.00 of €500.00 saved"
    # vanished goal renders gracefully
    assert pc.funding_summary(
        {"funding_source": "savings_goal", "funding_goal_ref": "Z"}, rows) == \
        "⚠️ Linked savings goal no longer exists"


def test_set_purchase_funding_validates_and_clears(user):
    _income(user, 100.0)
    cmd.deposit_to_goal(user, "G", 50.0, entry_date=D1)
    ref = _anchor_ref(user, "G")
    item_id = _add_item(user)

    with pytest.raises(cmd.CommandError):
        pc.set_purchase_funding(user, item_id, source="mattress")
    with pytest.raises(cmd.CommandError):
        pc.set_purchase_funding(user, item_id,
                                source="savings_goal", goal_ref="missing-row")
    res = pc.set_purchase_funding(user, item_id,
                                  source="savings_goal", goal_ref=ref)
    assert res.changed
    assert _item(user, item_id)["funding_goal_ref"] == ref
    res = pc.set_purchase_funding(user, item_id, source=None)
    assert res.changed
    row = _item(user, item_id)
    assert row["funding_source"] is None and row["funding_goal_ref"] is None


# ── FIN-07: atomic buy ────────────────────────────────────────────────────────

def test_buy_from_unallocated_writes_one_expense_and_bumps_once(user):
    _income(user, 1000.0)
    item_id = _add_item(user, price=250.0)
    rev_before = _rev(user)
    res = pc.buy_wishlist_item(user, item_id, entry_date=D1)
    assert res.changed and res.revision is not None
    assert _rev(user) == rev_before + 1                 # exactly one bump

    exp = _expenses(user)
    assert len(exp) == 1                                # ONE expense, once
    assert float(exp.iloc[0]["amount_eur"]) == 250.0
    assert exp.iloc[0]["description"] == "Laptop (big purchase)"
    row = _item(user, item_id)
    assert row["status"] == "bought"
    assert row["expense_id"] == exp.iloc[0]["id"]
    assert row["pre_buy_status"] == "wishlist"
    assert _unalloc(user) == pytest.approx(750.0, abs=0.01)


def test_buy_from_goal_debits_goal_exactly_once_and_is_cash_neutral(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 300.0, entry_date=D1)
    ref = _anchor_ref(user, "G")
    item_id = _add_item(user, price=250.0,
                        funding_source="savings_goal", funding_goal_ref=ref)
    unalloc_before = _unalloc(user)                     # 700.00

    res = pc.buy_wishlist_item(user, item_id, entry_date=D1)
    assert res.changed

    debits = _debit_rows(user)
    assert len(debits) == 1                             # exactly one debit leg
    assert float(debits.iloc[0]["deposited_eur"]) == -250.0
    assert debits.iloc[0]["settlement_ref"] == f"wishlist-buy:{item_id}"
    assert _principal(user, "G") == pytest.approx(50.0, abs=0.01)
    # expense −250 and allocation −250 cancel: unallocated cash unchanged
    assert _unalloc(user) == pytest.approx(unalloc_before, abs=0.01)
    assert len(_expenses(user)) == 1


def test_buy_insufficient_funds_rejected_without_any_write(user):
    _income(user, 100.0)
    item_id = _add_item(user, price=250.0)
    with pytest.raises(cmd.InsufficientFunds):
        pc.buy_wishlist_item(user, item_id)
    assert _expenses(user).empty
    assert _item(user, item_id)["status"] == "wishlist"
    assert _unalloc(user) == pytest.approx(100.0, abs=0.01)

    # same for a goal-funded purchase beyond the posted principal
    cmd.deposit_to_goal(user, "G", 100.0, entry_date=D1)
    ref = _anchor_ref(user, "G")
    item2 = _add_item(user, name="Boat", price=250.0,
                      funding_source="savings_goal", funding_goal_ref=ref)
    with pytest.raises(cmd.InsufficientFunds):
        pc.buy_wishlist_item(user, item2)
    assert _expenses(user).empty
    assert _debit_rows(user).empty
    assert _principal(user, "G") == pytest.approx(100.0, abs=0.01)


def test_double_buy_rejected_and_retry_is_idempotent(user):
    _income(user, 1000.0)
    item_id = _add_item(user, price=100.0)
    first = pc.buy_wishlist_item(user, item_id)
    assert first.changed

    # crash-retry / double click: recognized, nothing duplicated
    retry = pc.buy_wishlist_item(user, item_id)
    assert retry.changed is False and retry.revision is None
    assert len(_expenses(user)) == 1
    assert _unalloc(user) == pytest.approx(900.0, abs=0.01)

    # legacy row marked bought WITHOUT a linked expense (old free selector):
    # buying again must be rejected, not silently repaired
    legacy = _add_item(user, name="Legacy", price=50.0)
    db.update_big_purchase(user, legacy, {"status": "bought"})
    with pytest.raises(cmd.CommandError, match="already marked bought"):
        pc.buy_wishlist_item(user, legacy)
    assert len(_expenses(user)) == 1


def test_buy_rolls_back_everything_on_injected_failure(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 300.0, entry_date=D1)
    ref = _anchor_ref(user, "G")
    item_id = _add_item(user, price=250.0,
                        funding_source="savings_goal", funding_goal_ref=ref)
    unalloc_before = _unalloc(user)

    orig = db.log_audit
    def boom(*a, **k):
        raise RuntimeError("injected audit failure")
    db.log_audit = boom
    try:
        with pytest.raises(RuntimeError):
            pc.buy_wishlist_item(user, item_id)
    finally:
        db.log_audit = orig

    # NOTHING was written: no expense, no debit, no status/stamp changes
    assert _expenses(user).empty
    assert _debit_rows(user).empty
    row = _item(user, item_id)
    assert row["status"] == "wishlist"
    assert row["expense_id"] is None and row["pre_buy_status"] is None
    assert _principal(user, "G") == pytest.approx(300.0, abs=0.01)
    assert _unalloc(user) == pytest.approx(unalloc_before, abs=0.01)


def test_buy_rejects_missing_item_and_nonpositive_amount(user):
    with pytest.raises(cmd.CommandError):
        pc.buy_wishlist_item(user, "no-such-id")
    _income(user, 1000.0)
    zero = _add_item(user, name="Freebie", price=0.0)
    with pytest.raises(cmd.CommandError, match="greater than 0"):
        pc.buy_wishlist_item(user, zero)


# ── FIN-07: exact reversal / refund ──────────────────────────────────────────

def test_refund_restores_prior_state_exactly(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 300.0, entry_date=D1)
    ref = _anchor_ref(user, "G")
    item_id = _add_item(user, price=250.0, status="saving",
                        funding_source="savings_goal", funding_goal_ref=ref)
    unalloc_before = _unalloc(user)                     # 700.00
    pc.buy_wishlist_item(user, item_id, entry_date=D1)
    assert _item(user, item_id)["status"] == "bought"

    res = pc.refund_wishlist_item(user, item_id)
    assert res.changed and res.revision is not None

    # linked expense soft-deleted (history preserved, ledger clean)
    assert _expenses(user).empty
    kept = _expenses(user, include_deleted=True)
    assert len(kept) == 1 and bool(kept.iloc[0]["is_deleted"])
    # funding debit soft-deleted exactly as well
    assert _debit_rows(user).empty
    deleted_debits = _debit_rows(user, include_deleted=True)
    assert len(deleted_debits) == 1 and bool(deleted_debits.iloc[0]["is_deleted"])

    row = _item(user, item_id)
    assert row["status"] == "saving"                    # EXACT prior status
    assert row["pre_buy_status"] is None
    assert _principal(user, "G") == pytest.approx(300.0, abs=0.01)
    assert _unalloc(user) == pytest.approx(unalloc_before, abs=0.01)

    # nothing left to reverse
    with pytest.raises(cmd.CommandError):
        pc.refund_wishlist_item(user, item_id)


def test_refund_legacy_bought_item_restores_status_only(user):
    _income(user, 100.0)
    legacy = _add_item(user, name="Legacy", price=50.0)
    db.update_big_purchase(user, legacy, {"status": "bought"})
    res = pc.refund_wishlist_item(user, legacy)
    assert res.changed
    assert _item(user, legacy)["status"] == "wishlist"
    assert _expenses(user).empty


def test_refund_requires_existing_bought_item(user):
    with pytest.raises(cmd.CommandError):
        pc.refund_wishlist_item(user, "no-such-id")
    _income(user, 100.0)
    item_id = _add_item(user, price=10.0)
    with pytest.raises(cmd.CommandError, match="Only bought items"):
        pc.refund_wishlist_item(user, item_id)


# ── FIN-07: no status bypass ─────────────────────────────────────────────────

def test_status_selector_cannot_reach_bought():
    assert "bought" not in pc.SELECTABLE_STATUSES
    assert set(pc.SELECTABLE_STATUSES) == {"wishlist", "saving"}
    assert pc.is_selectable_status("saving")
    assert not pc.is_selectable_status("bought")
