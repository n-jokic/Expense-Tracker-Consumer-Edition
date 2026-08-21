"""
Tests for recurring-bill logic: reminder math and unlogged-template matching
(notifications.py), plus regression tests for recurring-template editing —
templates can be edited (description, expected amount, currency, due day,
start month, active) and edits NEVER rewrite expenses already logged from
the template.

Also includes regression tests for the click-swallowing bug:
app_pages/recurring.py used to call _persist_grouped_order BEFORE the
action handler, so when sort_order values mismatched widget positions
(guaranteed right after creating a template — db default sort_order=0),
the persistence function issued st.rerun() and the RerunException swallowed
any 'Log now' / 'Edit' / 'Remove' click in that run. The fix gates the
persistence call on `not action`.
"""

from datetime import date

import pandas as pd
import pytest

from notifications import due_reminder_day, _unlogged_templates
from db import (
    init_db, create_user, delete_user_account, add_recurring, update_recurring,
    get_recurring, add_expense, get_expenses,
    username_exists, get_user_by_username,
)
from auth import hash_password
from utils import filter_started_templates


# ── Reminder math (notifications.py) ─────────────────────────────────────────

def test_due_reminder_day_basic():
    assert due_reminder_day(15, 2, 31) == 13
    assert due_reminder_day(15, 0, 31) == 15


def test_due_reminder_day_never_wraps_below_one():
    assert due_reminder_day(1, 2, 31) == 1
    assert due_reminder_day(2, 5, 31) == 1


def test_due_reminder_day_clamps_to_month_length():
    # due 31st in a 28-day month, remind 2 days before -> last day (28)
    assert due_reminder_day(31, 2, 28) == 28
    assert due_reminder_day(29, 2, 28) == 27


def _rec_df(rows):
    df = pd.DataFrame(rows)
    df["active"] = True
    return df


def _exp_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── Unlogged-template matching (notifications.py) ─────────────────────────────

def test_unlogged_templates_matches_template_id():
    rec = _rec_df([{"id": "t1", "description": "Gym", "amount_eur": 30.0},
                   {"id": "t2", "description": "Netflix", "amount_eur": 12.0}])
    exp = _exp_df([{"date": "2025-06-05", "description": "Gym",
                    "amount_eur": 35.0, "rec_template_id": "t1"}])
    unlogged = _unlogged_templates(rec, exp, date(2025, 6, 10))
    assert [str(r["id"]) for r in unlogged] == ["t2"]


def test_unlogged_templates_actual_differs_from_expected_still_counts():
    """An actual amount different from the expected must not break matching."""
    rec = _rec_df([{"id": "t1", "description": "Gym", "amount_eur": 30.0}])
    exp = _exp_df([{"date": "2025-06-05", "description": "Gym",
                    "amount_eur": 45.0, "rec_template_id": "t1"}])
    assert _unlogged_templates(rec, exp, date(2025, 6, 10)) == []


def test_unlogged_templates_fallback_for_old_rows_without_template_id():
    rec = _rec_df([{"id": "t1", "description": "Gym", "amount_eur": 30.0}])
    exp = _exp_df([{"date": "2025-06-05", "description": "gym",
                    "amount_eur": 30.0, "rec_template_id": None}])
    assert _unlogged_templates(rec, exp, date(2025, 6, 10)) == []


def test_unlogged_templates_respects_month():
    rec = _rec_df([{"id": "t1", "description": "Gym", "amount_eur": 30.0}])
    exp = _exp_df([{"date": "2025-05-05", "description": "Gym",
                    "amount_eur": 30.0, "rec_template_id": "t1"}])
    unlogged = _unlogged_templates(rec, exp, date(2025, 6, 10))
    assert [str(r["id"]) for r in unlogged] == ["t1"]


def test_future_template_not_flagged_unlogged(test_user):
    """A template whose start month hasn't arrived must not trigger bill
    reminders or count as an unlogged bill."""
    today = date(2025, 3, 1)
    df = pd.DataFrame([
        {"id": "past", "description": "Started", "amount_eur": 10.0,
         "active": True, "start_month": "2025-01", "due_day": 5},
        {"id": "future", "description": "Future", "amount_eur": 20.0,
         "active": True, "start_month": "2025-06", "due_day": 5},
    ])
    unlogged = _unlogged_templates(df, pd.DataFrame(), today)
    ids = [str(r.get("id")) for r in unlogged]
    assert "past" in ids
    assert "future" not in ids


# ── Template editing (db.py + utils.filter_started_templates) ─────────────────

