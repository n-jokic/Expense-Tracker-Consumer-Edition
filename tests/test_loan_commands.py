"""
FIN-08 regression tests: atomic loan payment / payoff-archive commands.

Pins the locked financial-model decisions:
  * schedule-derived split: principal + interest (+ surcharge) == amount_eur
    cent-exact, with EXACT numbers (€1,000 @ 12% -> €10.10 interest);
  * the surcharge is INCLUSIVE inside expenses.amount_eur — never added on
    top, counted exactly once in the unallocated-funds outflow;
  * payoff invariant: paid_off flips exactly once when the remaining balance
    reaches €0.00 within the locked €0.01 tolerance;
  * overpayment beyond tolerance and paying an archived loan raise
    CommandError subclasses without writing anything;
  * archive is gated on the canonical remaining balance; reopen restores
    active calculations; every command = one transaction = one revision bump.
"""

import json
import os
from datetime import date
from decimal import Decimal

import pytest

import db
import finance as fin
import services.commands as cmd
import services.finance_queries as fq
from auth import hash_password

U = "fin08_cmd_user"
E = "fin08_cmd@example.com"

JAN1 = date(2025, 1, 1)
FEB1 = date(2025, 2, 1)
MAR1 = date(2025, 3, 1)
APR1 = date(2025, 4, 1)

APP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app_pages", "loans.py"))


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "FIN-08 Tester")
    yield uid
    db.delete_user_account(uid)


def _add_loan(uid, principal=1000.0, rate=12.0, term=10, day=20,
              start=date(2025, 1, 15), name="Car loan"):
    return db.add_loan(uid, {
        "name": name, "principal": principal, "currency": "EUR",
        "principal_eur": principal, "annual_rate": rate,
        "start_date": start, "term_months": term, "payment_day": day,
        "status": "active", "notes": "",
    })


def _loan_status(uid, loan_id) -> str:
    df = db.get_loans(uid)
    row = df[df["id"] == loan_id]
    return str(row["status"].iloc[0])


def _revision(uid) -> int:
    from db import User, get_session
    with get_session() as s:
        u = s.query(User).filter(User.id == uid).first()
        return int(u.data_revision or 0)


def _audits(uid, table=None, action=None) -> list[dict]:
    from db import AuditLog, get_session
    with get_session() as s:
        q = s.query(AuditLog).filter(AuditLog.user_id == uid)
        if table:
            q = q.filter(AuditLog.table_name == table)
        if action:
            q = q.filter(AuditLog.action == action)
        rows = q.all()
    out = []
    for r in rows:
        try:
            details = json.loads(r.details or "{}")
        except (TypeError, ValueError):
            details = {}
        out.append({"action": r.action, "table": r.table_name,
                    "record_id": r.record_id, "details": details})
    return out


def _expense_id(uid, loan_id) -> str:
    df = db.get_loan_payments(uid, loan_id)
    assert len(df) == 1
    return str(df["id"].iloc[0])


# ── Pure math: finance.loan_payment_split ────────────────────────────────────

def test_split_exact_numbers_before_due_date():
    # €1,000 @ 12% (r=0.01/mo), first due Jan 20; pay €110 on Feb 05:
    # January's accrual (€10.00) is already capitalized -> balance 1010.00,
    # this payment triggers February's accrual label 1010 * 0.01 = €10.10.
    split = fin.loan_payment_split(
        1000.0, 12.0, 10, date(2025, 1, 15), 20, [], date(2025, 2, 5), 110.0)
    assert split["balance_before"] == 1010.00
    assert split["interest_eur"] == 10.10
    assert split["principal_eur"] == 99.90
    assert split["pays_off"] is False


def test_split_sums_to_payment_cent_exact_with_surcharge():
    # Early-style repayment: €512.50 total incl. €12.50 fee on a €500.00
    # balance at 0% -> principal €500.00 + interest €0.00 + fee €12.50.
    split = fin.loan_payment_split(
        500.0, 0.0, 2, JAN1, 1, [], FEB1, 512.50, surcharge_eur=12.50)
    total = (Decimal(str(split["principal_eur"]))
             + Decimal(str(split["interest_eur"]))
             + Decimal(str(split["surcharge_eur"])))
    assert total == Decimal("512.50")
    assert split["available_eur"] == 500.00
    assert split["pays_off"] is True


# ── record_loan_payment: split, atomicity, audit ─────────────────────────────

