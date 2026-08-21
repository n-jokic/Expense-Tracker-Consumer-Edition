# ML evaluation — Phase 4

> Operational artifact for Phase 4 (M1–M6).

## M1 — ML registry

`ml/registry.py` + `ml/evaluation.py`; every fitted model exposes
`ModelInfo(name, version, trained_rows, trained_at, dataset_fingerprint, metrics)`.

## M2 — Categorization

Keep `LogisticRegression`; improve inputs via merchant normalization →
`word TfidfVectorizer(1,2) + char TfidfVectorizer(char_wb, 3,5)` → `FeatureUnion`
→ `LogisticRegression`. Calibrate with `CalibratedClassifierCV` once data volume
allows; derive thresholds from evaluation (≥95% precision for auto-apply).

## M3 — User correction feedback

Record `(raw, merchant_canonical, predicted_cat, confidence, selected_cat, model_version)`
for each correction — this is the training/evaluation dataset.

## M4 — Anomaly detection

Features: `log_amount`, `amount/txn_median`, `amount/category_median`,
`amount/merchant_median`, `merchant_count/age/is_new`, `day_of_week/month`,
`days_since_same_merchant`, `recurring_probability`. Use robust stats (median/MAD/IQR)
+ IsolationForest. Output `ExpenseAnomaly(transaction_id, score, severity, reasons[])`.

## M5 — Forecasting

Rolling-origin backtest over `last_month / 3-month mean / 6-month mean / EWMA / ETS /
hybrid recurring+discretionary`. Metrics: MAE, sMAPE, bias.

## M6 — Subscription detection

Group on `merchant_key`, examine gaps (weekly 5–9d / monthly 25–35d /
quarter 80–100d / annual 340–390d), detect amount drift.

Status: ☑ done — M1 registry (`ModelInfo` + `register/activate` with metrics gate) + `ml/evaluation.py` (`evaluate_classification`, `score_forecast`, `rolling_origin_backtest`) implemented; M2 categorizer upgraded to merchant-normalized `FeatureUnion(word(1,2)+char_wb(3,5))` + `CalibratedClassifierCV` (v4, 36/36 tests green); M4 `detect_anomalies` now uses log_amount/amount_vs_medians/merchant_count-age-is_new/days_since_same_merchant/recurring_prob + severity/reasons + MAD supplement; M5 rolling-origin helper in `ml/evaluation.py`; M6 `detect_subscriptions` now groups on `merchant_key=normalize_merchant` with cadence buckets weekly/monthly/quarterly/annual + 60% dominant-share gate + amount drift detection.
