"""
Regression tests for the edit-dialog widget-key collision bug.

Bug: the four page-level @st.dialog edit forms (big_purchases.py edit_purchase_dialog,
savings.py edit_account_dialog / edit_savings_dialog, log_income.py edit_income_dialog)
used FIXED widget keys (key="bp_edit_name" etc.) while their Save/Cancel buttons were
already row-scoped (key=f"bp_edit_save_{row['id']}"). Because Streamlit ignores a
widget's value= default once the key already exists in session_state, editing row A
then opening row B prefilled B's widgets with A's values and Save persisted A's data
into B's record.

Fix: append _{row['id']} to every edit-dialog widget key (matching the existing
Save/Cancel button-key convention), so each row gets an isolated key.

These tests provide three layers of defense:

1. test_fix_edit_dialog_keys_are_row_scoped_in_source (parametrized source-level guard)
   Asserts that NONE of the 27 vulnerable base keys appears as a bare key="<base>"
   literal in any of the four production page files -- all must be
   key=f"<base>_{row['id']}".

2. test_edit_dialog_row_isolation_via_apptest
   A dynamic AppTest simulation (mirroring the validator's own
   qa/_tmp_audit/simulate_stale_keys.py mini-app, but using the FIXED key pattern)
   that proves: pre-setting session_state to a stale string, then opening the dialog
   for a DIFFERENT row, renders the widget with the NEW row's default -- not the stale
   string.

3. test_edit_dialog_stale_keys_leak_as_negative_control
   Negative control: the ORIGINAL buggy fixed-key pattern leaks row A's stale value
   into row B, proving the simulation can detect the bug (so test #2 is meaningful).
"""
import os
import textwrap
import tempfile

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app_pages")


