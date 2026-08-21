"""
forecasting.py — Lightweight server-side ML for forecasts and insights.

All models run on the server (the phone only renders results), so they work
identically on any device including budget Android phones:

1. ETS (Holt-Winters) next-month spending forecast — statsmodels.
2. IsolationForest transaction anomaly detection — scikit-learn (M4 enriched).
3. Learned expense categorizer (TF-IDF + LogisticRegression) trained on the
   user's own descriptions — used by bank import and receipt OCR (M2 FeatureUnion).

Every model degrades gracefully: not enough history -> forecast falls back,
too few rows -> no anomalies, untrained classifier -> keyword-map fallback.
"""

import math
from dataclasses import dataclass
import pandas as pd
import streamlit as st

MIN_HISTORY_MONTHS = 6
MIN_ROWS_FOR_ANOMALIES = 20


@dataclass(frozen=True)
class ExpenseAnomaly:
    transaction_id: int | str
    score: float
    severity: str
    reasons: list[str]


# ── 1. Spend forecast (ETS) ──────────────────────────────────────────────────

def _monthly_totals(expenses_df: pd.DataFrame) -> pd.DataFrame:
    if expenses_df is None or expenses_df.empty:
        return pd.DataFrame()
    df = expenses_df.copy()
    df["ym"] = df["date"].dt.to_period("M")
    t = df.groupby("ym")["amount_eur"].sum().reset_index()
    t["ds"] = t["ym"].dt.to_timestamp()
    return t.sort_values("ds")


def _elapsed_months(t: pd.DataFrame) -> int:
    """Calendar months spanned by the history (max - min period + 1),
    NOT the number of rows — six purchases spread over three years are not
    six months of history."""
    if t is None or t.empty:
        return 0
    return int((t["ym"].max() - t["ym"].min()).n) + 1


def _ets_forecast(expenses_df: pd.DataFrame):
    """Returns (point, lower, upper) or (None, None, None) when history is
    too short or sparse. Sparse histories fall back instead of interpolating
    spending that never happened (a two-year-old purchase must not become
    continuous monthly spending)."""
    t = _monthly_totals(expenses_df)
    if _elapsed_months(t) < MIN_HISTORY_MONTHS:
        return None, None, None
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        idx = pd.period_range(t["ym"].min(), t["ym"].max(), freq="M")
        series = t.set_index("ym")["amount_eur"].reindex(idx).astype(float)
        if series.isna().any() or float(series.sum()) <= 0:
            return None, None, None
        ts = pd.Series(series.values, index=idx.to_timestamp())
        model = ExponentialSmoothing(ts, trend="add", initialization_method="estimated").fit()
        raw_fc = float(model.forecast(1).iloc[0])
        if not math.isfinite(raw_fc) or raw_fc < 0:
            return None, None, None
        sd = float(model.resid.std()) if len(model.resid) else 0.0
        return raw_fc, max(raw_fc - 2 * sd, 0.0), raw_fc + 2 * sd
    except Exception:
        return None, None, None


def _candidate_prediction(values, name: str, recurring_templates=None):
    """One-step forecast for a compact monthly series."""
    import numpy as np
    values = np.asarray(values, dtype=float)
    if not len(values):
        return None
    if name == "last_month":
        return float(values[-1])
    if name == "mean_3":
        return float(values[-3:].mean())
    if name == "mean_6":
        return float(values[-6:].mean())
    if name == "ewma":
        return float(pd.Series(values).ewm(span=min(6, len(values)), adjust=False).mean().iloc[-1])
    if name == "ets":
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            fit = ExponentialSmoothing(values, trend="add", initialization_method="estimated").fit()
            return max(0.0, float(fit.forecast(1)[0]))
        except Exception:
            return None
    if name == "hybrid" and recurring_templates is not None:
        try:
            recurring = recurring_templates
            if isinstance(recurring_templates, pd.DataFrame):
                recurring = recurring_templates
                if "active" in recurring:
                    recurring = recurring[recurring["active"].fillna(False).astype(bool)]
                recurring = recurring["amount_eur"]
            obligations = float(sum(float(x) for x in recurring))
            discretionary = pd.Series(values - obligations).clip(lower=0)
            return obligations + float(discretionary.ewm(
                span=min(6, len(discretionary)), adjust=False).mean().iloc[-1])
        except Exception:
            return None
    return None


def _forecast_metrics(actual, predicted):
    import numpy as np
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if not len(a):
        return {"mae": None, "smape": None, "bias": None}
    denom = np.maximum((np.abs(a) + np.abs(p)) / 2, 1e-9)
    return {"mae": float(np.mean(np.abs(a - p))),
            "smape": float(np.mean(np.abs(a - p) / denom) * 100),
            "bias": float(np.mean(p - a))}


