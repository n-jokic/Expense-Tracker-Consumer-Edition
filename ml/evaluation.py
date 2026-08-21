"""ml/evaluation.py — evaluation helpers (Phase 4 M1/M5).

Hermetic, no DB; sklearn used opportunistically.
"""

from __future__ import annotations

from typing import Any
import math


def evaluate_classification(y_true, y_pred, y_prob=None) -> dict[str, float]:
    """Return accuracy/precision/recall/f1 (+ log_loss when y_prob given)."""
    try:
        from sklearn.metrics import accuracy_score, precision_recall_fscore_support, log_loss
    except Exception:
        # Degrade: only accuracy if sklearn missing
        y_true_l = list(y_true)
        y_pred_l = list(y_pred)
        acc = sum(a == b for a, b in zip(y_true_l, y_pred_l)) / max(len(y_true_l), 1)
        return {"accuracy": float(acc)}
    y_true_l = list(y_true)
    y_pred_l = list(y_pred)
    out: dict[str, float] = {}
    out["accuracy"] = float(accuracy_score(y_true_l, y_pred_l))
    try:
        prec, rec, f1, _ = precision_recall_fscore_support(y_true_l, y_pred_l, average="weighted", zero_division=0)
        out["precision"] = float(prec)
        out["recall"] = float(rec)
        out["f1"] = float(f1)
    except Exception:
        pass
    if y_prob is not None:
        try:
            out["log_loss"] = float(log_loss(y_true_l, y_prob))
        except Exception:
            pass
    return out


def score_forecast(y_true, y_pred) -> dict[str, float]:
    """MAE, sMAPE, bias for forecast errors."""
    import numpy as np
    yt = np.asarray(list(y_true), dtype=float)
    yp = np.asarray(list(y_pred), dtype=float)
    if len(yt) == 0:
        return {"mae": float("nan"), "smape": float("nan"), "bias": float("nan")}
    mae = float(np.mean(np.abs(yt - yp)))
    denom = (np.abs(yt) + np.abs(yp)) / 2.0
    # sMAPE: avoid div0 where both 0
    mask = denom > 1e-9
    if mask.any():
        smape = float(np.mean(np.abs(yt[mask] - yp[mask]) / denom[mask]) * 100)
    else:
        smape = 0.0
    bias = float(np.mean(yp - yt))
    return {"mae": mae, "smape": smape, "bias": bias}


def suggest_threshold_for_precision(y_true, y_prob, target_precision: float = 0.95) -> float:
    """Threshold giving >= target_precision (if achievable), else max precision."""
    try:
        from sklearn.metrics import precision_recall_curve
        import numpy as np
    except Exception:
        return 0.5
    y_true_l = list(y_true)
    y_prob_l = list(y_prob)
    # Assume binary "positive = correct class"? For multiclass we use max prob as confidence
    # and y_true == y_pred as correctness proxy — caller provides those.
    precisions, recalls, thresholds = precision_recall_curve(y_true_l, y_prob_l)
    # thresholds length = len(precisions)-1
    best = 0.5
    for prec, thr in zip(precisions[:-1], thresholds):
        if prec >= target_precision:
            best = float(thr)
            break
    return float(best)


def rolling_origin_backtest(
    expenses_df,
    forecast_fn,
    min_train_months: int = 6,
) -> dict[str, Any]:
    """Rolling-origin backtest: train Jan..Jun→predict Jul, Jan..Jul→predict Aug, ..."""
    import pandas as pd
    if expenses_df is None or expenses_df.empty or "date" not in expenses_df.columns:
        return {"ok": False, "reason": "no data"}
    df = expenses_df.copy()
    df["ym"] = df["date"].dt.to_period("M")
    months = sorted(df["ym"].unique())
    if len(months) <= min_train_months:
        return {"ok": False, "reason": "short_history", "months": len(months)}
    preds: list[float] = []
    trues: list[float] = []
    for i in range(min_train_months, len(months)):
        train_yms = months[:i]
        test_ym = months[i]
        train_df = df[df["ym"].isin(train_yms)]
        true_val = float(df[df["ym"] == test_ym]["amount_eur"].sum())
        try:
            fc = forecast_fn(train_df)
            pred = float(fc.get("total")) if isinstance(fc, dict) and fc.get("total") is not None else float("nan")
        except Exception:
            pred = float("nan")
        if math.isfinite(pred):
            preds.append(pred)
            trues.append(true_val)
    if not preds:
        return {"ok": False, "reason": "no predictions"}
    scores = score_forecast(trues, preds)
    return {"ok": True, "n": len(preds), **scores, "y_true": trues, "y_pred": preds}
