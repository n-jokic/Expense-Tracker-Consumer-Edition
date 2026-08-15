"""
Recurring expenses page: monthly templates with a one-tap "Log now" checklist.
Bills are matched by (description, amount) so same-named templates don't collide.
"""

import calendar
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_expense, add_recurring, update_recurring
from utils import (
    CATEGORIES, CAT_LIST, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    fmt, to_eur, get_currency_symbol,
    help_expander,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

st.title("🔄 Recurring expenses")
st.caption("One-click logging for monthly fixed costs.")
help_expander("What are recurring expenses?",
              "These are fixed monthly costs like rent, subscriptions, or utilities. "
              "Add them here once — then tap 'Log now' each month instead of re-entering everything.")

dfe   = q.expenses(user_id)
today = date.today()

with st.expander("➕ Add new template"):
    oc, _ = st.columns([1, 3])
    with oc:
        rc = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="rec_cur")
    rcat = st.selectbox("Category", CAT_LIST, key="rec_cat")
    with st.form("rec_form", clear_on_submit=False):
        rsym = get_currency_symbol(rc)
        ra1, ra2 = st.columns(2)
        with ra1:
            rsub  = st.selectbox("Subcategory", ["—"] + CATEGORIES[rcat])
            rdesc = st.text_input("Description", placeholder="e.g. Monthly gym membership")
        with ra2:
            ramt   = st.number_input(f"Typical amount ({rsym})", min_value=0.01,
                                     max_value=MAX_AMOUNT, step=0.50, format="%.2f")
            rnotes = st.text_input("Notes")
        if st.form_submit_button("💾 Save template", type="primary"):
            re_eur = to_eur(ramt, rc, rates)
            add_recurring(user_id, {
                "category": rcat,
                "subcategory": rsub if rsub != "—" else "",
                "description": rdesc, "amount": ramt,
                "currency": rc, "amount_eur": re_eur,
                "notes": rnotes, "active": True,
            })
            q.bump_db_version()
            st.success(f"✅ {rdesc} saved as template!")
            st.rerun()

dfr    = q.recurring(user_id)
active = dfr[dfr["active"] == True] if not dfr.empty else pd.DataFrame()

if active.empty:
    st.info("No active templates yet. Add one above, or tick '🔄 Recurring' when logging an expense.")
else:
    st.subheader(f"Monthly checklist — {calendar.month_name[today.month]} {today.year}")
    logged = set()
    if not dfe.empty:
        tm = dfe[(dfe["date"].dt.year == today.year) & (dfe["date"].dt.month == today.month)]
        logged = set(zip(tm["description"].str.strip().str.lower(), tm["amount_eur"].round(2)))

    for idx, row in active.iterrows():
        key = (row["description"].strip().lower(), round(float(row["amount_eur"] or 0.0), 2))
        done = key in logged
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
            st.write(fmt(float(row["amount_eur"]), DC, rates))
        with rc3:
            if done:
                st.success("Logged ✓")
            elif st.button("Log now", key=f"lr_{idx}", type="primary", width="stretch"):
                add_expense(user_id, {
                    "date": today, "category": row["category"],
                    "subcategory": row["subcategory"],
                    "description": row["description"],
                    "amount": float(row["amount"]),
                    "currency": str(row["currency"]),
                    "amount_eur": float(row["amount_eur"]),
                    "recurring": True, "notes": str(row.get("notes","")),
                })
                q.bump_db_version()
                st.rerun()
        with rc4:
            if st.button("Remove", key=f"dr_{idx}", type="secondary", width="stretch"):
                update_recurring(user_id, row["id"], {"active": False})
                q.bump_db_version()
                st.rerun()
        st.divider()
