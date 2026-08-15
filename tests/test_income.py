"""
Tests for income-type handling (db._fill_income_types) and raise detection
(gamification.detect_raise).
"""

import pandas as pd

from db import _fill_income_types
from gamification import detect_raise


def test_fill_income_types_maps_legacy_sources():
    df = pd.DataFrame({
        "source": ["Primary Salary", "Freelance / Side Income",
                   "Investment Returns", "Rental Income", "Other", "Salary"],
        "income_type": [None, None, None, None, None, None],
    })
    out = _fill_income_types(df)
    assert out["income_type"].tolist() == [
        "Salary", "Freelance", "Investment", "Rental", "Other", "Other",
    ]


def test_fill_income_types_keeps_existing_values():
    df = pd.DataFrame({
        "source": ["Salary", "Primary Salary"],
        "income_type": ["Hourly", None],
    })
    out = _fill_income_types(df)
    assert out["income_type"].tolist() == ["Hourly", "Salary"]


def _inc_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_detect_raise_requires_two_salaries():
    df = _inc_df([{"income_type": "Salary", "date": "2025-01-01", "actual_eur": 1000.0}])
    assert detect_raise(df) is False


def test_detect_raise_finds_increase():
    df = _inc_df([
        {"income_type": "Salary", "date": "2025-01-01", "actual_eur": 1000.0},
        {"income_type": "Salary", "date": "2025-04-01", "actual_eur": 1100.0},
    ])
    assert detect_raise(df) is True


def test_detect_raise_ignores_decreases():
    df = _inc_df([
        {"income_type": "Salary", "date": "2025-01-01", "actual_eur": 1200.0},
        {"income_type": "Salary", "date": "2025-04-01", "actual_eur": 1100.0},
    ])
    assert detect_raise(df) is False


def test_detect_raise_ignores_other_types():
    df = _inc_df([
        {"income_type": "Salary", "date": "2025-01-01", "actual_eur": 1000.0},
        {"income_type": "Bonus / Raise", "date": "2025-02-01", "actual_eur": 5000.0},
    ])
    assert detect_raise(df) is False
