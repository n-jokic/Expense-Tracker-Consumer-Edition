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
    "recurring.py",
    "loans.py",
    "big_purchases.py",
    "travel.py",
    "forecast.py",
    "insights_view.py",
    "bank_import_view.py",
    "audit_log.py",
    "household.py",
    "settings.py",
]


@pytest.fixture(scope="module")
def smoke_user():
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
