"""
Tests for the server-side ML helpers (forecasting.py).
"""

import pandas as pd
import pytest

from forecasting import (
    forecast_next_month, detect_anomalies, suggest_category,
    detect_subscriptions, cluster_month_patterns, suggest_budgets,
)
from forecasting import _CategorizerModel


def _expenses(months: int, base: float = 1000.0) -> pd.DataFrame:
    rows = []
    for m in range(months):
        year, month = 2024 + (m // 12), (m % 12) + 1
        rows.append({"date": pd.Timestamp(year, month, 5),
                     "category": "Food & Dining", "description": "groceries",
                     "amount_eur": base + m * 20})
    return pd.DataFrame(rows)


def test_forecast_falls_back_with_short_history():
    out = forecast_next_month(_expenses(4))
    assert out["fallback"] is True
    assert out["total"] is None


def test_forecast_with_enough_history():
    out = forecast_next_month(_expenses(12, base=1000.0))
    assert out["fallback"] is False
    assert out["total"] is not None
    assert out["total"] > 0
    assert out["lower"] <= out["total"] <= out["upper"]
    assert out["history_months"] == 12


def test_forecast_history_months_are_elapsed_not_row_count():
    """Six purchases spread over three years are NOT six months of history."""
    rows = [
        {"date": pd.Timestamp(2022, 1, 5), "category": "Other", "description": "a", "amount_eur": 100.0},
        {"date": pd.Timestamp(2022, 7, 5), "category": "Other", "description": "b", "amount_eur": 100.0},
        {"date": pd.Timestamp(2023, 1, 5), "category": "Other", "description": "c", "amount_eur": 100.0},
        {"date": pd.Timestamp(2023, 9, 5), "category": "Other", "description": "d", "amount_eur": 100.0},
        {"date": pd.Timestamp(2024, 3, 5), "category": "Other", "description": "e", "amount_eur": 100.0},
        {"date": pd.Timestamp(2024, 12, 5), "category": "Other", "description": "f", "amount_eur": 100.0},
    ]
    out = forecast_next_month(pd.DataFrame(rows))
    assert out["fallback"] is True
    assert out["total"] is None
    assert out["history_months"] == 36


def test_forecast_falls_back_when_a_month_is_missing():
    """A gap in an otherwise long history must not be interpolated into
    artificial continuous spending."""
    rows = []
    for m in (1, 2, 4, 5, 6, 7):  # March missing
        rows.append({"date": pd.Timestamp(2025, m, 5), "category": "X",
                     "description": "x", "amount_eur": 100.0 + m})
    out = forecast_next_month(pd.DataFrame(rows))
    assert out["fallback"] is True
    assert out["total"] is None
    assert out["history_months"] == 7


def test_anomalies_flags_outlier():
    rows = [{"date": pd.Timestamp(2025, 1, d), "category": "Food & Dining",
             "description": f"t{d}", "amount_eur": 10.0 + (d % 3)}
            for d in range(1, 29)]
    rows.append({"date": pd.Timestamp(2025, 1, 29), "category": "Food & Dining",
                 "description": "huge", "amount_eur": 5000.0})
    df = pd.DataFrame(rows)
    flagged = detect_anomalies(df, contamination=0.05)
    assert not flagged.empty
    assert "huge" in flagged["description"].tolist()


def test_anomalies_returns_empty_for_small_data():
    df = _expenses(3)
    assert detect_anomalies(df).empty


def test_categorizer_trains_and_predicts():
    df = pd.DataFrame({
        "description": ["lidl", "aldi", "kaufland", "maxi", "netflix", "hbo", "spotify"] * 4,
        "category": ["Food & Dining"] * 16 + ["Entertainment"] * 12,
    })
    model = _CategorizerModel()
    assert model.train(df) is True
    cat, conf = model.predict("lidl supermarket")
    assert cat == "Food & Dining"
    assert conf > 0.5


def test_categorizer_refuses_tiny_data():
    df = pd.DataFrame({"description": ["a", "b"], "category": ["X", "Y"]})
    model = _CategorizerModel()
    assert model.train(df) is False
    assert model.predict("a") == (None, 0.0)


# ── Subscription detection ────────────────────────────────────────────────────

def _monthly_rows():
    rows = []
    for m in range(1, 6):
        rows.append({"date": pd.Timestamp(2025, m, 3), "category": "Entertainment",
                     "description": "NETFLIX", "amount_eur": 12.99})
        rows.append({"date": pd.Timestamp(2025, m, 15), "category": "Food & Dining",
                     "description": f"groceries {m}", "amount_eur": 40.0 + m})
    return pd.DataFrame(rows)


def test_detect_subscriptions_finds_monthly_charges():
    subs = detect_subscriptions(_monthly_rows())
    assert len(subs) == 1
    assert subs.iloc[0]["description"] == "NETFLIX"
    assert subs.iloc[0]["months_seen"] == 5
    assert 25 <= subs.iloc[0]["avg_gap_days"] <= 35


def test_detect_subscriptions_ignores_irregular():
    rows = [
        {"date": pd.Timestamp(2025, 1, 3), "category": "X", "description": "one-off", "amount_eur": 10.0},
        {"date": pd.Timestamp(2025, 2, 3), "category": "X", "description": "one-off", "amount_eur": 10.0},
    ]
    assert detect_subscriptions(pd.DataFrame(rows)).empty


# ── Pattern clustering & budget suggestions ───────────────────────────────────

def test_cluster_month_patterns():
    df = _expenses(12, base=800.0)
    out = cluster_month_patterns(df)
    assert out["ok"] is True
    assert out["label"] is not None
    assert isinstance(out["dominant_categories"], list)


def test_cluster_short_history():
    out = cluster_month_patterns(_expenses(4))
    assert out["ok"] is False


def test_suggest_budgets_returns_categories():
    df = _expenses(8, base=500.0)
    out = suggest_budgets(df)
    assert "Food & Dining" in out
    assert out["Food & Dining"] > 0
