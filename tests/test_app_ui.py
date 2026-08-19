"""
AppTest coverage for the Phase 3 UI work: grouped navigation, dashboard task
hub, persisted household invite code, and expense-history pagination.
"""

import os
import sys
from datetime import date, timedelta

import pytest
from streamlit.testing.v1 import AppTest

import llm
import queries as q
from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_user_by_username, create_household, get_household_by_member,
    add_expense, add_loan, bump_data_revision, save_settings,
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
    for fn in (q._expenses, q._income, q._savings, q._savings_accounts,
               q._budgets, q._recurring,
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


def test_loans_page_renders_with_current_month_payment(ui_user):
    """A normalized payment record must support the current-month overdue check."""
    today = date.today()
    loan_id = add_loan(ui_user, {
        "name": "UI test loan", "principal": 5000.0, "currency": "EUR",
        "principal_eur": 5000.0, "annual_rate": 5.0,
        "start_date": today - timedelta(days=100), "term_months": 24,
        "payment_day": today.day, "status": "active", "notes": "",
    })
    add_expense(ui_user, {
        "date": today, "category": "Loans & Debt",
        "subcategory": "Loan Repayment", "description": "Current payment",
        "amount": 215.0, "currency": "EUR", "amount_eur": 215.0,
        "recurring": False, "loan_id": loan_id, "notes": "",
    })
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "loans.py"))
    at.run()
    assert not at.exception, f"loans page failed with payment: {at.exception}"


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


def test_savings_page_goal_cards_and_term_deposits(ui_user):
    """The new savings page must render goal cards (with quick-action buttons)
    and term-deposit account cards when the user has data."""
    from db import add_savings, add_savings_account
    add_savings(ui_user, {
        "date": date(2025, 1, 1), "goal_name": "Laptop", "target_eur": 1500.0,
        "deposited": 300.0, "currency": "EUR", "deposited_eur": 300.0,
        "interest_rate": 3.0, "balance_eur": 300.0, "notes": "",
    })
    add_savings(ui_user, {
        "date": date(2025, 2, 1), "goal_name": "Laptop", "target_eur": 1500.0,
        "deposited": 200.0, "currency": "EUR", "deposited_eur": 200.0,
        "interest_rate": 3.0, "balance_eur": 500.0, "notes": "",
    })
    add_savings_account(ui_user, {
        "goal_name": "Laptop", "name": "6-month CD", "amount": 500.0,
        "currency": "EUR", "amount_eur": 500.0, "annual_rate": 4.0,
        "start_date": date.today(), "maturity_date": date.today() + timedelta(days=180),
        "status": "active", "notes": "",
    })
    bump_data_revision(ui_user, include_household=False)
    _clear_cached_readers()
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "savings.py"))
    at.run()
    assert not at.exception, f"savings page crashed: {at.exception}"
    text = (_main_text(at, "markdown") + " " + _main_text(at, "caption")
            + " " + _main_text(at, "subheader"))
    assert "Term-deposit accounts" in text
    assert "6-month CD" in text
    btn_labels2 = [str(getattr(el, "label", "") or "") for el in at.main
                   if el.type == "button"]
    assert any("Deposit" == lbl for lbl in btn_labels2)
    assert any("Withdraw" == lbl for lbl in btn_labels2)
    assert any("Edit goal" == lbl for lbl in btn_labels2)


# ── AI assistant UI ───────────────────────────────────────────────────────────

def _broken_llama_cpp():
    """Simulate a machine without the llama.cpp runtime: importing the module
    raises OSError (the DLL-load failure mode) — the page must degrade to an
    error banner, never crash."""
    class _Broken:
        def __getattr__(self, name):
            raise OSError("DLL load failed")
    return _Broken()


def test_ask_page_error_does_not_pollute_history(ui_user, tmp_path, monkeypatch):
    """Regression (B1.1): a failed provider call must surface as an st.error
    banner — NOT be appended to ask_history as an assistant message (which
    would later be fed back into prompts as 'CHAT SO FAR' context)."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    save_settings(ui_user, {"ai_provider": "local",
                            "ai_local_model": str(model_path)})
    monkeypatch.setitem(sys.modules, "llama_cpp", _broken_llama_cpp())
    llm._local_cache = ()
    llm._last_result = None

    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "ask.py"))
    at.run()
    assert not at.exception, at.exception
    assert at.chat_input, "chat input missing from ask page"

    at.chat_input[0].set_value("How much did I spend?")
    at.run()
    assert not at.exception, at.exception

    # A real error banner with the pointer must appear.
    err_text = " ".join(str(getattr(el, "value", "") or "") for el in at.error)
    assert err_text, "expected an error banner for the failed generation"
    assert "Settings" in err_text

    # And the failed turn was NOT stored: history has only the user's turn.
    hist = at.session_state["ask_history"]
    assert len(hist) == 1, f"failed turn polluted history: {hist}"
    assert hist[0]["role"] == "user"


def test_ask_page_pills_show_on_empty_chat(ui_user):
    """The suggestion pills render only while the chat is empty (B1.2)."""
    from crypto import encrypt_str
    save_settings(ui_user, {"ai_provider": "api",
                            "ai_api_key_enc": encrypt_str("sk-test")})
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "ask.py"))
    at.run()
    assert not at.exception, at.exception
    # One chat input, and the pills widget exists on the empty chat.
    assert at.chat_input
    assert any(el.type == "button_group" for el in at.main), \
        "suggestion pills missing on empty chat"
    assert not at.session_state["ask_history"]


def test_settings_page_renders_ai_section(ui_user):
    """The AI assistant settings moved to app_pages/settings_ai.py must still
    render inside the Settings → Notifications tab (C2)."""
    at = _authenticated(ui_user)
    at.switch_page(os.path.join(APP_DIR, "app_pages", "settings.py"))
    at.run()
    assert not at.exception, at.exception
    text = (_main_text(at, "markdown") + " " + _main_text(at, "caption")
            + " " + _main_text(at, "subheader"))
    assert "AI assistant (optional)" in text
