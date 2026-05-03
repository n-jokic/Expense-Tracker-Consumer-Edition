"""
app.py — Expense Tracker v3 — Consumer Edition
Main Streamlit entry point. Integrates auth, database, insights,
gamification, notifications, and bank import.
"""

import io
import uuid
import calendar
import datetime as dt
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db import (
    init_db,
    get_expenses, add_expense, update_expense, soft_delete_expense, restore_expense,
    get_income, add_income, soft_delete_income, restore_income,
    get_savings, add_savings, soft_delete_savings,
    get_budgets, add_budget, delete_budget,
    get_recurring, add_recurring, update_recurring,
    get_settings, save_settings,
    get_audit_log,
    create_household, join_household, get_household_members, get_household_expenses,
    set_onboarding_complete, update_user_display_name, delete_user_account,
)
from auth import require_auth, logout, change_password
from utils import (
    CATEGORIES, INCOME_SOURCES, SAVINGS_GOALS, CHART_COLORS, CAT_LIST,
    SUPPORTED_CURRENCIES, NEAR_LIMIT_THRESHOLD, SAVINGS_TARGET_PCT, SAVINGS_GOAL_PCT,
    APP_PORT, MAX_AMOUNT, MAX_SAVINGS_TARGET,
    fmt, fmt_both, pbar, get_currency_symbol, to_display,
    safe_error, safe_warning, help_expander, inject_mobile_css, get_ip,
)
from insights import render_insights
from gamification import render_gamification_sidebar
from notifications import (
    check_and_send_budget_alerts, check_and_send_bill_reminders,
    render_notification_settings,
)
from bank_import import render_bank_import_page

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="💰 Expense Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="auto",
)
inject_mobile_css()
init_db()

# ── Auth gate ─────────────────────────────────────────────────────────────────
if not require_auth():
    st.stop()

user_id      = st.session_state.user_id
display_name = st.session_state.display_name

# ── Helpers ───────────────────────────────────────────────────────────────────

def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def _cur_sym(cur: str) -> str:
    return "€" if cur == "EUR" else get_currency_symbol(cur)


def _eur(amount: float, currency: str, rate: float) -> float:
    if currency == "EUR":
        return amount
    return round(amount / rate, 4)


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING WIZARD
# ══════════════════════════════════════════════════════════════════════════════

