# Forecasting & Anomalies — Agent Intelligence Contract

> **Source of truth:** `forecasting.py` (459 LOC) + `app_pages/forecast.py` (175 LOC)
> **Tests:** `tests/test_forecasting.py`, `tests/test_forecast.py`
> **Owner:** Agent 7 — Intelligence (Forecasting / Insights / LLM)
> All models run **server-side** (Streamlit server). The phone only renders results.
> Every model **degrades gracefully** — never crashes the page.

---

## 1. Purpose & Scope

`forecasting.py` provides five server-side ML helpers:

| # | Helper | Library | What it does |
|---|--------|---------|---------------|
| 1 | **ETS next-month forecast** | `statsmodels.tsa.holtwinters.ExponentialSmoothing` | Holt-Winters point + 80% band for total spend and per-category |
| 2 | **Anomaly detection** | `sklearn.ensemble.IsolationForest` | Flags unusual transactions |
| 3 | **Learned categorizer** | `sklearn.feature_extraction.text.TfidfVectorizer` + `LogisticRegression` | Predicts `category` (and per-category `subcategory`) from `description` |
| 4 | **Subscription detection** | pure pandas | Finds `(description, amount)` pairs that repeat monthly |
| 5 | **Month-pattern clustering + budget recommender** | `sklearn.cluster.KMeans` / `numpy.polyfit` | Clusters months by category-mix; suggests next-month budgets |

`market_data.py` is treated as **optional intelligence input** (portfolio holdings) — it does not drive forecasts but shares the same "free-source, stale-safe" philosophy (see §8).

---

## 2. Forecasting Internals — ETS (Holt-Winters)

### 2.1 Helpers

```python
MIN_HISTORY_MONTHS = 6
MIN_ROWS_FOR_ANOMALIES = 20
```

#### `_monthly_totals(expenses_df) -> DataFrame[ym, amount_eur, ds]`
- Copies `expenses_df`, adds `ym = date.dt.to_period("M")`.
- Groups by `ym`, sums `amount_eur`, adds `ds = ym.dt.to_timestamp()`, sorted by `ds`.
- Returns empty DataFrame when input is `None`/empty.

#### `_elapsed_months(t) -> int`
- **Calendar months spanned**, not row count: `int((t["ym"].max() - t["ym"].min()).n) + 1`.
- Six purchases spread over three years = **36 months** of elapsed history — not six. This is the gate for `MIN_HISTORY_MONTHS`.
- Returns `0` for `None`/empty.

### 2.2 `_ets_forecast(expenses_df) -> (point, lower, upper)`

Full decision table (every guard returns `(None, None, None)`):

| Guard (in order) | Condition | Rationale |
|------------------|-----------|-----------|
| Elapsed history | `_elapsed_months(_monthly_totals(df)) < 6` | Not enough calendar coverage — fallback |
| Missing month | `series.isna().any()` after reindex | **Never interpolate.** A month with no rows is a gap, not zero spend |
| Zero history | `float(series.sum()) <= 0` | All-zero spending is no signal |
| Degenerate fit | `not math.isfinite(raw_fc) or raw_fc < 0` | Statsmodels can return `nan/inf/negative` on sparse data |
| Import/runtime | `ImportError / OSError / any Exception` | `statsmodels` missing or fit failed |

Detailed steps when guards pass:

```python
idx    = pd.period_range(t["ym"].min(), t["ym"].max(), freq="M")
series = t.set_index("ym")["amount_eur"].reindex(idx).astype(float)
# isna / sum guards here
ts    = pd.Series(series.values, index=idx.to_timestamp())
model = ExponentialSmoothing(ts, trend="add", initialization_method="estimated").fit()
raw_fc = float(model.forecast(1).iloc[0])
# finiteness guard here
fc = raw_fc
sd = float(model.resid.std()) if len(model.resid) else 0.0
return fc, max(fc - 2*sd, 0.0), fc + 2*sd   # 2-σ band, lower clamped at 0
```

Key contracts:
- **Series reindex:** `period_range(min, max, freq="M")` + `reindex(idx)` — one entry per calendar month in the span. The Timestamp index `idx.to_timestamp()` is used for the model.
- **`isna() -> None`:** Any `NaT`/missing month produces `None` for all three outputs. A gap in an otherwise 6+ month history must **not** be filled with zeros or forward-filled values (see `test_forecast_falls_back_when_a_month_is_missing`).
- **Sum <= 0 -> None:** Prevents fitting on empty/zero-only histories.
- **Raw forecast `finite && >= 0`:** `math.isfinite` + non-negative check before accepting. Negative or non-finite fits are discarded.
- **2-SD band:** `sd = model.resid.std()`. Lower = `max(fc - 2*sd, 0)`, Upper = `fc + 2*sd`. An 80%–95% style interval (no explicit `alpha` — pure residual dispersion).
- **Model:** `trend="add"`, `initialization_method="estimated"`, single-step `forecast(1)`. No seasonal component — spending seasonality is handled by the caller (period-average vs ML toggle).