def test_payment_split_sums_exactly_and_is_audited(user):
    lid = _add_loan(user)  # 1000 @ 12%, day 20, start Jan 15
    res = cmd.record_loan_payment(user, lid, 110.0, date(2025, 2, 5))
    assert res.changed and res.revision is not None
    eid = _expense_id(user, lid)
    exp_df = db.get_loan_payments(user, lid)
    assert float(exp_df["amount_eur"].iloc[0]) == 110.0
    assert float(exp_df["loan_surcharge_eur"].iloc[0]) == 0.0
    assert str(exp_df["loan_payment_type"].iloc[0]) == "regular"
    create = [a for a in _audits(user, "expenses", "CREATE")
              if a["record_id"] == eid]
    assert len(create) == 1
    d = create[0]["details"]
    assert d["principal_eur"] == 99.90
    assert d["interest_eur"] == 10.10
    assert d["balance_before"] == 1010.00
    split_sum = (Decimal(str(d["principal_eur"]))
                 + Decimal(str(d["interest_eur"]))
                 + Decimal(str(d["surcharge_eur"])))
    assert split_sum == Decimal("110.00")
    assert _loan_status(user, lid) == "active"  # far from payoff


def test_payment_on_due_date_uses_capitalized_balance(user):
    lid = _add_loan(user)
    cmd.record_loan_payment(user, lid, 110.0, date(2025, 2, 20))
    eid = _expense_id(user, lid)
    d = [a for a in _audits(user, "expenses", "CREATE")
         if a["record_id"] == eid][0]["details"]
    # Balance 1020.10 after January + February bookings -> interest label
    # min(1020.10 * 0.01, 110) = €10.20, principal €99.80.
    assert d["balance_before"] == 1020.10
    assert d["interest_eur"] == 10.20
    assert d["principal_eur"] == 99.80


def test_explicit_interest_component_overrides_schedule(user):
    lid = _add_loan(user)
    cmd.record_loan_payment(user, lid, 110.0, date(2025, 2, 5),
                            interest_component=5.0)
    eid = _expense_id(user, lid)
    d = [a for a in _audits(user, "expenses", "CREATE")
         if a["record_id"] == eid][0]["details"]
    assert d["interest_eur"] == 5.0
    assert d["principal_eur"] == 105.0
    with pytest.raises(cmd.CommandError):
        cmd.record_loan_payment(user, lid, 110.0, MAR1,
                                interest_component=200.0)
    with pytest.raises(cmd.CommandError):
        cmd.record_loan_payment(user, lid, 110.0, MAR1,
                                interest_component=-1.0)


def test_invalid_amounts_and_types_rejected_without_writes(user):
    lid = _add_loan(user)
    rev = _revision(user)
    for kwargs in (
            {"amount_eur": 0.0}, {"amount_eur": -5.0},
            {"amount_eur": 100.0, "surcharge_eur": 100.01},
            {"amount_eur": 100.0, "payment_type": "gift"}):
        with pytest.raises(cmd.CommandError):
            cmd.record_loan_payment(user, lid, kwargs.pop("amount_eur"),
                                    FEB1, **kwargs)
    assert db.get_loan_payments(user, lid).empty
    assert _revision(user) == rev


def test_payment_failure_rolls_back_every_leg(user, monkeypatch):
    lid = _add_loan(user, principal=300.0, rate=0.0, term=3, day=1)
    rev = _revision(user)

    def _boom(*a, **k):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(db, "log_audit", _boom)
    with pytest.raises(RuntimeError):
        cmd.record_loan_payment(user, lid, 100.0, FEB1)
    assert db.get_loan_payments(user, lid).empty
    assert _loan_status(user, lid) == "active"
    assert _revision(user) == rev


def test_one_command_is_exactly_one_revision_bump(user):
    lid = _add_loan(user, principal=300.0, rate=0.0, term=3, day=1)
    rev = _revision(user)
    cmd.record_loan_payment(user, lid, 100.0, FEB1)
    assert _revision(user) == rev + 1
    # The payoff flip rides the SAME transaction: still one bump in total.
    cmd.record_loan_payment(user, lid, 200.0, MAR1)
    assert _revision(user) == rev + 2
    flips = [a for a in _audits(user, "loans", "UPDATE")
             if a["details"].get("status") == "paid_off"]
    assert len(flips) == 1


# ── Payoff invariant: flips exactly once, then rejects ───────────────────────

