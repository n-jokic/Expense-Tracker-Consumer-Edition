"""
app.py — Expense Tracker v4 — Consumer Edition
Main Streamlit entry point: auth/onboarding gates, shared sidebar, alerts,
and st.navigation-based page routing (pages live in app_pages/).
"""

import streamlit as st

import queries as q
from db import init_db, backup_db, get_settings
from auth import require_auth, logout
from onboarding import render_onboarding
from utils import (
    SUPPORTED_CURRENCIES, get_rates, get_currency_symbol,
    get_lan_urls, get_server_port, qr_png,
    inject_mobile_css,
)
from gamification import render_gamification_sidebar
from notifications import (
    check_and_send_budget_alerts, check_and_send_bill_reminders,
    check_and_send_weekly_summary, check_loan_reminders,
)
from rates import refresh_rates_if_due
from market_data import maybe_refresh_in_background

# ── Page config & boot ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="💰 Expense Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="auto",
)
inject_mobile_css()
init_db()
backup_db()

# ── Auth gate ─────────────────────────────────────────────────────────────────
if not require_auth():
    st.stop()

# ── Shared session state ──────────────────────────────────────────────────────
user_id      = st.session_state.user_id
display_name = st.session_state.display_name
if "db_version" not in st.session_state:
    st.session_state.db_version = 0
st.session_state.settings = get_settings(user_id)
# Refresh exchange rates on login when they're older than 3 days
# (keeps the last known values on any network failure)
st.session_state.settings, _ = refresh_rates_if_due(user_id, st.session_state.settings)

# ── Onboarding gate (default False: never skip onboarding accidentally) ───────
if not st.session_state.get("onboarding_complete", False):
    render_onboarding()
    st.stop()

settings = st.session_state.settings
rates    = get_rates(settings)
st.session_state.rates = rates

# ── Shared sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## 👋 {display_name}")

    st.markdown("**Display currency**")
    cur_list  = list(SUPPORTED_CURRENCIES.keys())
    dc_default = settings.get("default_currency", "EUR")
    dc_idx    = cur_list.index(dc_default) if dc_default in cur_list else 0
    DC = st.selectbox("Currency", cur_list, index=dc_idx, key="dc_sidebar")
    st.session_state.dc = DC
    SYM = get_currency_symbol(DC)

    with st.form("rate_form"):
        rsd_val = st.number_input(f"Exchange rate (1 EUR = ? {SYM})",
                                  value=float(rates.get("RSD", 117.0)),
                                  step=1.0, format="%.2f")
        saved_rate = st.form_submit_button("💱 Update rate")
    if saved_rate:
        new_rates = dict(st.session_state.settings.get("currency_rates") or {})
        new_rates["RSD"] = float(rsd_val)
        q.save_settings(user_id, {"currency_rates": new_rates})
        st.rerun()
    st.caption(f"1 EUR = {rates['RSD']:.2f} din · other rates in ⚙️ Settings")

    st.divider()

    # Gamification
    render_gamification_sidebar(
        q.expenses(user_id), q.income(user_id),
        q.savings(user_id), q.budgets(user_id),
    )

    st.divider()

    # Phone access panel
    st.markdown("**📱 Phone access**")
    port = get_server_port()
    urls, hostname = get_lan_urls(port)
    if urls:
        st.code(urls[0], language=None)
        qr_bytes = qr_png(urls[0])
        st.image(qr_bytes, width=220)
        st.download_button(
            "⬇️ Download QR code", data=qr_bytes,
            file_name="expense_tracker_qr.png", mime="image/png",
            key="dl_qr", width="stretch",
        )
        st.caption("Scan with your phone camera — same Wi-Fi network.")
        if hostname:
            st.caption(f"or http://{hostname}:{port}")
    else:
        st.caption("Start the server with `run_server.bat` and allow Private network access in the firewall prompt.")

    st.divider()

    if st.button("🚪 Logout", width="stretch"):
        logout()
        st.rerun()

# ── Recurring bill & budget alerts (once per rerun; session-state deduped) ────
settings = st.session_state.settings
DC       = st.session_state.dc
check_and_send_bill_reminders(user_id, q.recurring(user_id), q.expenses(user_id), settings)
check_and_send_budget_alerts(user_id, q.expenses(user_id), q.budgets(user_id), settings, rates, DC)
check_loan_reminders(user_id, q.loans(user_id), q.expenses(user_id), settings)
check_and_send_weekly_summary(user_id, q.expenses(user_id), settings)
# Portfolio prices refresh daily in the background (never blocks the UI)
maybe_refresh_in_background(user_id)

# ── Page routing ──────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("app_pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("app_pages/log_expense.py", title="Log expense", icon=":material/receipt_long:"),
    st.Page("app_pages/log_income.py", title="Log income", icon=":material/payments:"),
    st.Page("app_pages/savings.py", title="Savings goals", icon=":material/savings:"),
    st.Page("app_pages/portfolio.py", title="Portfolio", icon=":material/trending_up:"),
    st.Page("app_pages/recurring.py", title="Recurring", icon=":material/event_repeat:"),
    st.Page("app_pages/loans.py", title="Loans", icon=":material/account_balance:"),
    st.Page("app_pages/big_purchases.py", title="Big purchases", icon=":material/shopping_bag:"),
    st.Page("app_pages/forecast.py", title="Forecast", icon=":material/query_stats:"),
    st.Page("app_pages/insights_view.py", title="Insights", icon=":material/lightbulb:"),
    st.Page("app_pages/bank_import_view.py", title="Bank import", icon=":material/account_balance_wallet:"),
    st.Page("app_pages/audit_log.py", title="Audit log", icon=":material/history:"),
    st.Page("app_pages/household.py", title="Household", icon=":material/groups:"),
    st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
])
pg.run()
