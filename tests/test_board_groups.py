"""
FIN-03 regression tests: accessible recurring-category collapse/reorder.

The CCv2 board component can't be driven from pytest, so these tests pin:
- the pure validation/merge helpers the board and page rely on,
- the accessibility/state-emission contract of the component source,
- the recurring layout namespace round-trip the page persists through.
"""

import pytest

from db import init_db, create_user, delete_user_account, username_exists, \
    get_user_by_username
from auth import hash_password
from ui.board import (
    _validate_group_order,
    _validate_collapsed,
    apply_persisted_group_order,
)
from ui.layout_state import load_layout, set_area_ids

BOARD_USER = "board_groups_user"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(BOARD_USER):
        delete_user_account(get_user_by_username(BOARD_USER)["id"])
    uid = create_user(BOARD_USER, "board_groups@example.com",
                      hash_password("test1234"), "Board Tester")
    yield uid
    delete_user_account(uid)


KNOWN = {"Housing", "Subscriptions", "Fun"}


# ── Validation before persistence ────────────────────────────────────────────

def test_validate_group_order_accepts_permutation_only():
    assert _validate_group_order(["Fun", "Housing", "Subscriptions"], KNOWN) == \
        ["Fun", "Housing", "Subscriptions"]
    assert _validate_group_order(list(KNOWN), KNOWN) is not None


def test_validate_group_order_rejects_mutations():
    assert _validate_group_order(["Fun", "Housing"], KNOWN) is None              # missing
    assert _validate_group_order(["Fun", "Housing", "Subscriptions", "Ghost"],
                                 KNOWN) is None                                  # extra
    assert _validate_group_order(["Fun", "Fun", "Housing"], KNOWN) is None       # duplicate
    assert _validate_group_order("Fun", KNOWN) is None                           # wrong type
    assert _validate_group_order(None, KNOWN) is None


def test_validate_collapsed_is_subset_of_known():
    assert _validate_collapsed(["Fun", "Ghost"], KNOWN) == {"Fun"}
    assert _validate_collapsed([], KNOWN) == set()
    assert _validate_collapsed(None, KNOWN) == set()
    assert _validate_collapsed({"not-a-list"}, KNOWN) == set()                   # wrong type


# ── Persisted-order merge (page opens in saved arrangement) ──────────────────

def test_apply_persisted_group_order_puts_saved_first_then_new():
    category_order = ["Housing", "Subscriptions", "Fun"]
    assert apply_persisted_group_order(category_order, ["Fun", "Housing"]) == \
        ["Fun", "Housing", "Subscriptions"]
    # Unknown persisted ids are dropped; new categories keep natural order.
    assert apply_persisted_group_order(category_order, ["Ghost", "Fun"]) == \
        ["Fun", "Housing", "Subscriptions"]
    # Duplicates ignored; empty/None → original order.
    assert apply_persisted_group_order(category_order, ["Fun", "Fun"]) == \
        ["Fun", "Housing", "Subscriptions"]
    assert apply_persisted_group_order(category_order, []) == category_order
    assert apply_persisted_group_order(category_order, None) == category_order


# ── Component accessibility / state-emission contract ────────────────────────

def _board_source() -> str:
    from pathlib import Path
    return Path(__file__).resolve().parents[1].joinpath("ui", "board.py").read_text(
        encoding="utf-8")


def test_board_component_has_aria_expanded_and_keyboard_controls():
    src = _board_source()
    # Collapse control exposes its expanded state to assistive tech…
    assert "aria-expanded" in src
    assert "aria-label" in src
    # …and every group control is a native <button> (keyboard operable).
    assert "toggle.type='button'" in src
    assert "up.type='button'" in src and "down.type='button'" in src
    assert "Move group '+category+' up" in src
    # The component reports all three state channels for persistence.
    assert "setStateValue('group_order'" in src
    assert "setStateValue('collapsed_groups'" in src
    assert "setStateValue('order'" in src


def test_recurring_page_persists_board_state_with_error_contract():
    """Page wiring: seeds initial state, persists changes via set_area_ids,
    and catches LayoutSaveError instead of crashing."""
    from pathlib import Path
    page = Path(__file__).resolve().parents[1].joinpath(
        "app_pages", "recurring.py").read_text(encoding="utf-8")
    assert "apply_persisted_group_order" in page
    assert "initial_collapsed=collapsed_init" in page
    assert 'set_area_ids(user_id, "recurring", "group_order"' in page
    assert 'set_area_ids(user_id, "recurring", "collapsed_groups"' in page
    assert "except LayoutSaveError" in page
    # Template validation stays in place: non-empty name, amount > 0.
    assert "Please add a description." in page
    assert "Typical amount must be greater than 0." in page


# ── End-to-end state round-trip through the FIN-02 namespace API ─────────────

def test_recurring_namespace_round_trip(test_user):
    """Collapse + reorder survive a reload exactly as the page persists them."""
    set_area_ids(test_user, "recurring", "group_order",
                 ["Fun", "Housing", "Subscriptions"])
    set_area_ids(test_user, "recurring", "collapsed_groups", ["Fun"])

    layout = load_layout(test_user)
    area = layout["recurring"]
    assert area["group_order"] == ["Fun", "Housing", "Subscriptions"]
    assert area["collapsed_groups"] == ["Fun"]

    # Unknown ids are filtered on write (validation before persistence).
    set_area_ids(test_user, "recurring", "group_order",
                 ["Ghost", "Fun", "Housing", "Subscriptions"],
                 known_ids=["Fun", "Housing", "Subscriptions"])
    assert load_layout(test_user)["recurring"]["group_order"] == \
        ["Fun", "Housing", "Subscriptions"]


def test_recurring_writes_leave_other_namespaces_untouched(test_user):
    set_area_ids(test_user, "dashboard", "collapsed", ["panel_x"])
    set_area_ids(test_user, "loans", "collapsed", ["loan_card_home"])
    set_area_ids(test_user, "recurring", "collapsed_groups", ["Fun"])

    raw = load_layout(test_user)
    assert raw["dashboard"]["collapsed"] == ["panel_x"]
    assert raw["loans"]["collapsed"] == ["loan_card_home"]
    assert raw["recurring"]["collapsed_groups"] == ["Fun"]