### 2.3 `forecast_next_month(expenses_df) -> dict`

```python
{
  "total":           float | None,   # point forecast
  "lower":           float | None,   # lower band
  "upper":           float | None,   # upper band
  "by_category":     dict[str, float],  # cat -> rounded fc (only when cat_fc is not None)
  "fallback":        bool,           # True when total is None — caller must use period-average
  "history_months":  int,            # _elapsed_months(_monthly_totals(df))
}
```

- Computes `total/lower/upper` via `_ets_forecast(df)`.
- Early return with `fallback=True` when `total is None` or `df` is empty.
- **Per-category loop:** for each `category` in `dropna().unique()`, slices `df[category == cat]`, runs `_ets_forecast(sub)` independently. Entry added only when `cat_fc is not None`, rounded to 2 decimals. Categories with insufficient history are silently omitted.
- **Caller contract (`app_pages/forecast.py`):** When `fallback` is `True` or `total is None`, the page shows a caption *"Not enough history (needs 6+ months)"* and falls back to `daily_avg * days_in_period` (burn-rate projection). When `fallback` is `False`, it shows `80% range: lower – upper` and `history_months` in the caption.

---

## 3. Anomaly Detection — IsolationForest

### 3.1 Signature

```python
def detect_anomalies(expenses_df: pd.DataFrame, contamination: float = 0.05) -> pd.DataFrame
```

### 3.2 Feature engineering

| Feature | Derivation | Notes |
|---------|-----------|-------|
| `amount_eur` | raw `amount_eur` column | Primary signal |
| `dow` | `date.dt.dayofweek` (0=Mon) | Captures weekday vs weekend patterns |
| `month` | `date.dt.month` (1–12) | Captures seasonal spikes |
| `cat_code` | `category.fillna("").astype("category").cat.codes` | **NaN -> empty string** first; NaN sentinel `-1` would be treated as a real class, so missing categories become their own code for `""` instead |
|`fillna(0)` on the final `X` matrix | | | Notes |

Feature matrix: `X = df[["amount_eur","dow","month","cat_code"]].fillna(0)`.

### 3.3 Model & output

```python
model  = IsolationForest(contamination=contamination, random_state=42)
labels = model.fit_predict(X)          # -1 = anomaly
scores = model.decision_function(X)     # stored as anomaly_score (lower = more anomalous)
flagged = df[labels == -1].sort_values("anomaly_score")
```

Enrichment on flagged rows:

```python
medians = df.groupby("category")["amount_eur"].median()
flagged["cat_median"] = flagged["category"].map(medians)
flagged["multiplier"] = amount / cat_median  # rounded to 1 decimal; None when median is 0/falsy
```

- Default `contamination=0.05` — ~5% flagged. Caller (`insights.py`) uses the default.
- Deterministic via `random_state=42`.
- Returned DataFrame is sorted by `anomaly_score` ascending (most anomalous first). Insights page re-sorts by `date desc` for display.
- `MIN_ROWS` gate: `len(df) < 20` -> empty frame. Mirrors the stated 20-row minimum.

---

## 4. Subscription / Recurring Detection

```python
def detect_subscriptions(expenses_df, min_months: int = 3) -> DataFrame
```

| Step | Detail |
|------|--------|
| Guard | `None`/empty or no `description` column -> empty frame |
| Normalization | `desc_norm = description.fillna("").astype(str).str.strip().str.lower()` — NaN-safe (pandas 3 raises on `NaN + .str`) |
| Empty filter | Drops `desc_norm == ""` — a subscription needs a name |
| Key | `desc_norm + "|" + amount_eur.round(2).astype(str)` — exact description + exact cent amount |
| Group gate | `len(grp) < min_months` skip |
| Regularity | `avg_gap = gaps.mean()`, `max_gap = gaps.max()` where `gaps = dates.diff().dt.days`. Require `25 <= avg_gap <= 35` **and** `max_gap <= 60`. Catches `[1, 59]` (avg 30 but irregular) |
| Output columns | `description` (original, not normalized), `category`, `amount_eur`, `months_seen`, `avg_gap_days` (1 dec), `last_date` |
| Sort | `last_date desc` |

---

## 5. Learned Categorizer — TF-IDF + LogisticRegression

### 5.1 Model classes

| Class | Training data | Gate | Vectorizer | Classifier |
|-------|--------------|------|------------|------------|
| `_CategorizerModel` | `description, category, subcategory` (dropna on desc+cat) | `len < 10` or `nunique(cat) < 2` -> fail | `TfidfVectorizer(ngram_range=(1,2), min_df=1)` | `LogisticRegression(max_iter=500)` |
| `_SubcategorizerModel` | Per-category rows with non-empty `subcategory` | `len < 8` or `nunique(subcat) < 2` -> fail | same | same |

