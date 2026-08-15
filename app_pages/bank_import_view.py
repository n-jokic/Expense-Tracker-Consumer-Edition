"""
Bank import page: delegates to bank_import.render_bank_import_page.
(Named bank_import_view.py so it doesn't shadow the root bank_import.py module.)
"""

import streamlit as st

from bank_import import render_bank_import_page

user_id = st.session_state.user_id

render_bank_import_page(user_id, st.session_state.rates)
