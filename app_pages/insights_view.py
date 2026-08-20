"""
Insights page: delegates to insights.render_insights.
(Named insights_view.py so it doesn't shadow the root insights.py module.)
"""

import streamlit as st

import queries as q
from insights import render_insights

user_id = st.session_state.user_id


def _add_subscription(user_id: int, row) -> None:
    """Delegate for subscription -> recurring promotion (keeps insights.py pure)."""
    from db import add_recurring
    add_recurring(user_id, {
        "category": row.category, "subcategory": "",
        "description": str(row.description),
        "amount": float(row.amount_eur),
        "currency": "EUR", "amount_eur": float(row.amount_eur),
        "due_day": None, "notes": "Detected from your spending",
        "active": True,
    })
    q.bump_db_version()
    st.toast(f"Added '{row.description}' to Recurring",
             icon=":material/repeat:")
    st.rerun()

render_insights(
    q.expenses(user_id),
    q.income(user_id),
    q.savings(user_id),
    st.session_state.settings,
    st.session_state.dc,
    st.session_state.rates,
    q.recurring(user_id),
    q.loans(user_id),
    user_id=user_id,
    on_add_subscription=_add_subscription,
)