TEST_USERNAME = "recurring_edit_user"
TEST_EMAIL    = "recurring_edit@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Recurring Edit Tester")
    yield uid
    delete_user_account(uid)


def _template(uid, **overrides):
    base = {
        "category": "Entertainment", "subcategory": "Streaming Services",
        "description": "Netflix", "amount": 12.99, "currency": "EUR",
        "amount_eur": 12.99, "due_day": 15, "start_month": "2025-01",
        "notes": "", "active": True,
    }
    base.update(overrides)
    return add_recurring(uid, base)


def test_start_month_persists_and_updates(test_user):
    rid = _template(test_user, start_month="2025-01")
    row = get_recurring(test_user).iloc[0]
    assert row["start_month"] == "2025-01"

    assert update_recurring(test_user, rid, {
        "description": "Netflix Premium", "amount": 19.99, "amount_eur": 19.99,
        "currency": "EUR", "due_day": 3, "start_month": "2026-02",
        "notes": "upgraded", "active": False,
    })
    row = get_recurring(test_user).iloc[0]
    assert row["description"] == "Netflix Premium"
    assert row["amount"] == 19.99
    assert row["due_day"] == 3
    assert row["start_month"] == "2026-02"
    assert row["notes"] == "upgraded"
    assert row["active"] is False or row["active"] == 0


def test_recurring_sort_order_persists(test_user):
    rid = _template(test_user, sort_order=7)
    row = get_recurring(test_user).iloc[0]
    assert row["sort_order"] == 7

    assert update_recurring(test_user, rid, {"sort_order": 2})
    row = get_recurring(test_user).iloc[0]
    assert row["sort_order"] == 2


def test_recurring_category_move_clears_invalid_subcategory(test_user):
    rid = _template(test_user)
    assert update_recurring(test_user, rid, {"category": "Groceries"})
    row = get_recurring(test_user).iloc[0]
    assert row["category"] == "Groceries"
    assert row["subcategory"] == ""


def test_editing_template_never_rewrites_past_logs(test_user):
    """The core guarantee: expenses logged from a template store their OWN
    copies of amount/description/category — editing the template afterwards
    must not touch them."""
    rid = _template(test_user, description="Old plan", amount=10.0,
                    amount_eur=10.0, start_month=None)
    add_expense(test_user, {
        "date": date(2025, 3, 10), "category": "Entertainment",
        "subcategory": "Streaming Services", "description": "Old plan",
        "amount": 10.0, "currency": "EUR", "amount_eur": 10.0,
        "recurring": True, "rec_template_id": rid, "notes": "",
    })

    update_recurring(test_user, rid, {
        "description": "New plan", "amount": 25.0, "amount_eur": 25.0,
        "category": "Other", "subcategory": "Miscellaneous",
    })

    tmpl = get_recurring(test_user).iloc[0]
    assert tmpl["description"] == "New plan"
    assert tmpl["amount_eur"] == 25.0

    logged = get_expenses(test_user).iloc[0]
    assert logged["description"] == "Old plan"      # untouched
    assert logged["amount"] == 10.0                # untouched
    assert logged["category"] == "Entertainment"   # untouched
    assert logged["rec_template_id"] == rid        # link preserved


def test_filter_started_templates():
    df = pd.DataFrame([
        {"id": "a", "active": True, "start_month": "2025-01"},
        {"id": "b", "active": True, "start_month": "2025-06"},
        {"id": "c", "active": True, "start_month": "2025-07"},
        {"id": "d", "active": True, "start_month": None},
        {"id": "e", "active": True, "start_month": " 2025-05 "},
    ])
    out = filter_started_templates(df, 2025, 6)
    assert set(out["id"]) == {"a", "b", "d", "e"}

    # missing column -> unchanged; empty -> unchanged
    assert filter_started_templates(pd.DataFrame({"id": [1]}), 2025, 6).shape == (1, 1)
    assert filter_started_templates(pd.DataFrame(), 2025, 6).empty


# ── Click-persistence gating regression (app_pages/recurring.py) ──────────────
#
# Bug: _persist_grouped_order(ordered, active) was called BEFORE the
# "if action:" block. Right after creating a template, all sort_order values
# are 0 (db default), so the board positional order mismatches sort_order
# -> _persist_grouped_order issued a reorder write + st.rerun()
# (+bump_db_version). The RerunException unwinds the script, so any
# "Log now" / "Edit" / "Remove" click in that same run was silently
# discarded (the page has no native button fallback).
#
# Fix: gate _persist_grouped_order on not action — clicks are processed in
# their own run; order-convergence then happens on the next action-less rerun
# (the function already no-ops when orders match, healing in exactly one rerun).


