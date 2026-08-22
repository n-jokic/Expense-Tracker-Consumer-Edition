"""ui/styles.py — CSS helpers and st helpers that are presentation-only."""

from __future__ import annotations

import streamlit as st

# research.md U1b: single source of truth for app colors - pages should
# import these names instead of hardcoding hex literals.
C_PRIMARY = "#0F3460"
C_NEG     = "#E94560"
C_POS     = "#00B050"
C_WARN    = "#F4A261"
C_BLUE    = "#457B9D"
C_LIGHT   = "#A8DADC"
C_SAND    = "#E9C46A"
C_TEAL    = "#2A9D8F"
C_GREY    = "#A8A8A8"
C_PRIMARY_SOFT = "rgba(15,52,96,0.08)"   # translucent fill for sparklines

CHART_COLORS = [C_PRIMARY, C_NEG, C_POS, C_WARN, C_BLUE, C_LIGHT, C_SAND, C_TEAL]

QUADRANT_COLORS = {
    "Quick wins":   C_POS,
    "Plan & save":  C_PRIMARY,
    "Maybe later":  C_GREY,
    "Reconsider":   C_NEG,
}

def safe_error(msg: str):
    st.error(msg, icon=":material/error:")


def safe_warning(msg: str):
    st.warning(msg, icon=":material/warning:")

def help_expander(title: str, content: str):
    with st.expander(title, icon=":material/help:"):
        st.markdown(content)

# research.md U1c: inject_mobile_css lives canonically in utils.py (the copy
# app.py and the pages import). This module no longer duplicates it.