- `_CategorizerModel.train` also trains one `_SubcategorizerModel` per `category` on its non-empty subcategory rows.
- `predict(text) -> (label, confidence)` via `predict_proba().max()`.
- Sub-model parent keeps `sub_models: dict[category, _SubcategorizerModel]`.

### 5.2 Caching & fingerprinting

```python
CATEGORIZER_MODEL_VERSION = 3   # bump invalidates all caches
CATEGORY_CONFIDENCE    = 0.5
SUBCATEGORY_CONFIDENCE = 0.4
```

```python
@st.cache_resource(max_entries=8)
def get_categorizer(user_id, model_version, fingerprint) -> _CategorizerModel: ...
```

- Cache keys on `(user_id, model_version, fingerprint)` — never leaks across users.
- **Fingerprint:** `len(df) + "|" + md5(sorted((desc|cat|subcat).lower().strip()))`. Any addition, deletion, or correction (category or subcategory edit) changes the fingerprint and forces a fresh model. `subcategory` missing -> filled with `""`.
- `clear_categorizers()` calls `get_categorizer.clear()` (e.g. on account deletion).

### 5.3 Suggestion pipeline

```python
suggest_category(df, text, min_confidence=0.5, user_id) -> (cat|None, conf)
# trains on demand if clf is None or fingerprint mismatch
```

```python
suggest_category_and_subcategory(df, text, min_confidence=0.5, min_sub_confidence=0.4, user_id)
  -> (category, subcategory, cat_conf, sub_conf)
```

Rules:
1. `categorize_expense(text)` (keyword map from `bank_import`) is always computed as `kw_cat, kw_sub`.
2. If `model.clf is None` -> return `(kw_cat, kw_sub, 0, 0)`.
3. `cat, cat_conf = model.predict(text)`. If `cat_conf < min_confidence` -> return `(kw_cat, kw_sub, 0, 0)` (keyword wins both fields).
4. Otherwise look up `sm = model.sub_models.get(cat)`. If it exists, `s, sc = sm.predict(text)` and keep it only when `sc >= min_sub_confidence`.
5. Fallback refinement: if no confident subcategory but `kw_cat == cat` and `kw_sub` exists, borrow `kw_sub` with `sub_conf = 0.0`.

---

## 6. Clustering & Budget Recommender

### 6.1 `cluster_month_patterns(df, n_clusters=3) -> dict`
- Pivots `ym x category` sums (`fillna(0)`). Gate: `len(pivot) < 6` -> `{"ok": False, "reason": "short_history"}`.
- `StandardScaler().fit_transform(pivot.values)` then `KMeans(n_clusters=min(n_clusters, len(pivot)), random_state=42, n_init=10)`.
- Current month = `pivot.index[-1]`, its label = `labels[-1]`.
- Dominant categories: `profile = pivot[labels==current_label].mean(axis=0)`, `overall = pivot.mean(axis=0)`, `diff = profile - overall`, top 3 positive diffs.
- Returns `{ok, month, label, n_months_in_cluster, dominant_categories, avg_total}`.

### 6.2 `suggest_budgets(df, months=6) -> dict[category, float]`
- Pivots last `months` months (`tail(months)`).
- Per category: skip if `len(series) < 3 or sum <= 0`. Otherwise `mean + slope` where `slope = np.polyfit(arange(len), values, 1)[0]` (one step ahead linear trend), `max(...,0)` rounded 2 decimals.

---

## 7. Degradation & Fallback Table

| Condition | Forecast | Anomalies | Categorizer | Clustering | Subscriptions |
|-----------|----------|-----------|-------------|------------|---------------|
| `< 6 elapsed months` | `fallback=True`, `total=None` -> caller uses period-average | — | — | `ok=False, reason="short_history"` | — |
| `< 20 rows` | — | empty `DataFrame` | — | — | — |
| `< 10 rows` or `< 2 categories` | — | — | `train()` returns `False`; `predict` returns `None,0`; callers fall back to keyword map | — | — |
| Missing `statsmodels` | `(None,None,None)` via broad `except` | — | — | — | — |
| Missing `sklearn` | — | empty frame | `False` / `None,0` | `ok=False, reason="no_sklearn"` | — |
| `llama_cpp` missing (LLM, not forecasting, but same pattern) | — | — | — | — | — |
| Any other exception during fit/predict | `(None,None,None)` / empty frame / `False` | | — | Every public helper catches `Exception` and returns a safe sentinel (dict with fallback, empty frame, or `False`). Pages never crash. |

