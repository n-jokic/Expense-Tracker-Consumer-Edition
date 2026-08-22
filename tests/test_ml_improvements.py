"""research.md Phase M wave-1 - evaluation helpers, hybrid backtest candidate,
calibrated intervals, scaled anomaly features, and the shared ML anomalies()
service wrapper. All hermetic: synthetic frames, no network, no real DB.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

import forecasting as fc
import ml.evaluation as ev
import services.finance_queries as fq


# - Synthetic data -------------------------------------------------------------

def _monthly_expenses(n_months=9, base=1000.0):
    """Contiguous monthly history ending with the current month."""
    rows = []
    y, m = date.today().year, date.today().month
    for i in range(n_months):
        d = date(y, m, 15)
        rows.append({"date": pd.Timestamp(d), "amount_eur": base + i * 10.0,
                     "description": "Shop", "category": "Groceries",
                     "amount": base + i * 10.0, "currency": "EUR"})
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)


def _anomaly_frame(n_rows=25, outlier_eur=5000.0):
    """Small behavioural history plus one glaring outlier."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2026-01-01", periods=n_rows, freq="D")
    df = pd.DataFrame({
        "date": dates,
        "amount_eur": rng.normal(40.0, 5.0, n_rows).clip(min=5.0),
        "description": ["Kaffeehaus"] * (n_rows - 4) + ["Billa"] * 4,
        "category": ["Dining Out"] * (n_rows - 4) + ["Groceries"] * 4,
        "amount": 0.0,
        "currency": "EUR",
    })
    df.loc[df.index[-1], "amount_eur"] = outlier_eur
    df.loc[df.index[-1], "description"] = "Luxury Widget GmbH"
    df.loc[df.index[-1], "category"] = "Other"
    return df


# - (a) score_forecast ----------------------------------------------------------

def test_score_forecast_perfect_and_offset():
    assert ev.score_forecast([100, 200], [100, 200])["mae"] == pytest.approx(0.0)
    out = ev.score_forecast([100.0, 200.0], [110.0, 190.0])
    assert out["mae"] == pytest.approx(10.0)
    assert out["bias"] == pytest.approx(0.0)
    assert 0.0 <= out["smape"] <= 100.0


def test_score_forecast_empty_is_nan_not_crash():
    out = ev.score_forecast([], [])
    assert all(np.isnan(v) for v in out.values())


# - (b) rolling_origin_backtest -------------------------------------------------

def test_rolling_origin_backtest_happy_path():
    df = _monthly_expenses(9)
    res = ev.rolling_origin_backtest(
        df, lambda train: {"total": float(train["amount_eur"].mean())})
    assert res["ok"] is True and res["n"] >= 2
    assert np.isfinite(res["mae"])


def test_rolling_origin_backtest_short_history_reports_reason():
    res = ev.rolling_origin_backtest(_monthly_expenses(4), lambda t: {})
    assert res["ok"] is False and res["reason"] == "short_history"


# - (c) hybrid backtest candidate -----------------------------------------------

def test_backtest_includes_hybrid_candidate_with_templates():
    totals = [800.0 + 20 * i for i in range(8)]
    res = fc.backtest_forecasts(totals, recurring_templates=[150.0] * 3)
    assert "hybrid" in res["metrics"]
    assert res["origins"] >= 3


# - (d) finance_queries.anomalies() ML wrapper -----------------------------------

def test_anomalies_service_returns_ml_enriched_rows(monkeypatch):
    frame = _anomaly_frame()
    monkeypatch.setattr(fq, "db", type("DB", (), {"get_expenses": staticmethod(lambda uid: frame)}))
    rows = fq.anomalies(user_id=1)
    assert rows, "outlier frame must produce at least one flagged row"
    top = max(rows, key=lambda r: r["amount_eur"])
    assert top["description"] == "Luxury Widget GmbH"
    assert "anomaly_score" in top and "severity" in top and "reasons" in top
    assert isinstance(top["date"], str)


def test_anomalies_service_empty_history(monkeypatch):
    empty = _monthly_expenses(2).head(0)
    monkeypatch.setattr(fq, "db", type("DB", (), {"get_expenses": staticmethod(lambda uid: empty)}))
    assert fq.anomalies(user_id=1) == []


# - (e) calibrated prediction intervals ------------------------------------------

def test_forecast_intervals_bracket_the_point_estimate():
    out = fc.forecast_next_month(_monthly_expenses(9))
    assert out["total"] is not None
    assert out["lower"] <= out["total"] <= out["upper"]
    assert out["lower"] >= 0.0
    if out["selected_model"] != "ets" and out["backtest_origins"] >= 3:
        assert (out["upper"] - out["lower"]) <= out["total"] * 2.4


def test_forecast_short_history_falls_back_cleanly():
    out = fc.forecast_next_month(_monthly_expenses(4))
    assert out["fallback"] is True and out["total"] is None
    assert out["selection_reason"]


# - M3 smoke ----------------------------------------------------------------------

def test_detect_anomalies_flags_planted_outlier_with_scaled_features():
    flagged = fc.detect_anomalies(_anomaly_frame())
    assert not flagged.empty
    assert "anomaly_score" in flagged.columns
    assert (flagged["description"] == "Luxury Widget GmbH").any()


# - M5: balanced classifiers -------------------------------------------------------

def test_categorizer_trains_on_imbalanced_data():
    rows = []
    for i in range(40):
        rows.append({"description": f"Kaffee {i}", "category": "Dining Out",
                     "subcategory": "Coffee"})
    for i in range(3):  # rare class must remain learnable, not drowned out
        rows.append({"description": f"Zahnarzt {i}", "category": "Health",
                     "subcategory": "Dentist"})
    model = fc._CategorizerModel()
    assert model.train(pd.DataFrame(rows)) is True
