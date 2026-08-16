"""
AppTest coverage for the Phase 3 UI work: grouped navigation, dashboard task
hub, persisted household invite code, and expense-history pagination.
"""

import os
from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

import queries as q
from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, create_household, get_household_by_member,
    add_expense, bump_data_revision,
)
from auth import hash_password

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
APP_DIR = os.path.dirname(APP_PATH)

TEST_USERNAME = "ui_test_user"
TEST_EMAIL = "ui_test@example.com"


def _clear_cached_readers():
    """st.cache_data persists across AppTest instances in one pytest process,
    and re-creating a user resets its data_revision to 0 — so a later test
    could otherwise hit a previous test's cache entry for the same
    (user_id, revision) key."""
    for fn in (q._expenses, q._income, q._savings, q._budgets, q._recurring,
               q._big_purchases, q._loans, q._loan_payments, q._holdings,
               q._holding_prices, q._audit, q._household_expenses,
               q._household_members):
        fn.clear()


@pytest.fixture()
def ui_user():
    init_db()
    _clear_cached_readers()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "UI Tester")
    bump_data_revision(uid, include_household=False)
    yield uid
    delete_user_account(uid)


def _authenticated(uid) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["authenticated"] = True
    at.session_state["user_id"] = uid
    at.session_state["username"] = TEST_USERNAME
    at.session_state["display_name"] = "UI Tester"
    at.session_state["household_id"] = None
    at.session_state["onboarding_complete"] = True
    at.session_state["onboarding_step"] = 0
    at.run()
    assert not at.exception
    return at


def _text(elements) -> str:
    return " ".join(str(getattr(el, "value", "") or "") for el in elements)


def _by_type(elements, elem_type: str):
    return [el for el in elements if el.type == elem_type]


def _main_text(at: AppTest, elem_type: str) -> str:
    return " ".join(
        str(getattr(el, "value", "") or getattr(el, "label", "") or "")
        for el in at.main if el.type == elem_type)


def test_grouped_navigation_routes_every_group(ui_user):
    """The dict-based st.navigation must route pages from every group."""
    at = _authenticated(ui_user)
    for page in ("dashboard.py", "savings.py", "loans.py",
                 "forecast.py", "household.py"):
        at.switch_page(os.path.join(APP_DIR, "app_pages", page))
        at.run()
        assert not at.exception, f"group page {page} failed: {at.exception}"


def test_dashboard_task_hub_quick_actions(ui_user):
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "dashboard.py"))
    at.run()
    assert not at.exception
    main_text = _main_text(at, "markdown") + " " + _main_text(at, "caption")
    assert "Quick actions" in main_text


def test_household_invite_code_persists(ui_user):
    hh = get_household_by_member(ui_user)
    if not hh:
        create_household(ui_user, "UI Test Home")
        hh = get_household_by_member(ui_user)
    code = hh["invite_code"]
    at = _authenticated(ui_user)
    at.session_state["household_id"] = hh["id"]
    at.switch_page(os.path.join(APP_DIR, "app_pages", "household.py"))
    at.run()
    assert not at.exception
    code_values = [str(el.value) for el in at.main if el.type == "code"]
    assert any(code in v for v in code_values), \
        f"invite code {code} not displayed on household page"
    assert "Share this code" in _main_text(at, "caption")


def test_expense_history_pagination_controls(ui_user):
    for i in range(60):
        add_expense(ui_user, {
            "date": date(2025, 6, 1) + timedelta(days=i % 28),
            "category": "Other", "subcategory": "Miscellaneous",
            "description": f"ui expense {i}", "amount": 1.0,
            "currency": "EUR", "amount_eur": 1.0,
            "recurring": False, "notes": "",
        })
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "log_expense.py"))
    at.run()
    assert not at.exception
    assert "Showing" in _main_text(at, "caption"), "pagination indicator missing"
    labels = [el.label for el in at.main if el.type == "selectbox"]
    assert any("Rows per page" in (lbl or "") for lbl in labels), \
        "page-size selector missing"


def test_dashboard_with_start_month_template_no_crash(ui_user):
    """Regression: a recurring template with a start_month used to shadow the
    page-level `sm` month-filter variable with a string and crash the
    dashboard with TypeError ('>' not supported between 'str' and 'int')."""
    from db import add_recurring
    add_recurring(ui_user, {
        "category": "Entertainment", "subcategory": "Streaming Services",
        "description": "Netflix", "amount": 12.99, "currency": "EUR",
        "amount_eur": 12.99, "due_day": 5, "start_month": "2025-01",
        "notes": "", "active": True,
    })
    add_expense(ui_user, {
        "date": date(2025, 6, 1), "category": "Other",
        "subcategory": "Miscellaneous", "description": "anything",
        "amount": 1.0, "currency": "EUR", "amount_eur": 1.0,
        "recurring": False, "notes": "",
    })
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "dashboard.py"))
    at.run()
    assert not at.exception, f"dashboard crashed: {at.exception}"
    labels = [str(getattr(el, "label", "") or "") for el in at.main]
    assert any("Fixed costs" in lbl for lbl in labels), \
        "fixed-costs metric missing from dashboard"
