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
    fmt, fmt_row, to_display, get_currency_symbol, effective_category_budgets,
    filter_started_templates,
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
personal_view = True
if hh_id:
    view = st.segmented_control("View", ["My data", "Household"], default="My data", key="dash_view")
    if view == "Household":
        personal_view = False
        dfe = q.household_expenses(hh_id)

# ── Personal task hub (Personal mode only) ──────────────────────────────────
if personal_view:
    with st.container(border=True):
        st.markdown("**Quick actions**")
        qa1, qa2, qa3 = st.columns(3)
        with qa1:
            st.page_link("app_pages/log_expense.py", label="Log expense",
                         icon=":material/receipt_long:", width="stretch")
        with qa2:
            st.page_link("app_pages/log_income.py", label="Log income",
                         icon=":material/payments:", width="stretch")
        with qa3:
            st.page_link("app_pages/settings.py", label="Add budget",
                         icon=":material/tune:", help="Budgets live in Settings",
                         width="stretch")
        st.caption("Budgets live in Settings — add or edit them there.")

    # Upcoming bills: active recurring templates with a due day within the
    # next 7 calendar days.
    rec_df  = q.recurring(user_id)
    today   = date.today()

    def _next_due(day, base):
        """Next occurrence of a due day-of-month on/after base, clamped to the
        month length (e.g. due_day 31 in a 30-day month)."""
        def _clamp(y, m):
            return min(day, calendar.monthrange(y, m)[1])
        if base.day <= day:
            d = date(base.year, base.month, _clamp(base.year, base.month))
            if d >= base:
                return d
        y, m = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
        return date(y, m, _clamp(y, m))

    upcoming = []
    if not rec_df.empty:
        started = filter_started_templates(
            rec_df[rec_df["active"] == True], today.year, today.month)
        for _, r in started.iterrows():
            dd = r["due_day"]
            if dd is None or pd.isna(dd):
                continue
            d = _next_due(int(dd), today)
            if 0 <= (d - today).days <= 7:
                upcoming.append((d, r))
    if upcoming:
        with st.container(border=True):
            st.markdown("**Upcoming bills**")
            for d, r in sorted(upcoming, key=lambda t: t[0]):
                desc = (r["description"] if pd.notna(r["description"])
                        else (r["category"] if pd.notna(r["category"]) else "Bill"))
                amt  = fmt_row(r["amount_eur"], r["amount"], r["currency"], DC, rates)
                st.markdown(f"- {d.strftime('%d %b')} — **{desc}** · {amt}")

    # Recent activity: the 5 most recent expenses.
    with st.container(border=True):
        st.markdown("**Recent activity**")
        recent = dfe.head(5)
        if recent.empty:
            st.caption("No expenses logged yet.")
            st.page_link("app_pages/log_expense.py", label="Log your first expense",
                         icon=":material/receipt_long:")
        else:
            rec = recent[["date", "description", "category", "amount", "currency", "amount_eur"]].copy()
            rec["date"] = rec["date"].dt.strftime("%d %b %Y").fillna("")
            rec["Amount"] = rec.apply(lambda r: fmt_row(r["amount_eur"], r["amount"],
                                                        r["currency"], DC, rates), axis=1)
            st.dataframe(rec[["date", "description", "category", "Amount"]],
                         hide_index=True, width="stretch")

if personal_view and dfe.empty and dfi.empty:
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
if personal_view:
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
else:
    # Household spending summary — no personal net balance or savings KPIs.
    hh_members = q.household_members(hh_id)
    hh_top     = (exp.groupby("category")["amount_eur"].sum().idxmax()
                  if not exp.empty else None)
    h1, h2, h3 = st.columns(3)
    for col, lbl, v, cls in [
        (h1, "Household spending", fmt(ee, DC, rates), "neg"),
        (h2, "Members",            str(len(hh_members)), "pos"),
        (h3, "Top category",       hh_top or "—", "pos"),
    ]:
        with col:
            st.markdown(
                f'<div class="kpi">'
                f'<div class="kpi-lbl">{lbl}</div>'
                f'<div class="kpi-val {cls}">{v}</div>'
                f'</div>', unsafe_allow_html=True
            )
    st.caption("Personal income, savings, budgets, loans and fun money are hidden "
               "in household view — switch to Personal mode to see them.")

