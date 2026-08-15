"""
Tests for insight computations (insights.py).
"""

from datetime import date

import pandas as pd
import pytest

from insights import month_over_month, unusual_expenses, days_until_budget_depleted, savings_projection


def _df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_month_over_month_up_and_down():
    df = _df([
        {"date": "2025-03-05", "amount_eur": 100.0},
        {"date": "2025-04-05", "amount_eur": 150.0},
    ])
    m = month_over_month(df, "amount_eur", 2025, 4)
    assert m["current"] == 150.0
    assert m["previous"] == 100.0
    assert m["trend"] == "up"
    assert m["change_pct"] == 50.0


def test_month_over_month_wraps_year():
    df = _df([
        {"date": "2024-12-10", "amount_eur": 200.0},
        {"date": "2025-01-10", "amount_eur": 100.0},
    ])
    m = month_over_month(df, "amount_eur", 2025, 1)
    assert m["previous"] == 200.0
    assert m["trend"] == "down"


def test_month_over_month_no_previous():
    df = _df([{"date": "2025-01-10", "amount_eur": 100.0}])
    m = month_over_month(df, "amount_eur", 2025, 1)
    assert m["change_pct"] == 100.0
    assert m["trend"] == "up"


def test_unusual_expenses_flags_outliers():
    df = _df([
        {"date": "2025-05-01", "category": "Food", "amount_eur": 10.0},
        {"date": "2025-05-02", "category": "Food", "amount_eur": 12.0},
        {"date": "2025-05-03", "category": "Food", "amount_eur": 200.0},
    ])
    out = unusual_expenses(df, multiplier=2.0)
    assert len(out) == 1
    assert out.iloc[0]["amount_eur"] == 200.0


def test_days_until_budget_depleted():
    df = _df([
        {"date": "2025-06-01", "amount_eur": 10.0},
        {"date": "2025-06-02", "amount_eur": 10.0},
    ])
    # period started Jun 1; "today" inside the function — spent 20 over >=2 days
    days = days_until_budget_depleted(df, 100.0, date(2025, 6, 1))
    assert days is not None and days > 0


def test_days_until_budget_depleted_over_budget_returns_zero():
    df = _df([{"date": "2025-06-01", "amount_eur": 500.0}])
    assert days_until_budget_depleted(df, 100.0, date(2025, 6, 1)) == 0


def test_savings_projection_reaches_goal():
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 100.0,
         "target_eur": 300.0, "deposited_eur": 100.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "balance_eur": 200.0,
         "target_eur": 300.0, "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    p = savings_projection(df, "G")
    assert p["months_to_goal"] == 1
    assert p["projected_date"] is not None


def test_savings_projection_empty_goal():
    assert savings_projection(pd.DataFrame(), "G")["months_to_goal"] is None
