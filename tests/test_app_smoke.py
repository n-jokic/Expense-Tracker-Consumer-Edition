"""
App smoke tests: run the real app with Streamlit's AppTest harness and
execute every page for an authenticated user, asserting no exceptions.
"""

import os

import pytest

from streamlit.testing.v1 import AppTest

from db import create_user, delete_user_account, username_exists
from auth import hash_password

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
APP_DIR  = os.path.dirname(APP_PATH)

TEST_USERNAME = "smoke_test_user"
TEST_EMAIL    = "smoke_test@example.com"

PAGES = [
    "dashboard.py",
    "log_expense.py",
    "log_income.py",
    "savings.py",
    "portfolio.py",
    "budgets.py",
    "rewards.py",
    "recurring.py",
    "loans.py",
    "big_purchases.py",
    "travel.py",
    "forecast.py",
    "insights_view.py",
    "ask.py",
    "bank_import_view.py",
    "audit_log.py",
    "household.py",
    "settings.py",
]


@pytest.fixture(scope="module")
def smoke_user():
    from db import init_db
    init_db()  # the fixture must not depend on other test modules having run
    if not username_exists(TEST_USERNAME):
        uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("smoke1234"), "Smoke Tester")
    else:
        from db import get_user_by_username
        uid = get_user_by_username(TEST_USERNAME)["id"]
    # Keep rates "fresh" so the smoke run never triggers a live network fetch.
    from db import save_settings as _save_settings
    from datetime import datetime, timezone
    _save_settings(uid, {"rates_updated_at": datetime.now(timezone.utc)})
    yield uid
    delete_user_account(uid)


def _authenticated_at(smoke_user) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["authenticated"] = True
    at.session_state["user_id"] = smoke_user
    at.session_state["username"] = TEST_USERNAME
    at.session_state["display_name"] = "Smoke Tester"
    at.session_state["household_id"] = None
    at.session_state["onboarding_complete"] = True
    at.session_state["onboarding_step"] = 0
    return at


def test_login_page_renders():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()
    assert not at.exception
    # The login form should be present
    labels = {t.label for t in at.text_input}
    assert {"Username", "Password"} <= labels
    # Registration must be available by default (regression: it was hidden
    # when no ALLOW_REGISTRATION env var was set)
    tab_labels = [t.label for t in at.tabs]
    assert any("Create Account" in lbl for lbl in tab_labels)


