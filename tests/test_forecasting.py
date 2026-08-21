"""
Tests for the server-side ML helpers (forecasting.py).
"""

import pandas as pd
import pytest

from forecasting import (
    forecast_next_month, detect_anomalies, suggest_category,
    suggest_category_and_subcategory,
    detect_subscriptions, cluster_month_patterns, suggest_budgets,
    backtest_forecasts,
)
from forecasting import _CategorizerModel, _SubcategorizerModel


def _expenses(months: int, base: float = 1000.0) -> pd.DataFrame:
    rows = []
    for m in range(months):
        year, month = 2024 + (m // 12), (m % 12) + 1
        rows.append({"date": pd.Timestamp(year, month, 5),
                     "category": "Groceries", "description": "groceries",
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


def test_forecast_selection_reports_backtest_metrics():
    out = forecast_next_month(_expenses(12, base=1000.0))
    assert out["selected_model"] in {"last_month", "mean_3", "mean_6", "ewma", "ets"}
    assert isinstance(out["model_metrics"], dict)
    assert out["backtest_origins"] >= 3


def test_backtest_requires_three_origins():
    result = backtest_forecasts([100.0, 110.0, 120.0])
    assert result["origins"] == 0
    assert result["selected_model"] == "last_month"


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


def test_forecast_never_leaks_nonfinite_total():
    """A corrupt legacy amount_eur (NaN/inf) must not turn the selected
    model's mean into a NaN 'prediction' that bypasses the is-None guards
    and renders as '€nan' — it falls back like missing history."""
    import numpy as np
    rows = []
    for m in range(1, 8):
        amount = 100.0 + m
        if m == 4:
            amount = float("nan")
        elif m == 6:
            amount = float("inf")
        rows.append({"date": pd.Timestamp(2025, m, 5), "category": "X",
                     "description": "x", "amount_eur": amount})
    out = forecast_next_month(pd.DataFrame(rows))
    total = out["total"]
    assert total is None or np.isfinite(total)
    if not out["fallback"]:
        assert np.isfinite(out["lower"]) and np.isfinite(out["upper"])


def test_candidate_prediction_returns_none_on_nonfinite():
    from forecasting import _candidate_prediction
    import numpy as np
    values = [100.0, 110.0, float("nan")]
    for name in ("last_month", "mean_3"):
        assert _candidate_prediction(values, name) is None
    assert _candidate_prediction([100.0, 200.0, 300.0], "mean_3") \
        == pytest.approx(200.0)


def test_anomalies_flags_outlier():
    rows = [{"date": pd.Timestamp(2025, 1, d), "category": "Groceries",
             "description": f"t{d}", "amount_eur": 10.0 + (d % 3)}
            for d in range(1, 29)]
    rows.append({"date": pd.Timestamp(2025, 1, 29), "category": "Groceries",
                 "description": "huge", "amount_eur": 5000.0})
    df = pd.DataFrame(rows)
    flagged = detect_anomalies(df, contamination=0.05)
    assert not flagged.empty
    assert "huge" in flagged["description"].tolist()


def test_anomalies_returns_empty_for_small_data():
    df = _expenses(3)
    assert detect_anomalies(df).empty


def test_categorizer_trains_and_predicts():
    rows = []
    for d in ["lidl", "aldi", "kaufland", "maxi"]:
        for _ in range(4):
            rows.append({"description": d, "category": "Groceries",
                         "subcategory": "Groceries"})
    for d in ["netflix", "hbo", "spotify", "cinema"]:
        for _ in range(4):
            rows.append({"description": d, "category": "Entertainment",
                         "subcategory": "Streaming Services"
                         if d in ("netflix", "hbo") else "Cinema & Theater"})
    df = pd.DataFrame(rows)
    model = _CategorizerModel()
    assert model.train(df) is True
    cat, conf = model.predict("lidl supermarket")
    assert cat == "Groceries"
    assert conf > 0.5
    # per-category submodels are trained where data allows (>=2 subcategories)
    assert "Entertainment" in model.sub_models


def test_categorizer_refuses_tiny_data():
    df = pd.DataFrame({"description": ["a", "b"], "category": ["X", "Y"]})
    model = _CategorizerModel()
    assert model.train(df) is False
    assert model.predict("a") == (None, 0.0)


# ── Per-category subcategorizer ───────────────────────────────────────────────

def test_subcategorizer_trains_and_predicts():
    df = pd.DataFrame({
        "description": ["lidl", "aldi", "kaufland", "tesco", "rewe", "edeka", "penny", "maxi"] * 3,
        "subcategory": ["Groceries"] * 16 + ["Supermarket"] * 8,
    })
    sm = _SubcategorizerModel()
    assert sm.train(df) is True
    sub, conf = sm.predict("lidl market")
    assert sub == "Groceries"
    assert conf > 0.5


def test_subcategorizer_refuses_thin_data():
    # fewer than 8 rows
    sm = _SubcategorizerModel()
    assert sm.train(pd.DataFrame({
        "description": ["a", "b", "c", "d", "e", "f", "g"],
        "subcategory": ["X"] * 4 + ["Y"] * 3,
    })) is False
    assert sm.predict("a") == (None, 0.0)
    # only one distinct subcategory
    assert _SubcategorizerModel().train(pd.DataFrame({
        "description": [f"d{i}" for i in range(10)],
        "subcategory": ["Only"] * 10,
    })) is False


# ── Combined category + subcategory suggestion ────────────────────────────────

def test_suggest_category_and_subcategory_classifier_wins():
    rows = [{"description": f"lidl {i}", "category": "Groceries", "subcategory": "Groceries"}
            for i in range(12)]
    rows += [{"description": f"starbucks {i}", "category": "Dining Out",
              "subcategory": "Coffee & Snacks"} for i in range(12)]
    rows += [{"description": f"pizzeria {i}", "category": "Dining Out",
              "subcategory": "Restaurants & Takeaway"} for i in range(12)]
    df = pd.DataFrame(rows)
    cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(
        df, "starbucks latte")
    assert cat == "Dining Out"
    assert sub == "Coffee & Snacks"
    assert cat_conf >= 0.5
    assert sub_conf >= 0.4


def test_suggest_category_and_subcategory_keyword_fallback_when_untrained():
    df = pd.DataFrame({"description": ["a"], "category": ["X"], "subcategory": [""]})
    cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(
        df, "lidl shop")
    assert cat == "Groceries"
    assert sub == "Groceries"
    assert cat_conf == 0.0
    assert sub_conf == 0.0


def test_suggest_refinement_borrows_keyword_subcategory():
    rows = [{"description": f"restaurant visit {i}", "category": "Dining Out",
             "subcategory": ""} for i in range(12)]
    rows += [{"description": f"netflix {i}", "category": "Entertainment",
              "subcategory": "Streaming Services"} for i in range(12)]
    df = pd.DataFrame(rows)
    cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(
        df, "restaurant")
    assert cat == "Dining Out"
    assert sub == "Restaurants & Takeaway"  # borrowed from the keyword map
    assert cat_conf >= 0.5
    assert sub_conf == 0.0


# ── Subscription detection ────────────────────────────────────────────────────

def _monthly_rows():
    rows = []
    for m in range(1, 6):
        rows.append({"date": pd.Timestamp(2025, m, 3), "category": "Entertainment",
                     "description": "NETFLIX", "amount_eur": 12.99})
        rows.append({"date": pd.Timestamp(2025, m, 15), "category": "Groceries",
                     "description": f"groceries {m}", "amount_eur": 40.0 + m})
    return pd.DataFrame(rows)


def test_detect_subscriptions_finds_monthly_charges():
    subs = detect_subscriptions(_monthly_rows())
    assert len(subs) == 1
    assert subs.iloc[0]["description"] == "NETFLIX"
    assert subs.iloc[0]["months_seen"] == 5
    assert 25 <= subs.iloc[0]["avg_gap_days"] <= 35
    assert subs.iloc[0]["old_median"] == subs.iloc[0]["new_median"] == 12.99
    assert subs.iloc[0]["price_change_narrative"] is None


def test_detect_subscriptions_reports_material_price_increase():
    rows = [{"date": pd.Timestamp(2025, m, 3), "category": "Entertainment",
             "description": "Spotify", "amount_eur": 7.99 if m < 5 else 8.99}
            for m in range(1, 8)]
    out = detect_subscriptions(pd.DataFrame(rows))
    assert len(out) == 1
    assert out.iloc[0]["old_median"] == 7.99
    assert out.iloc[0]["new_median"] == 8.99
    assert "increased" in out.iloc[0]["price_change_narrative"]


def test_detect_subscriptions_ignores_irregular():
    rows = [
        {"date": pd.Timestamp(2025, 1, 3), "category": "X", "description": "one-off", "amount_eur": 10.0},
        {"date": pd.Timestamp(2025, 2, 3), "category": "X", "description": "one-off", "amount_eur": 10.0},
    ]
    assert detect_subscriptions(pd.DataFrame(rows)).empty


def test_detect_subscriptions_tolerates_null_descriptions():
    """Regression: an all-null description column (nullable String dtype from
    sync/import) crashed the whole Insights page under pandas 3."""
    rows = [
        {"date": pd.Timestamp(2025, 1, 3), "category": "X", "description": None, "amount_eur": 10.0},
        {"date": pd.Timestamp(2025, 2, 3), "category": "X", "description": None, "amount_eur": 10.0},
        {"date": pd.Timestamp(2025, 3, 3), "category": "X", "description": None, "amount_eur": 10.0},
    ]
    out = detect_subscriptions(pd.DataFrame(rows))
    assert out.empty  # no crash; null descriptions simply match nothing


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
    assert "Groceries" in out
    assert out["Groceries"] > 0
