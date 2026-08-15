"""
Tests for recurring-bill logic (notifications.py).
"""

from datetime import date

import pandas as pd
import pytest

from notifications import due_reminder_day, _unlogged_templates


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
