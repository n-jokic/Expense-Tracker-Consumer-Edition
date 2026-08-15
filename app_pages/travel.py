"""
Travel page: a yearly travel budget with on-pace checking and a link to the
Vacation / Travel savings goal.
"""

import calendar
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from utils import (
    CATEGORIES, CAT_LIST, ALL_SUBCATS, DEFAULT_TRAVEL_CATEGORIES, CHART_COLORS,
    travel_spent, fmt, pbar, get_currency_symbol,
    help_expander,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()
year     = today.year

st.title("🎒 Travel budget")
st.caption("A yearly allowance for trips — flights, hotels and everything vacation.")
help_expander("How the travel budget works",
              "Set a yearly amount and choose which expense categories count as travel. "
              "The page checks whether you're spending faster than the year is passing, "
              "and shows your Vacation / Travel savings goal next to it.")

# ── Setup ─────────────────────────────────────────────────────────────────────
with st.expander("⚙️ Travel budget settings"):
    with st.form("travel_setup"):
        t_amt = st.number_input("Yearly travel budget (€)", min_value=0.0,
                                step=100.0, format="%.2f",
                                value=float(settings.get("travel_budget") or 0.0))
        all_pairs = ([f"{c} › (all)" for c in CAT_LIST] +
                     [f"{c} › {s}" for c in CAT_LIST for s in CATEGORIES[c]])
        current = settings.get("travel_categories") or DEFAULT_TRAVEL_CATEGORIES
        # map stored "Category › " (whole category) back to the "(all)" display form
        current_display = [p + "(all)" if p.endswith(" › ") else p for p in current]
        t_cats = st.multiselect("Categories that count as travel",
                                all_pairs,
                                default=[p for p in current_display if p in all_pairs])
        if st.form_submit_button("💾 Save", type="primary"):
            q.save_settings(user_id, {
                "travel_budget": float(t_amt),
                "travel_categories": [p.replace(" › (all)", " › ") for p in t_cats],
            })
            st.success("✅ Travel budget saved!")
            st.rerun()

budget = float(settings.get("travel_budget") or 0.0)
pairs  = settings.get("travel_categories") or DEFAULT_TRAVEL_CATEGORIES

# ── Status ────────────────────────────────────────────────────────────────────
dfe = q.expenses(user_id)
spent = travel_spent(dfe, pairs, year)

st.divider()
k1, k2, k3 = st.columns(3)
days_in_year = 366 if calendar.isleap(year) else 365
year_pct = today.timetuple().tm_yday / days_in_year * 100
budget_pct = (spent / budget * 100) if budget > 0 else 0.0

for col, lbl, val, cls in [
    (k1, f"Spent in {year}", spent, "neg"),
    (k2, "Budget", budget, "neu"),
    (k3, "Remaining", max(budget - spent, 0.0), "pos" if spent <= budget else "neg"),
]:
    with col:
        st.markdown(
            f'<div class="kpi"><div class="kpi-lbl">{lbl}</div>'
            f'<div class="kpi-val {cls}">{fmt(val, DC, rates)}</div></div>',
            unsafe_allow_html=True)

if budget > 0:
    st.markdown(f"**{budget_pct:.0f}%** of the travel budget used — "
                f"**{year_pct:.0f}%** of the year has passed.")
    color = "#E94560" if spent > budget else ("#F4A261" if budget_pct > year_pct else "#00B050")
    st.markdown(pbar(min(budget_pct, 100), color), unsafe_allow_html=True)

    if spent > budget:
        st.error(f"✈️ Travel budget exceeded by {fmt(spent - budget, DC, rates)} this year.")
    elif budget_pct > year_pct:
        st.warning("⏳ You're spending on travel faster than the year is passing — "
                   "consider slowing down or raising the budget.")
    else:
        st.success(f"✅ On pace! {fmt(budget - spent, DC, rates)} left for the rest of the year.")
else:
    st.info("Set a yearly travel budget in the settings above 👆")

# ── Breakdown + savings goal ──────────────────────────────────────────────────
st.divider()
c1, c2 = st.columns(2)
with c1:
    st.subheader(f"Travel spending by month ({year})")
    if not dfe.empty:
        def _is_travel(row):
            for p in pairs:
                if " › " in p:
                    cat, sub = p.split(" › ", 1)
                else:
                    cat, sub = p, ""
                if row["category"] != cat:
                    continue
                if sub and row["subcategory"] != sub:
                    continue
                return True
            return False

        ydf = dfe[dfe["date"].dt.year == year].copy()
        mt = ydf[ydf.apply(_is_travel, axis=1)].copy()
        if not mt.empty:
            mt["month"] = mt["date"].dt.to_period("M").astype(str)
            monthly = mt.groupby("month")["amount_eur"].sum().reset_index()
            monthly["d"] = monthly["amount_eur"].apply(lambda x: x * (rates.get(DC, 1.0) or 1.0) if DC != "EUR" else x)
            fig = px.bar(monthly, x="month", y="d",
                         labels={"d": f"Spent ({get_currency_symbol(DC)})", "month": "Month"},
                         color_discrete_sequence=[CHART_COLORS[3]])
            fig.update_layout(plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No travel spending logged this year yet.")
    else:
        st.info("No expenses yet.")

with c2:
    st.subheader("💰 Vacation savings goal")
    dfs = q.savings(user_id)
    if not dfs.empty:
        rows = dfs[dfs["goal_name"].isin(["Vacation / Travel", "Vacation"])]
        if not rows.empty:
            bal = float(rows.sort_values("date").iloc[-1]["balance_eur"])
            st.metric("Saved towards vacation", fmt(bal, DC, rates))
            st.caption("Deposit into the 'Vacation / Travel' savings goal to grow this.")
        else:
            st.info("Create a 'Vacation / Travel' savings goal to save for trips.")
    else:
        st.info("Create a 'Vacation / Travel' savings goal to save for trips.")
