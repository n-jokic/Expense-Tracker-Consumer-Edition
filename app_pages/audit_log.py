"""
Audit log page: full history of changes made to the user's data.
"""

import streamlit as st

import queries as q
from utils import help_expander

user_id = st.session_state.user_id

st.title("📋 Audit log")
help_expander("What is the audit log?",
              "Every change you make — adding expenses, editing, deleting, "
              "changing settings — is recorded here. This gives you a complete "
              "history of what happened to your data.")

df_audit = q.audit(user_id, limit=200)
if df_audit.empty:
    st.info("No activity recorded yet.")
else:
    actions = df_audit["action"].unique().tolist()
    filt = st.multiselect("Filter by action", actions, default=actions, key="audit_filt")
    df_show = df_audit[df_audit["action"].isin(filt)].copy()
    df_show["timestamp"] = df_show["timestamp"].dt.strftime("%d %b %Y %H:%M")
    st.dataframe(
        df_show[["timestamp","action","table_name","record_id","details"]],
        hide_index=True,
    )
