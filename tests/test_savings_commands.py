"""
FIN-04 regression tests: atomic zero-sum savings / term-account commands.

Pins the locked financial-model acceptances:
  * €1,000 @ 3.65% for all of January  ->  €3.10 posted interest
  * €1,000 @ 3.65%, €500 withdrawn Jan 15 -> 14×0.10 + 17×0.05 = €2.25
plus success / insufficient-funds / idempotency / rollback / delete-guard
paths for every command.
"""

from datetime import date

import pytest

import db
import services.commands as cmd
import services.finance_queries as fq
from auth import hash_password

U = "fin04_cmd_user"
E = "fin04_cmd@example.com"

JAN1 = date(2025, 1, 1)
JAN15 = date(2025, 1, 15)
JAN31 = date(2025, 1, 31)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "FIN-04 Tester")
    yield uid
    db.delete_user_account(uid)


def _income(uid, amount, d=JAN1, source="Salary"):
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


# ── Deposits / withdrawals vs the canonical pool ─────────────────────────────

def test_deposit_success_moves_pool_to_goal(user):
    _income(user, 1000.0)
    res = cmd.deposit_to_goal(user, "G", 300.0, entry_date=JAN1,
                              interest_rate=3.65)
    assert res.changed and res.revision is not None
    assert _unalloc(user) == pytest.approx(700.0, abs=0.01)
    assert _principal(user, "G") == 300.0


def test_deposit_beyond_pool_is_rejected_without_writes(user):
    _income(user, 1000.0)
    with pytest.raises(cmd.InsufficientFunds):
        cmd.deposit_to_goal(user, "G", 2000.0)
    assert _principal(user, "G") == 0.0
    assert _unalloc(user) == pytest.approx(1000.0, abs=0.01)


def test_withdraw_success_and_overdraft_rejection(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 300.0, entry_date=JAN1)
    with pytest.raises(cmd.InsufficientFunds):
        cmd.withdraw_from_goal(user, "G", 400.0)      # > principal
    assert _principal(user, "G") == 300.0
    cmd.withdraw_from_goal(user, "G", 200.0, entry_date=JAN15)
    assert _principal(user, "G") == 100.0
    assert _unalloc(user) == pytest.approx(900.0, abs=0.01)


# ── Term accounts: zero-sum open, single settlement ──────────────────────────

def test_open_term_is_zero_sum_and_debits_once(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    before = _unalloc(user)
    res = cmd.open_term_from_goal(user, "G", "CD-12", 200.0, 3.0,
                                  JAN1, date(2026, 1, 1))
    assert res.changed
    assert _unalloc(user) == pytest.approx(before, abs=0.01)      # zero-sum
    accs = db.get_savings_accounts(user)
    assert len(accs) == 1 and accs.iloc[0]["status"] == "active"
    # exactly one debit row
    df = db.get_savings(user)
    debits = df[df["deposited_eur"] < 0]
    assert len(debits) == 1 and debits.iloc[0]["deposited_eur"] == -200.0
    assert _principal(user, "G") == 300.0


def test_open_term_beyond_principal_rejected(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 100.0, entry_date=JAN1)
    with pytest.raises(cmd.InsufficientFunds):
        cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0,
                                JAN1, date(2026, 1, 1))
    assert db.get_savings_accounts(user).empty


