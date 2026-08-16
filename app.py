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
    SUPPORTED_CURRENCIES, get_rates,
    get_lan_urls, get_server_port, qr_png,
    inject_mobile_css, TLS_ENABLED,
)
from gamification import (
    render_gamification_sidebar, get_earned_milestones, award_new_milestones,
)
from notifications import (
    check_and_send_budget_alerts, check_and_send_bill_reminders,
    check_and_send_weekly_summary, check_loan_reminders,
)
from rates import refresh_rates_if_due
from market_data import maybe_refresh_in_background

# ── Page config & boot ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="💰 Expense Tracker",
    page_icon=":material/account_balance_wallet:",
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

# ── Milestone unlocks & rewards (persisted once; fun-money bonuses) ───────────
# Compute the earned set ONCE per rerun and reuse it for both the award flow
# and the sidebar renderer (previously recomputed, doubling the ML work).
_expenses_snap = q.expenses(user_id)
_income_snap   = q.income(user_id)
_savings_snap  = q.savings(user_id)
_budgets_snap  = q.budgets(user_id)
_loans_snap    = q.loans(user_id)
earned_ms = get_earned_milestones(
    _expenses_snap, _income_snap, _savings_snap, _budgets_snap,
    settings=settings, loans_df=_loans_snap,
)
new_ms, ms_bonus = award_new_milestones(user_id, earned_ms, settings)
if new_ms:
    names = ", ".join(f"{m['icon']} {m['title']}" for m in new_ms)
    st.toast(f"🏅 Milestone unlocked: {names}"
             + (f" — +€{ms_bonus:.0f} fun money next month!" if ms_bonus > 0 else ""),
             icon="🏅")
    st.balloons()
    settings = st.session_state.settings  # refresh after reward save

# ── Shared sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## {display_name}")

    cur_list  = list(SUPPORTED_CURRENCIES.keys())
    dc_default = settings.get("default_currency", "EUR")
    dc_idx    = cur_list.index(dc_default) if dc_default in cur_list else 0
    DC = st.selectbox("Display currency", cur_list, index=dc_idx, key="dc_sidebar")
    st.session_state.dc = DC

    with st.form("rate_form"):
        rsd_val = st.number_input("Exchange rate (1 EUR = ? din)",
                                  value=max(float(rates.get("RSD", 117.0)), 0.0001),
                                  step=1.0, format="%.2f", min_value=0.0001)
        saved_rate = st.form_submit_button(
            "Update rate", icon=":material/currency_exchange:", width="stretch",
        )
    if saved_rate:
        if not (float(rsd_val) > 0 and float(rsd_val) == float(rsd_val)):
            st.error("The exchange rate must be a positive number greater than 0.")
        else:
            new_rates = dict(st.session_state.settings.get("currency_rates") or {})
            new_rates["RSD"] = float(rsd_val)
            q.save_settings(user_id, {"currency_rates": new_rates})
            st.rerun()
    st.caption(f"1 EUR = {rates['RSD']:.2f} din · other rates in Settings")

    # Gamification
    render_gamification_sidebar(
        _expenses_snap, _income_snap,
        _savings_snap, _budgets_snap,
        settings=settings, loans_df=_loans_snap,
    )

    # Phone access panel (experimental)
    st.markdown("**Phone access**")
    port = get_server_port()
    urls, hostname = get_lan_urls(port)
    if urls:
        st.code(urls[0], language=None)
        try:
            qr_bytes = qr_png(urls[0])
            st.image(qr_bytes, width=220)
            st.download_button(
                "Download QR code", data=qr_bytes,
                file_name="expense_tracker_qr.png", mime="image/png",
                key="dl_qr", icon=":material/download:", width="stretch",
            )
        except Exception:
            # A QR failure must never take the whole shell down.
            st.caption("QR code unavailable — open the address above manually.")
        st.caption("Scan with your phone camera — same Wi-Fi network.")
        if hostname:
            scheme = "https" if TLS_ENABLED else "http"
            st.caption(f"or {scheme}://{hostname}:{port}")
        st.caption("Phone access & sync are **experimental** — "
                   "see Settings → Sync for pairing.")
    else:
        st.caption("Start the server with `run_server.bat` and allow Private network access in the firewall prompt.")

    if st.button("Logout", icon=":material/logout:", width="stretch"):
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

# ── Page routing (grouped) ────────────────────────────────────────────────────
pg = st.navigation({
    "Overview": [
        st.Page("app_pages/dashboard.py", title="Dashboard",
                icon=":material/dashboard:", default=True),
    ],
    "Track": [
        st.Page("app_pages/log_expense.py", title="Log expense", icon=":material/receipt_long:"),
        st.Page("app_pages/log_income.py", title="Log income", icon=":material/payments:"),
        st.Page("app_pages/savings.py", title="Savings goals", icon=":material/savings:"),
        st.Page("app_pages/bank_import_view.py", title="Bank import", icon=":material/account_balance_wallet:"),
    ],
    "Plan": [
        st.Page("app_pages/recurring.py", title="Recurring", icon=":material/event_repeat:"),
        st.Page("app_pages/loans.py", title="Loans", icon=":material/account_balance:"),
        st.Page("app_pages/big_purchases.py", title="Big purchases", icon=":material/shopping_bag:"),
        st.Page("app_pages/travel.py", title="Travel budget", icon=":material/flight:"),
        st.Page("app_pages/portfolio.py", title="Portfolio", icon=":material/trending_up:"),
    ],
    "Understand": [
        st.Page("app_pages/forecast.py", title="Forecast", icon=":material/query_stats:"),
        st.Page("app_pages/insights_view.py", title="Insights", icon=":material/lightbulb:"),
    ],
    "Household & Data": [
        st.Page("app_pages/household.py", title="Household", icon=":material/groups:"),
        st.Page("app_pages/audit_log.py", title="Audit log", icon=":material/history:"),
        st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:"),
    ],
})
pg.run()
