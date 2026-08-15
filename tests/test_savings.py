"""
Tests for savings balance recomputation (db._recompute_savings_balances).
"""

import pandas as pd
import pytest

from db import _recompute_savings_balances


def _savings_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return _recompute_savings_balances(df)


def test_first_deposit_is_the_balance():
    df = _savings_df([{
        "goal_name": "Emergency Fund", "date": "2025-01-05",
        "deposited_eur": 100.0, "interest_rate": 12.0, "balance_eur": 999.0,
    }])
    assert df.iloc[0]["balance_eur"] == 100.0


def test_interest_compounds_over_elapsed_months():
    # 100 deposit on Jan 1 at 12% p.a. (1%/month), +100 on Mar 1
    # -> 100*1.01^2 + 100 = 202.01
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(202.01, abs=1e-3)


def test_two_deposits_in_same_month_get_no_interest():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-01-20", "deposited_eur": 50.0,  "interest_rate": 12.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 150.0


def test_interest_uses_the_earlier_deposits_rate():
    # Growth between Jan and Feb uses the January rate (12%), not February's (0%).
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 100.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == pytest.approx(201.0, abs=1e-3)


def test_goals_are_independent():
    df = _savings_df([
        {"goal_name": "A", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "B", "date": "2025-02-01", "deposited_eur": 500.0, "interest_rate": 0.0},
        {"goal_name": "A", "date": "2025-03-01", "deposited_eur": 100.0, "interest_rate": 12.0},
    ])
    a = df[df["goal_name"] == "A"].sort_values("date")
    b = df[df["goal_name"] == "B"]
    assert a.iloc[-1]["balance_eur"] == pytest.approx(202.01, abs=1e-3)
    assert b.iloc[0]["balance_eur"] == 500.0


def test_recompute_handles_missing_dates_gracefully():
    df = _savings_df([
        {"goal_name": "G", "date": None, "deposited_eur": 50.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": 50.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 100.0


def test_withdrawal_reduces_balance_with_interest():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 12.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -30.0, "interest_rate": 12.0},
    ])
    # 100 * 1.01 - 30 = 71.0
    assert df.iloc[-1]["balance_eur"] == pytest.approx(71.0, abs=1e-3)


def test_withdrawal_cannot_push_balance_below_zero():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": 100.0, "interest_rate": 0.0},
        {"goal_name": "G", "date": "2025-02-01", "deposited_eur": -250.0, "interest_rate": 0.0},
    ])
    assert df.iloc[-1]["balance_eur"] == 0.0


def test_negative_first_deposit_clamped_to_zero():
    df = _savings_df([
        {"goal_name": "G", "date": "2025-01-01", "deposited_eur": -50.0, "interest_rate": 0.0},
    ])
    assert df.iloc[0]["balance_eur"] == 0.0
