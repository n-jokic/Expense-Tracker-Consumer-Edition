"""
Regression tests for recurring-template editing: templates can be edited
(description, expected amount, currency, due day, start month, active) and
edits NEVER rewrite expenses already logged from the template.
"""

from datetime import date

import pandas as pd
import pytest

from db import (
    init_db, create_user, delete_user_account, add_recurring, update_recurring,
    get_recurring, add_expense, get_expenses,
    username_exists, get_user_by_username,
)
from auth import hash_password
from utils import filter_started_templates
from notifications import _unlogged_templates

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
