"""
D1 (item 2): the "Where your money goes" dashboard panel. Pins:
  * headline Unallocated now reconciles with the FIN-01 service value;
  * per-goal breakdown rows render from get_savings_summary;
  * upcoming-bills reserve counts only active templates still unlogged
    this month (rec_template_id month-gate semantics);
  * planning layers are visually labelled outside the balance rule;
  * the panel collapses and persists its state like every other panel.
"""

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest
import os

import db
import queries as q
from services.finance_queries import unallocated_funds_eur
from ui.formatting import fmt
from auth import hash_password

U = "d1_allocation_user"
E = "d1_allocation@example.com"
APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
APP_DIR = os.path.dirname(APP)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "D1 Tester")
    yield uid
    db.delete_user_account(uid)


@pytest.fixture()
def at(user):
    t = AppTest.from_file(APP, default_timeout=60)
    t.session_state["authenticated"] = True
    t.session_state["user_id"] = user
    t.session_state["username"] = U
    t.session_state["display_name"] = "D1"
    t.session_state["household_id"] = None
    t.session_state["onboarding_complete"] = True
    t.session_state["onboarding_step"] = 0
    t.run()
    t.switch_page(os.path.join(APP_DIR, "app_pages", "dashboard.py"))
    t.run()
    assert not t.exception, t.exception
    return t


def _seed(user):
    today = date.today()
    db.add_income(user, {
        "date": today, "source": "Salary", "income_type": "Salary",
        "budgeted": 3000, "budgeted_eur": 3000.0,
        "actual": 3000, "actual_eur": 3000.0, "currency": "EUR", "notes": "",
    })
    db.add_expense(user, {
        "date": today, "category": "Food", "description": "Groceries",
        "amount": 500, "currency": "EUR", "amount_eur": 500.0,
        "recurring": False, "notes": "",
    })
    from services.commands import deposit_to_goal
    deposit_to_goal(user, goal_name="Holiday", amount_eur=400.0,
                    entry_date=today)
    # Bills: one still unpaid this month (reserve), one already logged.
    unpaid_id = db.add_recurring(user, {
        "category": "Housing", "description": "Rent", "amount": 120.0,
        "currency": "EUR", "amount_eur": 120.0, "due_day": 28,
        "start_month": f"{today.year:04d}-{today.month:02d}",
        "notes": "", "active": True,
    })
    logged_id = db.add_recurring(user, {
        "category": "Utilities", "description": "Internet", "amount": 80.0,
        "currency": "EUR", "amount_eur": 80.0, "due_day": 5,
        "start_month": f"{today.year:04d}-{today.month:02d}",
        "notes": "", "active": True,
    })
    db.add_expense(user, {
        "date": today, "category": "Utilities", "description": "Internet",
        "amount": 80, "currency": "EUR", "amount_eur": 80.0,
        "recurring": True, "rec_template_id": logged_id, "notes": "",
    })
    # Direct db writes bypass the pages' invalidation, and user ids can be
    # REUSED after deletes — so any process-global cache entries for this
    # numeric id must be dropped before the page renders. NOTE: the public
    # q.* names are thin wrappers; only the underscore readers are cached.
    for _name in ("_expenses", "_recurring", "_savings", "_savings_accounts",
                  "_budgets", "_income"):
        _fn = getattr(q, _name, None)
        if _fn is not None and hasattr(_fn, "clear"):
            try:
                _fn.clear()
            except Exception:
                pass
    return unpaid_id


def test_headline_reconciles_with_fin01_service(user, at):
    _seed(user)
    at.run()
    assert not at.exception, at.exception
    expected = unallocated_funds_eur(user)
    # 3000 in - 500 groceries - 80 logged internet bill - 400 goal deposit.
    assert abs(expected - 2020.0) < 0.01, expected
    m = [x for x in at.metric if x.label == "Unallocated now"]
    assert m, "Unallocated now metric missing"
    want = fmt(expected, "EUR", {})
    assert want in str(m[0].value) or str(expected) in str(m[0].value), (
        m[0].value, want)


def test_breakdown_shows_goals_and_reserve(user, at):
    _seed(user)
    at.run()
    body = "\n".join(str(x.value) for x in at.markdown)
    assert "Holiday" in body, "per-goal row missing"
    metrics = {x.label: str(x.value) for x in at.metric}
    assert "Upcoming-bills reserve" in metrics
    # Only the UNLOGGED bill counts toward the reserve.
    assert "120" in metrics["Upcoming-bills reserve"]
    assert "Category budgets" in metrics
    caps = "\n".join(str(c.value) for c in at.caption)
    assert "planning aids" in caps or "planning only" in body or any(
        "planning" in s for s in [body, caps]), "planning label missing"


def test_panel_collapse_persists(user, at):
    chev = [b for b in at.button if b.key == "panel_toggle_dash_allocation"]
    assert chev, "allocation panel toggle missing"
    chev[0].click()
    at.run()
    assert not at.exception, at.exception
    from ui.layout_state import is_collapsed
    assert is_collapsed(user, "dash_allocation", area="dashboard"), (
        "collapse did not persist")
