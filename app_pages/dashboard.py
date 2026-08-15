"""
Dashboard page: KPIs, budget alerts, spending charts, monthly trends.
"""

import calendar
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import queries as q
from utils import (
    NEAR_LIMIT_THRESHOLD, SAVINGS_TARGET_PCT, SAVINGS_GOAL_PCT, CHART_COLORS,
    fmt, fmt_row, to_display, get_currency_symbol,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
SYM     = get_currency_symbol(DC)

st.title("📊 Dashboard")

dfe = q.expenses(user_id)
dfi = q.income(user_id)
dfs = q.savings(user_id)
dfb = q.budgets(user_id)

# Household toggle
hh_id = st.session_state.get("household_id")
if hh_id:
    view = st.segmented_control("View", ["My data", "Household"], default="My data", key="dash_view")
    if view == "Household":
        dfe = q.household_expenses(hh_id)

if dfe.empty and dfi.empty:
    st.info("No data yet — start logging expenses or income 👈")
    st.stop()

ayrs = sorted(set(
    (list(dfe["date"].dropna().dt.year.unique()) if not dfe.empty else []) +
    (list(dfi["date"].dropna().dt.year.unique()) if not dfi.empty else [])
), reverse=True) or [date.today().year]

fc1, fc2 = st.columns([1, 2])
with fc1:
    sy = st.selectbox("Year", ayrs)
with fc2:
    mo_opts = ["All months"] + [calendar.month_name[m] for m in range(1, 13)]
    sml = st.select_slider("Month", mo_opts)
    sm  = mo_opts.index(sml)

def flt(df):
    if df.empty: return df
    mask = df["date"].dt.year == sy
    if sm > 0: mask = mask & (df["date"].dt.month == sm)
    return df[mask]

def prev_flt(df):
    """Same filter, shifted one period back (month or year)."""
    if df.empty: return df
    if sm > 0:
        py, pm = (sy, sm - 1) if sm > 1 else (sy - 1, 12)
        mask = (df["date"].dt.year == py) & (df["date"].dt.month == pm)
    else:
        mask = df["date"].dt.year == sy - 1
    return df[mask]

def _delta(cur, prev):
    if prev and prev > 0:
        pct = (cur - prev) / prev * 100
        arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
        return f"{arrow} {abs(pct):.0f}% vs prev"
    return ""

exp   = flt(dfe)
inc   = flt(dfi)
svyr  = dfs[dfs["date"].dt.year == sy] if not dfs.empty else dfs
prev_exp = prev_flt(dfe)
prev_inc = prev_flt(dfi)

ie = float(inc["actual_eur"].sum())  if not inc.empty  else 0.0
ee = float(exp["amount_eur"].sum())  if not exp.empty  else 0.0
sd = float(svyr["deposited_eur"].sum()) if not svyr.empty else 0.0
ne = ie - ee - sd
sr = (sd / ie * 100) if ie > 0 else 0.0

pie = float(prev_inc["actual_eur"].sum()) if not prev_inc.empty else 0.0
pee = float(prev_exp["amount_eur"].sum()) if not prev_exp.empty else 0.0

st.divider()
k1, k2, k3, k4, k5 = st.columns(5)
for col, lbl, eur, cls, dlt in [
    (k1, "Income",       ie,   "pos", _delta(ie, pie)),
    (k2, "Expenses",     ee,   "neg", _delta(ee, pee)),
    (k3, "Saved",        sd,   "pos", ""),
    (k4, "Net Balance",  ne,   "pos" if ne >= 0 else "neg", ""),
    (k5, "Savings Rate", None, "pos" if sr >= 15 else "neg", ""),
]:
    with col:
        v   = f"{sr:.1f}%" if lbl == "Savings Rate" else fmt(eur, DC, rates)
        sub = "" if lbl == "Savings Rate" else (
            f'<div class="kpi-sub">{fmt(eur, "EUR" if DC != "EUR" else "RSD", rates)}</div>'
        )
        dlt_html = f'<div class="kpi-sub">{dlt}</div>' if dlt else ""
        st.markdown(
            f'<div class="kpi">'
            f'<div class="kpi-lbl">{lbl}</div>'
            f'<div class="kpi-val {cls}">{v}</div>{sub}{dlt_html}'
            f'</div>', unsafe_allow_html=True
        )

# Fixed costs metric
rec_df = q.recurring(user_id)
if not rec_df.empty:
    rec_active = rec_df[rec_df["active"] == True]
    if not rec_active.empty:
        yearly = float(rec_active["amount_eur"].sum()) * 12
        st.caption("")
        st.metric("🔁 Fixed costs / year (recurring bills)",
                  f"{fmt(yearly, DC, rates)} · {len(rec_active)} bills")

st.divider()

# Budget alerts
if not dfb.empty and not exp.empty:
    bf = dfb[dfb["year"] == sy]
    if sm > 0: bf = bf[bf["month"] == sm]
    cb  = bf.groupby("category")["budgeted_eur"].sum()
    ca  = exp.groupby("category")["amount_eur"].sum()
    alts = []
    for c in ca.index:
        bud_val = float(cb.get(c, 0))
        act_val = float(ca.get(c, 0))
        if bud_val > 0 and act_val > bud_val * NEAR_LIMIT_THRESHOLD:
            if act_val > bud_val:
                alts.append(("🔴", "error", c, act_val, bud_val,
                              f"Over by {fmt(act_val - bud_val, DC, rates)}"))
            else:
                alts.append(("🟡", "warning", c, act_val, bud_val,
                              f"{act_val / bud_val * 100:.0f}% used"))
    if alts:
        st.subheader("⚠️ Budget alerts")
        for icon, lvl, c, a, b, msg in alts:
            fn = st.error if lvl == "error" else st.warning
            fn(f"{icon} **{c}** — spent {fmt(a, DC, rates)} of {fmt(b, DC, rates)} budget. {msg}")
        st.divider()

# Budget progress bars for the selected month
if sm > 0 and not dfb.empty and not exp.empty:
    bf3 = dfb[(dfb["year"] == sy) & (dfb["month"] == sm)]
    if not bf3.empty:
        st.subheader(f"📊 Budget progress — {calendar.month_name[sm]}")
        cb3 = bf3.groupby("category")["budgeted_eur"].sum()
        ca3 = exp.groupby("category")["amount_eur"].sum()
        for c in ca3.index:
            b = float(cb3.get(c, 0))
            if b <= 0:
                continue
            a = float(ca3.get(c, 0))
            pct = min(a / b, 1.0)
            st.markdown(f"**{c}** — {fmt(a, DC, rates)} of {fmt(b, DC, rates)} ({pct*100:.0f}%)")
            st.progress(pct)
        st.divider()

# Charts row 1
r1a, r1b = st.columns(2)
with r1a:
    st.subheader("Spending by category")
    if not exp.empty:
        ct  = exp.groupby("category")["amount_eur"].sum().reset_index()
        ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        fig = px.pie(ct, values="d", names="category", hole=0.45,
                     color_discrete_sequence=CHART_COLORS)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No expenses for this period.")

with r1b:
    st.subheader("Budget vs actual")
    if not exp.empty:
        ac = exp.groupby("category")["amount_eur"].sum().reset_index().rename(
            columns={"amount_eur": "ae"})
        if not dfb.empty:
            bf2 = dfb[dfb["year"] == sy]
            if sm > 0: bf2 = bf2[bf2["month"] == sm]
            bc  = bf2.groupby("category")["budgeted_eur"].sum().reset_index()
            mg  = ac.merge(bc, on="category", how="outer").fillna(0)
        else:
            mg = ac.copy(); mg["budgeted_eur"] = 0
        mg["status"] = mg.apply(
            lambda r: "Over budget" if r["budgeted_eur"] > 0 and r["ae"] > r["budgeted_eur"]
            else ("Near limit" if r["budgeted_eur"] > 0 and r["ae"] > r["budgeted_eur"] * NEAR_LIMIT_THRESHOLD
                  else "On track"), axis=1)
        cmap = {"Over budget": "#E94560", "Near limit": "#F4A261", "On track": "#00B050"}
        fig  = go.Figure()
        fig.add_trace(go.Bar(name="Budget", x=mg["category"],
                             y=mg["budgeted_eur"].apply(lambda x: to_display(x, DC, rates)),
                             marker_color="#0F3460", opacity=0.45))
        for st2, col2 in cmap.items():
            sub = mg[mg["status"] == st2]
            if not sub.empty:
                fig.add_trace(go.Bar(name=st2, x=sub["category"],
                                     y=sub["ae"].apply(lambda x: to_display(x, DC, rates)),
                                     marker_color=col2, opacity=0.9))
        fig.update_layout(barmode="group", plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)",
                          margin=dict(t=0,b=0),
                          legend=dict(orientation="h", y=1.08),
                          xaxis_tickangle=-30, yaxis_title=SYM)
        st.plotly_chart(fig, width="stretch")

