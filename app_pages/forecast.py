"""
Forecast page: project current salary-cycle spending and compare against budget.
"""

from datetime import date

import math

import pandas as pd
import streamlit as st

import queries as q
from forecasting import (forecast_next_month, projection_band,
                         projection_breakdown, savings_scenario)
from utils import (
    compute_salary_cycle, fmt, safe_warning, get_currency_symbol, to_display,
    progress_ratio,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
settings = st.session_state.settings

SYM     = get_currency_symbol(DC)
AMT_FMT = f"%.0f {SYM}" if DC in ("RSD", "HUF", "HRK") else f"{SYM}%.2f"

st.title(":material/query_stats: Spending forecast")
st.caption("Based on your salary cycle: detected from your last income entry.")

today   = date.today()
dfi_all = q.income(user_id)
SALARY_DAY = 10

salary_rows = pd.DataFrame()
if not dfi_all.empty:
    if "income_type" in dfi_all.columns:
        salary_rows = dfi_all[dfi_all["income_type"].fillna("Other") == "Salary"]
    if salary_rows.empty:
        salary_rows = dfi_all[dfi_all["source"] == "Primary Salary"]
if salary_rows.empty:
    period_start, period_end = compute_salary_cycle(today, SALARY_DAY)
    safe_warning("No salary entry found — using the 10th as cycle start. "
                 "Log a 'Primary Salary' income entry to enable automatic detection.")
else:
    salary_rows = salary_rows[salary_rows["date"].notna()] if not salary_rows.empty else salary_rows
    if salary_rows.empty:
        period_start, period_end = compute_salary_cycle(today, SALARY_DAY)
    else:
        latest_salary = salary_rows.sort_values("date").iloc[-1]
        period_start, period_end = compute_salary_cycle(today, SALARY_DAY,
                                                        latest_salary["date"].date())
    st.success(f"Cycle start: **{period_start.strftime('%d %b %Y')}**",
               icon=":material/check_circle:")

days_in_period = (period_end - period_start).days + 1
days_elapsed   = max((today - period_start).days + 1, 1)
days_remaining = max((period_end - today).days, 0)

st.caption(f":material/calendar_month: **{period_start.strftime('%d %b')} → {period_end.strftime('%d %b %Y')}** "
           f"({days_in_period} days · {days_elapsed} in · {days_remaining} left)")

dfe = q.expenses(user_id)
dfb = q.budgets(user_id)

if dfe.empty:
    st.info("Log expenses to see a forecast", icon=":material/add_chart:")
    st.stop()

period_start_ts = pd.Timestamp(period_start)
period_end_ts   = pd.Timestamp(period_end)

period_exp = dfe[
    (dfe["date"] >= period_start_ts) & (dfe["date"] <= period_end_ts)
].copy() if not dfe.empty else pd.DataFrame(columns=["amount_eur","date","category"])

# Burn-rate method: whole-period average, recent 7-day average, or the ML model
method = st.segmented_control(
    "Forecast method",
    ["Period average", "7-day average", "ML model"],
    default="Period average",
    key="forecast_method",
)

st.subheader(":material/payments: Total spending forecast")

total_spent = float(period_exp["amount_eur"].sum()) if not period_exp.empty else 0.0

ml_result = None
if method == "ML model":
    ml_result = forecast_next_month(
        dfe if not dfe.empty else pd.DataFrame(), q.recurring(user_id))
    if (ml_result["fallback"] or ml_result["total"] is None
            or not math.isfinite(float(ml_result["total"]))):
        st.caption("Not enough history for the model yet (needs 6+ months) — "
                   "showing the period-average projection instead.")
        daily_avg = total_spent / days_elapsed if days_elapsed > 0 else 0.0
        projected = daily_avg * days_in_period
    else:
        projected = float(ml_result["total"])
        daily_avg = projected / days_in_period if days_in_period > 0 else 0.0
        st.caption(
            f":material/psychology: {ml_result['selected_model'].replace('_', ' ')} model over "
            f"{ml_result['history_months']} months of history · "
            f"80% range: **{fmt(ml_result['lower'], DC, rates)} – {fmt(ml_result['upper'], DC, rates)}**"
        )
        # research.md M1: backtest accuracy is computed on every call — show it
        # so the prediction range carries visible evidence instead of blind trust.
        with st.expander("Model accuracy", icon=":material/verified:"):
            _mm = ml_result.get("model_metrics") or {}
            if _mm:
                _mmdf = pd.DataFrame(_mm).T.rename(columns={
                    "mae": "MAE", "smape": "sMAPE %", "bias": "Bias"})
                _mmdf.index.name = "model"
                st.dataframe(_mmdf)
            else:
                st.caption("No backtest metrics yet - the model needs at least "
                           "3 rolling origins of history.")
            st.caption(f"{ml_result.get('backtest_origins', 0)} rolling origins · "
                       f"selection: {ml_result.get('selection_reason', 'n/a')}")
elif method == "7-day average":
    recent = period_exp[period_exp["date"] >= pd.Timestamp(today) - pd.Timedelta(days=6)]
    n_days = min(max(days_elapsed, 1), 7)
    if recent.empty:
        # An empty recent window must fall back to the period average —
        # a 0 daily average would project a 0 total.
        daily_avg = total_spent / days_elapsed if days_elapsed > 0 else 0.0
    else:
        daily_avg = float(recent["amount_eur"].sum()) / n_days
    projected = daily_avg * days_in_period
else:
    daily_avg = total_spent / days_elapsed if days_elapsed > 0 else 0.0
    projected = daily_avg * days_in_period

# #17: uncertainty on the non-ML methods too — same caption shape as ML.
if method != "ML model":
    _lo, _hi = projection_band(projected)
    st.caption(f"80% range: **{fmt(_lo, DC, rates)} – {fmt(_hi, DC, rates)}** "
               f"(naive ±15% band)")

# #17: recurring bills vs discretionary split — makes the number actionable.
_rec_df = q.recurring(user_id)
recurring_total = 0.0
if not _rec_df.empty and "amount_eur" in _rec_df.columns:
    _act = _rec_df[_rec_df["active"] == True] if "active" in _rec_df.columns else _rec_df  # noqa: E712
    recurring_total = float(_act["amount_eur"].fillna(0).sum())
_breakdown = projection_breakdown(projected, recurring_total)

total_budget = 0.0
_overall_raw = float(settings.get("monthly_budget") or 0.0)
overall_bud = _overall_raw if math.isfinite(_overall_raw) else 0.0
if overall_bud > 0:
    total_budget = overall_bud
elif not dfb.empty:
    from utils import effective_category_budgets
    bud_m = dfb[(dfb["year"] == period_start.year) &
                (dfb["month"] == period_start.month)]
    total_budget = float(sum(effective_category_budgets(bud_m).values()))

over_under = projected - total_budget
on_track   = total_budget == 0 or projected <= total_budget

alt_ccy = "EUR" if DC != "EUR" else "RSD"
with st.container(horizontal=True):
    # The alt-currency value is a conversion, not a change — no delta arrows.
    st.metric("Spent so far", fmt(total_spent, DC, rates),
              delta=fmt(total_spent, alt_ccy, rates), delta_color="off", border=True)
    st.metric("Daily average", fmt(daily_avg, DC, rates),
              delta=fmt(daily_avg, alt_ccy, rates), delta_color="off", border=True)
    st.metric("Projected total", fmt(projected, DC, rates),
              delta=fmt(projected, alt_ccy, rates), delta_color="off", border=True)
    st.metric("Monthly budget", fmt(total_budget, DC, rates),
              delta=fmt(total_budget, alt_ccy, rates), delta_color="off", border=True)

if total_budget == 0:
    st.warning("No budget set. Go to Settings → Budget to set one.",
               icon=":material/warning:")
elif on_track:
    st.success(f"On track! Projected: **{fmt(projected, DC, rates)}** — "
               f"**{fmt(total_budget - projected, DC, rates)} under budget**.",
               icon=":material/check_circle:")
else:
    st.error(f"Overspend risk. Projected: **{fmt(projected, DC, rates)}** — "
             f"**{fmt(over_under, DC, rates)} over budget**. "
             f"Target: **{fmt((total_budget - total_spent) / max(days_remaining, 1), DC, rates)}/day**.",
             icon=":material/error:")

if total_budget > 0:
    ratio_spent = progress_ratio(total_spent, total_budget)
    st.progress(
        ratio_spent,
        text=f"**Spent** {fmt(total_spent, DC, rates)} of {fmt(total_budget, DC, rates)} ({ratio_spent * 100:.1f}%)",
    )

# #17: pacing marker — the projection promoted to an explicit statement.
st.info(f":material/speed: Pacing **{fmt(daily_avg, DC, rates)}/day** — "
        f"on track for **{fmt(projected, DC, rates)}** by "
        f"**{period_end.strftime('%d %b')}** ({days_remaining} days left).",
        icon=":material/speed:")

# ── Fixed vs discretionary breakdown + savings scenarios (#17) ───────────────
sc1, sc2 = st.columns(2)
with sc1:
    with st.expander("Fixed vs discretionary", icon=":material/pie_chart:"):
        b1, b2 = st.columns(2)
        b1.metric("Fixed bills (recurring)", fmt(_breakdown["fixed"], DC, rates))
        b2.metric("Discretionary (projected)",
                  fmt(_breakdown["discretionary"], DC, rates))
        if _breakdown["under_fixed"]:
            st.caption("Projected spend is below your recurring bill total — "
                       "you are not even consuming your fixed set this cycle.")
        else:
            st.caption("Only the discretionary slice flexes; bills are "
                       "contractual until you cancel them.")
with sc2:
    with st.expander("Savings scenarios", icon=":material/tune:"):
        _salary_raw = float(settings.get("salary_amount") or 0.0)
        if _salary_raw <= 0 and not salary_rows.empty:
            _latest_sal = salary_rows.sort_values("date").iloc[-1]
            try:
                _salary_raw = float(_latest_sal.get("actual_eur") or 0.0)
            except Exception:
                _salary_raw = 0.0
        if _salary_raw <= 0:
            st.caption("Set your salary in the Income tab to model savings.")
        else:
            hyp_salary = st.slider("Monthly income", 0.0,
                                   max(_salary_raw * 2.0, 1000.0),
                                   _salary_raw, step=50.0,
                                   format=AMT_FMT, key="scen_salary")
            rec_delta = st.slider("Change fixed bills", -50, 100, 0, 5,
                                  format="%d%%", key="scen_rec_delta",
                                  help="What if you cancelled or added subscriptions?")
            scen = savings_scenario(hyp_salary, _breakdown["fixed"],
                                    _breakdown["discretionary"], rec_delta)
            s1, s2, s3 = st.columns(3)
            s1.metric("Projected spend", fmt(scen["projected_spend"], DC, rates))
            s2.metric("Saved / month", fmt(scen["monthly_savings"], DC, rates))
            rate = scen["savings_rate"]
            s3.metric("Savings rate",
                      f"{rate * 100:.0f}%" if rate is not None else "—")
            if scen["monthly_savings"] < 0:
                st.warning("This scenario spends more than you earn.",
                           icon=":material/warning:")

# Per-category ML forecast table
if ml_result and not ml_result["fallback"] and ml_result["by_category"]:
    st.subheader(":material/psychology: Predicted next month by category")
    cats = pd.DataFrame([
        {"Category": c, "Forecast": to_display(v, DC, rates)}
        for c, v in sorted(ml_result["by_category"].items(), key=lambda x: -x[1])
    ])
    st.dataframe(
        cats,
        hide_index=True,
        column_config={
            "Forecast": st.column_config.NumberColumn("Forecast", format=AMT_FMT),
        },
    )