def backtest_forecasts(monthly_values, recurring_templates=None) -> dict:
    """Rolling-origin evaluation and conservative model selection.

    A candidate must beat last-month MAE by at least 5%; fewer than three
    origins always retain the baseline.
    """
    import numpy as np
    values = np.asarray(list(monthly_values), dtype=float)
    names = ["last_month", "mean_3", "mean_6", "ewma", "ets"]
    if recurring_templates is not None:
        names.append("hybrid")
    predictions = {name: [] for name in names}
    actual = {name: [] for name in names}
    # Three months of training is enough for rolling comparisons; mean_6 is
    # skipped until six observations are available at a given origin.
    for i in range(3, len(values)):
        train = values[:i]
        for name in names:
            if name == "mean_6" and len(train) < 6:
                continue
            pred = _candidate_prediction(train, name, recurring_templates)
            if pred is not None and np.isfinite(pred):
                predictions[name].append(pred)
                actual[name].append(values[i])
    metrics = {name: _forecast_metrics(actual[name], predictions[name])
               for name in names if len(actual[name]) >= 3}
    selected = "last_month"
    reason = "baseline (fewer than 3 backtest origins)"
    baseline = metrics.get("last_month", {}).get("mae")
    if baseline is not None and baseline > 0:
        eligible = [(m["mae"], name) for name, m in metrics.items()
                    if name != "last_month" and m.get("mae") is not None
                    and m["mae"] <= baseline * 0.95]
        if eligible:
            _, selected = min(eligible)
            reason = f"{selected} beats last_month by at least 5% MAE"
        else:
            reason = "baseline retained (no candidate beats it by 5% MAE)"
    return {"selected_model": selected, "metrics": metrics,
            "origins": len(actual.get("last_month", [])), "reason": reason}
def forecast_next_month(expenses_df: pd.DataFrame, recurring_templates=None) -> dict:
    """ML forecast of next month's spending (total + per category).

    Returns {"total", "lower", "upper", "by_category", "fallback",
    "history_months"}. When history is too short, fallback=True and the
    caller uses the existing period-average projection.
    """
    ets_total, ets_lower, ets_upper = _ets_forecast(expenses_df)
    totals = _monthly_totals(expenses_df)
    selection = backtest_forecasts(totals["amount_eur"].tolist(), recurring_templates) if not totals.empty else {
        "selected_model": "last_month", "metrics": {}, "origins": 0,
        "reason": "baseline (no history)"}
    selected = selection["selected_model"]
    total = _candidate_prediction(totals["amount_eur"].tolist(), selected, recurring_templates) if ets_total is not None else None
    if total is None:
        total, lower, upper = ets_total, ets_lower, ets_upper
    elif selected == "ets":
        lower, upper = ets_lower, ets_upper
    else:
        lower, upper = max(0.0, total * 0.8), total * 1.2
    out = {
        "total": total, "lower": lower, "upper": upper,
        "by_category": {}, "fallback": total is None,
        "history_months": _elapsed_months(_monthly_totals(expenses_df)),
        "selected_model": selected,
        "model_metrics": selection["metrics"],
        "backtest_origins": selection["origins"],
        "selection_reason": selection["reason"],
    }
    if expenses_df is None or expenses_df.empty:
        return out
    for cat in expenses_df["category"].dropna().unique():
        sub = expenses_df[expenses_df["category"] == cat]
        cat_fc, _, _ = _ets_forecast(sub)
        if cat_fc is not None:
            out["by_category"][cat] = round(cat_fc, 2)
    return out


# ── 2. Anomaly detection (IsolationForest + robust stats) ────────────────────

def _merchant_key_series(desc_series: pd.Series) -> pd.Series:
    """Merchant-normalized key for grouping."""
    try:
        from domain.merchant import normalize_merchant
        return desc_series.fillna("").astype(str).apply(normalize_merchant)
    except Exception:
        return desc_series.fillna("").astype(str).str.strip().str.lower()