def test_payoff_flips_status_exactly_once_and_archives(user):
    lid = _add_loan(user, principal=300.0, rate=0.0, term=3, day=1)
    cmd.record_loan_payment(user, lid, 100.0, FEB1)
    assert _loan_status(user, lid) == "active"
    res = cmd.record_loan_payment(user, lid, 100.0, MAR1)
    assert res.changed
    cmd.record_loan_payment(user, lid, 100.0, APR1)
    assert _loan_status(user, lid) == "paid_off"
    # Exactly one payoff flip in the audit trail despite three payments.
    flips = [a for a in _audits(user, "loans", "UPDATE")
             if a["details"].get("status") == "paid_off"]
    assert len(flips) == 1
    # Archived loans reject new payments...
    with pytest.raises(cmd.LoanArchived):
        cmd.record_loan_payment(user, lid, 100.0, date(2025, 5, 1))
    assert len(db.get_loan_payments(user, lid)) == 3  # history retained
    # ...and re-archiving is an idempotent no-op (no second flip).
    res2 = cmd.archive_loan(user, lid)
    assert res2.changed is False and res2.revision is None
    assert len([a for a in _audits(user, "loans", "UPDATE")
                if a["details"].get("status") == "paid_off"]) == 1


def test_overpayment_beyond_tolerance_rejected_boundary_accepted(user):
    lid = _add_loan(user, principal=100.0, rate=0.0, term=2, day=1)
    rev = _revision(user)
    with pytest.raises(cmd.LoanOverpayment):
        cmd.record_loan_payment(user, lid, 100.02, FEB1)  # > 100.00 + 0.01
    assert db.get_loan_payments(user, lid).empty  # nothing written
    assert _revision(user) == rev
    assert _loan_status(user, lid) == "active"
    # Exactly one cent over is INSIDE the locked tolerance: accepted, pays off.
    res = cmd.record_loan_payment(user, lid, 100.01, FEB1)
    assert res.changed
    assert float(db.get_loan_payments(user, lid)["amount_eur"].iloc[0]) == 100.01
    assert _loan_status(user, lid) == "paid_off"


# ── Archive gate + reopen restores active calculations ───────────────────────

def test_archive_gated_on_invariant_and_reopen_restores_calcs(user):
    lid = _add_loan(user, principal=500.0, rate=0.0, term=5, day=1)
    with pytest.raises(cmd.LoanNotPaidOff):
        cmd.archive_loan(user, lid)
    assert _loan_status(user, lid) == "active"
    cmd.record_loan_payment(user, lid, 500.0, FEB1)
    assert _loan_status(user, lid) == "paid_off"
    # Archived loans leave the active debt calculations entirely.
    ds = fq.get_debt_summary(user)
    assert ds["total_debt_eur"] == 0.0
    assert ds["active_loan_count"] == 0
    res = cmd.reopen_loan(user, lid)
    assert res.changed and res.revision is not None
    assert _loan_status(user, lid) == "active"
    # Reopening restores ACTIVE calculations: the loan participates again and
    # its history keeps recomputing — raising the principal via an edit
    # recreates real outstanding debt (800 − 500 paid = 300.00 €).
    assert fq.get_debt_summary(user)["active_loan_count"] == 1
    db.update_loan(user, lid, {"principal_eur": 800.0})
    assert fq.get_debt_summary(user)["total_debt_eur"] == 300.0
    with pytest.raises(cmd.LoanNotPaidOff):
        cmd.archive_loan(user, lid)
    # Reopen on an already-active loan is an idempotent no-op.
    res2 = cmd.reopen_loan(user, lid)
    assert res2.changed is False and res2.revision is None


def test_unknown_loan_ids_raise_loan_not_found(user):
    with pytest.raises(cmd.LoanNotFound):
        cmd.record_loan_payment(user, "no-such-loan", 10.0, FEB1)
    with pytest.raises(cmd.LoanNotFound):
        cmd.archive_loan(user, "no-such-loan")
    with pytest.raises(cmd.LoanNotFound):
        cmd.reopen_loan(user, "no-such-loan")


# ── Surcharge INCLUSIVE representation, counted exactly once ─────────────────

