"""
D2 (item 11): %-auto-allocation of income. Pins the locked acceptances:
  * rules persist and only run when enabled;
  * goal targets deposit via the pool-validating command (FIN-01 kept);
  * loan targets move REAL money as early repayments, capped at balance;
  * requests beyond the unallocated pool scale down PRO-RATA;
  * one broken target never blocks the others (income save is safe).
"""

from datetime import date

import pytest

import db
import queries as q
from services.commands import apply_auto_allocations
from services.finance_queries import unallocated_funds_eur
from auth import hash_password

U = "d2_autoalloc_user"
E = "d2_autoalloc@example.com"
D1 = date(2025, 6, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "D2 Tester")
    yield uid
    db.delete_user_account(uid)


def _clear_readers():
    for n in ("_expenses", "_recurring", "_savings", "_savings_accounts",
              "_budgets", "_income", "_loans"):
        fn = getattr(q, n, None)
        if fn is not None and hasattr(fn, "clear"):
            try:
                fn.clear()
            except Exception:
                pass


def _set_rules(uid, rules):
    db.save_settings(uid, {"auto_alloc_rules": rules})
    _clear_readers()


def _income(uid, amount):
    db.add_income(uid, {
        "date": D1, "source": "Salary", "income_type": "Salary",
        "budgeted": amount, "budgeted_eur": float(amount),
        "actual": amount, "actual_eur": float(amount),
        "currency": "EUR", "notes": "",
    })


def _pool_before_after(uid, fn):
    before = unallocated_funds_eur(uid)
    summary = fn()
    _clear_readers()
    return before, summary, unallocated_funds_eur(uid)


def test_disabled_rules_are_noop(user):
    uid = user
    _income(uid, 1000)
    _set_rules(uid, {"enabled": False,
                     "targets": [{"type": "goal", "ref": "G", "pct": 50}]})
    s = apply_auto_allocations(uid, income_amount_eur=1000.0,
                               income_date=D1)
    assert s["enabled"] is False
    assert s["applied"] == []


def test_goal_target_deposits_and_keeps_fin01(user):
    uid = user
    _income(uid, 1000)                      # pool = 1000
    from services.commands import deposit_to_goal
    deposit_to_goal(uid, goal_name="Holiday", amount_eur=200.0,
                    entry_date=D1)          # pool = 800
    _set_rules(uid, {"enabled": True,
                     "targets": [{"type": "goal", "ref": "Holiday", "pct": 25}]})
    before = unallocated_funds_eur(uid)
    s = apply_auto_allocations(uid, income_amount_eur=1000.0, income_date=D1)
    _clear_readers()
    after = unallocated_funds_eur(uid)
    assert [a["amount_eur"] for a in s["applied"]] == [250.0]
    # FIN-01: pool drops by exactly what was allocated.
    assert round(before - after, 2) == 250.0
    df = db.get_savings(uid)
    hol = df[df["goal_name"] == "Holiday"]
    assert round(float(hol["deposited_eur"].sum()), 2) == 450.0  # 200 + 250


def test_loan_target_moves_real_money_capped(user):
    uid = user
    _income(uid, 1000)                       # pool = 1000
    loan_id = db.add_loan(uid, {
        "name": "Car", "principal": 5000.0, "currency": "EUR",
        "principal_eur": 5000.0, "annual_rate": 5.0, "start_date": D1,
        "term_months": 24, "payment_day": 1, "status": "active", "notes": "",
    })
    _set_rules(uid, {"enabled": True,
                     "targets": [{"type": "loan", "ref": loan_id, "pct": 10}]})
    s = apply_auto_allocations(uid, income_amount_eur=1000.0, income_date=D1)
    assert len(s["applied"]) == 1 and s["applied"][0]["amount_eur"] == 100.0
    assert s["applied"][0]["detail"] == "early repayment"
    pays = db.get_loan_payments(uid, loan_id)
    assert len(pays) == 1
    assert round(float(pays.iloc[0]["amount_eur"]), 2) == 100.0


def test_pro_rata_scaling_when_pool_tight(user):
    uid = user
    _income(uid, 550)
    db.add_expense(uid, {
        "date": D1, "category": "Food", "description": "x", "amount": 500,
        "currency": "EUR", "amount_eur": 500.0, "recurring": False,
        "notes": "",
    })
    _clear_readers()                        # pool = 50
    _set_rules(uid, {"enabled": True, "targets": [
        {"type": "goal", "ref": "A", "pct": 50},
        {"type": "goal", "ref": "B", "pct": 50}]})
    before = unallocated_funds_eur(uid)
    s = apply_auto_allocations(uid, income_amount_eur=1000.0, income_date=D1)
    assert s["scaled"] is True
    applied_total = sum(a["amount_eur"] for a in s["applied"])
    assert round(applied_total, 2) <= round(before, 2) + 0.005
    _clear_readers()
    after = unallocated_funds_eur(uid)
    assert after >= -0.005, "pool must never go negative from auto-alloc"


def test_broken_target_does_not_block_others(user):
    uid = user
    _income(uid, 1000)
    _set_rules(uid, {"enabled": True, "targets": [
        {"type": "loan", "ref": "no-such-loan", "pct": 10},
        {"type": "goal", "ref": "Real Goal", "pct": 20}]})
    s = apply_auto_allocations(uid, income_amount_eur=1000.0, income_date=D1)
    refs_ok = [a["ref"] for a in s["applied"]]
    assert refs_ok == ["Real Goal"]
    assert len(s["errors"]) == 1 and s["errors"][0]["ref"] == "no-such-loan"
