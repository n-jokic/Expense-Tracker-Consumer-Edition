"""ui/styles.py — CSS helpers and st helpers that are presentation-only."""

from __future__ import annotations

import streamlit as st

CHART_COLORS = ["#0F3460","#E94560","#00B050","#F4A261","#457B9D","#A8DADC","#E9C46A","#2A9D8F"]

QUADRANT_COLORS = {
    "Quick wins":   "#00B050",
    "Plan & save":  "#0F3460",
    "Maybe later":  "#A8A8A8",
    "Reconsider":   "#E94560",
}

def safe_error(msg: str):
    st.error(msg, icon=":material/error:")

def safe_warning(msg: str):
    st.warning(msg, icon=":material/warning:")

def help_expander(title: str, content: str):
    with st.expander(title, icon=":material/help:"):
        st.markdown(content)

def inject_mobile_css():
    st.markdown("""
    <style>
    .kpi { background: var(--secondary-background-color); border-radius: 14px; padding: 18px 12px; text-align: center; border: 1px solid rgba(128,128,128,0.15); margin-bottom: 6px; transition: box-shadow 0.2s ease, transform 0.15s ease; }
    .kpi:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.10); transform: translateY(-2px); }
    .kpi-val { font-size: 22px; font-weight: 700; margin: 6px 0; }
    .kpi-sub { font-size: 11px; color: #888; margin-top: 3px; }
    .kpi-lbl { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: .7px; }
    .pos { color: #00B050; } .neg { color: #E94560; } .neu { color: #0F3460; }
    .pw { background: #e0e0e0; border-radius: 8px; height: 14px; overflow: hidden; margin: 5px 0; }
    .pb { height: 100%; border-radius: 8px; transition: width 0.4s ease; }
    .badge { display: inline-block; background: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.2); border-radius: 20px; padding: 4px 10px; font-size: 12px; margin: 2px; }
    @media (max-width: 768px) {
        .stButton > button { width: 100%; font-size: 16px; padding: 12px; border-radius: 10px; }
        div[data-testid="column"]:has([data-testid="stMetric"]) { min-width: 100% !important; }
        .stDataFrame { font-size: 13px; }
        h1 { font-size: 1.6rem !important; } h2 { font-size: 1.3rem !important; }
    }
    div[data-testid="stForm"] { border: 1px solid rgba(128,128,128,0.15); border-radius: 12px; padding: 16px; }
    section[data-testid="stSidebar"] { min-width: 240px; }
    </style>
    """, unsafe_allow_html=True)
