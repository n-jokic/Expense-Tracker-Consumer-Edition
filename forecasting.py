"""
forecasting.py — Lightweight server-side ML for forecasts and insights.

All models run on the server (the phone only renders results), so they work
identically on any device including budget Android phones:

1. ETS (Holt-Winters) next-month spending forecast — statsmodels.
2. IsolationForest transaction anomaly detection — scikit-learn.
3. Learned expense categorizer (TF-IDF + LogisticRegression) trained on the
   user's own descriptions — used by bank import and receipt OCR.

Every model degrades gracefully: not enough history -> forecast falls back,
too few rows -> no anomalies, untrained classifier -> keyword-map fallback.
"""

import pandas as pd
import streamlit as st

MIN_HISTORY_MONTHS = 6
MIN_ROWS_FOR_ANOMALIES = 20


# ── 1. Spend forecast (ETS) ──────────────────────────────────────────────────

def _monthly_totals(expenses_df: pd.DataFrame) -> pd.DataFrame:
    if expenses_df is None or expenses_df.empty:
        return pd.DataFrame()
    df = expenses_df.copy()
    df["ym"] = df["date"].dt.to_period("M")
    t = df.groupby("ym")["amount_eur"].sum().reset_index()
    t["ds"] = t["ym"].dt.to_timestamp()
    return t.sort_values("ds")


def _ets_forecast(expenses_df: pd.DataFrame):
    """Returns (point, lower, upper) or (None, None, None) with <6 months."""
    t = _monthly_totals(expenses_df)
    if len(t) < MIN_HISTORY_MONTHS:
        return None, None, None
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        series = t.set_index("ds")["amount_eur"].asfreq("MS").interpolate()
        model = ExponentialSmoothing(
            series, trend="add", initialization_method="estimated").fit()
        fc = max(float(model.forecast(1).iloc[0]), 0.0)
        sd = float(model.resid.std()) if len(model.resid) else 0.0
        return fc, max(fc - 2 * sd, 0.0), fc + 2 * sd
    except Exception:
        return None, None, None


def forecast_next_month(expenses_df: pd.DataFrame) -> dict:
    """ML forecast of next month's spending (total + per category).

    Returns {"total", "lower", "upper", "by_category", "fallback",
    "history_months"}. When history is too short, fallback=True and the
    caller uses the existing period-average projection.
    """
    total, lower, upper = _ets_forecast(expenses_df)
    out = {
        "total": total, "lower": lower, "upper": upper,
        "by_category": {}, "fallback": total is None,
        "history_months": len(_monthly_totals(expenses_df)),
    }
    if total is None or expenses_df is None or expenses_df.empty:
        return out
    for cat in expenses_df["category"].dropna().unique():
        sub = expenses_df[expenses_df["category"] == cat]
        cat_fc, _, _ = _ets_forecast(sub)
        if cat_fc is not None:
            out["by_category"][cat] = round(cat_fc, 2)
    return out


# ── 2. Anomaly detection (IsolationForest) ───────────────────────────────────

def detect_anomalies(expenses_df: pd.DataFrame, contamination: float = 0.05) -> pd.DataFrame:
    """Flag unusual transactions; returns the flagged rows with scores."""
    if expenses_df is None or expenses_df.empty or len(expenses_df) < MIN_ROWS_FOR_ANOMALIES:
        return pd.DataFrame()
    try:
        from sklearn.ensemble import IsolationForest
    except Exception:
        return pd.DataFrame()

    df = expenses_df.copy()
    df["dow"]      = df["date"].dt.dayofweek
    df["month"]    = df["date"].dt.month
    df["cat_code"] = df["category"].astype("category").cat.codes
    X = df[["amount_eur","dow","month","cat_code"]].fillna(0)

    model = IsolationForest(contamination=contamination, random_state=42)
    labels = model.fit_predict(X)
    df["anomaly_score"] = model.decision_function(X)
    flagged = df[labels == -1].sort_values("anomaly_score").copy()

    # Explanation: how far above the category's median amount this row is
    medians = df.groupby("category")["amount_eur"].median()
    flagged["cat_median"] = flagged["category"].map(medians)
    flagged["multiplier"] = flagged.apply(
        lambda r: round(float(r["amount_eur"]) / float(r["cat_median"]), 1)
        if r["cat_median"] and r["cat_median"] > 0 else None, axis=1)
    return flagged


# ── 3. Learned categorizer (TF-IDF + LogisticRegression) ─────────────────────

class _CategorizerModel:
    def __init__(self):
        self.vec = None
        self.clf = None
        self.categories = []
        self.trained_rows = 0

    def train(self, expenses_df: pd.DataFrame) -> bool:
        if expenses_df is None or len(expenses_df) < 10:
            return False
        df = expenses_df[["description","category"]].dropna()
        if df["category"].nunique() < 2 or len(df) < 10:
            return False
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
        except Exception:
            return False
        self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        X = self.vec.fit_transform(df["description"].astype(str))
        self.clf = LogisticRegression(max_iter=500)
        self.clf.fit(X, df["category"])
        self.categories = list(self.clf.classes_)
        self.trained_rows = len(df)
        return True

    def predict(self, text: str):
        if self.clf is None:
            return None, 0.0
        X = self.vec.transform([str(text)])
        probs = self.clf.predict_proba(X)[0]
        idx = probs.argmax()
        return self.categories[idx], float(probs[idx])


@st.cache_resource
def get_categorizer() -> _CategorizerModel:
    return _CategorizerModel()


def suggest_category(expenses_df: pd.DataFrame, text: str,
                     min_confidence: float = 0.5):
    """Train-on-demand categorizer. Returns (category, confidence) or
    (None, conf) when untrained or below confidence."""
    model = get_categorizer()
    if model.clf is None:
        model.train(expenses_df)
    if model.clf is None:
        return None, 0.0
    cat, conf = model.predict(text)
    if conf >= min_confidence:
        return cat, conf
    return None, conf