# Monthly trends
st.subheader("Monthly trends")
def mv(df, col, m):
    if df.empty: return 0.0
    return float(df[(df["date"].dt.year == sy) & (df["date"].dt.month == m)][col].sum())

trnd = pd.DataFrame([{
    "Month":    calendar.month_abbr[m],
    "Income":   to_display(mv(dfi, "actual_eur", m),    DC, rates),
    "Expenses": to_display(mv(dfe, "amount_eur", m),    DC, rates),
    "Savings":  to_display(mv(dfs, "deposited_eur", m), DC, rates),
} for m in range(1, 13)])
fig = go.Figure()
for col3, clr, dsh in [("Income","#00B050","solid"),("Expenses","#E94560","solid"),("Savings","#0F3460","dot")]:
    fig.add_trace(go.Scatter(x=trnd["Month"], y=trnd[col3], name=col3,
                             line=dict(color=clr, width=2.5, dash=dsh), mode="lines+markers"))
fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                  legend=dict(orientation="h", y=1.06), margin=dict(t=20,b=0), yaxis_title=SYM)
st.plotly_chart(fig, width="stretch")

# Cumulative net cash flow
st.subheader("Cumulative net cash flow")
cf = pd.DataFrame([{
    "Month": calendar.month_abbr[m],
    "Net": to_display(mv(dfi, "actual_eur", m) - mv(dfe, "amount_eur", m) - mv(dfs, "deposited_eur", m),
                      DC, rates),
} for m in range(1, 13)])
cf["Cumulative"] = cf["Net"].cumsum()
figc = go.Figure()
figc.add_trace(go.Scatter(x=cf["Month"], y=cf["Net"], name="Monthly net",
                          mode="lines+markers", line=dict(color="#457B9D", width=2)))