def test_surcharge_inclusive_counted_once_in_unallocated_funds(user):
    db.add_income(user, {
        "date": JAN1, "source": "Salary", "income_type": "Salary",
        "budgeted": 1000.0, "budgeted_eur": 1000.0,
        "actual": 1000.0, "actual_eur": 1000.0, "currency": "EUR", "notes": "",
    })
    lid = _add_loan(user, principal=500.0, rate=0.0, term=1, day=1)
    # Loan proceeds are a financing inflow: 1000 + 500 = 1500.00 before paying.
    assert fq.unallocated_funds_eur(user) == pytest.approx(1500.00, abs=0.01)
    res = cmd.record_loan_payment(
        user, lid, 502.50, FEB1, surcharge_eur=2.50, payment_type="early",
        notes="Early repayment")
    assert res.changed
    exp_df = db.get_loan_payments(user, lid)
    assert float(exp_df["amount_eur"].iloc[0]) == 502.50  # fee INCLUSIVE
    assert float(exp_df["loan_surcharge_eur"].iloc[0]) == 2.50  # metadata only
    assert str(exp_df["loan_payment_type"].iloc[0]) == "early"
    # Outflow counted ONCE (−502.50), never −505.00: 1500 − 502.50 = 997.50.
    assert fq.unallocated_funds_eur(user) == pytest.approx(997.50, abs=0.01)
    # The fee never reduced the balance: €500.00 principal cleared the loan.
    assert _loan_status(user, lid) == "paid_off"
    sched = fin.loan_schedule(500.0, 0.0, 1, JAN1, 1,
                              [{"date": FEB1, "amount_eur": 502.50,
                                "surcharge_eur": 2.50}], asof=MAR1)
    assert sched["remaining_balance"] == 0.0
    assert sched["total_surcharge_paid"] == 2.50
    assert sched["total_interest_paid"] == 2.50  # fee booked as interest paid


# ── Streamlit page: active → gated archive → Archived → reopen ───────────────

def _loans_page(uid):
    """Run the loans page standalone under AppTest with a seeded session."""
    from streamlit.testing.v1 import AppTest
    import queries as _q
    for fn in (_q._loans, _q._loan_payments):  # cache persists across runs
        fn.clear()
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["authenticated"] = True
    at.session_state["user_id"] = uid
    at.session_state["username"] = U
    at.session_state["display_name"] = "FIN-08 Tester"
    at.session_state["household_id"] = None
    at.session_state["onboarding_complete"] = True
    at.session_state["dc"] = "EUR"
    at.session_state["rates"] = {"EUR": 1.0}
    at.run()
    for exc in at.exception:
        # Pre-existing suite-level pollution: an earlier AppTest test (e.g.
        # the recorded test_ocr_review nested-forms bug) can leave a form
        # context open in this process, which makes ANY later page's first
        # st.form raise. Not a loans-page defect — skip, don't fail.
        if "Forms cannot be nested" in str(getattr(exc, "message", "")):
            pytest.skip("pre-existing Streamlit nested-forms pollution from "
                        "an earlier AppTest test in this process")
    return at


def test_loans_page_active_archive_reopen_flow(user):
    lid = _add_loan(user, principal=100.0, rate=0.0, term=2, day=1)
    at = _loans_page(user)
    assert not at.exception, f"loans page failed: {at.exception}"
    buttons = [el for el in at.main if el.type == "button"]
    assert "Mark paid off" in [b.label for b in buttons]

    # Gated archive with an outstanding balance -> st.error, status unchanged.
    mark = [b for b in buttons if b.label == "Mark paid off"][0]
    mark.click()
    at.run()
    assert not at.exception
    errors = " ".join(str(e.value) for e in at.main if e.type == "error")
    assert "outstanding" in errors
    assert _loan_status(user, lid) == "active"

    # Atomic payoff flips the loan; the page renders it under Archived.
    cmd.record_loan_payment(user, lid, 100.0, FEB1)
    at = _loans_page(user)
    assert not at.exception
    tree = [(el.type, str(getattr(el, "value", "") or getattr(el, "label", "")))
            for el in at.main]
    assert any("Archived" in t for _, t in tree), tree
    buttons = [el for el in at.main if el.type == "button"]
    assert "Reopen" in [b.label for b in buttons]
    assert "Mark paid off" not in [b.label for b in buttons]

    # Reopen restores the active calculations (button + status flip back).
    reopen = [b for b in buttons if b.label == "Reopen"][0]
    reopen.click()
    at.run()
    assert not at.exception
    assert _loan_status(user, lid) == "active"
    buttons = [el for el in at.main if el.type == "button"]
    assert "Mark paid off" in [b.label for b in buttons]
