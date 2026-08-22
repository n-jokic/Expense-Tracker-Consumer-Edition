"""#24 — the allocation donut's slices must reconcile with the canonical
unallocated breakdown: slice sum == savings + term + holdings allocations."""
from datetime import date

import pytest

from auth import hash_password
from services.finance_queries import (allocation_donut_slices,
                                      unallocated_breakdown)
from db import (add_expense, add_holding, add_income, add_savings,
                add_savings_account, create_user, delete_user_account,
                get_user_by_username, init_db, username_exists)

U = "donut_user"
E = "donut@example.com"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Donut Tester")
    yield uid
    delete_user_account(uid)


def _seed(uid):
    add_income(uid, {"date": date(2026, 1, 1), "source": "Salary",
                     "income_type": "Salary", "budgeted": 3000.0,
                     "budgeted_eur": 3000.0, "actual": 3000.0,
                     "actual_eur": 3000.0, "currency": "EUR", "notes": ""})
    # allocated across all three components
    add_savings(uid, {"date": date(2026, 1, 2), "goal_name": "Emergency fund",
                      "target_eur": 500.0, "deposited": 200.0,
                      "currency": "EUR", "deposited_eur": 200.0,
                      "interest_rate": 0.0, "balance_eur": 0.0, "notes": ""})
    add_savings(uid, {"date": date(2026, 1, 2), "goal_name": "Bike",
                      "target_eur": 100.0, "deposited": 0.0,
                      "currency": "EUR", "deposited_eur": 0.0,
                      "interest_rate": 0.0, "balance_eur": 0.0,
                      "notes": ""})                    # empty -> no wedge
    add_savings_account(uid, {"goal_name": "", "name": "Term 1y",
                              "amount": 150.0, "currency": "EUR",
                              "amount_eur": 150.0, "annual_rate": 2.0,
                              "start_date": date(2026, 1, 1),
                              "maturity_date": date(2027, 1, 1),
                              "status": "active", "notes": ""})
    add_holding(uid, {"symbol": "VWCE.DE", "name": "Vanguard All-World",
                      "quantity": 2.0, "currency": "EUR", "cost_total": 100.0,
                      "cost_eur": 100.0, "last_price": 55.0,
                      "last_price_date": date(2026, 1, 3)})
    # and something spent so the numbers are not trivially the whole income
    add_expense(uid, {"date": date(2026, 1, 5), "category": "Groceries",
                      "description": "Lidl", "amount": 50.0,
                      "currency": "EUR", "amount_eur": 50.0, "notes": ""})


def test_slice_sum_equals_allocated_parts(user):
    _seed(user)
    slices = allocation_donut_slices(user)
    assert slices, "seeded user must have visible wedges"
    labels = [lbl for lbl, _ in slices]
    assert any("Emergency fund" in lbl for lbl in labels)
    assert not any("Bike" in lbl for lbl in labels), \
        "zero-balance goals must not render phantom wedges"
    b = unallocated_breakdown(user)
    allocated = (b["savings_allocations_eur"] + b["term_allocations_eur"]
                 + b["holdings_allocations_eur"])
    assert round(sum(v for _, v in slices), 2) == round(allocated, 2)


def test_empty_wallet_has_no_slices(user):
    assert allocation_donut_slices(user) == []
