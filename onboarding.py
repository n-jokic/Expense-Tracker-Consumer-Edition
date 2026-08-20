"""
onboarding.py — Two-step onboarding wizard shown to new users before the main app.
"""

from datetime import date

import streamlit as st

import queries as q
from db import add_expense, set_onboarding_complete, get_settings
from utils import (
    CAT_LIST, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    get_rates, get_currency_symbol,
)


def render_onboarding():
    user_id      = st.session_state.user_id
    display_name = st.session_state.display_name
    step = st.session_state.get("onboarding_step", 0)

    if step == 0:
        with st.container(horizontal_alignment="center"):
            st.markdown(f"# Welcome, {display_name}!")
            st.caption("Let's get you set up. It only takes 2 minutes.")

        c1, c2, c3 = st.columns(3)
        for col, icon, title, desc in [
            (c1, ":material/receipt_long:", "Track expenses", "Log every purchase — it takes seconds."),
            (c2, ":material/savings:",      "Set budgets",    "Define spending limits per category."),
            (c3, ":material/insights:",     "Get insights",   "See where your money is going automatically."),
        ]:
            with col:
                with st.container(border=True):
                    st.markdown(icon)
                    st.markdown(f"**{title}**")
                    st.caption(desc)

        with st.container(horizontal_alignment="center"):
            if st.button("Let's get started →", type="primary"):
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
            rate_val = None
            if dc != "EUR":
                rate_val = st.number_input(
                    f"Exchange rate (1 EUR = ? {get_currency_symbol(dc)})",
                    value=max(float(rates.get(dc, 117.0)), 0.0001),
                    step=1.0, format="%.2f", min_value=0.0001,
                    help="Used to convert your amounts for display.")
            budget   = st.number_input("Monthly budget (EUR)",
                                       min_value=0.0, step=100.0, format="%.2f",
                                       help="Your total spending limit per month. You can set category limits later.")
            if st.form_submit_button("Save & Continue →", type="primary", width="stretch"):
                if dc != "EUR" and not (rate_val > 0 and rate_val == rate_val):
                    st.error("❌ The exchange rate must be a positive number "
                             "greater than zero.")
                else:
                    updates = {
                        "default_currency": dc,
                        "monthly_budget": budget,
                    }
                    if dc != "EUR":
                        from db import get_settings as _db_get_settings
                        fresh_rates = dict((_db_get_settings(user_id) or {}).get("currency_rates") or {})
                        fresh_rates[dc] = float(rate_val)
                        updates["currency_rates"] = fresh_rates
                    q.save_settings(user_id, updates)
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
                amount = st.number_input("Amount (€)", min_value=0.0,
                                         max_value=MAX_AMOUNT, step=1.0, format="%.2f",
                                         value=0.0)
                desc   = st.text_input("Description", placeholder="e.g. Weekly groceries")

            c_save, c_skip = st.columns(2)
            with c_save:
                saved = st.form_submit_button("Save & Finish", type="primary", icon=":material/check:", width="stretch")
            with c_skip:
                skipped = st.form_submit_button("Skip for now", width="stretch")

        if saved:
            if not desc.strip():
                st.error("Please add a description.")
            elif amount <= 0:
                st.error("Amount must be greater than 0.")
            else:
                try:
                    add_expense(user_id, {
                        "date": exp_date, "category": cat, "subcategory": "",
                        "description": desc, "amount": amount,
                        "currency": "EUR", "amount_eur": amount,
                        "recurring": False, "notes": "",
                    })
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
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
