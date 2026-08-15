"""
Tests for the server-side ML helpers (forecasting.py).
"""

import pandas as pd
import pytest

from forecasting import forecast_next_month, detect_anomalies, suggest_category
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