def test_registration_disabled_when_configured(monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.run()
    assert not at.exception
    tab_labels = [t.label for t in at.tabs]
    assert not any("Create Account" in lbl for lbl in tab_labels)


def test_main_app_renders_and_navigates(smoke_user):
    at = _authenticated_at(smoke_user)
    at.run()
    assert not at.exception, f"main app failed: {at.exception}"
    sidebar_text = " ".join(str(md.value) for md in at.sidebar.markdown)
    assert "Smoke Tester" in sidebar_text

    # Regression: the phone-access QR code must render as an image element
    qr_images = [img for img in at.sidebar.image if img.value is not None]
    assert qr_images, "QR code image missing from the sidebar phone-access panel"
    # ... and offer a download button for it
    assert any("Download QR" in (b.label or "") for b in at.sidebar.download_button)

    for page in PAGES:
        at.switch_page(os.path.join(APP_DIR, "app_pages", page))
        at.run()
        assert not at.exception, f"page {page} failed: {at.exception}"


def test_all_pages_with_rich_data(smoke_user):
    """Sweep every page with data in EVERY table (incl. edge rows: a
    future-dated expense, a matured term deposit, a loan with a logged
    payment, holdings with a price snapshot). Each page must render without
    an exception."""
    from datetime import date, timedelta, datetime, timezone
    from db import (
        add_expense, add_income, add_budget, add_recurring, add_loan,
        add_big_purchase, add_holding, add_holding_price,
        add_savings, add_savings_account, bump_data_revision,
    )
    import queries as q
    _readers = (q._expenses, q._income, q._savings, q._savings_accounts, q._budgets,
                q._recurring, q._big_purchases, q._loans, q._loan_payments,
                q._holdings, q._holding_prices, q._audit, q._household_expenses,
                q._household_members)
    for fn in _readers:
        fn.clear()

    today = date.today()
    for i, cat in enumerate(["Food & Dining", "Transport", "Housing & Utilities",
                             "Entertainment", "Other"]):
        add_expense(smoke_user, {"date": today - timedelta(days=10 + i),
                                 "category": cat, "subcategory": "",
                                 "description": f"rich exp {i}", "amount": 10.0 + i,
                                 "currency": "EUR", "amount_eur": 10.0 + i,
                                 "recurring": False, "notes": ""})
    add_expense(smoke_user, {"date": today + timedelta(days=5), "category": "Other",
                             "subcategory": "", "description": "future row",
                             "amount": 5.0, "currency": "EUR", "amount_eur": 5.0,
                             "recurring": False, "notes": ""})
    add_income(smoke_user, {"date": today - timedelta(days=20), "source": "Job",
                            "income_type": "Salary", "hours": None, "rate": None,
                            "budgeted": 2000.0, "actual": 2000.0, "currency": "EUR",
                            "budgeted_eur": 2000.0, "actual_eur": 2000.0, "notes": ""})
    add_budget(smoke_user, {"year": today.year, "month": today.month,
                            "category": "Food & Dining", "subcategory": "",
                            "budgeted_eur": 300.0})
    add_recurring(smoke_user, {"category": "Entertainment",
                               "subcategory": "Streaming Services",
                               "description": "Netflix", "amount": 12.99,
                               "currency": "EUR", "amount_eur": 12.99, "due_day": 5,
                               "start_month": f"{today.year - 1}-01",
                               "notes": "", "active": True})
    loan_id = add_loan(smoke_user, {"name": "Car", "principal": 5000.0,
                                    "currency": "EUR", "principal_eur": 5000.0,
                                    "annual_rate": 5.0,
                                    "start_date": today - timedelta(days=100),
                                    "term_months": 24, "payment_day": 1,
                                    "status": "active", "notes": ""})
    add_expense(smoke_user, {"date": today - timedelta(days=40),
                             "category": "Loans & Debt", "subcategory": "Loan Repayment",
                             "description": "Car payment", "amount": 215.0,
                             "currency": "EUR", "amount_eur": 215.0,
                             "recurring": False, "loan_id": loan_id, "notes": ""})
    add_big_purchase(smoke_user, {"name": "Laptop", "category": "Other",
                                  "price": 1200.0, "currency": "EUR", "price_eur": 1200.0,
                                  "usage_hours": 60.0, "importance": 4,
                                  "status": "saving", "notes": ""})
    hid = add_holding(smoke_user, {"symbol": "VWCE.DE", "name": "Vanguard All-World",
                                   "quantity": 10.0, "currency": "EUR",
                                   "cost_total": 1000.0, "cost_eur": 1000.0,
                                   "last_price": 110.0,
                                   "last_price_date": datetime.now(timezone.utc)})
    add_holding_price(hid, 110.0, quantity=10.0, rate=1.0)
    add_savings(smoke_user, {"date": today - timedelta(days=90), "goal_name": "House",
                             "target_eur": 10000.0, "deposited": 2000.0, "currency": "EUR",
                             "deposited_eur": 2000.0, "interest_rate": 3.0,
                             "balance_eur": 2000.0, "notes": ""})
    add_savings_account(smoke_user, {"goal_name": "House", "name": "Matured CD",
                                     "amount": 1000.0, "currency": "EUR", "amount_eur": 1000.0,
                                     "annual_rate": 4.0,
                                     "start_date": today - timedelta(days=400),
                                     "maturity_date": today - timedelta(days=10),
                                     "status": "active", "notes": ""})
    bump_data_revision(smoke_user, include_household=False)
    for fn in _readers:
        fn.clear()

    at = _authenticated_at(smoke_user)
    at.run()
    assert not at.exception, f"main app with data failed: {at.exception}"
    for page in PAGES:
        at.switch_page(os.path.join(APP_DIR, "app_pages", page))
        at.run()
        assert not at.exception, f"page {page} with data failed: {at.exception}"


def test_ai_settings_provider_switch_saves_without_crash(smoke_user):
    """Regression: switching the AI provider selectbox and saving in ONE
    submit leaves the provider-specific fields unrendered (None) — saving
    used to crash with int(None) on api→local. The handler must fall back to
    the stored values instead."""
    at = _authenticated_at(smoke_user)
    at.run()
    at.switch_page(os.path.join(APP_DIR, "app_pages", "settings.py"))
    at.run()
    assert not at.exception, at.exception

    provider = [s for s in at.selectbox if s.label == "Provider"]
    assert provider, "AI provider selectbox missing"
    provider[0].select("local")   # fields for 'local' are not rendered yet
    at.run()
    save_btn = [b for b in at.button if b.label == "Save AI settings"]
    assert save_btn
    save_btn[0].click()
    at.run()
    assert not at.exception, f"provider-switch save crashed: {at.exception}"

    # And back to api in one submit (fields None → stored values kept).
    provider = [s for s in at.selectbox if s.label == "Provider"]
    provider[0].select("api")
    at.run()
    save_btn = [b for b in at.button if b.label == "Save AI settings"]
    save_btn[0].click()
    at.run()
    assert not at.exception, f"provider-switch back crashed: {at.exception}"


def test_dashboard_quick_add_logs_expense(smoke_user):
    """Regression: the one-tap quick-add buttons must log a real expense."""
    from db import get_expenses
    before = len(get_expenses(smoke_user))
    at = _authenticated_at(smoke_user)
    at.run()
    at.switch_page(os.path.join(APP_DIR, "app_pages", "dashboard.py"))
    at.run()
    assert not at.exception, at.exception

    coffee = [b for b in at.button if "Coffee" in (b.label or "")]
    assert coffee, "quick-add Coffee button missing"
    coffee[0].click()
    at.run()
    assert not at.exception, at.exception

    after = get_expenses(smoke_user)
    assert len(after) == before + 1
    row = after[after["description"] == "Coffee"]
    assert len(row) == 1
    assert row.iloc[0]["amount_eur"] == 2.5
    assert row.iloc[0]["category"] == "Dining Out"
    assert row.iloc[0]["subcategory"] == "Coffee & Snacks"


def test_onboarding_gate_blocks_new_users(smoke_user):
    at = _authenticated_at(smoke_user)
    at.session_state["onboarding_complete"] = False
    at.run()
    assert not at.exception
    # Onboarding step 0 shows the welcome heading
    assert any("Welcome" in str(md.value) for md in at.markdown)


def test_onboarding_flow_submits_without_name_errors(smoke_user):
    """Regression: the full onboarding flow (incl. save_settings on submit)
    must run without NameError/exception."""
    at = _authenticated_at(smoke_user)
    at.session_state["onboarding_complete"] = False
    at.session_state["onboarding_step"] = 0
    at.run()
    assert not at.exception

    # step 0 -> step 1
    for b in at.button:
        if "started" in (b.label or ""):
            b.click()
            break
    at.run()
    assert not at.exception
    assert at.session_state["onboarding_step"] == 1

    # submit step 1 (currency + budget save). With EUR selected only the
    # budget input is rendered; otherwise [0] is the rate, [1] the budget.
    inputs = at.number_input
    budget_idx = 0 if len(inputs) == 1 else 1
    inputs[budget_idx].set_value(500.0)
    for b in at.button:
        if "Continue" in (b.label or ""):
            b.click()
            break
    at.run()
    assert not at.exception, f"onboarding submit failed: {at.exception}"
    assert at.session_state["onboarding_step"] == 2