def detect_anomalies(expenses_df: pd.DataFrame, contamination: float = 0.05) -> pd.DataFrame:
    """Flag unusual transactions; returns the flagged rows with scores.

    M4 enriched features:
      log_amount, amount/user_median, amount/category_median, amount/merchant_median,
      merchant counts/age/is_new, dow/month/dom, days_since_same_merchant,
      recurring_probability, plus robust median/MAD explanations.
    """
    if expenses_df is None or expenses_df.empty or len(expenses_df) < MIN_ROWS_FOR_ANOMALIES:
        return pd.DataFrame()
    try:
        from sklearn.ensemble import IsolationForest
    except Exception:
        return pd.DataFrame()

    import numpy as np

    df = expenses_df.copy()
    # Ensure date is datetime
    if not pd.api.types.is_datetime64_any_dtype(df.get("date")):
        try:
            df["date"] = pd.to_datetime(df["date"])
        except Exception:
            pass

    # Base temporal
    try:
        df["dow"] = df["date"].dt.dayofweek
        df["month"] = df["date"].dt.month
        df["dom"] = df["date"].dt.day
    except Exception:
        df["dow"] = 0
        df["month"] = 1
        df["dom"] = 1

    # Amount features
    df["log_amount"] = np.log1p(df["amount_eur"].fillna(0).clip(lower=0))
    user_median = float(df["amount_eur"].median()) if not df["amount_eur"].empty else 1.0
    if user_median <= 0:
        user_median = 1.0
    df["amount_vs_user_median"] = df["amount_eur"] / user_median

    # Category medians
    try:
        cat_medians = df.groupby("category")["amount_eur"].median()
        df["cat_median"] = df["category"].map(cat_medians)
        df["amount_vs_cat_median"] = df["amount_eur"] / df["cat_median"].replace(0, np.nan)
        df["amount_vs_cat_median"] = df["amount_vs_cat_median"].fillna(1.0)
    except Exception:
        df["cat_median"] = user_median
        df["amount_vs_cat_median"] = df["amount_vs_user_median"]

    # Merchant features
    df["_merchant_key"] = _merchant_key_series(df["description"] if "description" in df.columns else pd.Series([""] * len(df)))
    try:
        merch_medians = df.groupby("_merchant_key")["amount_eur"].median()
        df["merch_median"] = df["_merchant_key"].map(merch_medians)
        df["amount_vs_merch_median"] = df["amount_eur"] / df["merch_median"].replace(0, np.nan)
        df["amount_vs_merch_median"] = df["amount_vs_merch_median"].fillna(1.0)
        merch_counts = df.groupby("_merchant_key")["_merchant_key"].transform("count")
        df["merchant_count"] = merch_counts.astype(float)
        # merchant age
        merch_min = df.groupby("_merchant_key")["date"].transform("min")
        merch_max = df.groupby("_merchant_key")["date"].transform("max")
        try:
            df["merchant_age_days"] = (merch_max - merch_min).dt.days.astype(float).fillna(0)
        except Exception:
            df["merchant_age_days"] = 0.0
        df["is_new_merchant"] = (df["merchant_count"] <= 1).astype(float)
        # days since same merchant
        df_sorted = df.sort_values(["_merchant_key", "date"])
        df_sorted["_prev_date"] = df_sorted.groupby("_merchant_key")["date"].shift(1)
        try:
            df_sorted["days_since_same_merchant"] = (df_sorted["date"] - df_sorted["_prev_date"]).dt.days.astype(float)
        except Exception:
            df_sorted["days_since_same_merchant"] = np.nan
        df_sorted["days_since_same_merchant"] = df_sorted["days_since_same_merchant"].fillna(999.0)
        # map back to original order
        df["days_since_same_merchant"] = df_sorted["days_since_same_merchant"].reindex(df.index).fillna(999.0)
    except Exception:
        df["merch_median"] = user_median
        df["amount_vs_merch_median"] = df["amount_vs_user_median"]
        df["merchant_count"] = 1.0
        df["merchant_age_days"] = 0.0
        df["is_new_merchant"] = 0.0
        df["days_since_same_merchant"] = 999.0

    # Recurring probability heuristic: merchants with monthly cadence -> high prob
    # Use detect_subscriptions as signal would be circular; approximate via gap stats
    try:
        gap_means = {}
        for key, grp in df.groupby("_merchant_key"):
            if len(grp) >= 3:
                dates = grp["date"].dropna().sort_values()
                gaps = dates.diff().dropna().dt.days
                avg_gap = float(gaps.mean()) if len(gaps) else 999
                if 25 <= avg_gap <= 35:
                    gap_means[key] = 0.9
                elif 5 <= avg_gap <= 9:
                    gap_means[key] = 0.85
                elif 80 <= avg_gap <= 100:
                    gap_means[key] = 0.8
                elif 340 <= avg_gap <= 390:
                    gap_means[key] = 0.8
                else:
                    gap_means[key] = 0.2
            else:
                gap_means[key] = 0.1
        df["recurring_prob"] = df["_merchant_key"].map(gap_means).fillna(0.1).astype(float)
    except Exception:
        df["recurring_prob"] = 0.1

    feature_cols = [
        "amount_eur", "log_amount", "amount_vs_user_median", "amount_vs_cat_median",
        "amount_vs_merch_median", "merchant_count", "merchant_age_days",
        "is_new_merchant", "dow", "month", "dom", "days_since_same_merchant",
        "recurring_prob",
    ]
    # Keep only existing
    feature_cols = [c for c in feature_cols if c in df.columns]
    X = df[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)

    model = IsolationForest(contamination=contamination, random_state=42)
    labels = model.fit_predict(X)
    df["anomaly_score"] = model.decision_function(X)
    flagged = df[labels == -1].sort_values("anomaly_score").copy()

    # Backward-compat multiplier (already computed cat_median)
    flagged["multiplier"] = flagged.apply(
        lambda r: round(float(r["amount_eur"]) / float(r["cat_median"]), 1)
        if r["cat_median"] and r["cat_median"] > 0 else None, axis=1)

    # Severity by score quantile
    try:
        q_low = flagged["anomaly_score"].quantile(0.33) if not flagged.empty else 0
        q_mid = flagged["anomaly_score"].quantile(0.66) if not flagged.empty else 0
        def _severity(s):
            if s <= q_low:
                return "high"
            if s <= q_mid:
                return "medium"
            return "low"
        flagged["severity"] = flagged["anomaly_score"].apply(_severity)
    except Exception:
        flagged["severity"] = "medium"

    # Reasons per flagged row
    def _reasons_for_row(r):
        reasons: list[str] = []
        try:
            amt = float(r["amount_eur"])
            cat_med = float(r.get("cat_median") or 0)
            if cat_med > 0 and amt > cat_med * 3:
                mult = round(amt / cat_med, 1)
                reasons.append(f"{mult}× your normal {r['category']} transaction")
            merch_med = float(r.get("merch_median") or 0)
            if merch_med > 0 and amt > merch_med * 2:
                reasons.append(f"{round(amt/merch_med,1)}× your typical {r.get('_merchant_key','merchant')} amount")
            if r.get("is_new_merchant", 0) == 1:
                reasons.append("first transaction with this merchant")
            # largest for merchant in window
            try:
                key = r.get("_merchant_key")
                if key and not df.empty:
                    mer_rows = df[df["_merchant_key"] == key]
                    if not mer_rows.empty and amt >= float(mer_rows["amount_eur"].max()) - 1e-9:
                        # count months window
                        span_days = float(r.get("merchant_age_days", 0))
                        months = max(1, int(span_days // 30))
                        reasons.append(f"largest {_merchant_key_display(key)} transaction in {months} months")
            except Exception:
                pass
            if float(r.get("amount_vs_user_median", 0)) > 4:
                reasons.append(f"{round(float(r['amount_vs_user_median']),1)}× your median transaction")
            if not reasons:
                reasons.append(f"unusual amount €{amt:.2f} (score {float(r['anomaly_score']):.3f})")
        except Exception:
            reasons.append("unusual transaction")
        return reasons

    def _merchant_key_display(k: str) -> str:
        try:
            from domain.merchant import match_merchant
            m = match_merchant(k)
            return m.canonical or k
        except Exception:
            return k

    flagged["reasons"] = flagged.apply(_reasons_for_row, axis=1)

    # Robust MAD-based explanations as supplement: flag if |modified z| > 3.5
    try:
        for cat, grp in df.groupby("category"):
            med = float(grp["amount_eur"].median())
            mad = float((grp["amount_eur"] - med).abs().median())
            q1 = float(grp["amount_eur"].quantile(0.25))
            q3 = float(grp["amount_eur"].quantile(0.75))
            iqr = q3 - q1
            if mad > 0:
                for idx in flagged[flagged["category"] == cat].index:
                    amt = float(flagged.at[idx, "amount_eur"])
                    mz = 0.6745 * (amt - med) / mad
                    if abs(mz) > 3.5 and len(flagged.at[idx, "reasons"]) < 3:
                        flagged.at[idx, "reasons"] = list(flagged.at[idx, "reasons"]) + [f"robust outlier (modified z={mz:.1f})"]
            if iqr > 0:
                upper = q3 + 1.5 * iqr
                for idx in flagged[flagged["category"] == cat].index:
                    amt = float(flagged.at[idx, "amount_eur"])
                    if amt > upper and len(flagged.at[idx, "reasons"]) < 3:
                        flagged.at[idx, "reasons"] = list(flagged.at[idx, "reasons"]) + [
                            f"above the normal {cat} range (IQR)"
                        ]
    except Exception:
        pass

    # Clean helper columns for output: keep useful ones, drop internal
    # Preserve _merchant_key for debugging but not required
    return flagged


def structured_anomalies(expenses_df: pd.DataFrame, contamination: float = 0.05) -> list[ExpenseAnomaly]:
    """Return the anomaly scan as stable domain records for UI/API callers."""
    flagged = detect_anomalies(expenses_df, contamination)
    if flagged.empty:
        return []
    return [
        ExpenseAnomaly(
            transaction_id=row.get("transaction_id", row.get("id", idx)),
            score=float(row["anomaly_score"]),
            severity=str(row.get("severity", "medium")),
            reasons=list(row.get("reasons", [])),
        )
        for idx, row in flagged.iterrows()
    ]


# ── 3. Learned categorizer (TF-IDF + LogisticRegression) ─────────────────────

def _prepare_texts(series: pd.Series) -> list[str]:
    """Merchant-normalized + cleaned texts for ML."""
    out: list[str] = []
    try:
        from domain.merchant import normalize_merchant
        has_merchant = True
    except Exception:
        has_merchant = False
        normalize_merchant = None  # type: ignore
    for raw in series.astype(str):
        s = str(raw).strip()
        base = s.lower()
        if has_merchant and s:
            try:
                norm = normalize_merchant(s)
                if norm and norm not in base:
                    # keep merchant token prefix for char n-grams to catch noisy strings
                    out.append(f"{norm} {base}")
                    continue
            except Exception:
                pass
        out.append(base)
    return out


def _build_vectorizer():
    """Word + char FeatureUnion for noisy merchant strings like MCDONALDS BG."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import FeatureUnion
        word = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
        return FeatureUnion([("word", word), ("char", char)])
    except Exception:
        # Fallback to single word vectorizer
        from sklearn.feature_extraction.text import TfidfVectorizer
        return TfidfVectorizer(ngram_range=(1, 2), min_df=1)


class _SubcategorizerModel:
    """Per-category subcategory classifier: FeatureUnion(TF-IDF) + LogisticRegression.

    Trained only on rows of ONE category that have a non-empty subcategory;
    requires at least 8 rows and 2 distinct subcategories.
    """

    def __init__(self):
        self.vec = None
        self.clf = None
        self.subcategories = []
        self.trained_rows = 0

    def train(self, df: pd.DataFrame) -> bool:
        if df is None or len(df) < 8 or "subcategory" not in df.columns:
            return False
        d = df[df["subcategory"].fillna("").astype(str).str.strip() != ""].copy()
        d = d[["description", "subcategory"]].dropna()
        if d["subcategory"].nunique() < 2 or len(d) < 8:
            return False
        try:
            from sklearn.linear_model import LogisticRegression
        except Exception:
            return False
        try:
            self.vec = _build_vectorizer()
            texts = _prepare_texts(d["description"])
            X = self.vec.fit_transform(texts)
        except Exception:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
            X = self.vec.fit_transform(d["description"].astype(str))
        self.clf = LogisticRegression(max_iter=500)
        # Calibrated classifier when enough data
        if len(d) >= 50 and d["subcategory"].nunique() >= 2:
            try:
                from sklearn.calibration import CalibratedClassifierCV
                base = self.clf
                self.clf = CalibratedClassifierCV(base, cv=3)
            except Exception:
                pass
        try:
            self.clf.fit(X, d["subcategory"])
        except Exception:
            # Calibration may fail on tiny folds; fall back to base
            try:
                from sklearn.linear_model import LogisticRegression
                self.clf = LogisticRegression(max_iter=500)
                self.clf.fit(X, d["subcategory"])
            except Exception:
                return False
        try:
            self.subcategories = list(self.clf.classes_)
        except Exception:
            # CalibratedClassifierCV wraps classes differently
            try:
                self.subcategories = list(self.clf.base_estimator.classes_)  # type: ignore
            except Exception:
                self.subcategories = sorted(d["subcategory"].unique())
        self.trained_rows = len(d)
        return True

    def predict(self, text: str):
        if self.clf is None or self.vec is None:
            return None, 0.0
        try:
            texts = _prepare_texts(pd.Series([str(text)]))
            X = self.vec.transform(texts)
        except Exception:
            X = self.vec.transform([str(text)])
        probs = self.clf.predict_proba(X)[0]
        idx = probs.argmax()
        return self.subcategories[idx], float(probs[idx])


class _CategorizerModel:
    def __init__(self):
        self.vec = None
        self.clf = None
        self.categories = []
        self.trained_rows = 0
        self.trained_fingerprint = None
        self.sub_models: dict = {}

    def train(self, expenses_df: pd.DataFrame) -> bool:
        if expenses_df is None or len(expenses_df) < 10:
            return False
        if "subcategory" not in expenses_df.columns:
            expenses_df = expenses_df.assign(subcategory="")
        df = expenses_df[["description","category","subcategory"]].dropna(
            subset=["description","category"])
        if df["category"].nunique() < 2 or len(df) < 10:
            return False
        try:
            from sklearn.linear_model import LogisticRegression
        except Exception:
            return False
        try:
            self.vec = _build_vectorizer()
            texts = _prepare_texts(df["description"])
            X = self.vec.fit_transform(texts)
        except Exception:
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
            X = self.vec.fit_transform(df["description"].astype(str))
        self.clf = LogisticRegression(max_iter=500)
        if len(df) >= 50 and df["category"].nunique() >= 3:
            try:
                from sklearn.calibration import CalibratedClassifierCV
                base = self.clf
                self.clf = CalibratedClassifierCV(base, cv=3)
            except Exception:
                pass
        try:
            self.clf.fit(X, df["category"])
        except Exception:
            try:
                from sklearn.linear_model import LogisticRegression
                self.clf = LogisticRegression(max_iter=500)
                self.clf.fit(X, df["category"])
            except Exception:
                return False
        try:
            self.categories = list(self.clf.classes_)
        except Exception:
            try:
                self.categories = list(self.clf.base_estimator.classes_)  # type: ignore
            except Exception:
                self.categories = sorted(df["category"].unique())
        self.trained_rows = len(df)
        # Train one subcategory classifier per category on its non-empty rows.
        self.sub_models = {}
        for cat, grp in df.groupby("category"):
            sm = _SubcategorizerModel()
            if sm.train(grp):
                self.sub_models[cat] = sm
        return True

    def predict(self, text: str):
        if self.clf is None or self.vec is None:
            return None, 0.0
        try:
            texts = _prepare_texts(pd.Series([str(text)]))
            X = self.vec.transform(texts)
        except Exception:
            X = self.vec.transform([str(text)])
        probs = self.clf.predict_proba(X)[0]
        idx = probs.argmax()
        return self.categories[idx], float(probs[idx])


# Bump when the training pipeline changes so old cached models are discarded.
# v4: word+char FeatureUnion + merchant normalization + optional calibration (M2).
CATEGORIZER_MODEL_VERSION = 4

# Confidence thresholds for the combined suggestion pipeline.
# Derive from evaluation: Auto-apply ≥ threshold giving ≥95% precision,
# suggest-only below that. Current 0.5 is the fallback.
CATEGORY_CONFIDENCE    = 0.5
SUBCATEGORY_CONFIDENCE = 0.4


def suggest_threshold_for_precision(y_true, y_prob, target_precision: float = 0.95) -> float:
    """Return threshold achieving ≥ target_precision if possible (helper for M2)."""
    try:
        from sklearn.metrics import precision_recall_curve
        precisions, _recalls, thresholds = precision_recall_curve(y_true, y_prob)
        for prec, thr in zip(precisions[:-1], thresholds):
            if prec >= target_precision:
                return float(thr)
    except Exception:
        pass
    return 0.5


def _categorizer_metrics(expenses_df: pd.DataFrame) -> dict[str, float]:
    """Evaluate a small held-out tail; fall back to a non-empty safe report."""
    if len(expenses_df) < 12:
        return {"accuracy": 0.0, "auto_threshold": CATEGORY_CONFIDENCE}
    split = max(2, len(expenses_df) // 5)
    train, holdout = expenses_df.iloc[:-split], expenses_df.iloc[-split:]
    probe = _CategorizerModel()
    if not probe.train(train):
        return {"accuracy": 0.0, "auto_threshold": CATEGORY_CONFIDENCE}
    predictions, confidences = zip(*(probe.predict(text) for text in holdout["description"]))
    try:
        from ml.evaluation import evaluate_classification, suggest_threshold_for_precision
        metrics = evaluate_classification(holdout["category"], predictions)
        correct = [actual == predicted for actual, predicted in zip(holdout["category"], predictions)]
        metrics["auto_threshold"] = suggest_threshold_for_precision(correct, confidences)
        return metrics or {"accuracy": 0.0, "auto_threshold": CATEGORY_CONFIDENCE}
    except Exception:
        return {"accuracy": 0.0, "auto_threshold": CATEGORY_CONFIDENCE}


def _active_categorizer(expenses_df: pd.DataFrame, user_id):
    """Train and register a candidate, but predict only from an active match."""
    fp = _dataset_fingerprint(expenses_df)
    model = get_categorizer(user_id, CATEGORIZER_MODEL_VERSION, fp)
    if model.clf is None or model.trained_fingerprint != fp:
        if model.train(expenses_df):
            model.trained_fingerprint = fp
    if model.clf is None or user_id is None:
        return model if user_id is None else None, CATEGORY_CONFIDENCE
    try:
        from ml.registry import get_active, get_registered, make_model_info, register_model
        existing = get_registered("expense_categorizer", user_id=user_id)
        if not any(info.dataset_fingerprint == fp for info in existing):
            register_model(make_model_info(
                "expense_categorizer", 0, model.trained_rows, fp,
                _categorizer_metrics(expenses_df)), user_id=user_id)
        active = get_active("expense_categorizer", user_id=user_id)
        if active is None or active.dataset_fingerprint != fp:
            return None, CATEGORY_CONFIDENCE
        return model, float(active.metrics.get("auto_threshold", CATEGORY_CONFIDENCE))
    except Exception:
        return None, CATEGORY_CONFIDENCE


def _dataset_fingerprint(expenses_df: pd.DataFrame) -> str:
    """Fingerprint of the labelled dataset: row count + a hash of every
    (description, category, subcategory) triple. ANY correction (category or
    subcategory edit), addition, or deletion changes the fingerprint and
    invalidates the cached model."""
    import hashlib
    if expenses_df is None or expenses_df.empty:
        return "empty"
    if "subcategory" not in expenses_df.columns:
        expenses_df = expenses_df.assign(subcategory="")
    df = expenses_df[["description", "category", "subcategory"]].dropna(
        subset=["description", "category"]).copy()
    df["description"] = df["description"].astype(str).str.strip().str.lower()
    df["category"] = df["category"].astype(str).str.strip().str.lower()
    df["subcategory"] = df["subcategory"].fillna("").astype(str).str.strip().str.lower()
    joined = sorted(
        f"{d}|{c}|{s}"
        for d, c, s in zip(df["description"], df["category"], df["subcategory"])
    )
    digest = hashlib.md5("\n".join(joined).encode("utf-8")).hexdigest()
    return f"{len(df)}|{digest}"


@st.cache_resource(max_entries=8)
def get_categorizer(user_id=None, model_version: int = CATEGORIZER_MODEL_VERSION,
                    fingerprint: str = "") -> _CategorizerModel:
    """One classifier per (user, model version, dataset fingerprint).

    cache_resource keys on all arguments, so when the user corrects or
    deletes categorised expenses the fingerprint changes and a FRESH model is
    trained on the new labels — no stale suggestions after edits. Accounts
    never leak training data into each other's suggestions."""
    return _CategorizerModel()


def clear_categorizers():
    """Drop every cached categorizer (e.g. on account deletion)."""
    get_categorizer.clear()


def suggest_category(expenses_df: pd.DataFrame, text: str,
                     min_confidence: float = 0.5, user_id=None):
    """Train-on-demand categorizer. Returns (category, confidence) or
    (None, conf) when untrained or below confidence."""
    model, evaluated_threshold = _active_categorizer(expenses_df, user_id)
    if model is None or model.clf is None:
        return None, 0.0
    cat, conf = model.predict(text)
    if conf >= max(min_confidence, evaluated_threshold):
        return cat, conf
    return None, conf


def suggest_category_and_subcategory(expenses_df: pd.DataFrame, text: str,
                                     min_confidence: float = CATEGORY_CONFIDENCE,
                                     min_sub_confidence: float = SUBCATEGORY_CONFIDENCE,
                                     user_id=None):
    """Combined category + subcategory suggestion.

    Returns (category, subcategory, cat_conf, sub_conf).

    The global classifier decides the category when its confidence is at
    least `min_confidence`; otherwise the keyword map decides BOTH. When the
    classifier wins and a per-category submodel exists, its subcategory is
    kept only when confident (>= min_sub_confidence), else "" — and the
    keyword map's subcategory is substituted only when the keyword map's
    category equals the classifier's category.

    cat_conf is the classifier probability when the classifier decided the
    category, otherwise 0.0 (keyword fallback). sub_conf is the submodel
    probability when the submodel decided the subcategory, otherwise 0.0.
    """
    from bank_import import categorize_expense

    kw_cat, kw_sub = categorize_expense(text)

    model, evaluated_threshold = _active_categorizer(expenses_df, user_id)

    if model is None or model.clf is None:
        return kw_cat, kw_sub, 0.0, 0.0

    cat, cat_conf = model.predict(text)
    if cat_conf < max(min_confidence, evaluated_threshold):
        # Classifier is not confident — keyword map decides both.
        return kw_cat, kw_sub, 0.0, 0.0

    sub, sub_conf = "", 0.0
    sm = model.sub_models.get(cat)
    if sm is not None:
        s, sc = sm.predict(text)
        if sc >= min_sub_confidence:
            sub, sub_conf = s, sc
    if not sub and kw_cat == cat and kw_sub:
        # Refinement: no confident subcategory, but the keyword map agrees on
        # the category — borrow its subcategory.
        sub = kw_sub
        sub_conf = 0.0
    return cat, sub, cat_conf, sub_conf


# ── 4. Subscription / recurring detection (M6 cadence-aware) ─────────────────

# Cadence buckets per M6
_CADENCE_RANGES: dict[str, tuple[int, int]] = {
    "weekly": (5, 9),
    "monthly": (25, 35),
    "quarterly": (80, 100),
    "annual": (340, 390),
}
_CADENCE_MAX_GAP: dict[str, int] = {
    "weekly": 15,
    "monthly": 60,
    "quarterly": 120,
    "annual": 500,
}


def _classify_gap(days: float) -> str | None:
    for name, (lo, hi) in _CADENCE_RANGES.items():
        if lo <= days <= hi:
            return name
    return None


def detect_subscriptions(expenses_df: pd.DataFrame, min_months: int = 3) -> pd.DataFrame:
    """Find recurring merchant charges with cadence detection (M6).

    Groups on merchant_key (domain/merchant normalized), examines transaction
    gaps for weekly/monthly/quarterly/annual cadence, allows amount drift,
    and detects median amount changes.

    Returns DataFrame with description, amount_eur (median), months_seen,
    avg_gap_days, cadence, amount_change_pct, last_date, sorted by most recent.
    """
    if expenses_df is None or expenses_df.empty:
        return pd.DataFrame()
    df = expenses_df.copy()
    if "description" not in df.columns:
        return pd.DataFrame()
    # Merchant-normalized grouping key (falls back to lowercased stripped)
    try:
        from domain.merchant import normalize_merchant
        df["_merchant_key"] = df["description"].fillna("").astype(str).apply(normalize_merchant)
        df["_merchant_key"] = df["_merchant_key"].replace("", pd.NA)
        # fallback for empty normalized keys -> original lower
        mask_empty = df["_merchant_key"].isna() | (df["_merchant_key"].str.strip() == "")
        df.loc[mask_empty, "_merchant_key"] = df.loc[mask_empty, "description"].fillna("").astype(str).str.strip().str.lower()
    except Exception:
        df["_merchant_key"] = df["description"].fillna("").astype(str).str.strip().str.lower()
    # Also need stripped lower for fallback display
    df["_desc_norm"] = df["description"].fillna("").astype(str).str.strip().str.lower()
    df = df[df["_merchant_key"].fillna("").astype(str).str.strip() != ""]
    if df.empty:
        return pd.DataFrame()
    groups = []
    for merchant_key, grp in df.groupby("_merchant_key"):
        if len(grp) < min_months:
            continue
        dates = grp["date"].dropna().sort_values()
        if len(dates) < min_months:
            continue
        gaps = dates.diff().dropna().dt.days.astype(float)
        if gaps.empty:
            continue
        avg_gap = float(gaps.mean()) if len(gaps) else 0.0
        max_gap = float(gaps.max()) if len(gaps) else 0.0
        # Cadence classification: majority bucket
        bucket_counts: dict[str, int] = {k: 0 for k in _CADENCE_RANGES}
        for g in gaps:
            b = _classify_gap(float(g))
            if b:
                bucket_counts[b] += 1
        dominant = max(bucket_counts, key=lambda k: bucket_counts[k])
        dominant_share = bucket_counts[dominant] / max(len(gaps), 1)
        # Require ≥60% of gaps in dominant bucket for a confident cadence
        if dominant_share < 0.6:
            # fallback: check if avg_gap falls in any bucket
            cadence = _classify_gap(avg_gap)
            if not cadence:
                continue
            # need at least one gap in that bucket
            if bucket_counts.get(cadence, 0) == 0:
                continue
        else:
            cadence = dominant
        # Validate max_gap bound for cadence
        if max_gap > _CADENCE_MAX_GAP.get(cadence, 60):
            continue
        # Regularity already enforced by dominant_share; also avg_gap must be in cadence range
        lo, hi = _CADENCE_RANGES[cadence]
        if not (lo <= avg_gap <= hi):
            # allow if dominant_share high but avg slightly off — still accept if 80% gaps in range
            if dominant_share < 0.8:
                continue
        # Amount drift: median overall, old vs new median
        amounts = grp["amount_eur"].dropna().astype(float)
        if amounts.empty:
            continue
        median_amt = float(amounts.median())
        # split half for drift
        half = len(amounts) // 2
        old_median = median_amt
        new_median = median_amt
        amount_change_pct = 0.0
        if half >= 2:
            sorted_by_date = grp.sort_values("date")
            old_median = float(sorted_by_date["amount_eur"].iloc[:half].median())
            new_median = float(sorted_by_date["amount_eur"].iloc[half:].median())
            if old_median > 0:
                amount_change_pct = round((new_median - old_median) / old_median * 100, 1)
            else:
                amount_change_pct = 0.0
        # Pick representative description: most frequent original description for this merchant
        try:
            rep_desc = grp["description"].mode().iloc[0] if not grp["description"].mode().empty else grp.iloc[0]["description"]
        except Exception:
            rep_desc = grp.iloc[0]["description"]
        # Most common category
        try:
            rep_cat = grp["category"].mode().iloc[0] if not grp["category"].mode().empty else grp.iloc[0]["category"]
        except Exception:
            rep_cat = grp.iloc[0].get("category", "")
        narrative = None
        if old_median > 0 and amount_change_pct >= 10:
            narrative = (f"{rep_desc} appears to have increased from "
                         f"€{old_median:.2f} to €{new_median:.2f}.")
        groups.append({
            "description": rep_desc,
            "merchant_key": merchant_key,
            "category": rep_cat,
            "amount_eur": median_amt,
            "old_median": old_median,
            "new_median": new_median,
            "months_seen": len(grp),
            "avg_gap_days": round(avg_gap, 1),
            "cadence": cadence,
            "amount_change_pct": amount_change_pct,
            "price_change_narrative": narrative,
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
