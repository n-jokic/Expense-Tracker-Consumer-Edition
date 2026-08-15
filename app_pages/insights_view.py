"""
Insights page: delegates to insights.render_insights.
(Named insights_view.py so it doesn't shadow the root insights.py module.)
"""

import streamlit as st

import queries as q
from insights import render_insights

user_id = st.session_state.user_id

render_insights(
    q.expenses(user_id),
    q.income(user_id),
    q.savings(user_id),
    st.session_state.settings,
    st.session_state.dc,
    st.session_state.rates,
    q.recurring(user_id),
)
