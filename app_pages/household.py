"""
Household page: create/join shared households, view members and combined expenses.
"""

import plotly.express as px
import streamlit as st

import queries as q
from db import (create_household, join_household, leave_household,
                get_user_by_username, get_household_by_member)
from utils import CHART_COLORS, fmt, to_display, safe_error, help_expander

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

st.title("👥 Shared household")
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
                    st.success("✅ Household created!")
                    st.info(f"**Invite code:** `{code}` — share this with your partner.")
                    st.rerun()
                else:
                    safe_error("Please enter a household name.")
    with tab_join:
        with st.form("hh_join"):
            code_in = st.text_input("Invite code", placeholder="e.g. AB12CD34")
            if st.form_submit_button("Join →", type="primary"):
                if join_household(user_id, code_in):
                    # Refresh session state immediately (previously needed a re-login)
                    u = get_user_by_username(st.session_state.username)
                    st.session_state.household_id = u["household_id"] if u else None
                    st.success("✅ Joined household!")
                    st.rerun()
                else:
                    safe_error("Invalid invite code. Please check and try again.")
else:
    members = q.household_members(hh_id)
    st.subheader(f"👥 {len(members)} member(s)")
    for m in members:
        st.markdown(f"- {m['display_name']}")

    # The invite code persists in the households table — always show it so
    # members can share it again after joining.
    hh_info = get_household_by_member(user_id)
    if hh_info and hh_info.get("invite_code"):
        st.markdown("**Invite code**")
        st.code(hh_info["invite_code"])
        st.caption("Share this code — members join with it.")

    st.divider()
    if st.button("🚪 Leave household", type="secondary"):
        leave_household(user_id)
        st.session_state.household_id = None
        st.toast("You left the household.", icon="👋")
        st.rerun()

    hh_exp = q.household_expenses(hh_id)
    if not hh_exp.empty:
        st.divider()
        st.subheader("Combined expenses")
        ct = hh_exp.groupby("category")["amount_eur"].sum().reset_index()
        ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        fig = px.pie(ct, values="d", names="category", hole=0.4,
                     color_discrete_sequence=CHART_COLORS)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")

        st.subheader("Spending by member")
        pm = hh_exp.groupby("member")["amount_eur"].sum().reset_index()
        pm["Total"] = pm["amount_eur"].apply(lambda x: fmt(x, DC, rates))
        st.dataframe(pm[["member","Total"]], hide_index=True)
    else:
        st.info("No household expenses yet — log some and they'll show up here.")
