"""
Forecast page: project current salary-cycle spending and compare against budget.
"""

from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from utils import (
    compute_salary_cycle, fmt, pbar, safe_warning,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
settings = st.session_state.settings

st.title("📈 Spending forecast")
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
    latest_salary = salary_rows.sort_values("date").iloc[-1]
    period_start, period_end = compute_salary_cycle(today, SALARY_DAY,
                                                    latest_salary["date"].date())
    st.success(f"✅ Cycle start: **{period_start.strftime('%d %b %Y')}**")

days_in_period = (period_end - period_start).days + 1
days_elapsed   = max((today - period_start).days + 1, 1)
days_remaining = max((period_end - today).days, 0)

st.info(f"📅 **{period_start.strftime('%d %b')} → {period_end.strftime('%d %b %Y')}** "
        f"({days_in_period} days · {days_elapsed} in · {days_remaining} left)")

dfe = q.expenses(user_id)
dfb = q.budgets(user_id)
period_start_ts = pd.Timestamp(period_start)
period_end_ts   = pd.Timestamp(period_end)

period_exp = dfe[
    (dfe["date"] >= period_start_ts) & (dfe["date"] <= period_end_ts)
].copy() if not dfe.empty else pd.DataFrame(columns=["amount_eur","date","category"])

# Burn-rate method: whole-period average vs recent 7-day average
method = st.segmented_control(
    "Forecast method",
    ["Period average", "7-day average"],
    default="Period average",
    key="forecast_method",
)

st.divider()
st.subheader("💰 Total spending forecast")

total_spent = float(period_exp["amount_eur"].sum()) if not period_exp.empty else 0.0

if method == "7-day average":
    recent = period_exp[period_exp["date"] >= pd.Timestamp(today) - pd.Timedelta(days=6)]
    n_days = min(max(days_elapsed, 1), 7)
    daily_avg = float(recent["amount_eur"].sum()) / n_days if not recent.empty else 0.0
else:
    daily_avg = total_spent / days_elapsed if days_elapsed > 0 else 0.0

projected = daily_avg * days_in_period

total_budget = 0.0
overall_bud  = float(settings.get("monthly_budget", 0.0))
if overall_bud > 0:
    total_budget = overall_bud
elif not dfb.empty:
    bud_m = dfb[(dfb["year"] == period_start.year) &
                (dfb["month"] == period_start.month)]["budgeted_eur"].sum()
    total_budget = float(bud_m)

over_under = projected - total_budget
on_track   = total_budget == 0 or projected <= total_budget

fc1, fc2, fc3, fc4 = st.columns(4)
for col, lbl, val, cls in [
    (fc1, "Spent so far",    total_spent,  "neg"),
    (fc2, "Daily average",   daily_avg,    "neu"),
    (fc3, "Projected total", projected,    "pos" if on_track else "neg"),
    (fc4, "Monthly budget",  total_budget, "neu"),
]:
    with col:
        st.markdown(
            f'<div class="kpi">'
            f'<div class="kpi-lbl">{lbl}</div>'
            f'<div class="kpi-val {cls}">{fmt(val, DC, rates)}</div>'
            f'<div class="kpi-sub">{fmt(val, "EUR" if DC != "EUR" else "RSD", rates)}</div>'
            f'</div>', unsafe_allow_html=True
        )

st.write("")
if total_budget == 0:
    safe_warning("No budget set. Go to ⚙️ Settings → Budget to set one.")
elif on_track:
    st.success(f"✅ On track! Projected: **{fmt(projected, DC, rates)}** — "
               f"**{fmt(total_budget - projected, DC, rates)} under budget**.")
else:
    st.error(f"⚠️ Overspend risk. Projected: **{fmt(projected, DC, rates)}** — "
             f"**{fmt(over_under, DC, rates)} over budget**. "
             f"Target: **{fmt((total_budget - total_spent) / max(days_remaining, 1), DC, rates)}/day**.")

if total_budget > 0:
    pct_spent = min(total_spent / total_budget * 100, 100)
    bar_color = "#00B050" if on_track else "#E94560"
    st.markdown(f"**Spent** {fmt(total_spent, DC, rates)} of {fmt(total_budget, DC, rates)} ({pct_spent:.1f}%)")
    st.markdown(pbar(pct_spent, bar_color), unsafe_allow_html=True)
