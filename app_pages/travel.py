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
from db import add_trip, delete_trip, get_trips, update_trip
from services.travel_apis import destination_forecast, geocode_destination
from utils import (
    CATEGORIES, CAT_LIST, ALL_SUBCATS, DEFAULT_TRAVEL_CATEGORIES, CHART_COLORS,
    SUPPORTED_CURRENCIES, MAX_AMOUNT,
    travel_spent, travel_spent_in_range, fmt, fmt_dual,
    get_currency_symbol, progress_ratio, to_eur,
    help_expander,
)

user_id  = st.session_state.user_id
# #16: the user's editable taxonomy drives every picker on this page
CAT_LIST, CATEGORIES = q.effective_categories(user_id)
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

# ── Trips (#14): envelopes, pacing, checklist, weather ───────────────────────
trips = get_trips(user_id)


@st.dialog("New trip")
def new_trip_dialog():
    with st.form("trip_new_form", clear_on_submit=True):
        n_name = st.text_input("Trip name", placeholder="e.g. Lisbon week")
        n_dest = st.text_input("Destination (optional — enables forecast)",
                               key="trip_ndest")
        tc1, tc2 = st.columns(2)
        with tc1:
            n_start = st.date_input("Start date",
                                    value=today + pd.Timedelta(days=30))
        with tc2:
            n_end = st.date_input("End date",
                                  value=today + pd.Timedelta(days=37))
        nc1, nc2 = st.columns(2)
        with nc1:
            n_env = st.number_input(f"Envelope ({get_currency_symbol('EUR')})",
                                    min_value=0.0, max_value=MAX_AMOUNT,
                                    step=100.0, format="%.2f")
        with nc2:
            n_cur = st.selectbox("Local currency",
                                 list(SUPPORTED_CURRENCIES.keys()),
                                 key="trip_ncur")
        n_parts = st.text_input("Participants (comma-separated, optional)",
                                key="trip_nparts")
        if st.form_submit_button("Create trip", type="primary"):
            if not n_name.strip() or n_end < n_start:
                st.error("A name and a valid date range are required.")
                return
            parts = [p.strip() for p in n_parts.split(",") if p.strip()]
            add_trip(user_id, {"name": n_name.strip(),
                               "destination": n_dest.strip(),
                               "start_date": n_start, "end_date": n_end,
                               "envelope_eur": float(n_env),
                               "dest_currency": n_cur,
                               "participants_json": parts})
            q.bump_db_version()
            st.session_state["travel_flash"] = f"Trip **{n_name.strip()}** created."
            st.rerun()


hdr_l, hdr_r = st.columns([3, 1])
with hdr_l:
    st.subheader("Trips")
with hdr_r:
    if st.button("New trip", icon=":material/add:", width="stretch",
                 key="trip_new_btn"):
        new_trip_dialog()

if trips.empty:
    st.caption("No trips yet — plan one to track its envelope day by day.")
