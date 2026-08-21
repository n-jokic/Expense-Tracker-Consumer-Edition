"""
FIN-01 regression tests: the canonical virtual unallocated-funds invariant.

Every scenario uses exact euro amounts and asserts them to the cent
(tolerance EUR_TOLERANCE = €0.01). Negative states must be returned
verbatim — no clamping anywhere.
"""

from datetime import date, timedelta

import pytest

import db
import services.finance_queries as fq
from auth import hash_password

U = "unallocated_user"
E = "unallocated@example.com"


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "Unalloc Tester")
    yield uid
    db.delete_user_account(uid)


def _income(uid, amount, d=None, source="Salary"):
    db.add_income(uid, {
        "date": d or date(2025, 1, 1), "source": source, "income_type": source,
        "budgeted": amount, "budgeted_eur": amount,
        "actual": amount, "actual_eur": amount, "currency": "EUR", "notes": "",
    })


def _expense(uid, amount, d=None, **extra):
    db.add_expense(uid, {
        "date": d or date(2025, 1, 2), "category": "Groceries", "description": "x",
        "amount": amount, "currency": "EUR", "amount_eur": amount, "notes": "",
        **extra,
    })


def _goal_deposit(uid, goal, amount, d=None):
    db.add_savings(uid, {
        "date": d or date(2025, 1, 3), "goal_name": goal, "target_eur": 0.0,
        "deposited": amount, "currency": "EUR", "deposited_eur": amount,
        "interest_rate": 0.0, "balance_eur": 0.0, "notes": "",
    })


def _term(uid, goal, amount, status="active"):
    return db.add_savings_account(uid, {
        "goal_name": goal, "name": "CD", "amount": amount, "currency": "EUR",
        "amount_eur": amount, "annual_rate": 2.0,
        "start_date": date(2025, 1, 3), "maturity_date": date(2026, 1, 3),
        "status": status, "notes": "",
    })


def _loan(uid, principal, name="Car loan"):
    return db.add_loan(uid, {
        "name": name, "principal": principal, "currency": "EUR",
        "principal_eur": principal, "annual_rate": 5.0,
        "start_date": date(2025, 1, 1), "term_months": 48, "payment_day": 1,
        "status": "active", "notes": "",
    })


def _holding(uid, cost, symbol="VOO"):
    return db.add_holding(uid, {
        "symbol": symbol, "name": "Vanguard S&P", "quantity": 2.0,
        "currency": "EUR", "cost_total": cost, "cost_eur": cost,
        "last_price": 100.0, "last_price_date": date(2025, 1, 1),
    })


def _unalloc(uid) -> float:
    return fq.unallocated_funds_eur(uid)


def _close_to(a, b):
    assert abs(a - b) <= fq.EUR_TOLERANCE, f"{a} != {b} within {fq.EUR_TOLERANCE}"


# ── Core scenarios (contract FIN-01) ─────────────────────────────────────────

def test_base_income_only(user):
    _income(user, 1000.0)
    _close_to(_unalloc(user), 1000.00)


def test_expense_delete_restore_is_deterministic(user):
    _income(user, 1000.0)
    _expense(user, 200.0)
    _close_to(_unalloc(user), 800.00)
    exp_id = db.get_expenses(user).iloc[0]["id"]
    db.soft_delete_expense(user, exp_id)
    _close_to(_unalloc(user), 1000.00)
    db.restore_expense(user, exp_id)
    _close_to(_unalloc(user), 800.00)


def test_goal_deposit_and_withdrawal(user):
    _income(user, 1000.0)
    _goal_deposit(user, "Emergency Fund", 300.0)
    _close_to(_unalloc(user), 700.00)
    b = fq.unallocated_breakdown(user)
    _close_to(b["savings_allocations_eur"], 300.00)

    _goal_deposit(user, "Emergency Fund", -100.0)
    _close_to(_unalloc(user), 800.00)
    b = fq.unallocated_breakdown(user)
    _close_to(b["savings_allocations_eur"], 200.00)


def test_term_open_from_goal_is_zero_sum(user):
    _income(user, 1000.0)
    _goal_deposit(user, "G", 300.0)
    before = _unalloc(user)

    # Both legs of "open €200 term from goal", written directly (FIN-04 will
    # make this one command; the invariant must already be indifferent).
    _goal_deposit(user, "G", -200.0)
    _term(user, "G", 200.0, status="active")
    _close_to(_unalloc(user), before)


def test_settlement_to_goal_realizes_interest_exactly_once(user):
    _income(user, 1000.0)
    _goal_deposit(user, "G", 300.0)
    acc_id = _term(user, "G", 200.0)
    _goal_deposit(user, "G", -200.0)

    # Settle: principal + 10 interest back to the goal, interest as income.
    db.update_savings_account(user, acc_id, {"status": "closed"})
    _goal_deposit(user, "G", 210.0)
    _income(user, 10.0, source="Term interest")

    _close_to(_unalloc(user), 700.00)          # 1010 inflows - 310 allocations
    incomes = db.get_income(user)
    realized = incomes[incomes["source"] == "Term interest"]
    assert len(realized) == 1                  # exactly one realized-interest row
    total_value = _unalloc(user) + fq.unallocated_breakdown(user)["savings_allocations_eur"]
    _close_to(total_value, 1010.00)            # the €10 external return is real


