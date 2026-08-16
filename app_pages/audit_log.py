"""
Audit log page: full history of changes made to the user's data.
"""

import json

import streamlit as st

import queries as q
from utils import help_expander

user_id = st.session_state.user_id

st.title(":material/history: Audit log")
help_expander("What is the audit log?",
              "Every change you make — adding expenses, editing, deleting, "
              "changing settings — is recorded here. This gives you a complete "
              "history of what happened to your data.")

df_audit = q.audit(user_id, limit=200)
if df_audit.empty:
    st.info("No activity recorded yet.", icon=":material/info:")
else:
    actions = df_audit["action"].unique().tolist()
    filt = st.multiselect("Filter by action", actions, default=actions, key="audit_filt")
    df_show = df_audit[df_audit["action"].isin(filt)].copy()
    # Pretty-print the JSON details blobs (stored as strings by log_audit).
    def _pretty(d):
        if isinstance(d, str) and d.strip().startswith("{"):
            try:
                return json.dumps(json.loads(d), indent=1, ensure_ascii=False)
            except (ValueError, TypeError):
                return d
        return d

    if "details" in df_show.columns:
        df_show["details"] = df_show["details"].map(_pretty)
    st.caption("Showing the latest 200 changes")
    st.dataframe(
        df_show[["timestamp","action","table_name","details"]],
        hide_index=True,
        column_config={
            "timestamp": st.column_config.DatetimeColumn("Timestamp",
                                                        format="DD MMM YYYY, HH:mm"),
            "table_name": st.column_config.TextColumn("Area"),
        },
    )
