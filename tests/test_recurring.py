"""
Tests for recurring-bill logic: reminder math and unlogged-template matching
(notifications.py), plus regression tests for recurring-template editing —
templates can be edited (description, expected amount, currency, due day,
start month, active) and edits NEVER rewrite expenses already logged from
the template.
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