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


def test_savings_projection_with_net_withdrawals_has_no_projection():
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 100.0,
         "target_eur": 500.0, "deposited_eur": -20.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "balance_eur": 80.0,
         "target_eur": 500.0, "deposited_eur": -20.0, "interest_rate": 0.0},
    ])
    p = savings_projection(df, "G")
    assert p["months_to_goal"] is None
    assert p["projected_date"] is None


def test_savings_projection_nan_inputs_have_no_bogus_projection():
    """Regression: NaN interest rate or NaN deposits used to return a fake
    'goal in 1 month' projection."""
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 100.0,
         "target_eur": 500.0, "deposited_eur": float("nan"),
         "interest_rate": float("nan")},
    ])
    p = savings_projection(df, "G")
    assert p["months_to_goal"] is None
    assert p["projected_date"] is None


def test_savings_projection_excludes_opening_deposit_from_run_rate():
    """The first deposit creates the goal and must not inflate the monthly run-rate.

    Deposits [1000, 100, 100] with a large opening seed of 1000 must compute a
    run-rate of ~100/mo (last 2 months), not 400/mo (all 3 months avg).
    """
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 1000.0,
         "target_eur": 50000.0, "deposited_eur": 1000.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "balance_eur": 1100.0,
         "target_eur": 50000.0, "deposited_eur": 100.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-03-01", "balance_eur": 1200.0,
         "target_eur": 50000.0, "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    p = savings_projection(df, "G")

    # With the 1000 seed excluded, run-rate ~= 100/mo. Starting from 1200
    # balance toward 50000 target: (50000 - 1200) / 100 = 488 months.
    # Without exclusion, run-rate would be ~400/mo → ~120 months.
    assert p["months_to_goal"] is not None
    assert p["months_to_goal"] > 200, (
        f"Expected >200 months with 100/mo run-rate, got {p['months_to_goal']}")


def test_savings_projection_two_rows_uses_second_deposit():
    """Two-row case [500, 100] excludes the first row, uses the 100/mo deposit."""
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 500.0,
         "target_eur": 5500.0, "deposited_eur": 500.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "balance_eur": 600.0,
         "target_eur": 5500.0, "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    p = savings_projection(df, "G")

    # Excluding the 500 seed: run-rate = 100/mo. (5500 - 600) / 100 = 49 months.
    assert p["months_to_goal"] is not None
    assert p["months_to_goal"] == 49, (
        f"Expected 49 months with 100/mo run-rate, got {p['months_to_goal']}")


def test_savings_projection_single_deposit_matches_old_behavior():
    """Single-deposit goal: first row == only row, falls back to all-rows logic."""
    df = _df([
        {"goal_name": "G", "date": "2025-01-01", "balance_eur": 100.0,
         "target_eur": 500.0, "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    p = savings_projection(df, "G")

    # Single deposit → monthly_dep = mean(deposited_eur) = 100.
    # (500 - 100) / 100 = 4 months.
    assert p["months_to_goal"] is not None
    assert p["months_to_goal"] == 4, (
        f"Expected 4 months with 100/mo run-rate, got {p['months_to_goal']}")
    assert p["projected_date"] is not None
