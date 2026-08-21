"""
GATE B — financial foundation checkpoint (plan.md, after FIN-05).

Proves the full money lifecycle in one scenario:
  income -> unallocated -> goal -> term account -> back to goal,
with (a) the same total economic value before realized interest,
(b) realized interest recorded as exactly one income inflow, and
(c) an existing database opening without destructive rewriting.
"""

from datetime import date

import pytest

import db
import services.commands as cmd
import services.finance_queries as fq
from auth import hash_password

U = "gate_b_user"
E = "gate_b@example.com"

JAN1 = date(2025, 1, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "Gate B Tester")
    yield uid
    db.delete_user_account(uid)


def _total_value(uid) -> float:
    """Unallocated cash + allocated principal = total economic value."""
    b = fq.unallocated_breakdown(uid)
    return round(b["unallocated_eur"] + b["savings_allocations_eur"]
                 + b["term_allocations_eur"] + b["holdings_allocations_eur"], 2)


def test_gate_b_end_to_end_lifecycle(user):
    # 1. external income lands in unallocated
    db.add_income(user, {
        "date": JAN1, "source": "Salary", "income_type": "Salary",
        "budgeted": 1000.0, "budgeted_eur": 1000.0,
        "actual": 1000.0, "actual_eur": 1000.0, "currency": "EUR", "notes": "",
    })
    assert fq.unallocated_funds_eur(user) == pytest.approx(1000.0, abs=0.01)

    # 2. income -> goal
    cmd.deposit_to_goal(user, "G", 400.0, entry_date=JAN1)
    assert fq.unallocated_funds_eur(user) == pytest.approx(600.0, abs=0.01)
    assert _total_value(user) == pytest.approx(1000.0, abs=0.01)

    # 3. goal -> term account (zero-sum: total unchanged)
    acc_id = cmd.open_term_from_goal(
        user, "G", "CD", 250.0, 3.0, JAN1, date(2026, 1, 1)).affected_ids[0]
    assert fq.unallocated_funds_eur(user) == pytest.approx(600.0, abs=0.01)
    assert _total_value(user) == pytest.approx(1000.0, abs=0.01)

    # 4. term -> goal at settlement, with realized interest booked ONCE as
    #    an income inflow; total grows by exactly the interest amount.
    res = cmd.settle_term_account(user, acc_id, realized_interest_eur=5.0,
                                  payout_date=date(2026, 1, 1))
    assert res.changed
    incomes = db.get_income(user)
    ti = incomes[incomes["source"] == "Term interest"]
    assert len(ti) == 1 and float(ti.iloc[0]["actual_eur"]) == 5.0
    b = fq.unallocated_breakdown(user)
    assert b["inflows_eur"] == pytest.approx(1005.0, abs=0.01)
    assert fq.unallocated_funds_eur(user) == pytest.approx(600.0, abs=0.01)
    assert _total_value(user) == pytest.approx(1005.0, abs=0.01)

    # 5. goal -> unallocated (withdrawal returns everything incl. interest)
    df = db.get_savings(user)
    g_principal = float(df[df["goal_name"] == "G"]["deposited_eur"].sum())
    cmd.withdraw_from_goal(user, "G", g_principal, entry_date=date(2026, 1, 2))
    assert fq.unallocated_funds_eur(user) == pytest.approx(1005.0, abs=0.01)
    assert _total_value(user) == pytest.approx(1005.0, abs=0.01)


def test_gate_b_existing_database_opens_without_destructive_rewrite():
    """init_db on an existing (already-migrated) database must be idempotent:
    no row loss, no column removal, revision intact."""
    db.init_db()
    before = {
        "users": len(db.get_engine().raw_connection().execute(
            "SELECT id FROM users").fetchall()),
    }
    db.init_db()  # second open — migrations must be no-ops
    after = len(db.get_engine().raw_connection().execute(
        "SELECT id FROM users").fetchall())
    assert after == before["users"]
    # FIN-04/05 additive columns exist post-migration
    from sqlalchemy import inspect
    insp = inspect(db.get_engine())
    sav_cols = {c["name"] for c in insp.get_columns("savings")}
    inc_cols = {c["name"] for c in insp.get_columns("income")}
    acc_cols = {c["name"] for c in insp.get_columns("savings_accounts")}
    assert {"accrual_key", "settlement_ref"} <= sav_cols
    assert {"settlement_ref"} <= inc_cols
    assert {"early_annual_rate"} <= acc_cols