def render_onboarding():
    step = st.session_state.get("onboarding_step", 0)

    if step == 0:
        st.markdown(f"""
        <div style="text-align:center;padding:40px 0 20px;">
            <div style="font-size:4rem;">👋</div>
            <h1>Welcome, {display_name}!</h1>
            <p style="color:#666;font-size:1.1rem;max-width:500px;margin:0 auto;">
                Let's get you set up. It only takes 2 minutes.
            </p>
        </div>
        """, unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        for col, icon, title, desc in [
            (c1, "📊", "Track Expenses", "Log every purchase — it takes seconds."),
            (c2, "🎯", "Set Budgets",    "Define spending limits per category."),
            (c3, "💡", "Get Insights",   "See where your money is going automatically."),
        ]:
            with col:
                st.markdown(f"""
                <div class="kpi">
                    <div style="font-size:2rem;">{icon}</div>
                    <div class="kpi-val">{title}</div>
                    <div class="kpi-sub">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

        st.write("")
        _, btn_col, _ = st.columns([2, 1, 2])
        with btn_col:
            if st.button("Let's get started →", type="primary", use_container_width=True):
                st.session_state.onboarding_step = 1
                st.rerun()

    elif step == 1:
        st.title("Step 1 of 2 — Your Currency & Budget")
        st.caption("You can change these any time in Settings.")

        settings = get_settings(user_id)
        with st.form("onboard_step1"):
            dc = st.selectbox("Display currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=0, help="The currency you'll see amounts in.")
            rate_val = st.number_input("Exchange rate (1 EUR = ? in local currency)",
                                       value=float(settings.get("exchange_rate", 117.0)),
                                       step=1.0, format="%.2f",
                                       help="If you use EUR only, leave this as-is.")
            budget   = st.number_input("Monthly budget (EUR)",
                                       min_value=0.0, step=100.0, format="%.2f",
                                       help="Your total spending limit per month. You can set category limits later.")
            if st.form_submit_button("Save & Continue →", type="primary", use_container_width=True):
                save_settings(user_id, {
                    "default_currency": dc,
                    "exchange_rate": rate_val,
                    "monthly_budget": budget,
                })
                st.session_state.onboarding_step = 2
                st.rerun()

    elif step == 2:
        st.title("Step 2 of 2 — Log Your First Expense")
        st.caption("Or skip — you can log expenses anytime from the main menu.")

        settings = get_settings(user_id)
        rate     = float(settings.get("exchange_rate", 117.0))

        with st.form("onboard_exp"):
            c1, c2 = st.columns(2)
            with c1:
                exp_date = st.date_input("Date", value=date.today())
                cat      = st.selectbox("Category", CAT_LIST)
            with c2:
                amount = st.number_input("Amount (€)", min_value=0.01,
                                         max_value=MAX_AMOUNT, step=1.0, format="%.2f")
                desc   = st.text_input("Description", placeholder="e.g. Weekly groceries")

            c_save, c_skip = st.columns(2)
            with c_save:
                saved = st.form_submit_button("Save & Finish ✅", type="primary", use_container_width=True)
            with c_skip:
                skipped = st.form_submit_button("Skip for now →", use_container_width=True)

        if saved:
            if desc.strip():
                add_expense(user_id, {
                    "date": exp_date, "category": cat, "subcategory": "",
                    "description": desc, "amount": amount,
                    "currency": "EUR", "amount_eur": amount,
                    "recurring": False, "notes": "",
                })
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.success("🎉 You're all set! Welcome to your Expense Tracker.")
            st.balloons()
            st.rerun()

        if skipped:
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.rerun()


# ── Run onboarding if not complete ───────────────────────────────────────────
if not st.session_state.get("onboarding_complete", True):
    render_onboarding()
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

settings = get_settings(user_id)
rate     = float(settings.get("exchange_rate", 117.0))

PAGES = [
    "📅 Log Expense", "💵 Log Income", "🎯 Savings Goals", "🔄 Recurring",
    "📊 Dashboard", "📈 Forecast", "💡 Insights", "🏦 Bank Import",
    "📋 Audit Log", "👥 Household", "⚙️ Settings",
]

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## 👋 {display_name}")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()

    st.markdown("**Display currency**")
    cur_list = list(SUPPORTED_CURRENCIES.keys())
    dc_default = settings.get("default_currency", "EUR")
    dc_idx = cur_list.index(dc_default) if dc_default in cur_list else 0
    DC = st.selectbox("Currency", cur_list, index=dc_idx, label_visibility="collapsed")
    SYM = get_currency_symbol(DC)

    st.markdown("**💱 Exchange rate (1 EUR)**")
    nr = st.number_input("Rate", value=rate, step=1.0, format="%.2f",
                          label_visibility="collapsed")
    if abs(nr - rate) > 0.001:
        save_settings(user_id, {**settings, "exchange_rate": nr})
        st.rerun()
    st.caption(f"1 EUR = {rate:.2f} {get_currency_symbol(DC)}")

    st.divider()

    # Gamification
    _exp_df_side = get_expenses(user_id)
    _inc_df_side = get_income(user_id)
    _sav_df_side = get_savings(user_id)
    _bud_df_side = get_budgets(user_id)
    render_gamification_sidebar(_exp_df_side, _inc_df_side, _sav_df_side, _bud_df_side)

    st.divider()
    st.caption("📱 Phone access:")
    st.code(f"http://{get_ip()}:{APP_PORT}", language=None)

    if st.button("🚪 Logout", use_container_width=True):
        logout()
        st.rerun()

# ── Recurring bill & budget alerts ───────────────────────────────────────────
_rec_df_alerts  = get_recurring(user_id)
_exp_df_alerts  = get_expenses(user_id)
_bud_df_alerts  = get_budgets(user_id)
check_and_send_bill_reminders(user_id, _rec_df_alerts, _exp_df_alerts, settings)
check_and_send_budget_alerts(user_id, _exp_df_alerts, _bud_df_alerts, settings, rate, DC)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOG EXPENSE
# ══════════════════════════════════════════════════════════════════════════════
if page == "📅 Log Expense":
    st.title("📅 Log Expense")
    help_expander("How to log an expense",
                  "Choose a category first — the subcategory list will update automatically. "
                  "Add a short description so you can search for it later. "
                  "Tick '🔄 Recurring' to also save it as a monthly template.")

    oc1, oc2 = st.columns([3, 1])
    with oc1:
        cat = st.selectbox("Category", CAT_LIST, key="exp_cat_outer")
    with oc2:
        cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="exp_cur_outer")
    sym = _cur_sym(cur)

    with st.form("exp_form", clear_on_submit=False):
        f1, f2 = st.columns(2)
        with f1:
            exp_date = st.date_input("Date", value=date.today())
            subcat   = st.selectbox("Subcategory", ["—"] + CATEGORIES[cat])
        with f2:
            amount  = st.number_input(f"Amount ({sym})", min_value=0.01,
                                      max_value=MAX_AMOUNT, step=0.50, format="%.2f")
            is_rec  = st.checkbox("🔄 Also save as recurring template")
        desc  = st.text_input("Description *", placeholder="e.g. Lidl weekly shop")
        notes = st.text_input("Notes (optional)")
        saved = st.form_submit_button("✅ Save Expense", use_container_width=True, type="primary")

    if saved:
        if not desc.strip():
            safe_error("Please add a description so you can find this expense later.")
        else:
            ae = _eur(amount, cur, rate)
            add_expense(user_id, {
                "date": exp_date, "category": cat,
                "subcategory": subcat if subcat != "—" else "",
                "description": desc, "amount": amount,
                "currency": cur, "amount_eur": ae,
                "recurring": is_rec, "notes": notes,
            })
            if is_rec:
                add_recurring(user_id, {
                    "category": cat,
                    "subcategory": subcat if subcat != "—" else "",
                    "description": desc, "amount": amount,
                    "currency": cur, "amount_eur": ae,
                    "notes": notes, "active": True,
                })
            st.success(f"✅ **{desc}** — {fmt_both(ae, rate)}")
            st.balloons()

    # ── Expense history ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Expense history")
    df_exp = get_expenses(user_id)

    if not df_exp.empty:
        sc1, sc2, sc3 = st.columns([3, 2, 2])
        with sc1: srch = st.text_input("🔍 Search", placeholder="Search description...", key="exp_srch")
        with sc2: catf = st.multiselect("Category filter", CAT_LIST, key="exp_catf")
        with sc3: curf = st.multiselect("Currency filter", list(SUPPORTED_CURRENCIES.keys()), key="exp_curf")

        v = df_exp.sort_values("date", ascending=False).copy()
        if srch: v = v[v["description"].str.contains(srch, case=False, na=False)]
        if catf: v = v[v["category"].isin(catf)]
        if curf: v = v[v["currency"].isin(curf)]

        d = v.head(50).copy()
        d["#"]        = range(len(d))
        d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
        d["Amount"]   = d["amount_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Original"] = d.apply(lambda r: f"{r['amount']:,.2f} {r['currency']}", axis=1)
        d["🔄"]       = d["recurring"].apply(lambda x: "🔄" if str(x).lower() in ("true","1") else "")

        st.dataframe(
            d[["#","Date","category","subcategory","description","Amount","Original","🔄","notes"]],
            use_container_width=True, hide_index=True,
        )

        # Edit / soft-delete
        with st.expander("✏️ Edit or delete a row"):
            row_num = st.number_input("Row # from table above", min_value=0,
                                      max_value=max(0, len(d)-1), step=1, key="exp_edit_idx")
            act = st.radio("Action", ["Edit", "Delete"], horizontal=True, key="exp_act")

            if row_num in d["#"].values:
                sel_id = d[d["#"] == row_num]["id"].iloc[0]
                rd     = df_exp[df_exp["id"] == sel_id].iloc[0]

                if act == "Delete":
                    if st.button("🗑️ Move to Trash", type="secondary"):
                        soft_delete_expense(user_id, sel_id)
                        st.toast("Moved to trash — you can restore it below.", icon="🗑️")
                        st.rerun()
                else:
                    with st.form("exp_edit_form"):
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            ed  = st.date_input("Date", value=rd["date"].date()
                                                if pd.notna(rd["date"]) else date.today())
                            edd = st.text_input("Description", value=str(rd.get("description","")))
                        with ec2:
                            ea  = st.number_input("Amount", value=float(rd.get("amount",0)),
                                                  min_value=0.01, max_value=MAX_AMOUNT,
                                                  step=0.01, format="%.2f")
                            en  = st.text_input("Notes", value=str(rd.get("notes","")))
                        if st.form_submit_button("💾 Save changes", type="primary"):
                            ec = str(rd.get("currency","EUR"))
                            update_expense(user_id, sel_id, {
                                "date": ed, "description": edd,
                                "amount": ea, "amount_eur": _eur(ea, ec, rate), "notes": en,
                            })
                            st.toast("✅ Updated!", icon="✅")
                            st.rerun()

        # Restore deleted
        df_deleted = get_expenses(user_id, include_deleted=True)
        df_deleted = df_deleted[df_deleted["is_deleted"] == True]
        if not df_deleted.empty:
            with st.expander(f"🗑️ Recently deleted ({len(df_deleted)})"):
                for _, row in df_deleted.iterrows():
                    rc1, rc2, rc3 = st.columns([3, 2, 1])
                    with rc1: st.write(f"{row['description']} — {row['category']}")
                    with rc2: st.write(fmt(row["amount_eur"], DC, rate))
                    with rc3:
                        if st.button("↩️ Restore", key=f"rst_{row['id']}"):
                            restore_expense(user_id, row["id"])
                            st.toast("Expense restored!", icon="↩️")
                            st.rerun()

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download expenses.xlsx", data=to_excel(df_exp),
                               file_name="expenses.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else:
        st.info("No expenses yet — add your first one above 👆")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOG INCOME
# ══════════════════════════════════════════════════════════════════════════════
elif page == "💵 Log Income":
    st.title("💵 Log Income")
    help_expander("Budgeted vs actual", "Enter what you *expected* to earn (budgeted) "
                  "and what you *actually* received. This powers your savings rate calculations.")

    oc, _ = st.columns([1, 3])
    with oc:
        cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="inc_cur")
    sym = _cur_sym(cur)

    with st.form("inc_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            inc_date = st.date_input("Date", value=date.today())
            source   = st.selectbox("Source", INCOME_SOURCES)
        with c2:
            budgeted = st.number_input(f"Budgeted ({sym})", min_value=0.0,
                                       max_value=MAX_AMOUNT, step=10.0, format="%.2f")
            actual   = st.number_input(f"Actual ({sym})", min_value=0.0,
                                       max_value=MAX_AMOUNT, step=10.0, format="%.2f")
        notes = st.text_input("Notes")
        saved = st.form_submit_button("✅ Save Income", use_container_width=True, type="primary")

    if saved:
        be = _eur(budgeted, cur, rate)
        ae = _eur(actual,   cur, rate)
        add_income(user_id, {
            "date": inc_date, "source": source,
            "budgeted": budgeted, "actual": actual,
            "currency": cur, "budgeted_eur": be, "actual_eur": ae, "notes": notes,
        })
        st.success(f"✅ {source} — {fmt_both(ae, rate)}")

    dfi = get_income(user_id)
    if not dfi.empty:
        st.divider()
        st.subheader("Income history")
        d = dfi.sort_values("date", ascending=False).head(30).copy()
        d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
        d["Budgeted"] = d["budgeted_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Actual"]   = d["actual_eur"].apply(lambda x: fmt(x, DC, rate))
        d["Original"] = d.apply(lambda r: f"{r['actual']:,.0f} {r['currency']}", axis=1)
        st.dataframe(d[["Date","source","Budgeted","Actual","Original","notes"]],
                     use_container_width=True, hide_index=True)

        with st.expander("🗑️ Delete an income entry"):
            del_ids = dfi["id"].tolist()
            del_labels = [f"{r['date'].strftime('%d %b %Y')} — {r['source']} {fmt(r['actual_eur'], DC, rate)}"
                          for _, r in dfi.iterrows()]
            sel = st.selectbox("Select entry", del_labels, key="inc_del_sel")
            if st.button("🗑️ Move to trash", type="secondary", key="inc_del_btn"):
                sel_idx = del_labels.index(sel)
                soft_delete_income(user_id, del_ids[sel_idx])
                st.toast("Income entry moved to trash.", icon="🗑️")
                st.rerun()

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download income.xlsx", data=to_excel(dfi),
                               file_name="income.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SAVINGS GOALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Savings Goals":
    st.title("🎯 Savings Goals")
    st.caption("Log each deposit. Compound interest is calculated monthly on your running balance.")
    help_expander("How compound interest works",
                  "Each time you log a deposit, the app calculates: "
                  "`new balance = previous_balance × (1 + monthly_rate) + deposit`. "
                  "The monthly rate is your annual interest rate ÷ 12.")

    oc, _ = st.columns([1, 3])
    with oc:
        cur = st.selectbox("Save in", list(SUPPORTED_CURRENCIES.keys()), key="sav_cur")
    sym = _cur_sym(cur)

    with st.form("sav_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            sd  = st.date_input("Date", value=date.today())
            gn  = st.selectbox("Goal", SAVINGS_GOALS)
            tgt = st.number_input(f"Target ({sym})", min_value=0.0,
                                  max_value=MAX_SAVINGS_TARGET, step=100.0, format="%.2f")
        with c2:
            dep  = st.number_input(f"Amount deposited ({sym})", min_value=0.0,
                                   max_value=MAX_AMOUNT, step=10.0, format="%.2f")
            ir   = st.number_input("Annual interest rate (%)", min_value=0.0,
                                   max_value=100.0, step=0.01, format="%.2f",
                                   help="e.g. 4.50 for 4.5% p.a., compounded monthly")
        notes = st.text_input("Notes")
        saved = st.form_submit_button("✅ Save Entry", use_container_width=True, type="primary")

    if saved:
        de = _eur(dep, cur, rate)
        te = _eur(tgt, cur, rate)
        dfs_prev = get_savings(user_id)
        pb = 0.0
        if not dfs_prev.empty:
            pr = dfs_prev[dfs_prev["goal_name"] == gn]
            if not pr.empty:
                pb = float(pr.sort_values("date").iloc[-1]["balance_eur"])
        mr = (ir / 100) / 12
        nb = round(pb * (1 + mr) + de, 4)
        add_savings(user_id, {
            "date": sd, "goal_name": gn, "target_eur": te,
            "deposited": dep, "currency": cur,
            "deposited_eur": de, "interest_rate": ir, "balance_eur": nb, "notes": notes,
        })
        st.success(f"✅ {gn} — new balance: {fmt_both(nb, rate)}")

    dfs = get_savings(user_id)
    if not dfs.empty:
        st.divider()
        st.subheader("Goal progress")
        from insights import savings_projection
        for g in dfs["goal_name"].unique():
            rows  = dfs[dfs["goal_name"] == g].sort_values("date")
            lat   = rows.iloc[-1]
            bal   = float(lat["balance_eur"])
            td    = float(rows["deposited_eur"].sum())
            interest = bal - td
            tr    = rows[rows["target_eur"] > 0]
            tgtv  = float(tr["target_eur"].iloc[-1]) if not tr.empty else 0
            pct   = min(bal / tgtv * 100, 100) if tgtv > 0 else 0
            col   = "#00B050" if pct >= 75 else ("#F4A261" if pct >= 40 else "#E94560")

            gc1, gc2 = st.columns([4, 1])
            with gc1:
                st.markdown(f"**{g}**")
                st.markdown(pbar(pct, col), unsafe_allow_html=True)
                proj = savings_projection(dfs, g)
                proj_str = ""
                if proj["months_to_goal"] and proj["months_to_goal"] > 0 and proj["projected_date"]:
                    proj_str = f" · 🎯 Goal in ~{proj['months_to_goal']}mo ({proj['projected_date'].strftime('%b %Y')})"
                st.caption(
                    f"Balance: **{fmt(bal, DC, rate)}** · "
                    f"Target: {fmt(tgtv, DC, rate) if tgtv > 0 else '—'} · "
                    f"Interest earned: {fmt(interest, DC, rate)} · "
                    f"Rate: {lat['interest_rate']:.2f}%"
                    + proj_str
                )
            with gc2:
                st.metric("", f"{pct:.1f}%" if tgtv > 0 else "—")
            st.write("")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Balance over time")
            fig = px.line(dfs, x="date",
                          y=dfs["balance_eur"].apply(lambda x: to_display(x, DC, rate)),
                          color="goal_name", markers=True,
                          labels={"y": f"Balance ({SYM})", "date": "Date", "goal_name": "Goal"},
                          color_discrete_sequence=CHART_COLORS)
            fig.update_layout(legend_title_text="",
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("Interest rate over time")
            fig2 = px.line(dfs, x="date", y="interest_rate", color="goal_name", markers=True,
                           labels={"interest_rate": "Annual Rate (%)", "date": "Date"},
                           color_discrete_sequence=CHART_COLORS)
            fig2.update_layout(legend_title_text="",
                               plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig2, use_container_width=True)

        with st.expander("📥 Export"):
            st.download_button("⬇️ Download savings.xlsx", data=to_excel(dfs),
                               file_name="savings.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: RECURRING
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔄 Recurring":
    st.title("🔄 Recurring Expenses")
    st.caption("One-click logging for monthly fixed costs.")
    help_expander("What are recurring expenses?",
                  "These are fixed monthly costs like rent, subscriptions, or utilities. "
                  "Add them here once — then tap 'Log now' each month instead of re-entering everything.")

    dfe   = get_expenses(user_id)
    today = date.today()

    with st.expander("➕ Add new template"):
        oc, _ = st.columns([1, 3])
        with oc:
            rc = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="rec_cur")
        rcat = st.selectbox("Category", CAT_LIST, key="rec_cat")
        with st.form("rec_form", clear_on_submit=False):
            rsym = _cur_sym(rc)
            ra1, ra2 = st.columns(2)
            with ra1:
                rsub  = st.selectbox("Subcategory", ["—"] + CATEGORIES[rcat])
                rdesc = st.text_input("Description", placeholder="e.g. Monthly gym membership")
            with ra2:
                ramt   = st.number_input(f"Typical amount ({rsym})", min_value=0.01,
                                         max_value=MAX_AMOUNT, step=0.50, format="%.2f")
                rnotes = st.text_input("Notes")
            if st.form_submit_button("💾 Save template", type="primary"):
                re_eur = _eur(ramt, rc, rate)
                add_recurring(user_id, {
                    "category": rcat,
                    "subcategory": rsub if rsub != "—" else "",
                    "description": rdesc, "amount": ramt,
                    "currency": rc, "amount_eur": re_eur,
                    "notes": rnotes, "active": True,
                })
                st.success(f"✅ {rdesc} saved as template!")
                st.rerun()

    dfr    = get_recurring(user_id)
    active = dfr[dfr["active"] == True] if not dfr.empty else pd.DataFrame()

    if active.empty:
        st.info("No active templates yet. Add one above, or tick '🔄 Recurring' when logging an expense.")
    else:
        st.subheader(f"Monthly checklist — {calendar.month_name[today.month]} {today.year}")
        logged = set()
        if not dfe.empty:
            tm = dfe[(dfe["date"].dt.year == today.year) & (dfe["date"].dt.month == today.month)]
            logged = set(tm["description"].str.strip().tolist())

        for idx, row in active.iterrows():
            done = row["description"].strip() in logged
            rc1, rc2, rc3, rc4 = st.columns([3, 2, 1.5, 1])
            with rc1:
                ic = "✅" if done else "⏳"
                st.markdown(
                    f"{ic} **{row['description']}**  \n"
                    f"<span style='color:#888;font-size:12px;'>"
                    f"{row['category']}{' › '+row['subcategory'] if row['subcategory'] else ''}"
                    f"</span>", unsafe_allow_html=True
                )
            with rc2:
                st.write(fmt(float(row["amount_eur"]), DC, rate))
            with rc3:
                if done:
                    st.success("Logged ✓")
                elif st.button("Log now", key=f"lr_{idx}", type="primary"):
                    add_expense(user_id, {
                        "date": today, "category": row["category"],
                        "subcategory": row["subcategory"],
                        "description": row["description"],
                        "amount": float(row["amount"]),
                        "currency": str(row["currency"]),
                        "amount_eur": float(row["amount_eur"]),
                        "recurring": True, "notes": str(row.get("notes","")),
                    })
                    st.rerun()
            with rc4:
                if st.button("Remove", key=f"dr_{idx}", type="secondary"):
                    update_recurring(user_id, row["id"], {"active": False})
                    st.rerun()
            st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Dashboard":
    st.title("📊 Dashboard")

    dfe = get_expenses(user_id)
    dfi = get_income(user_id)
    dfs = get_savings(user_id)
    dfb = get_budgets(user_id)

    # Household toggle
    hh_id = st.session_state.get("household_id")
    if hh_id:
        view = st.radio("View", ["My data", "Household"], horizontal=True, key="dash_view")
        if view == "Household":
            dfe = get_household_expenses(hh_id)

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

    exp   = flt(dfe)
    inc   = flt(dfi)
    svyr  = dfs[dfs["date"].dt.year == sy] if not dfs.empty else dfs

    ie = float(inc["actual_eur"].sum())  if not inc.empty  else 0.0
    ee = float(exp["amount_eur"].sum())  if not exp.empty  else 0.0
    sd = float(svyr["deposited_eur"].sum()) if not svyr.empty else 0.0
    ne = ie - ee - sd
    sr = (sd / ie * 100) if ie > 0 else 0.0

    st.divider()
    k1, k2, k3, k4, k5 = st.columns(5)
    for col, lbl, eur, cls in [
        (k1, "Income",       ie,   "pos"),
        (k2, "Expenses",     ee,   "neg"),
        (k3, "Saved",        sd,   "pos"),
        (k4, "Net Balance",  ne,   "pos" if ne >= 0 else "neg"),
        (k5, "Savings Rate", None, "pos" if sr >= 15 else "neg"),
    ]:
        with col:
            v   = f"{sr:.1f}%" if lbl == "Savings Rate" else fmt(eur, DC, rate)
            sub = "" if lbl == "Savings Rate" else (
                f'<div class="kpi-sub">'
                f'{fmt(eur, "RSD" if DC == "EUR" else "EUR", rate)}'
                f'</div>'
            )
            st.markdown(
                f'<div class="kpi">'
                f'<div class="kpi-lbl">{lbl}</div>'
                f'<div class="kpi-val {cls}">{v}</div>{sub}'
                f'</div>', unsafe_allow_html=True
            )
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
                                  f"Over by {fmt(act_val - bud_val, DC, rate)}"))
                else:
                    alts.append(("🟡", "warning", c, act_val, bud_val,
                                  f"{act_val / bud_val * 100:.0f}% used"))
        if alts:
            st.subheader("⚠️ Budget alerts")
            for icon, lvl, c, a, b, msg in alts:
                fn = st.error if lvl == "error" else st.warning
                fn(f"{icon} **{c}** — spent {fmt(a, DC, rate)} of {fmt(b, DC, rate)} budget. {msg}")
            st.divider()

    # Charts row 1
    r1a, r1b = st.columns(2)
    with r1a:
        st.subheader("Spending by category")
        if not exp.empty:
            ct  = exp.groupby("category")["amount_eur"].sum().reset_index()
            ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rate))
            fig = px.pie(ct, values="d", names="category", hole=0.45,
                         color_discrete_sequence=CHART_COLORS)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
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
                                 y=mg["budgeted_eur"].apply(lambda x: to_display(x, DC, rate)),
                                 marker_color="#0F3460", opacity=0.45))
            for st2, col2 in cmap.items():
                sub = mg[mg["status"] == st2]
                if not sub.empty:
                    fig.add_trace(go.Bar(name=st2, x=sub["category"],
                                         y=sub["ae"].apply(lambda x: to_display(x, DC, rate)),
                                         marker_color=col2, opacity=0.9))
            fig.update_layout(barmode="group", plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)",
                              margin=dict(t=0,b=0),
                              legend=dict(orientation="h", y=1.08),
                              xaxis_tickangle=-30, yaxis_title=SYM)
            st.plotly_chart(fig, use_container_width=True)

    # Monthly trends
    st.subheader("Monthly trends")
    def mv(df, col, m):
        if df.empty: return 0.0
        return float(df[(df["date"].dt.year == sy) & (df["date"].dt.month == m)][col].sum())

    trnd = pd.DataFrame([{
        "Month":    calendar.month_abbr[m],
        "Income":   to_display(mv(dfi, "actual_eur", m),    DC, rate),
        "Expenses": to_display(mv(dfe, "amount_eur", m),    DC, rate),
        "Savings":  to_display(mv(dfs, "deposited_eur", m), DC, rate),
    } for m in range(1, 13)])
    fig = go.Figure()
    for col3, clr, dsh in [("Income","#00B050","solid"),("Expenses","#E94560","solid"),("Savings","#0F3460","dot")]:
        fig.add_trace(go.Scatter(x=trnd["Month"], y=trnd[col3], name=col3,
                                 line=dict(color=clr, width=2.5, dash=dsh), mode="lines+markers"))
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.06), margin=dict(t=20,b=0), yaxis_title=SYM)
    st.plotly_chart(fig, use_container_width=True)

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
        st.plotly_chart(fig, use_container_width=True)

    with r3b:
        st.subheader("Savings balance")
        if not svyr.empty:
            sp  = svyr.sort_values("date").copy()
            sp["bd"] = sp["balance_eur"].apply(lambda x: to_display(x, DC, rate))
            fig = px.area(sp, x="date", y="bd", color="goal_name",
                          labels={"bd": f"Balance ({SYM})", "goal_name": "Goal"},
                          color_discrete_sequence=CHART_COLORS)
            fig.update_layout(legend_title_text="", plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No savings data for this year.")

    # Top 10
    if not exp.empty:
        st.subheader("Top 10 largest expenses")
        tp = exp.nlargest(10, "amount_eur")[
            ["date","category","subcategory","description","amount_eur","currency"]
        ].copy()
        tp["date"]     = tp["date"].dt.strftime("%d %b %Y").fillna("")
        tp["EUR / RSD"]= tp["amount_eur"].apply(lambda x: fmt_both(x, rate))
        st.dataframe(tp.drop(columns=["amount_eur"]), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: FORECAST
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📈 Forecast":
    st.title("📈 Spending Forecast")
    st.caption("Based on your salary cycle: detected from your last income entry.")

    today      = date.today()
    dfi_all    = get_income(user_id)
    SALARY_DAY = 10

    salary_rows = dfi_all[dfi_all["source"] == "Primary Salary"] if not dfi_all.empty else pd.DataFrame()
    if salary_rows.empty:
        period_start = date(today.year, today.month, SALARY_DAY) if today.day >= SALARY_DAY else (
            date(today.year, today.month - 1, SALARY_DAY) if today.month > 1
            else date(today.year - 1, 12, SALARY_DAY)
        )
        safe_warning("No salary entry found — using the 10th as cycle start. "
                     "Log a 'Primary Salary' income entry to enable automatic detection.")
    else:
        latest_salary = salary_rows.sort_values("date").iloc[-1]
        period_start  = latest_salary["date"].date()
        st.success(f"✅ Cycle start: **{period_start.strftime('%d %b %Y')}**")

    next_m      = period_start.month + 1 if period_start.month < 12 else 1
    next_y      = period_start.year  if period_start.month < 12 else period_start.year + 1
    period_end  = date(next_y, next_m, period_start.day) - dt.timedelta(days=1)

    days_in_period = (period_end - period_start).days + 1
    days_elapsed   = max((today - period_start).days + 1, 1)
    days_remaining = max((period_end - today).days, 0)

    st.info(f"📅 **{period_start.strftime('%d %b')} → {period_end.strftime('%d %b %Y')}** "
            f"({days_in_period} days · {days_elapsed} in · {days_remaining} left)")

    dfe = get_expenses(user_id)
    dfb = get_budgets(user_id)
    period_start_ts = pd.Timestamp(period_start)
    period_end_ts   = pd.Timestamp(period_end)

    period_exp = dfe[
        (dfe["date"] >= period_start_ts) & (dfe["date"] <= period_end_ts)
    ].copy() if not dfe.empty else pd.DataFrame(columns=["amount_eur","date","category"])

    st.divider()
    st.subheader("💰 Total spending forecast")

    total_spent = float(period_exp["amount_eur"].sum()) if not period_exp.empty else 0.0
    daily_avg   = total_spent / days_elapsed if days_elapsed > 0 else 0.0
    projected   = daily_avg * days_in_period

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
                f'<div class="kpi-val {cls}">{fmt(val, DC, rate)}</div>'
                f'<div class="kpi-sub">{fmt(val, "RSD" if DC=="EUR" else "EUR", rate)}</div>'
                f'</div>', unsafe_allow_html=True
            )

    st.write("")
    if total_budget == 0:
        safe_warning("No budget set. Go to ⚙️ Settings → Budget to set one.")
    elif on_track:
        st.success(f"✅ On track! Projected: **{fmt(projected, DC, rate)}** — "
                   f"**{fmt(total_budget - projected, DC, rate)} under budget**.")
    else:
        st.error(f"⚠️ Overspend risk. Projected: **{fmt(projected, DC, rate)}** — "
                 f"**{fmt(over_under, DC, rate)} over budget**. "
                 f"Target: **{fmt((total_budget - total_spent) / max(days_remaining, 1), DC, rate)}/day**.")

    if total_budget > 0:
        pct_spent = min(total_spent / total_budget * 100, 100)
        bar_color = "#00B050" if on_track else "#E94560"
        st.markdown(f"**Spent** {fmt(total_spent, DC, rate)} of {fmt(total_budget, DC, rate)} ({pct_spent:.1f}%)")
        st.markdown(pbar(pct_spent, bar_color), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "💡 Insights":
    render_insights(
        get_expenses(user_id), get_income(user_id),
        get_savings(user_id), settings, DC, rate,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BANK IMPORT
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏦 Bank Import":
    render_bank_import_page(user_id, rate)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Audit Log":
    st.title("📋 Audit Log")
    help_expander("What is the audit log?",
                  "Every change you make — adding expenses, editing, deleting, "
                  "changing settings — is recorded here. This gives you a complete "
                  "history of what happened to your data.")

    df_audit = get_audit_log(user_id, limit=200)
    if df_audit.empty:
        st.info("No activity recorded yet.")
    else:
        actions = df_audit["action"].unique().tolist()
        filt = st.multiselect("Filter by action", actions, default=actions, key="audit_filt")
        df_show = df_audit[df_audit["action"].isin(filt)].copy()
        df_show["timestamp"] = df_show["timestamp"].dt.strftime("%d %b %Y %H:%M")
        st.dataframe(
            df_show[["timestamp","action","table_name","record_id","details"]],
            use_container_width=True, hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: HOUSEHOLD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "👥 Household":
    st.title("👥 Shared Household")
    st.caption("Share your budget view with family or a partner.")
    help_expander("How households work",
                  "Create a household and share the invite code with your partner or family. "
                  "Once they join, you can view combined expenses on the Dashboard.")

    hh_id = st.session_state.get("household_id")

    if not hh_id:
        tab_create, tab_join = st.tabs(["🏠 Create household", "🔗 Join existing"])
        with tab_create:
            with st.form("hh_create"):
                hh_name = st.text_input("Household name", placeholder="e.g. The Smiths")
                if st.form_submit_button("Create →", type="primary"):
                    if hh_name.strip():
                        new_hh_id, code = create_household(user_id, hh_name.strip())
                        st.session_state.household_id = new_hh_id
                        st.success(f"✅ Household created!")
                        st.info(f"**Invite code:** `{code}` — share this with your partner.")
                        st.rerun()
                    else:
                        safe_error("Please enter a household name.")
        with tab_join:
            with st.form("hh_join"):
                code_in = st.text_input("Invite code", placeholder="e.g. AB12CD34")
                if st.form_submit_button("Join →", type="primary"):
                    if join_household(user_id, code_in):
                        st.success("✅ Joined household!")
                        st.rerun()
                    else:
                        safe_error("Invalid invite code. Please check and try again.")
    else:
        members = get_household_members(hh_id)
        st.subheader(f"👥 {len(members)} member(s)")
        for m in members:
            st.markdown(f"- {m['display_name']}")

        hh_exp = get_household_expenses(hh_id)
        if not hh_exp.empty:
            st.divider()
            st.subheader("Combined expenses")
            ct = hh_exp.groupby("category")["amount_eur"].sum().reset_index()
            ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rate))
            fig = px.pie(ct, values="d", names="category", hole=0.4,
                         color_discrete_sequence=CHART_COLORS)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.title("⚙️ Settings")

    tab_cur, tab_bud, tab_notif, tab_acct, tab_data = st.tabs(
        ["💱 Currency", "💰 Budget", "📧 Notifications", "🔐 Account", "📦 Data"]
    )

    # ── Currency tab ──────────────────────────────────────────────────────────
    with tab_cur:
        st.subheader("💱 Currency & Exchange Rate")
        with st.form("cur_form"):
            nr2 = st.number_input("Exchange rate (1 EUR = ? local)",
                                   value=float(settings.get("exchange_rate", 117.0)),
                                   step=0.5, format="%.2f")
            dc2 = st.selectbox("Default display currency",
                                list(SUPPORTED_CURRENCIES.keys()),
                                index=list(SUPPORTED_CURRENCIES.keys()).index(
                                    settings.get("default_currency","EUR")))
            if st.form_submit_button("💾 Save", type="primary"):
                save_settings(user_id, {"exchange_rate": nr2, "default_currency": dc2})
                st.success(f"✅ Saved — 1 EUR = {nr2:.2f} {get_currency_symbol(dc2)}")
                st.rerun()

    # ── Budget tab ────────────────────────────────────────────────────────────
    with tab_bud:
        st.subheader("💰 Overall Monthly Budget")
        cur_eur = float(settings.get("monthly_budget", 0.0))
        with st.form("overall_bud_form"):
            ob_amt = st.number_input("Total monthly budget (€)", min_value=0.0,
                                     max_value=MAX_SAVINGS_TARGET,
                                     step=50.0, format="%.2f", value=cur_eur)
            if st.form_submit_button("💾 Save budget", type="primary"):
                save_settings(user_id, {"monthly_budget": ob_amt})
                st.success(f"✅ Budget set to {fmt_both(ob_amt, rate)}")
                st.rerun()

        st.divider()
        st.subheader("Category Budgets")
        bcat = st.selectbox("Category", CAT_LIST, key="bud_cat")
        bcur = st.selectbox("Enter in", list(SUPPORTED_CURRENCIES.keys()), key="bud_cur")
        with st.form("cat_bud_form", clear_on_submit=False):
            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                by = st.number_input("Year",  value=date.today().year,  step=1, format="%d")
                bm = st.selectbox("Month", range(1,13),
                                  format_func=lambda x: calendar.month_name[x])
            with bc2:
                bsub = st.selectbox("Subcategory",
                                    ["(entire category)"] + CATEGORIES[bcat])
            with bc3:
                ba = st.number_input(f"Budget ({_cur_sym(bcur)})", min_value=0.0,
                                     max_value=MAX_SAVINGS_TARGET, step=10.0, format="%.2f")
            if st.form_submit_button("💾 Save", type="primary"):
                be = _eur(ba, bcur, rate)
                add_budget(user_id, {
                    "year": int(by), "month": int(bm), "category": bcat,
                    "subcategory": bsub if bsub != "(entire category)" else "",
                    "budgeted_eur": be,
                })
                st.success("✅ Budget saved")
                st.rerun()

        dfb = get_budgets(user_id)
        if not dfb.empty:
            d = dfb.copy()
            d["month"]  = d["month"].apply(lambda x: calendar.month_name[int(x)])
            d["Budget"] = d["budgeted_eur"].apply(lambda x: fmt_both(x, rate))
            st.dataframe(d[["year","month","category","subcategory","Budget"]],
                         use_container_width=True, hide_index=True)
            with st.expander("🗑️ Delete a budget row"):
                di = st.number_input("Row index (from table)", min_value=0,
                                     max_value=max(0, len(dfb)-1), step=1)
                if st.button("Delete", type="secondary", key="del_bud"):
                    bid = int(dfb.iloc[di]["id"])
                    delete_budget(user_id, bid)
                    st.toast("Budget row deleted.", icon="🗑️")
                    st.rerun()

    # ── Notifications tab ─────────────────────────────────────────────────────
    with tab_notif:
        render_notification_settings(user_id, settings)

    # ── Account tab ───────────────────────────────────────────────────────────
    with tab_acct:
        st.subheader("🔐 Account")

        with st.form("display_name_form"):
            new_name = st.text_input("Display name", value=display_name)
            if st.form_submit_button("💾 Update name"):
                if new_name.strip():
                    update_user_display_name(user_id, new_name.strip())
                    st.session_state.display_name = new_name.strip()
                    st.success("✅ Name updated!")
                    st.rerun()

        st.divider()
        st.subheader("Change Password")
        with st.form("pw_form"):
            old_pw  = st.text_input("Current password", type="password")
            new_pw  = st.text_input("New password", type="password",
                                    placeholder="min. 8 chars, one number")
            conf_pw = st.text_input("Confirm new password", type="password")
            if st.form_submit_button("🔒 Change password", type="primary"):
                if new_pw != conf_pw:
                    safe_error("New passwords don't match.")
                else:
                    ok, msg = change_password(user_id, old_pw, new_pw)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        safe_error(msg)

        st.divider()
        st.subheader("⚠️ Danger Zone")
        with st.expander("🗑️ Delete my account"):
            st.error("This will permanently delete **all** your data. This cannot be undone.")
            confirm = st.text_input("Type DELETE to confirm")
            if st.button("Delete account permanently", type="secondary"):
                if confirm == "DELETE":
                    delete_user_account(user_id)
                    logout()
                    st.rerun()
                else:
                    safe_error("Please type DELETE exactly to confirm.")

    # ── Data tab ──────────────────────────────────────────────────────────────
    with tab_data:
        st.subheader("📦 Export Your Data")
        st.caption("Download your data as Excel files. Back these up to Google Drive or OneDrive regularly.")

        data_map = {
            "expenses":  get_expenses(user_id, include_deleted=True),
            "income":    get_income(user_id, include_deleted=True),
            "savings":   get_savings(user_id, include_deleted=True),
            "budgets":   get_budgets(user_id),
            "recurring": get_recurring(user_id),
        }
        cols = st.columns(len(data_map))
        for i, (key, df_d) in enumerate(data_map.items()):
            with cols[i]:
                if not df_d.empty:
                    st.download_button(
                        f"⬇️ {key}", data=to_excel(df_d),
                        file_name=f"{key}_export.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_{key}", use_container_width=True,
                    )
                else:
                    st.button(f"{key} (empty)", disabled=True,
                              use_container_width=True, key=f"dis_{key}")

        st.divider()
        st.subheader("📤 Audit Log Export")
        df_audit_exp = get_audit_log(user_id, limit=10000)
        if not df_audit_exp.empty:
            st.download_button("⬇️ audit_log.xlsx", data=to_excel(df_audit_exp),
                               file_name="audit_log.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