def _write_script(script: str) -> str:
    """Write a simulation script to a writable workspace-backed temp file and
    return its path. AppTest.from_file is used instead of from_string because
    from_string writes to the system TEMP dir, which is outside the sandbox;
    the workspace-backed tempdir (set up by tests/conftest.py) is writable."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="edit_dialog_sim_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(script))
    return path


# The 27 base widget keys that were vulnerable, grouped by site, as
# (relative_file, [base keys]).
VULNERABLE_SITES = [
    ("big_purchases.py", [
        "bp_edit_name", "bp_edit_cat", "bp_edit_cur", "bp_edit_price",
        "bp_edit_use", "bp_edit_imp", "bp_edit_notes",
    ]),
    ("savings.py", [
        "dlg_acc_name", "dlg_acc_cur", "dlg_acc_amt", "dlg_acc_rate",
        "dlg_acc_start", "dlg_acc_mat", "dlg_acc_goal",
    ]),
    ("savings.py", [
        "sav_edit_date", "sav_edit_cur", "sav_edit_dep", "sav_edit_tgt",
        "sav_edit_ir", "sav_edit_notes",
    ]),
    ("log_income.py", [
        "inc_edit_date", "inc_edit_source", "inc_edit_type", "inc_edit_cur",
        "inc_edit_actual", "inc_edit_budgeted", "inc_edit_notes",
    ]),
]


def _read_page(name):
    with open(os.path.join(APP_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("site_file,base_keys", VULNERABLE_SITES,
                         ids=["big_purchases", "savings_account", "savings_goal", "log_income"])
def test_fix_edit_dialog_keys_are_row_scoped_in_source(site_file, base_keys):
    """No vulnerable base key may appear as a bare key="<base>" literal; each must
    be key=f"<base>_{row['id']}"."""
    src = _read_page(site_file)
    for base in base_keys:
        # bare key="<base>"  -> the vulnerable form that must NOT exist anymore
        bare = 'key="' + base + '"'
        assert bare not in src, (
            site_file + ": bare widget key literal " + repr(bare) +
            " still present -- must be row-scoped with _{row['id']}."
        )
        # key=f"<base>_{row['id']}"  -> the fixed form that MUST exist
        # Built from single-quoted pieces to avoid escaping both quote types.
        scoped = 'key=f"' + base + "_{row['id']}" + '"'
        assert scoped in src, (
            site_file + ": row-scoped key for " + base + " not found."
        )


# -- Dynamic AppTest simulation of the fixed dialog pattern ---------------------
# Mirrors the validator's qa/_tmp_audit/simulate_stale_keys.py mini-app, but uses
# row-scoped keys to prove the isolation fix.


FIXED_DIALOG_SCRIPT = '''
import streamlit as st

@st.dialog("Edit item")
def edit_dialog(row):
    # FIX: each widget key is row-scoped, matching the Save/Cancel convention.
    e_name  = st.text_input("Name", value=str(row["name"]),  key=f"bp_edit_name_{row['id']}")
    e_price = st.number_input("Price", min_value=0.01, value=float(row["price"]),
                              step=10.0, key=f"bp_edit_price_{row['id']}")
    if st.button("Save",  key=f"bp_edit_save_{row['id']}"):
        st.session_state._saved = (e_name, e_price, row["id"])
        st.rerun()
    if st.button("Cancel", key=f"bp_edit_cancel_{row['id']}"):
        st.rerun()

row_a = {"id": 1, "name": "Laptop", "price": 1000.0}
row_b = {"id": 2, "name": "Phone", "price": 500.0}

if st.button("Edit row A", key="edit_a"):
    edit_dialog(row_a)
if st.button("Edit row B", key="edit_b"):
    edit_dialog(row_b)
'''


# Negative control: the OLD buggy pattern with fixed keys -- should leak.
STALE_DIALOG_SCRIPT = '''
import streamlit as st

@st.dialog("Edit item")
def edit_dialog(row):
    # BUG (original): fixed widget keys without row id.
    e_name  = st.text_input("Name",  value=str(row["name"]),  key="bp_edit_name")
    e_price = st.number_input("Price", min_value=0.01, value=float(row["price"]),
                              step=10.0, key="bp_edit_price")
    if st.button("Save",  key=f"bp_edit_save_{row['id']}"):
        st.session_state._saved = (e_name, e_price, row["id"])
        st.rerun()
    if st.button("Cancel", key=f"bp_edit_cancel_{row['id']}"):
        st.rerun()

row_a = {"id": 1, "name": "Laptop", "price": 1000.0}
row_b = {"id": 2, "name": "Phone", "price": 500.0}

if st.button("Edit row A", key="edit_a"):
    edit_dialog(row_a)
if st.button("Edit row B", key="edit_b"):
    edit_dialog(row_b)
'''


def test_edit_dialog_row_isolation_via_apptest():
    """Fixed (row-scoped) keys: pre-setting a stale session_state value for row A
    must NOT leak into row B's dialog -- the widget must render row B's default."""
    script_path = _write_script(FIXED_DIALOG_SCRIPT)
    try:
        at = AppTest.from_file(script_path, default_timeout=30)
        at.run()
        assert not at.exception

        # Simulate that row A was edited earlier, leaving a stale value in
        # session_state under row A's key.
        at.session_state["bp_edit_name_1"] = "STALE_FROM_ROW_A"
        at.session_state["bp_edit_price_1"] = 9999.0

        # Open the dialog for row B (a different row -> different keys).
        at.button(key="edit_b").click().run()
        assert not at.exception

        # The row-scoped keys for row B must render row B's defaults, NOT the
        # stale A values.
        name_b = at.text_input(key="bp_edit_name_2")
        price_b = at.number_input(key="bp_edit_price_2")
        assert name_b.value == "Phone", (
            "row B name widget showed " + repr(name_b.value) +
            ", expected 'Phone' -- stale key leakage detected."
        )
        assert price_b.value == 500.0, (
            "row B price widget showed " + repr(price_b.value) +
            ", expected 500.0 -- stale key leakage detected."
        )

        # The stale A values must remain untouched (the fix isolates keys
        # but must not erase pre-existing row A state). SafeSessionState supports
        # __contains__ but not .get(), so use membership + indexing.
        assert "bp_edit_name_1" in at.session_state
        assert at.session_state["bp_edit_name_1"] == "STALE_FROM_ROW_A"
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def test_edit_dialog_stale_keys_leak_as_negative_control():
    """Negative control: the ORIGINAL fixed-key pattern DOES leak row A's stale
    value into row B. This proves the simulation can detect the bug, so the
    isolation test above is meaningful (not a no-op)."""
    script_path = _write_script(STALE_DIALOG_SCRIPT)
    try:
        at = AppTest.from_file(script_path, default_timeout=30)
        at.run()
        assert not at.exception

        at.session_state["bp_edit_name"] = "STALE_FROM_ROW_A"

        at.button(key="edit_b").click().run()
        assert not at.exception

        name_b = at.text_input(key="bp_edit_name")
        # With the buggy fixed key, row B's widget reads the stale session_state value.
        assert name_b.value == "STALE_FROM_ROW_A", (
            "expected the buggy pattern to leak the stale value into row B"
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
