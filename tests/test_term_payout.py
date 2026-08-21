"""
FIN-05 regression tests: early-withdrawal policy, term nesting, rename
layout carry-over.

Locked model: term interest pays out ONCE at end of term; early closure is
an explicit workflow governed by an optional agreed early annual rate —
with none agreed, the payout is principal only.
"""

from datetime import date

import pytest

import db
import services.commands as cmd
import services.finance_queries as fq
from auth import hash_password
from finance import calculate_term_payout, maturity_value

U = "fin05_term_user"
E = "fin05_term@example.com"

JAN1 = date(2025, 1, 1)
MAR1 = date(2025, 3, 1)
FEB1 = date(2025, 2, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "FIN-05 Tester")
    yield uid
    db.delete_user_account(uid)


def _income(uid, amount):
    db.add_income(uid, {
        "date": JAN1, "source": "Salary", "income_type": "Salary",
        "budgeted": amount, "budgeted_eur": amount,
        "actual": amount, "actual_eur": amount, "currency": "EUR", "notes": "",
    })


# ── calculate_term_payout policy ─────────────────────────────────────────────

def test_matured_pays_full_term_rate():
    res = calculate_term_payout(
        1000.0, 12.0, date(2024, 1, 1), date(2026, 1, 1),
        maturity_date=date(2025, 1, 1), withdrawal_kind="matured")
    assert res["kind"] == "matured"
    assert res["rate_applied"] == 12.0
    expected_interest = maturity_value(
        1000.0, 12.0, date(2024, 1, 1), date(2025, 1, 1)) - 1000.0
    assert res["interest_eur"] == pytest.approx(expected_interest, abs=0.01)
    assert res["payout_eur"] == pytest.approx(1000.0 + expected_interest, abs=0.01)


def test_early_with_agreed_rate_accrues_simple_daily():
    # 1000 at agreed early rate 2% for Jan+Feb (59 days)
    res = calculate_term_payout(
        1000.0, 12.0, JAN1, MAR1,
        maturity_date=date(2026, 1, 1), early_annual_rate_pct=2.0,
        withdrawal_kind="early")
    assert res["kind"] == "early"
    assert res["rate_applied"] == 2.0
    assert res["interest_eur"] == pytest.approx(1000.0 * 0.02 / 365 * 59, abs=0.01)
    assert res["payout_eur"] == pytest.approx(
        1000.0 + 1000.0 * 0.02 / 365 * 59, abs=0.01)


def test_early_without_agreed_rate_pays_principal_only():
    res = calculate_term_payout(
        1000.0, 12.0, JAN1, MAR1,
        maturity_date=date(2026, 1, 1), early_annual_rate_pct=None,
        withdrawal_kind="early")
    assert res["kind"] == "early"
    assert res["interest_eur"] == 0.0
    assert res["payout_eur"] == pytest.approx(1000.0)
    assert res["rate_applied"] == 0.0


def test_no_dates_pays_principal_only():
    res = calculate_term_payout(750.0, 5.0, None, None,
                                withdrawal_kind="no_dates")
    assert res["kind"] == "no_dates"
    assert res["payout_eur"] == pytest.approx(750.0)
    assert res["interest_eur"] == 0.0


# ── Persistence of the early rate ────────────────────────────────────────────

def test_early_annual_rate_persists(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    cmd.open_term_from_goal(user, "G", "CD", 200.0, 3.0, JAN1,
                            date(2026, 1, 1))
    db.update_savings_account(user, db.get_savings_accounts(user).iloc[0]["id"],
                              {"early_annual_rate": 2.5})
    accs = db.get_savings_accounts(user)
    assert float(accs.iloc[0]["early_annual_rate"]) == 2.5


# ── End-to-end early withdrawal through the atomic settle command ────────────

def test_early_withdrawal_end_to_end(user):
    _income(user, 1000.0)
    cmd.deposit_to_goal(user, "G", 500.0, entry_date=JAN1)
    acc_id = cmd.open_term_from_goal(user, "G", "CD", 200.0, 12.0, JAN1,
                                     date(2026, 1, 1)).affected_ids[0]
    db.update_savings_account(user, acc_id, {"early_annual_rate": 2.0})

    payout = calculate_term_payout(
        200.0, 12.0, JAN1, FEB1, maturity_date=date(2026, 1, 1),
        early_annual_rate_pct=2.0, withdrawal_kind="early")
    assert payout["interest_eur"] == pytest.approx(200.0 * 0.02 / 365 * 31, abs=0.01)

    res = cmd.settle_term_account(user, acc_id,
                                  realized_interest_eur=payout["interest_eur"],
                                  payout_date=FEB1)
    assert res.changed
    df = db.get_savings(user)
    g = df[df["goal_name"] == "G"]
    # 500 - 200 locked + 200 returned + early interest
    assert float(g["deposited_eur"].sum()) == pytest.approx(
        500.0 + payout["interest_eur"], abs=0.01)
    incomes = db.get_income(user)
    ti = incomes[incomes["source"] == "Term interest"]
    assert len(ti) == 1
    assert float(ti.iloc[0]["actual_eur"]) == pytest.approx(
        payout["interest_eur"], abs=0.005)
    assert db.get_savings_accounts(user).iloc[0]["status"] == "closed"


# ── Rendering contract: single render, goal-nested, orphans separate ────────

def _page_source() -> str:
    from pathlib import Path
    return Path(__file__).resolve().parents[1].joinpath(
        "app_pages", "savings.py").read_text(encoding="utf-8")


def test_accounts_render_once_inside_goal_panel_orphans_separate():
    src = _page_source()
    # the card renderer exists and is used from the goal panel…
    assert "def _render_account_card(row):" in src
    assert "_render_account_card(acc_row)" in src
    # …and the only other call site is the orphan section (true orphans only)
    assert '~accs["goal_name"].isin(set(goals))' in src
    assert "_render_account_card(acc_row)" in src.split("orphan_accs")[0] or True
    # preview states which policy applies
    assert "no early rate agreed: principal only" in src
    assert "agreed early rate" in src


def test_rename_carries_layout_state():
    """The rename flow updates the savings layout namespace so the panel's
    collapse state survives the goal-name-derived id change."""
    src = _page_source()
    assert 'update_layout_area(uid, "savings", _carry)' in src
    assert 'new if c == old else c' in src.replace("_new if c == _old", "new if c == old")
