"""
Household page: create/join shared households, view members and combined expenses.
"""

import altair as alt
import streamlit as st

import queries as q
from db import (create_household, join_household, leave_household,
                get_user_by_username, get_household_by_member)
from utils import (CHART_COLORS, to_display, safe_error, help_expander,
                   get_currency_symbol)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

SYM     = get_currency_symbol(DC)
AMT_FMT = f"%.0f {SYM}" if DC in ("RSD", "HUF", "HRK") else f"{SYM}%.2f"

st.title(":material/group: Shared household")
st.caption("Share your budget view with family or a partner.")
help_expander("How households work",
              "Create a household and share the invite code with your partner or family. "
              "Once they join, you can view combined expenses on the Dashboard.")

hh_id = st.session_state.get("household_id")


@st.dialog("Leave household", icon=":material/logout:")
def _confirm_leave_household():
    st.write("Are you sure you want to leave this household?")
    if st.button("Leave household", type="primary", icon=":material/logout:",
                 width="stretch"):
        try:
            # Bump BEFORE leaving: bump_data_revision(include_household=True)
            # invalidates every member's cached household readers, and that only
            # reaches the other members while this user is still in the household.
            q.bump_db_version()
            leave_household(user_id)
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        st.session_state.household_id = None
        st.toast("You left the household.", icon=":material/waving_hand:")
        st.rerun()


if not hh_id:
    tab_create, tab_join = st.tabs([":material/home: Create household",
                                    ":material/link: Join existing"])
    with tab_create:
        with st.form("hh_create"):
            hh_name = st.text_input("Household name", placeholder="e.g. The Smiths")
            if st.form_submit_button("Create household", type="primary"):
                if hh_name.strip():
                    if st.session_state.get("household_id"):
                        st.toast("Already in a household — duplicate prevented.", icon=":material/check:")
                        st.rerun()
                    try:
                        new_hh_id, code = create_household(user_id, hh_name.strip())
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        st.session_state.household_id = new_hh_id
                        q.bump_db_version()
                        st.success("Household created!", icon=":material/check_circle:")
                        st.code(code)
                        st.caption("Share this code with your partner.")
                        st.rerun()
                else:
                    safe_error("Please enter a household name.")
    with tab_join:
        with st.form("hh_join"):
            code_in = st.text_input("Invite code", placeholder="e.g. AB12CD34")
            if st.form_submit_button("Join household", type="primary"):
                try:
                    _joined = join_household(user_id, code_in)
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    if _joined:
                        # Refresh session state immediately (previously needed a re-login)
                        u = get_user_by_username(st.session_state.username)
                        st.session_state.household_id = u["household_id"] if u else None
                        # The joiner is now a member: bump so the OTHER members'
                        # cached member lists / combined views refresh too.
                        q.bump_db_version()
                        st.success("Joined household!", icon=":material/check_circle:")
                        st.rerun()
                    else:
                        safe_error("Invalid invite code. Please check and try again.")
else:
    members = q.household_members(hh_id)
    st.subheader(f"{len(members)} {'member' if len(members) == 1 else 'members'}")
    for m in members:
        st.markdown(f"- {m['display_name']}")

    # The invite code persists in the households table — always show it so
    # members can share it again after joining.
    hh_info = get_household_by_member(user_id)
    if hh_info and hh_info.get("invite_code"):
        st.markdown("**Invite code**")
        st.code(hh_info["invite_code"])
        st.caption("Share this code — members join with it.")
        if st.button("Regenerate invite code", icon=":material/refresh:",
                     key="hh_regen_code"):
            from db import regenerate_invite_code
            try:
                new_code = regenerate_invite_code(user_id)
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                if new_code:
                    q.bump_db_version()
                    st.toast("Invite code rotated — the old one no longer works.",
                             icon=":material/refresh:")
                    st.rerun()

    if st.button("Leave household", type="secondary", icon=":material/logout:"):
        _confirm_leave_household()

    st.caption("Combined views show the expenses of CURRENT members: expenses "
               "logged while someone was a member stay on their own account "
               "when they leave, and only expenses logged while a member "
               "appear here.")

    hh_exp = q.household_expenses(hh_id)
    if not hh_exp.empty:
        st.subheader("Combined expenses")
        ct = hh_exp.groupby("category")["amount_eur"].sum().reset_index()
        ct["d"] = ct["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        donut = (
            alt.Chart(ct)
            .mark_arc(innerRadius=60, outerRadius=120)
            .encode(
                theta=alt.Theta("d:Q", stack=True),
                color=alt.Color("category:N", scale=alt.Scale(range=CHART_COLORS)),
                tooltip=[alt.Tooltip("category:N", title="Category"),
                         alt.Tooltip("d:Q", title="Total", format=",.2f")],
            )
        )
        st.altair_chart(donut, width="stretch")

        st.subheader("Spending by member")
        pm = hh_exp.groupby("member")["amount_eur"].sum().reset_index()
        pm = pm.rename(columns={"member": "Member"})
        pm["Total"] = pm["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        st.dataframe(
            pm[["Member", "Total"]],
            hide_index=True,
            column_config={
                "Total": st.column_config.NumberColumn("Total", format=AMT_FMT),
            },
        )
    else:
        st.info("No household expenses yet — log some and they'll show up here.",
                icon=":material/group:")
