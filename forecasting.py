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
def get_categorizer(user_id=None) -> _CategorizerModel:
    """One classifier per user (cache_resource keys on the argument), so
    accounts never leak training data into each other's suggestions."""
    return _CategorizerModel()


def suggest_category(expenses_df: pd.DataFrame, text: str,
                     min_confidence: float = 0.5, user_id=None):
    """Train-on-demand categorizer. Returns (category, confidence) or
    (None, conf) when untrained or below confidence."""
    model = get_categorizer(user_id)
    if model.clf is None:
        model.train(expenses_df)
    elif expenses_df is not None and len(expenses_df) >= int(model.trained_rows * 1.5) + 20:
        # enough new labelled data — retrain
        model.train(expenses_df)
    if model.clf is None:
        return None, 0.0
    cat, conf = model.predict(text)
    if conf >= min_confidence:
        return cat, conf
    return None, conf


# ── 4. Subscription / recurring detection ────────────────────────────────────

def detect_subscriptions(expenses_df: pd.DataFrame, min_months: int = 3) -> pd.DataFrame:
    """Find (description, amount) pairs that repeat monthly — likely bills.

    Returns a DataFrame with description, amount_eur, months_seen, avg_gap_days
    and last_date, sorted by most recent.
    """
    if expenses_df is None or expenses_df.empty:
        return pd.DataFrame()
    df = expenses_df.copy()
    df["key"] = (df["description"].str.strip().str.lower()
                 + "|" + df["amount_eur"].round(2).astype(str))
    groups = []
    for key, grp in df.groupby("key"):
        if len(grp) < min_months:
            continue
        dates = grp["date"].dropna().sort_values()
        if len(dates) < min_months:
            continue
        gaps = dates.diff().dropna().dt.days
        avg_gap = float(gaps.mean()) if len(gaps) else 0.0
        if not (25 <= avg_gap <= 35):
            continue
        groups.append({
            "description": grp.iloc[0]["description"],
            "category": grp.iloc[0]["category"],
            "amount_eur": float(grp.iloc[0]["amount_eur"]),
            "months_seen": len(grp),
            "avg_gap_days": round(avg_gap, 1),
            "last_date": dates.iloc[-1],
        })
    out = pd.DataFrame(groups)
    if out.empty:
        return out
    return out.sort_values("last_date", ascending=False)


# ── 5. Monthly spending-pattern clustering (KMeans) ──────────────────────────

def cluster_month_patterns(expenses_df: pd.DataFrame, n_clusters: int = 3) -> dict:
    """Cluster months by their category spending mix; describe the current
    month's cluster. Returns {"ok", "label", "dominant_categories", ...}."""
    if expenses_df is None or expenses_df.empty:
        return {"ok": False}
    df = expenses_df.copy()
    df["ym"] = df["date"].dt.to_period("M")
    pivot = (df.pivot_table(index="ym", columns="category",
                            values="amount_eur", aggfunc="sum")
             .fillna(0))
    if len(pivot) < MIN_HISTORY_MONTHS:
        return {"ok": False, "reason": "short_history"}

    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return {"ok": False, "reason": "no_sklearn"}

    X = StandardScaler().fit_transform(pivot.values)
    km = KMeans(n_clusters=min(n_clusters, len(pivot)), random_state=42, n_init=10)
    labels = km.fit_predict(X)

    current = pivot.index[-1]
    current_label = int(labels[-1])
    # dominant categories: average profile of this cluster minus overall avg
    cluster_mask = labels == current_label
    profile = pivot.values[cluster_mask].mean(axis=0)
    overall = pivot.values.mean(axis=0)
    diff = profile - overall
    dom_idx = diff.argsort()[::-1][:3]
    dom = [(pivot.columns[i], float(diff[i])) for i in dom_idx if diff[i] > 0]

    return {
        "ok": True,
        "month": str(current),
        "label": int(current_label),
        "n_months_in_cluster": int(cluster_mask.sum()),
        "dominant_categories": dom,
        "avg_total": float(pivot.values[cluster_mask].sum(axis=1).mean()),
    }


# ── 6. Budget recommender (linear trend) ─────────────────────────────────────

def suggest_budgets(expenses_df: pd.DataFrame, months: int = 6) -> dict:
    """Per-category budget suggestion: recent mean + linear trend.

    Returns {category: suggested_monthly_eur} for categories with enough data.
    """
    if expenses_df is None or expenses_df.empty:
        return {}
    df = expenses_df.copy()
    df["ym"] = df["date"].dt.to_period("M")
    pivot = (df.pivot_table(index="ym", columns="category",
                            values="amount_eur", aggfunc="sum")
             .fillna(0).tail(months))
    out = {}
    for cat in pivot.columns:
        series = pivot[cat]
        if len(series) < 3 or float(series.sum()) <= 0:
            continue
        mean = float(series.mean())
        # linear trend over month index
        import numpy as np
        x = np.arange(len(series), dtype=float)
        y = series.values.astype(float)
        slope = float(np.polyfit(x, y, 1)[0])
        suggestion = mean + slope  # one step ahead
        out[cat] = round(max(suggestion, 0.0), 2)
    return out