def test_holding_cost_counted_once_not_market_value(user):
    _income(user, 1000.0)
    h_id = _holding(user, 500.0)
    _close_to(_unalloc(user), 500.00)          # cost basis is the single representation

    # Market moves are valuation-only for the invariant.
    db.update_holding(user, h_id, {"last_price": 99999.0})
    _close_to(_unalloc(user), 500.00)

    # Mistake-correction delete releases the cost basis (documented interim rule).
    db.delete_holding(user, h_id)
    _close_to(_unalloc(user), 1000.00)


def test_loan_proceeds_and_payment_counted_once_with_inclusive_surcharge(user):
    _income(user, 1000.0)
    loan_id = _loan(user, 10000.0)
    _close_to(_unalloc(user), 11000.00)        # financing inflow

    # Early payment with a €2.5 fee: amount_eur is INCLUSIVE of the surcharge.
    _expense(user, 102.5, loan_id=loan_id, loan_payment_type="early",
             loan_surcharge_eur=2.5)
    _close_to(_unalloc(user), 11000.00 - 102.50)

    # Hard-deleting the loan removes only the financing inflow; the payment
    # expense stays an ordinary outflow (dangling loan_id is expected).
    db.delete_loan(user, loan_id)
    _close_to(_unalloc(user), 1000.00 - 102.50)


def test_negative_history_returned_verbatim(user):
    _income(user, 750.0)
    _expense(user, 1000.0)
    assert _unalloc(user) == pytest.approx(-250.00, abs=fq.EUR_TOLERANCE)
    assert _unalloc(user) < 0                  # never clamped to zero


def test_soft_deleted_rows_participate_explicitly(user):
    _income(user, 1000.0)
    _expense(user, 100.0)
    _goal_deposit(user, "G", 400.0)
    acc_id = _term(user, "G", 50.0)
    _close_to(_unalloc(user), 450.00)          # 1000 - 100 - 400 - 50

    inc_id = db.get_income(user).iloc[0]["id"]
    db.soft_delete_income(user, inc_id)
    _close_to(_unalloc(user), -550.00)
    db.restore_income(user, inc_id)

    exp_id = db.get_expenses(user).iloc[0]["id"]
    db.soft_delete_expense(user, exp_id)
    _close_to(_unalloc(user), 550.00)
    db.restore_expense(user, exp_id)

    sav_id = db.get_savings(user).iloc[0]["id"]
    db.soft_delete_savings(user, sav_id)
    _close_to(_unalloc(user), 850.00)
    db.restore_savings(user, sav_id)

    db.soft_delete_savings_account(user, acc_id)
    _close_to(_unalloc(user), 500.00)          # deleted term releases principal


def test_breakdown_reconciles(user):
    _income(user, 2000.0)
    _expense(user, 300.0)
    _goal_deposit(user, "A", 700.0)
    _term(user, "A", 200.0)
    _loan(user, 5000.0, name="Mortgage")
    _holding(user, 150.0, symbol="BTC")

    b = fq.unallocated_breakdown(user)
    lhs = b["unallocated_eur"]
    rhs = (b["inflows_eur"] + b["financing_inflows_eur"] - b["outflows_eur"]
           - (b["savings_allocations_eur"] + b["term_allocations_eur"]
              + b["holdings_allocations_eur"]))
    _close_to(lhs, rhs)
    _close_to(b["inflows_eur"], 2000.00)
    _close_to(b["financing_inflows_eur"], 5000.00)
    _close_to(b["outflows_eur"], 300.00)
    _close_to(b["savings_allocations_eur"], 700.00)
    _close_to(b["term_allocations_eur"], 200.00)
    _close_to(b["holdings_allocations_eur"], 150.00)
    _close_to(lhs, 2000 + 5000 - 300 - 1050)


def test_cross_user_isolation(user):
    db.init_db()
    other = "unalloc_other_user"
    if db.username_exists(other):
        db.delete_user_account(db.get_user_by_username(other)["id"])
    oid = db.create_user(other, "unalloc_other@example.com",
                         hash_password("test1234"), "Other")
    try:
        _income(user, 1000.0)
        _income(oid, 999999.0)
        _expense(oid, 500.0)
        _close_to(_unalloc(user), 1000.00)     # B's rows never touch A's cash
        _close_to(_unalloc(oid), 999499.00)
    finally:
        db.delete_user_account(oid)


def test_tolerance_constant_is_one_cent():
    """Contract 0.5: user-facing money tolerance is €0.01 — never smaller epsilons."""
    assert fq.EUR_TOLERANCE == 0.01