In `app_pages/forecast.py`:
- `method == "ML model"` computed block always checks `ml_result["fallback"] or ml_result["total"] is None` and shows the period-average projection + explanatory caption instead.
- `method == "7-day average"` with empty recent window also falls back to period average (avoids 0 projection).

---

## 8. Optional Intelligence Input — `market_data.py`

Treated as intelligence input, not a forecast driver. Refresh is **optional, key-less, and non-blocking**:

- Primary: `query1.finance.yahoo.com/v8/finance/chart/{sym}` (`regularMarketPrice` or last non-null `close`). Fallback: `stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv` (`Close` CSV).
- `PRICES_MAX_AGE_DAYS = 1`. `prices_are_stale(df)` returns `True` when any `last_price_date` is `None/NaT` or `days >= 1` (UTC-aware comparison).
- `@st.cache_data(ttl=1800)` caches even failures (`None`) for 30 min. Background thread via `maybe_refresh_in_background` uses a process-wide `_refresh_lock` and a daemon `Thread` that calls `refresh_prices_if_due(..., cached=False)` and `bump_data_revision` on success.
- No intelligence feature depends on market prices being fresh — stale prices keep the previous value.

---

## 9. Page Integration — `app_pages/forecast.py`

```
q.income(user_id) -> salary-cycle detection (compute_salary_cycle, SALARY_DAY=10)
q.expenses(user_id), q.budgets(user_id) -> period slice [period_start, period_end]
Method toggle: "Period average" | "7-day average" | "ML model"
  Period average: daily_avg = total_spent / days_elapsed
  7-day:          daily_avg = sum(recent_7d) / min(days_elapsed,7)  (fallback to period if recent empty)
  ML model:       forecast_next_month(dfe)  (fallback to period if fallback)
  projected = daily_avg * days_in_period   (except ML path where projected = ml total directly)
```

Displays four `st.metric` cards (spent, daily avg, projected, budget) with alt-currency deltas (`delta_color="off"`), a budget progress bar, and an overspend target `target_per_day = (budget - spent)/days_remaining`.

Per-category table rendered only when `ml_result and not fallback and by_category`.

---

## 10. Optional-Dependency Guards

Every `sklearn` / `statsmodels` / `llama_cpp` import is **lazy inside the function** and caught as `Exception` (not just `ImportError`):

```python
try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
except Exception:
    return None, None, None
```

```python
try:
    from sklearn.ensemble import IsolationForest
except Exception:
    return pd.DataFrame()
```

On Windows, missing Vulkan/MSVC DLLs raise `OSError` on import — the `Exception` catch prevents a page crash and surfaces a diagnostic (LLM) or silent fallback (forecasting).

---

## 11. Test Coverage Map

| Test file | Key cases |
|-----------|-----------|
| `tests/test_forecasting.py` | `test_forecast_falls_back_with_short_history` (<6 Months) ; `test_forecast_with_enough_history` (band ordering, history_months==12); `test_forecast_history_months_are_elapsed_not_row_count` (6 rows over 36 months -> fallback); `test_forecast_falls_back_when_a_month_is_missing` (March gap -> None); `test_anomalies_flags_outlier` (huge row flagged); `test_anomalies_returns_empty_for_small_data` (<20 rows); categorizer train/predict, cluster, subscriptions, budgets |
| `tests/test_forecast.py` | `compute_salary_cycle` salary-day clamping (Feb 28/29, month-end 31->30) and latest-salary override; forecast method toggles |
| `tests/test_insights.py` | `month_over_month`, `unusual_expenses`, `days_until_budget_depleted`, `savings_projection` (NaN guards, withdrawal, 600-month cap) |

New code must add/extend tests when changing: reindex logic, gap handling, feature list, contamination, or the fallback dict shape.

---

## 12. Agent Checklist

- [ ] Did you keep `_elapsed_months` as calendar-span, not row count?
- [ ] Does any new month handling reindex with `period_range` and treat `isna()` as `None`? (No interpolation.)
- [ ] Is the ETS `trend="add"` / `initialization_method="estimated"` unchanged without a test update?
- [ ] Are `lower/upper` clamped (`max(lower,0)`) and is `raw_fc` checked for `finite && >=0`?
- [ ] Does anomaly feature list still include all four (`amount_eur, dow, month, cat_code`) with `NaN->""` for categories?
- [ ] Is `contamination` left at `0.05` unless the caller explicitly overrides?
- [ ] Are all new `sklearn`/`statsmodels` imports inside `try/except Exception`?
- [ ] Does `forecast_next_month` still return `{total, lower, upper, by_category, fallback, history_months}` exactly?
- [ ] Are categorizer `model_version` / `fingerprint` changes reflected in the cache key?

---

*Last verified against `forecasting.py` rev. at head and `app_pages/forecast.py` + `tests/test_forecasting.py`.*