else:
    for _, t in trips.iterrows():
        # ORM Date columns arrive as python dates; normalize defensively
        t_start = pd.Timestamp(t["start_date"]).date()
        t_end = pd.Timestamp(t["end_date"]).date()
        days = max((t_end - t_start).days + 1, 1)
        is_active = t_start <= today <= t_end
        is_upcoming = t_start > today
        title = str(t["name"])
        badge = ("🟢 ongoing" if is_active
                 else ("⏳ upcoming" if is_upcoming else "📁 past"))
        with st.container(border=True):
            h1c, h2c, h3c = st.columns([3, 2, 1])
            with h1c:
                st.markdown(f"**{title}** · {badge}")
                dest = str(t.get("destination") or "")
                if dest:
                    st.caption(dest)
                st.caption(f"{t_start.strftime('%d %b %Y')} → "
                           f"{t_end.strftime('%d %b %Y')} · {days} days"
                           + (f" · {len(t['participants_json'] or [])} people"
                              if t["participants_json"] else ""))
            with h2c:
                env = float(t["envelope_eur"] or 0.0)
                spent_t = travel_spent_in_range(dfe, pairs,
                                                t_start.date(), t_end.date())
                left = env - spent_t
                st.metric("Envelope", fmt_dual(env, DC, rates),
                          border=True)
                st.metric("Spent so far", fmt_dual(spent_t, DC, rates),
                          delta=(fmt(left, DC, rates) + " left"),
                          border=True)
            with h3c:
                if st.button("Delete", key=f"trip_del_{t['id']}",
                             icon=":material/delete:"):
                    delete_trip(user_id, t["id"])
                    q.bump_db_version()
                    st.rerun()

            # pacing: per-day budget vs actual, only meaningful mid/past-trip
            elapsed = min(max((today - t_start).days + 1, 0), days)
            per_day = env / days if env > 0 else 0.0
            if is_active and spent_t > per_day * elapsed and env > 0:
                st.warning(f"Burning faster than planned: "
                           f"{fmt(per_day * elapsed, DC, rates)} was the "
                           f"day-{elapsed} mark.", icon=":material/speed:")
            elif is_active and env > 0:
                st.success(f"On pace — day {elapsed}/{days}, daily budget "
                           f"{fmt(per_day, DC, rates)}.",
                           icon=":material/check_circle:")

            # cumulative spend vs ideal line for the window
            if not dfe.empty:
                win = dfe[(dfe["date"].dt.date >= t_start.date())
                          & (dfe["date"].dt.date <= min(today, t_end).date())]
                if not win.empty:
                    daily = (win.groupby(win["date"].dt.date)["amount_eur"]
                             .sum().sort_index().cumsum().reset_index())
                    daily.columns = ["d", "spent"]
                    ideal = [(per_day * i) for i in range(len(daily))]
                    daily["ideal pace"] = ideal
                    chart_df = daily.rename(columns={"d": "Day"}).set_index("Day")
                    st.line_chart(chart_df, height=220)

            with st.expander("Packing checklist", icon=":material/checklist:"):
                items = list(t["checklist_json"] or [])
                new_item = st.text_input("Add item", key=f"trip_item_{t['id']}")
                cadd, cclear = st.columns(2)
                changed = False
                if cadd.button("Add", key=f"trip_item_add_{t['id']}") and new_item.strip():
                    items.append({"text": new_item.strip(), "done": False})
                    changed = True
                if items:
                    for i, it in enumerate(items):
                        kdone = st.checkbox(
                            it["text"], value=bool(it["done"]),
                            key=f"trip_chk_{t['id']}_{i}")
                        if kdone != bool(it["done"]):
                            items[i]["done"] = bool(kdone)
                            changed = True
                    ndone = sum(1 for it in items if it["done"])
                    st.caption(f"{ndone}/{len(items)} packed")
                if changed:
                    update_trip(user_id, t["id"], {"checklist_json": items})
                    q.bump_db_version()
                    st.rerun()

            if is_upcoming and env > 0:
                # savings-gap card: vacation goal balance vs envelope
                dfs_t = q.savings(user_id)
                bal_v = 0.0
                if not dfs_t.empty:
                    rows_g = dfs_t[dfs_t["goal_name"].isin(
                        ["Vacation / Travel", "Vacation"])]
                    if not rows_g.empty:
                        _bv = rows_g.sort_values("date").iloc[-1]["balance_eur"]
                        bal_v = float(_bv) if pd.notna(_bv) else 0.0
                gap = max(env - bal_v, 0.0)
                until = (t_start - today).days
                monthly = (gap / max(until / 30.4, 0.5)) if gap > 0 else 0.0
                if gap > 0:
                    st.info(f"Savings gap {fmt(gap, DC, rates)} — set aside "
                            f"~{fmt(monthly, DC, rates)}/month to be ready.",
                            icon=":material/savings:")

            # destination search + forecast (graceful when offline)
            with st.expander("Destination & weather", icon=":material/partly_cloudy_day:"):
                dest_q = st.text_input("Search destination",
                                       value=str(t.get("destination") or ""),
                                       key=f"trip_dest_{t['id']}")
                if st.button("Look up", key=f"trip_geo_{t['id']}") and dest_q.strip():
                    hits = geocode_destination(dest_q.strip())
                    if hits:
                        pick = st.radio("Match", [h["name"] for h in hits],
                                        key=f"trip_pick_{t['id']}")
                        sel = next(h for h in hits if h["name"] == pick)
                        if st.button("Use this location",
                                     key=f"trip_use_{t['id']}"):
                            update_trip(user_id, t["id"],
                                        {"destination": pick,
                                         "participants_json":
                                             list(t["participants_json"] or [])})
                            q.bump_db_version()
                            st.session_state[f"trip_geo_{t['id']}"] = (
                                sel["lat"], sel["lon"])
                            st.rerun()
                    else:
                        st.caption("No match found (or offline).")
                geo = st.session_state.get(f"trip_geo_{t['id']}")
                if geo:
                    fc = destination_forecast(geo[0], geo[1],
                                              str(t_start), str(t_end))
                    if fc and fc["days"]:
                        import plotly.graph_objects as go
                        xs = [d["date"] for d in fc["days"]]
                        fig_w = go.Figure()
                        fig_w.add_trace(go.Scatter(
                            x=xs, y=[d["t_max"] for d in fc["days"]],
                            name="max °C", line=dict(color=CHART_COLORS[0])))
                        fig_w.add_trace(go.Scatter(
                            x=xs, y=[d["t_min"] for d in fc["days"]],
                            name="min °C", line=dict(color=CHART_COLORS[3])))
                        fig_w.update_layout(height=200,
                                            paper_bgcolor="rgba(0,0,0,0)",
                                            plot_bgcolor="rgba(0,0,0,0)")
                        st.plotly_chart(fig_w, width="stretch")
                        wet = sum(1 for d in fc["days"]
                                  if (d["precip_mm"] or 0) >= 1)
                        st.caption(f"{wet} of {len(fc['days'])} days with "
                                   f"rain ≥ 1 mm · source: open-meteo.com")
                    else:
                        st.caption("Forecast unavailable (window out of "
                                   "range or offline).")