# Fixed costs metric (personal) — templates count only from their start month.
rec_df = q.recurring(user_id)
if personal_view and not rec_df.empty:
    rec_active = rec_df[rec_df["active"] == True]
    if not rec_active.empty:
        yearly = 0.0
        for _, r in rec_active.iterrows():
            start_m = str(r.get("start_month") or "").strip()  # NB: not `sm` — that's the month filter below
            months = 12
            if start_m:
                try:
                    y, m = int(start_m.split("-")[0]), int(start_m.split("-")[1])
                    if y == date.today().year:
                        months = max(0, 13 - m)
                    elif y > date.today().year:
                        months = 0
                except (ValueError, TypeError):
                    pass
            yearly += float(r["amount_eur"]) * months
        st.caption("")
        st.metric("🔁 Fixed costs / year (recurring bills)",
                  f"{fmt(yearly, DC, rates)} · {len(rec_active)} bills")

# Debt KPIs (loans) — personal
from finance import loan_schedule
df_loans = q.loans(user_id)
if personal_view and not df_loans.empty:
    total_debt = 0.0
    free_dates = []
    for _, row in df_loans.iterrows():
        if row["status"] != "active":
            continue
        pay_df = q.loan_payments(user_id, str(row["id"]))
        payments = [(r["date"].date(), float(r["amount_eur"]))
                    for _, r in pay_df.iterrows() if pd.notna(r["date"])]
        start_date = (row["start_date"].date() if pd.notna(row["start_date"])
                      else date.today())
        sched = loan_schedule(float(row["principal_eur"]), float(row["annual_rate"]),
                              int(row["term_months"]), start_date,
                              int(row["payment_day"]), payments)
        total_debt += sched["remaining_balance"]
        if sched["payoff_date"]:
            free_dates.append(sched["payoff_date"])
    if total_debt > 0 or free_dates:
        st.caption("")
        d1, d2 = st.columns(2)
        with d1:
            st.metric("💳 Total debt", fmt(total_debt, DC, rates))
        with d2:
            free = max(free_dates).strftime("%b %Y") if free_dates else "—"
            st.metric("Debt-free by", free)

st.divider()

# Budget alerts (personal)
if personal_view and not dfb.empty and not exp.empty:
    bf = dfb[dfb["year"] == sy]
    if sm > 0: bf = bf[bf["month"] == sm]
    cb  = effective_category_budgets(bf)
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

# Budget progress bars for the selected month (personal)
if personal_view and sm > 0 and not dfb.empty and not exp.empty:
    bf3 = dfb[(dfb["year"] == sy) & (dfb["month"] == sm)]
    if not bf3.empty:
        st.subheader(f"📊 Budget progress — {calendar.month_name[sm]}")
        cb3 = effective_category_budgets(bf3)
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

# Fun money (current calendar month, regardless of the selected period) — personal
settings_dash = st.session_state.settings
fun_allowance = float(settings_dash.get("fun_money") or 0.0)
if personal_view and fun_allowance > 0:
    from utils import fun_spent, DEFAULT_FUN_CATEGORIES
    fun_cats = settings_dash.get("fun_categories") or DEFAULT_FUN_CATEGORIES
    fun_month = fun_spent(dfe, fun_cats, date.today().year, date.today().month)
    bonus = 0.0
    if settings_dash.get("fun_bonus_month") == f"{date.today().year:04d}-{date.today().month:02d}":
        bonus = float(settings_dash.get("fun_bonus_amount") or 0.0)
    allowance = fun_allowance + bonus
    fpct = min(fun_month / allowance, 1.0) if allowance > 0 else 0.0
    st.subheader("🎈 Fun money")
    bonus_str = f" · incl. +€{bonus:.0f} milestone bonus" if bonus > 0 else ""
    st.markdown(f"**{fmt(fun_month, DC, rates)}** of {fmt(allowance, DC, rates)} "
                f"({fpct*100:.0f}%{bonus_str})")
    st.progress(fpct)
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
    if personal_view:
        st.subheader("Budget vs actual")
        if not exp.empty:
            ac = exp.groupby("category")["amount_eur"].sum().reset_index().rename(
                columns={"amount_eur": "ae"})
            if not dfb.empty:
                bf2 = dfb[dfb["year"] == sy]
                if sm > 0: bf2 = bf2[bf2["month"] == sm]
                bc = pd.DataFrame(
                    [(c, v) for c, v in effective_category_budgets(bf2).items()],
                    columns=["category", "budgeted_eur"])
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
    else:
        st.info("Budget vs actual is personal — switch to Personal mode to see it.")

# Monthly trends (personal — mixes income/savings with expenses)
if personal_view:
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

    # Cumulative net cash flow (personal)
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

    # Savings rate chart (personal)
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