figc.add_trace(go.Scatter(x=cf["Month"], y=cf["Cumulative"], name="Cumulative",
                          mode="lines+markers", line=dict(color="#0F3460", width=2.5)))
figc.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                   legend=dict(orientation="h", y=1.1), margin=dict(t=20,b=0),
                   yaxis_title=SYM)
st.plotly_chart(figc, width="stretch")

# Savings rate chart
r3a, r3b = st.columns(2)
with r3a:
    st.subheader("Savings rate by month")
    rts = pd.DataFrame([{
        "Month": calendar.month_abbr[m],
        "Rate%": round(mv(dfs, "deposited_eur", m) / mv(dfi, "actual_eur", m) * 100, 1)
                 if mv(dfi, "actual_eur", m) > 0 else 0
    } for m in range(1, 13)])
    fig = px.bar(rts, x="Month", y="Rate%",
                 text=rts["Rate%"].apply(lambda x: f"{x:.1f}%"),
                 color="Rate%", color_continuous_scale=["#E94560","#F4A261","#00B050"],
                 range_color=[0, 30])
    fig.add_hline(y=SAVINGS_TARGET_PCT, line_dash="dash", line_color="#F4A261",
                  annotation_text=f"{SAVINGS_TARGET_PCT}% target")
    fig.add_hline(y=SAVINGS_GOAL_PCT, line_dash="dash", line_color="#00B050",
                  annotation_text=f"{SAVINGS_GOAL_PCT}% goal")
    fig.update_traces(textposition="outside")
    fig.update_layout(coloraxis_showscale=False, plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", margin=dict(t=20,b=0))
    st.plotly_chart(fig, width="stretch")

with r3b:
    st.subheader("Savings balance")
    if not svyr.empty:
        sp  = svyr.sort_values("date").copy()
        sp["bd"] = sp["balance_eur"].apply(lambda x: to_display(x, DC, rates))
        fig = px.area(sp, x="date", y="bd", color="goal_name",
                      labels={"bd": f"Balance ({SYM})", "goal_name": "Goal"},
                      color_discrete_sequence=CHART_COLORS)
        fig.update_layout(legend_title_text="", plot_bgcolor="rgba(0,0,0,0)",
                          paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No savings data for this year.")

# Top 10
if not exp.empty:
    st.subheader("Top 10 largest expenses")
    tp = exp.nlargest(10, "amount_eur")[
        ["date","category","subcategory","description","amount","currency","amount_eur"]
    ].copy()
    tp["date"]   = tp["date"].dt.strftime("%d %b %Y").fillna("")
    tp["Amount"] = tp.apply(lambda r: fmt_row(r["amount_eur"], r["amount"], r["currency"], DC, rates), axis=1)
    st.dataframe(tp[["date","category","subcategory","description","Amount"]], hide_index=True)
