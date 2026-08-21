"""
A3 verification: the recurring grouped_board must render via its canonical
ui.board.grouped_board path (no silent fallback), and its layout state
(group order / collapsed groups) must persist per user.

Uses Streamlit AppTest like tests/test_app_smoke.py.
"""

import os

import pytest

from streamlit.testing.v1 import AppTest

from db import (
    create_user, delete_user_account, username_exists, get_user_by_username,
    add_recurring, init_db, save_settings,
)
from auth import hash_password
from datetime import datetime, timezone

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
APP_DIR = os.path.dirname(APP_PATH)
RECURRING_PAGE = os.path.join(APP_DIR, "app_pages", "recurring.py")

USERNAME = "recurring_board_user"
FALLBACK_MARKER = "drag-and-drop board hit an error"


@pytest.fixture(scope="module")
def board_user():
    init_db()
    if not username_exists(USERNAME):
        uid = create_user(USERNAME, f"{USERNAME}@example.com",
                          hash_password("board1234"), "Board Tester")
    else:
        uid = get_user_by_username(USERNAME)["id"]
    save_settings(uid, {"rates_updated_at": datetime.now(timezone.utc)})
    add_recurring(uid, {"category": "Entertainment",
                        "subcategory": "Streaming Services",
                        "description": "Netflix", "amount": 12.99,
                        "currency": "EUR", "amount_eur": 12.99,
                        "due_day": 5, "start_month": None,
                        "notes": "", "active": True})
    yield uid
    delete_user_account(uid)


def _authenticated_at(uid) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["authenticated"] = True
    at.session_state["user_id"] = uid
    at.session_state["username"] = USERNAME
    at.session_state["display_name"] = "Board Tester"
    at.session_state["household_id"] = None
    at.session_state["onboarding_complete"] = True
    at.session_state["onboarding_step"] = 0
    return at


def test_board_renders_via_canonical_path(board_user):
    at = _authenticated_at(board_user)
    at.switch_page(RECURRING_PAGE)
    at.run()
    assert not at.exception, f"recurring page failed: {at.exception}"
    # The loud fallback must NOT have fired — canonical board is healthy.
    warnings_text = " ".join(str(w.value) for w in at.warning)
    assert FALLBACK_MARKER not in warnings_text, warnings_text[:400]
    # Board usage hint proves the template board section rendered.
    captions = " ".join(str(c.value) for c in at.caption)
    assert "Drag complete cards between categories" in captions


def test_layout_state_round_trip(board_user):
    from ui.layout_state import load_layout, set_area_ids
    uid = board_user
    set_area_ids(uid, "recurring", "group_order", ["Entertainment"],
                 known_ids={"Entertainment"})
    set_area_ids(uid, "recurring", "collapsed_groups", ["Entertainment"],
                 known_ids={"Entertainment"})
    layout = load_layout(uid)
    rec = layout.get("recurring", {})
    assert rec.get("group_order") == ["Entertainment"]
    assert "Entertainment" in rec.get("collapsed_groups", [])
    # Unknown ids are sanitized away instead of corrupting state.
    set_area_ids(uid, "recurring", "group_order", ["Ghost"],
                 known_ids={"Entertainment"})
    assert load_layout(uid)["recurring"]["group_order"] == []