def test_recurring_source_gates_persist_on_no_action():
    """Source-level guard: _persist_grouped_order must be inside the
    if not action: branch, never called unconditionally before the
    action handler."""
    import os
    page_src = os.path.join(
        os.path.dirname(__file__), "..", "app_pages", "recurring.py")
    with open(page_src, "r", encoding="utf-8") as fh:
        src = fh.read()

    # The guard must exist.
    assert "if not action:" in src, (
        "recurring.py must gate _persist_grouped_order on if not action:"
    )
    # The old unconditional pattern: _persist at 4-space indent (directly
    # in the function body, not nested in if not action:) followed by the
    # action handler. Must be ABSENT — the call must be at 8-space indent
    # inside the if not action: block.
    src_lines = src.split("\n")
    # Find any line where _persist_grouped_order is called at exactly 4-space
    # indent (not nested deeper) and followed (next non-blank line) by "    if action:".
    _violating = []
    for i, line in enumerate(src_lines):
        stripped = line.lstrip()
        if stripped.startswith("_persist_grouped_order(ordered, active)"):
            indent = len(line) - len(stripped)
            # Walk forward to find next non-blank line
            j = i + 1
            while j < len(src_lines) and src_lines[j].strip() == "":
                j += 1
            next_nonblank = src_lines[j] if j < len(src_lines) else ""
            if (indent == 4
                    and next_nonblank.strip() == "if action:"):
                _violating.append((i + 1, line))
    assert not _violating, (
        f"recurring.py calls _persist_grouped_order at 4-space indent before "
        f"the action handler (lines: {_violating}). It must be gated by "
        f"'if not action:' (8-space indent)."
    )
    # Confirm the guarded call is present and correctly indented.
    assert "    if not action:\n        _persist_grouped_order(ordered, active)" in src, (
        "_persist_grouped_order must be called inside the if not action: block"
    )


# ── Behavioral spy tests via a stripped-down mini-app ───────────────────────
# These replicate the exact gating pattern from recurring.py (minus all the
# Streamlit rendering / board calls) so we can spy on _persist_grouped_order
# without firing up the full AppTest harness. The mini-app mirrors the
# production call site: if not action: _persist(ordered, active) then
# if action: <handle>.


_GATED_SCRIPT_TEMPLATE = """
import streamlit as st

persist_calls = []

def _persist_grouped_order(ordered, active):
    persist_calls.append((ordered, active))

# Inputs come from session_state so the test can control them.
ordered = st.session_state.get("ordered", {})
action = st.session_state.get("action", None)
active_rows = st.session_state.get("active_rows", [])

if not action:
    _persist_grouped_order(ordered, active_rows)
if action:
    st.session_state._last_action_handled = dict(action)
st.session_state._persist_count = len(persist_calls)
"""


def test_persist_not_invoked_when_action_present():
    """When the page has an action payload (Log now / Edit / Remove),
    _persist_grouped_order must NOT be called — the click must be processed
    in its own run without a swallowing st.rerun()."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(_GATED_SCRIPT_TEMPLATE, default_timeout=30)
    at.session_state["action"] = {"id": "t1", "action": "log", "value": None}
    at.session_state["ordered"] = {"Other": ["t1"]}
    at.session_state["active_rows"] = [{"id": "t1", "category": "Other",
                                        "sort_order": 0}]
    at.run()
    assert not at.exception, f"mini-app failed: {at.exception}"
    assert at.session_state["_persist_count"] == 0, (
        "Expected _persist_grouped_order NOT to be called when action present, "
        f"but it was called {at.session_state['_persist_count']} time(s)"
    )
    assert at.session_state["_last_action_handled"]["action"] == "log"


def test_persist_invoked_when_no_action():
    """When the page has no action (plain reorder / rerun),
    _persist_grouped_order MUST be called so orders converge."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(_GATED_SCRIPT_TEMPLATE, default_timeout=30)
    at.session_state["action"] = None
    at.session_state["ordered"] = {"Other": ["t1"]}
    at.session_state["active_rows"] = [{"id": "t1", "category": "Other",
                                        "sort_order": 5}]
    at.run()
    assert not at.exception, f"mini-app failed: {at.exception}"
    assert at.session_state["_persist_count"] == 1, (
        "Expected _persist_grouped_order to be called once when no action, "
        f"but it was called {at.session_state['_persist_count']} time(s)"
    )