def test_open_term_validates_dates_name_rate(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    with pytest.raises(cmd.CommandError):
        cmd.open_term_from_goal(user, "G", "", 100.0, 3.0, JAN1, date(2026, 1, 1))
    with pytest.raises(cmd.CommandError):
        cmd.open_term_from_goal(user, "G", "CD", 100.0, 3.0,
                                date(2026, 1, 1), JAN1)               # inverted
    with pytest.raises(cmd.CommandError):
        cmd.open_term_from_goal(user, "G", "CD", 100.0, 250.0,
                                JAN1, date(2026, 1, 1))               # rate


def test_settle_credits_once_books_income_once_idempotent(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    acc_id = cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0,
                                     JAN1, date(2026, 1, 1)).affected_ids[0]
    res = cmd.settle_term_account(user, acc_id, realized_interest_eur=6.15,
                                  payout_date=date(2026, 1, 1))
    assert res.changed
    # principal + interest credited exactly once each
    df = db.get_savings(user)
    credits = df[df["deposited_eur"] > 0]
    settle_credits = credits[credits["deposited_eur"].isin([200.0, 6.15])]
    assert len(settle_credits) == 2
    # 500 deposited - 200 locked + 200 returned + 6.15 interest
    assert _principal(user, "G") == pytest.approx(506.15, abs=0.01)
    incomes = db.get_income(user)
    ti = incomes[incomes["source"] == "Term interest"]
    assert len(ti) == 1 and ti.iloc[0]["actual_eur"] == 6.15
    accs = db.get_savings_accounts(user)
    assert accs.iloc[0]["status"] == "closed"
    # idempotent retry: no duplicates
    res2 = cmd.settle_term_account(user, acc_id, realized_interest_eur=6.15)
    assert res2.changed is False
    assert len(db.get_income(user)[db.get_income(user)["source"] == "Term interest"]) == 1
    assert _principal(user, "G") == pytest.approx(506.15, abs=0.01)


def test_settle_rolls_back_on_injected_failure(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    acc_id = cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0,
                                     JAN1, date(2026, 1, 1)).affected_ids[0]
    import db as db_mod
    orig = db_mod.log_audit
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 1:
            raise RuntimeError("injected audit failure")
    db_mod.log_audit = boom
    try:
        with pytest.raises(RuntimeError):
            cmd.settle_term_account(user, acc_id, realized_interest_eur=6.15)
    finally:
        db_mod.log_audit = orig
    # nothing settled: account still active, no credit/income rows
    accs = db.get_savings_accounts(user)
    assert accs.iloc[0]["status"] == "active"
    assert _principal(user, "G") == 300.0
    assert db.get_income(user)[db.get_income(user)["source"] == "Term interest"].empty


# ── Locked acceptance: daily accrual, monthly payout ─────────────────────────

def test_monthly_interest_acceptance_full_january(user):
    """€1,000 @ 3.65% for all of January -> €3.10 posted once; rerun no-op."""
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 1000.0, entry_date=JAN1, interest_rate=3.65)
    res = cmd.post_monthly_interest(user, asof=JAN31)
    assert res.changed
    assert len(res.affected_ids) == 1
    incomes = db.get_income(user)
    si = incomes[incomes["source"] == "Savings interest"]
    assert len(si) == 1 and float(si.iloc[0]["actual_eur"]) == pytest.approx(3.10, abs=0.005)
    assert _principal(user, "G") == pytest.approx(1003.10, abs=0.01)
    # rerun is a no-op (idempotent)
    res2 = cmd.post_monthly_interest(user, asof=JAN31)
    assert res2.changed is False
    incomes = db.get_income(user)
    assert len(incomes[incomes["source"] == "Savings interest"]) == 1


def test_monthly_interest_acceptance_mid_month_withdrawal(user):
    """€1,000 @ 3.65%, €500 withdrawn Jan 15 -> 14×0.10 + 17×0.05 = €2.25."""
    _income(user, 2000.0)
    cmd.deposit_to_goal(user, "G", 1000.0, entry_date=JAN1, interest_rate=3.65)
    cmd.withdraw_from_goal(user, "G", 500.0, entry_date=JAN15)
    cmd.post_monthly_interest(user, asof=JAN31)
    incomes = db.get_income(user)
    si = incomes[incomes["source"] == "Savings interest"]
    assert len(si) == 1
    assert float(si.iloc[0]["actual_eur"]) == pytest.approx(2.25, abs=0.005)


def test_posted_interest_is_spendable_principal(user):
    """After posting, the credited interest counts toward withdrawable principal."""
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 1000.0, entry_date=JAN1, interest_rate=3.65)
    cmd.post_monthly_interest(user, asof=JAN31)
    cmd.withdraw_from_goal(user, "G", 1003.10, entry_date=date(2025, 2, 1))
    assert _principal(user, "G") == pytest.approx(0.0, abs=0.01)


# ── Deletion guards: settle-or-block ─────────────────────────────────────────

def test_delete_goal_blocked_while_nonempty(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 300.0, entry_date=JAN1)
    with pytest.raises(cmd.CommandError):
        cmd.soft_delete_goal_checked(user, "G")
    # empty goal deletes fine
    cmd.withdraw_from_goal(user, "G", 300.0, entry_date=JAN15)
    res = cmd.soft_delete_goal_checked(user, "G")
    assert res.changed


def test_delete_goal_blocked_with_active_terms(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0, JAN1, date(2026, 1, 1))
    cmd.withdraw_from_goal(user, "G", 300.0, entry_date=JAN15)   # principal 0
    with pytest.raises(cmd.CommandError):
        cmd.soft_delete_goal_checked(user, "G")                  # active term


def test_delete_account_blocked_active_allowed_closed(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    acc_id = cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0,
                                     JAN1, date(2026, 1, 1)).affected_ids[0]
    with pytest.raises(cmd.CommandError):
        cmd.soft_delete_account_checked(user, acc_id)
    cmd.settle_term_account(user, acc_id, realized_interest_eur=0.0)
    res = cmd.soft_delete_account_checked(user, acc_id)
    assert res.changed
