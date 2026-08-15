"""
onboarding.py — Two-step onboarding wizard shown to new users before the main app.
"""

from datetime import date

import streamlit as st

import queries as q
from db import add_expense, set_onboarding_complete, get_settings
from utils import (
    CATEGORIES, CAT_LIST, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    get_rates, get_currency_symbol,
)


def render_onboarding():
    user_id      = st.session_state.user_id
    display_name = st.session_state.display_name
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
            if st.button("Let's get started →", type="primary", width="stretch"):
                st.session_state.onboarding_step = 1
                st.rerun()

    elif step == 1:
        st.title("Step 1 of 2 — Your currency & budget")
        st.caption("You can change these any time in Settings.")

        settings = get_settings(user_id)
        rates = get_rates(settings)
        dc_default = settings.get("default_currency", "EUR")
        dc_idx = list(SUPPORTED_CURRENCIES.keys()).index(dc_default) \
            if dc_default in SUPPORTED_CURRENCIES else 0

        with st.form("onboard_step1"):
            dc = st.selectbox("Display currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=dc_idx, help="The currency you'll see amounts in.")
            rate_val = st.number_input(
                f"Exchange rate (1 EUR = ? {get_currency_symbol(dc)})",
                value=float(rates.get(dc if dc != "EUR" else "RSD", 117.0)),
                step=1.0, format="%.2f",
                help="If you use EUR only, leave this as-is.")
            budget   = st.number_input("Monthly budget (EUR)",
                                       min_value=0.0, step=100.0, format="%.2f",
                                       help="Your total spending limit per month. You can set category limits later.")
            if st.form_submit_button("Save & Continue →", type="primary", width="stretch"):
                new_rates = dict(rates)
                new_rates[dc if dc != "EUR" else "RSD"] = float(rate_val)
                q.save_settings(user_id, {
                    "default_currency": dc,
                    "currency_rates": new_rates,
                    "monthly_budget": budget,
                })
                st.session_state.onboarding_step = 2
                st.rerun()

    elif step == 2:
        st.title("Step 2 of 2 — Log your first expense")
        st.caption("Or skip — you can log expenses anytime from the main menu.")

        settings = get_settings(user_id)
        rates    = get_rates(settings)

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
                saved = st.form_submit_button("Save & Finish ✅", type="primary", width="stretch")
            with c_skip:
                skipped = st.form_submit_button("Skip for now →", width="stretch")

        if saved:
            if desc.strip():
                add_expense(user_id, {
                    "date": exp_date, "category": cat, "subcategory": "",
                    "description": desc, "amount": amount,
                    "currency": "EUR", "amount_eur": amount,
                    "recurring": False, "notes": "",
                })
                q.bump_db_version()
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.success("🎉 You're all set! Welcome to your Expense Tracker.")
            st.balloons()
            st.rerun()

        if skipped:
            set_onboarding_complete(user_id)
            st.session_state.onboarding_complete = True
            st.rerun()
