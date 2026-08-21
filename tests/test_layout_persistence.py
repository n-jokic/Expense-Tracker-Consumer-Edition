"""
Regression tests for dashboard layout persistence (Phase 2 U2 / FIN-02).

save_layout (ui/layout_state.py) stores the layout under the "ui_layout" key
in user_settings. Before the fix, UserSettings had no ui_layout column, so
save_settings silently dropped the key (logged "ignoring unknown key") and
get_settings never returned it — load_layout therefore always fell back to
DEFAULT_LAYOUT. These tests pin the round-trip.

FIN-02 additions pin the namespaced shape, per-area atomic writes, tolerant
normalization, sanitized values, the never-raise read path, and the
LayoutSaveError write contract.
"""

import logging

import pytest

from db import init_db, create_user, delete_user_account, username_exists, \
    get_user_by_username, save_settings, get_settings
from auth import hash_password
from ui.layout_state import (
    load_layout, save_layout, get_dashboard_order, set_dashboard_order,
    toggle_collapsed, is_collapsed, LAYOUT_SETTINGS_KEY, DEFAULT_LAYOUT,
    update_layout_area, set_area_ids, sanitize_area, LayoutSaveError,
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


# ── FIN-02: namespaced shape, isolation, safety, error contract ──────────────

def test_toggle_persists_and_survives_reload(test_user):
    """Collapse → raw stored blob contains the id; is_collapsed true on reload."""
    toggle_collapsed(test_user, "loan_card_auto", area="loans")
    raw = get_settings(test_user)[LAYOUT_SETTINGS_KEY]
    assert "loan_card_auto" in raw["loans"]["collapsed"]
    assert is_collapsed(test_user, "loan_card_auto", area="loans") is True


def test_namespaces_are_isolated_per_area(test_user):
    """Writing one area must never clobber another area's namespace."""
    save_layout(test_user, {"dashboard": {"order": ["p1", "p2"],
                                          "collapsed": ["p2"]}})
    set_area_ids(test_user, "loans", "collapsed", ["loan_a"])
    toggle_collapsed(test_user, "sav_x", area="savings")

    raw = get_settings(test_user)[LAYOUT_SETTINGS_KEY]
    # loans survives the savings write verbatim…
    assert raw["loans"] == {"collapsed": ["loan_a"]}
    # …and so does dashboard.
    assert raw["dashboard"]["order"] == ["p1", "p2"]
    assert raw["dashboard"]["collapsed"] == ["p2"]
    assert raw["savings"]["collapsed"] == ["sav_x"]


def test_load_layout_malformed_json_falls_back_with_warning(test_user, caplog):
    """Garbage in the settings column → defaults + a warning, never a raise."""
    from sqlalchemy import text as _text
    from db import get_engine
    with get_engine().begin() as conn:
        conn.execute(
            _text(f"UPDATE user_settings SET {LAYOUT_SETTINGS_KEY}=:v "
                  "WHERE user_id=:u"),
            {"v": "{{{ definitely not json", "u": test_user})
    with caplog.at_level(logging.WARNING):
        loaded = load_layout(test_user)
    assert loaded == DEFAULT_LAYOUT
    layout_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and LAYOUT_SETTINGS_KEY in r.getMessage()
        and str(test_user) in r.getMessage()
    ]
    assert layout_warnings, "expected a warning naming ui_layout and the user"


def test_sanitize_area_coerces_and_filters():
    """Duplicates removed, order kept, non-strings dropped, unknown ids filtered."""
    out = sanitize_area(
        "dashboard",
        {"order": ["b", "a", "b", 5, "", "a"], "collapsed": ["x", None, "x"]},
        known_ids=["a", "b", "x"])
    assert out == {"order": ["b", "a"], "collapsed": ["x"]}

    # recurring: only group_order may contain known group ids.
    out = sanitize_area(
        "recurring",
        {"collapsed_groups": ["g1", "g1", 7],
         "group_order": ["g2", "ghost", "g1"]},
        known_ids=["g1", "g2"])
    assert out == {"collapsed_groups": ["g1"], "group_order": ["g2", "g1"]}

    # Malformed area value → defaulted shape, not an error.
    assert sanitize_area("loans", "junk") == {"collapsed": []}
    # Unknown area → best-effort coercion of dict values.
    assert sanitize_area("future_area", {"things": ["z", "z", 1]}) == \
        {"things": ["z"]}


def test_collapse_state_is_per_user(test_user):
    """User A's collapse is invisible to user B (isolated settings rows)."""
    uid_b = create_user("layout_test_user_b", "layout_test_b@example.com",
                        hash_password("test1234"), "Layout Tester B")
    try:
        toggle_collapsed(test_user, "loan_card_home", area="loans")

        assert is_collapsed(test_user, "loan_card_home", area="loans") is True
        assert is_collapsed(uid_b, "loan_card_home", area="loans") is False
        # B's row was never written → stored value may be None (= no layout).
        raw_b = get_settings(uid_b)[LAYOUT_SETTINGS_KEY] or {}
        assert (raw_b.get("loans") or {}).get("collapsed", []) == []
    finally:
        delete_user_account(uid_b)


def test_update_layout_area_raises_layout_save_error(test_user, monkeypatch):
    """Persistence failure → LayoutSaveError carrying user_id + area."""
    import db as db_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(db_mod, "atomic_update_setting_json", _boom)
    with pytest.raises(LayoutSaveError) as excinfo:
        update_layout_area(test_user, "dashboard", lambda av: av)
    assert excinfo.value.user_id == test_user
    assert excinfo.value.area == "dashboard"
    assert "dashboard" in str(excinfo.value)
    assert str(test_user) in str(excinfo.value)


def test_save_layout_raises_layout_save_error(test_user, monkeypatch):
    """Full-blob save surfaces failures too (area=None, user attached)."""
    import db as db_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("locked")

    monkeypatch.setattr(db_mod, "atomic_update_setting_json", _boom)
    with pytest.raises(LayoutSaveError) as excinfo:
        save_layout(test_user, {"dashboard": {"order": ["a"]}})
    assert excinfo.value.user_id == test_user
    assert excinfo.value.area is None


def test_warn_layout_unsaved_helper_shows_warning(monkeypatch):
    """UI-level catcher logs + warns without raising (panel integration)."""
    import ui.panel as panel_mod

    shown: list[str] = []
    monkeypatch.setattr(panel_mod.st, "warning",
                        lambda message: shown.append(message))
    err = LayoutSaveError("persist failed", user_id=42, area="loans")
    panel_mod.warn_layout_unsaved(err)  # must not raise
    assert shown == [panel_mod.LAYOUT_UNSAVED_MESSAGE]
