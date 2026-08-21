"""
Regression tests for dashboard layout persistence (Phase 2 U2).

save_layout (ui/layout_state.py) stores the layout under the "ui_layout" key
in user_settings. Before the fix, UserSettings had no ui_layout column, so
save_settings silently dropped the key (logged "ignoring unknown key") and
get_settings never returned it — load_layout therefore always fell back to
DEFAULT_LAYOUT. These tests pin the round-trip.
"""

import pytest

from db import init_db, create_user, delete_user_account, username_exists, \
    get_user_by_username, save_settings, get_settings
from auth import hash_password
from ui.layout_state import (
    load_layout, save_layout, get_dashboard_order, set_dashboard_order,
    toggle_collapsed, is_collapsed, LAYOUT_SETTINGS_KEY, DEFAULT_LAYOUT,
)

TEST_USERNAME = "layout_test_user"
TEST_EMAIL    = "layout_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Layout Tester")
    yield uid
    delete_user_account(uid)


def test_save_settings_persists_ui_layout_roundtrip(test_user, caplog):
    """save_settings must store — and get_settings must return — ui_layout."""
    payload = {"order": ["a", "b"], "collapsed": ["c"]}
    save_settings(test_user, {"ui_layout": payload})
    s = get_settings(test_user)
    assert s["ui_layout"] == payload
    # No "ignoring unknown key" warning for ui_layout.
    warnings = [r for r in caplog.records if "ignoring unknown key" in r.message]
    assert not any("ui_layout" in r.getMessage() for r in warnings)


def test_load_layout_returns_saved_state(test_user):
    """load_layout must round-trip the exact dict persisted via save_layout."""
    layout = {
        "version": 1,
        "dashboard": {"order": ["panel_one", "panel_two"], "collapsed": ["panel_two"]},
    }
    saved = save_layout(test_user, layout)
    loaded = load_layout(test_user)
    assert loaded["dashboard"]["order"] == ["panel_one", "panel_two"]
    assert loaded["dashboard"]["collapsed"] == ["panel_two"]
    # save_layout normalizes (order/collapsed stay lists) — stable contract.
    assert loaded["version"] == saved["version"]


def test_load_layout_falls_back_to_default(test_user):
    """A fresh user with no saved layout still gets DEFAULT_LAYOUT."""
    loaded = load_layout(test_user)
    assert loaded == DEFAULT_LAYOUT


def test_get_dashboard_order_and_toggle(test_user):
    """set_dashboard_order / toggle_collapsed must persist via ui_layout."""
    set_dashboard_order(test_user, ["alpha", "beta", "gamma"])
    assert get_dashboard_order(test_user) == ["alpha", "beta", "gamma"]

    # Initially nothing is collapsed.
    assert is_collapsed(test_user, "beta") is False

    # Toggle collapses it.
    toggle_collapsed(test_user, "beta")
    assert is_collapsed(test_user, "beta") is True

    # Toggle again expands it.
    toggle_collapsed(test_user, "beta")
    assert is_collapsed(test_user, "beta") is False

    # Reload from a fresh get_settings still sees the (re-expanded) state.
    s = get_settings(test_user)
    assert s["ui_layout"]["dashboard"]["order"] == ["alpha", "beta", "gamma"]
