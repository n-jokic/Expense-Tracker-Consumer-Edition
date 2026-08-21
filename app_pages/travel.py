"""
Travel page: a yearly travel budget with on-pace checking and a link to the
Vacation / Travel savings goal.
"""

import calendar
import math
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from utils import (
    CATEGORIES, CAT_LIST, ALL_SUBCATS, DEFAULT_TRAVEL_CATEGORIES, CHART_COLORS,
    travel_spent, fmt, get_currency_symbol, progress_ratio,
    help_expander,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()
year     = today.year

st.title(":material/flight: Travel budget")
st.caption("A yearly allowance for trips — flights, hotels and everything vacation.")
help_expander("How the travel budget works",
              "Set a yearly amount and choose which expense categories count as travel. "
              "The page checks whether you're spending faster than the year is passing, "
              "and shows your Vacation / Travel savings goal next to it.")

if (msg := st.session_state.pop("travel_flash", None)):
    st.success(msg, icon=":material/check_circle:")

# ── Setup ─────────────────────────────────────────────────────────────────────
with st.expander("Travel budget settings", icon=":material/settings:"):
    with st.form("travel_setup", clear_on_submit=True):
        _tb = float(settings.get("travel_budget") or 0.0)
        t_amt = st.number_input(f"Yearly travel budget ({get_currency_symbol('EUR')})", min_value=0.0,
                                step=100.0, format="%.2f",
                                value=_tb if math.isfinite(_tb) else 0.0)
        all_pairs = ([f"{c} › (all)" for c in CAT_LIST] +
                     [f"{c} › {s}" for c in CAT_LIST for s in CATEGORIES[c]])
        current = settings.get("travel_categories") or DEFAULT_TRAVEL_CATEGORIES
        # Map stored forms back to the display forms: "Category › " (whole
        # category) -> "Category › (all)" and BARE category names (the
        # default ["Travel"]) -> "Category › (all)" too, so a fresh user's
        # defaults stay selected instead of being silently wiped on save.
        def _to_display(p: str) -> str:
            p = str(p or "")
            if p.endswith(" › "):
                return p + "(all)"
            if p in CAT_LIST:
                return p + " › (all)"
            return p
        current_display = [_to_display(p) for p in current]
        t_cats = st.multiselect("Categories that count as travel",
                                all_pairs,
                                default=[p for p in current_display if p in all_pairs])
        if st.form_submit_button("Save", type="primary", width="stretch", icon=":material/save:"):
            try:
                q.save_settings(user_id, {
                    "travel_budget": float(t_amt),
                    "travel_categories": [p.replace(" › (all)", " › ") for p in t_cats],
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                st.session_state["travel_flash"] = "Travel budget saved."
                st.rerun()

budget = float(settings.get("travel_budget") or 0.0)
pairs  = settings.get("travel_categories") or DEFAULT_TRAVEL_CATEGORIES

# ── Status ────────────────────────────────────────────────────────────────────
dfe = q.expenses(user_id)
spent = travel_spent(dfe, pairs, year)

days_in_year = 366 if calendar.isleap(year) else 365
year_pct = today.timetuple().tm_yday / days_in_year * 100
budget_pct = (spent / budget * 100) if budget > 0 else 0.0

with st.container(horizontal=True):
    st.metric(f"Spent in {year}", fmt(spent, DC, rates), border=True)
    st.metric("Budget", fmt(budget, DC, rates), border=True)
    st.metric("Remaining", fmt(max(budget - spent, 0.0), DC, rates), border=True)

if budget > 0:
    st.markdown(f"**{budget_pct:.0f}%** of the travel budget used — "
                f"**{year_pct:.0f}%** of the year has passed.")
    st.progress(progress_ratio(budget_pct, 100), text=f"{budget_pct:.0f}% used")

    if spent > budget:
        st.error(f"✈️ Travel budget exceeded by {fmt(spent - budget, DC, rates)} this year.")
    elif budget_pct > year_pct:
        st.warning("⏳ You're spending on travel faster than the year is passing — "
                   "consider slowing down or raising the budget.")
    else:
        st.success(f"✅ On pace! {fmt(budget - spent, DC, rates)} left for the rest of the year.")
else:
    st.info("Set a yearly travel budget in the settings above")

# ── Breakdown + savings goal ──────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.subheader(f"Travel spending by month ({year})")
    if not dfe.empty:
        def _is_travel(row):
            # Mirror travel_spent's semantics: a bare SUBCATEGORY name in the
            # pool matches that subcategory across categories.
            for p in pairs:
                if " › " in p:
                    cat, sub = p.split(" › ", 1)
                    cat, sub = cat.strip(), sub.strip()
                else:
                    cat, sub = p.strip(), ""
                if sub and cat in CAT_LIST:
                    if row["category"] != cat or row["subcategory"] != sub:
                        continue
                    return True
                if not sub and cat in CAT_LIST:
                    if row["category"] == cat:
                        return True
                elif not sub and cat in ALL_SUBCATS:
                    if row["subcategory"] == cat:
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
            st.caption("No travel spending logged this year yet.")
    else:
        st.caption("No expenses yet.")

with c2:
    st.subheader("Vacation savings goal")
    dfs = q.savings(user_id)
    no_goal_msg = "Create a 'Vacation / Travel' savings goal to save for trips."
    if not dfs.empty:
        rows = dfs[dfs["goal_name"].isin(["Vacation / Travel", "Vacation"])]
        if not rows.empty:
            _bal = rows.sort_values("date").iloc[-1]["balance_eur"]
            bal = float(_bal) if pd.notna(_bal) else 0.0
            st.metric("Saved towards vacation", fmt(bal, DC, rates))
            st.caption("Deposit into the 'Vacation / Travel' savings goal to grow this.")
        else:
            st.caption(no_goal_msg)
    else:
        st.caption(no_goal_msg)
