"""
queries.py — Cached DB read helpers for the Streamlit UI.

Cache keys include (user_id, db_version) so mutations become visible
immediately: every write path calls bump_db_version() after committing.
"""

import streamlit as st

from db import (
    get_expenses, get_income, get_savings, get_budgets, get_recurring,
    get_audit_log, get_settings as _db_get_settings,
    get_household_expenses, get_household_members, save_settings as _db_save_settings,
    get_household_by_member as _db_household_by_member,
    get_big_purchases, get_loans, get_loan_payments,
    get_holdings, get_holding_prices, get_savings_accounts,
    get_data_revision as _db_get_revision,
    bump_data_revision as _db_bump_revision,
)


def _run_id() -> int | None:
    """Per-rerun identifier (P2 version-tear fix — T2-003).

    Streamlit reuses one ScriptRunContext per browser session but resets
    its per-run state each rerun (cursors dict is replaced, ThreadState is
    re-initialized). Its identity therefore changes each rerun and can be
    used as a cheap snapshot key so every q.* helper within ONE script
    execution sees the SAME db_version, while the NEXT rerun (even in the
    same session) fetches a fresh revision from the DB.
    Outside a Streamlit run (tests, scripts) returns None -> no snapshot.
    """
    try:
        from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
        ctx = get_script_run_ctx(suppress_warning=True)
        if ctx is not None:
            # ctx.cursors is replaced with a new {} each ScriptRunContext.reset()
            return id(ctx.cursors)
    except Exception:
        pass
    return None


def db_version() -> int:
    """The shared data revision from the DB (per-rerun snapshot).

    Every browser session, household member, and background job reads the
    SAME value, so a write in one session invalidates cached readers
    everywhere immediately — no waiting for per-session counters or TTLs.
    Falls back to a session-local counter before login.

    Snapshot: within a single Streamlit script execution all callers share
    one DB read (fixes N+1 tear across ~15 helpers in app.py and pages).
    The NEXT rerun re-reads the DB so cross-session bumps are visible
    without waiting for TTLs (G2, P2).
    """
    uid = st.session_state.get("user_id")
    if uid is None:
        return int(st.session_state.get("db_version", 0))
    uid = int(uid)
    run_id = _run_id()
    if run_id is not None:
        snap_key = "_snap_version"
        snap_run_key = "_snap_run_id"
        snap_user_key = "_snap_user_id"
        if (st.session_state.get(snap_run_key) == run_id
                and st.session_state.get(snap_user_key) == uid
                and snap_key in st.session_state):
            return int(st.session_state[snap_key])
        rev = _db_get_revision(uid)
        st.session_state[snap_run_key] = run_id
        st.session_state[snap_user_key] = uid
        st.session_state[snap_key] = int(rev)
        return int(rev)
    return _db_get_revision(uid)


def bump_db_version() -> int:
    """Invalidate all cached reads after any DB mutation (shared revision)."""
    uid = st.session_state.get("user_id")
    if uid is None:
        st.session_state.db_version = int(st.session_state.get("db_version", 0)) + 1
        return st.session_state.db_version
    rev = _db_bump_revision(int(uid))
    st.session_state.db_version = rev
    # Keep the per-rerun snapshot coherent within this same rerun: future
    # q.* calls after the bump in the same script execution must see the
    # NEW revision, not the stale snapshot taken before the write.
    try:
        run_id = _run_id()
        if run_id is not None:
            st.session_state["_snap_run_id"] = run_id
            st.session_state["_snap_user_id"] = int(uid)
            st.session_state["_snap_version"] = int(rev)
    except Exception:
        pass
    return rev


# ── Cached readers ────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _household_of(uid: int, version: int):
    return _db_household_by_member(uid)


def current_household_id(user_id: int) -> int | None:
    """Authoritative household membership for the CURRENT device (#21a).

    st.session_state.household_id goes stale when membership changes on
    another device (leave/join/kick bumps revisions there, not here). This
    read is cached on the shared revision, so any membership bump anywhere
    invalidates it on the next rerun — the dashboard re-derives instead of
    trusting session state."""
    info = _household_of(int(user_id), db_version())
    if info and info.get("id"):
        return int(info["id"])
    return None


@st.cache_data(ttl=300, show_spinner=False)
def _expenses(user_id: int, version: int, include_deleted: bool):
    return get_expenses(user_id, include_deleted=include_deleted)


@st.cache_data(ttl=300, show_spinner=False)
def _income(user_id: int, version: int, include_deleted: bool):
    return get_income(user_id, include_deleted=include_deleted)


@st.cache_data(ttl=300, show_spinner=False)
def _savings(user_id: int, version: int, include_deleted: bool):
    return get_savings(user_id, include_deleted=include_deleted)


@st.cache_data(ttl=300, show_spinner=False)
def _savings_accounts(user_id: int, version: int, include_deleted: bool):
    return get_savings_accounts(user_id, include_deleted=include_deleted)


@st.cache_data(ttl=300, show_spinner=False)
def _budgets(user_id: int, version: int):
    return get_budgets(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _recurring(user_id: int, version: int):
    return get_recurring(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _big_purchases(user_id: int, version: int):
    return get_big_purchases(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _loans(user_id: int, version: int):
    return get_loans(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _loan_payments(user_id: int, loan_id: str, version: int):
    return get_loan_payments(user_id, loan_id)


@st.cache_data(ttl=120, show_spinner=False)
def _holdings(user_id: int, version: int):
    return get_holdings(user_id)


@st.cache_data(ttl=120, show_spinner=False)
def _holding_prices(user_id: int, version: int):
    return get_holding_prices(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _audit(user_id: int, version: int, limit: int):
    return get_audit_log(user_id, limit=limit)


@st.cache_data(ttl=300, show_spinner=False)
def _household_expenses(household_id: int, version: int):
    return get_household_expenses(household_id)


@st.cache_data(ttl=300, show_spinner=False)
def _household_members(household_id: int, version: int):
    return get_household_members(household_id)


# ── Public helpers ────────────────────────────────────────────────────────────

def expenses(user_id: int, include_deleted: bool = False):
    return _expenses(user_id, db_version(), include_deleted)


def income(user_id: int, include_deleted: bool = False):
    return _income(user_id, db_version(), include_deleted)


def savings(user_id: int, include_deleted: bool = False):
    return _savings(user_id, db_version(), include_deleted)


def savings_accounts(user_id: int, include_deleted: bool = False):
    return _savings_accounts(user_id, db_version(), include_deleted)


def budgets(user_id: int):
    return _budgets(user_id, db_version())


def recurring(user_id: int):
    return _recurring(user_id, db_version())


def big_purchases(user_id: int):
    return _big_purchases(user_id, db_version())


def loans(user_id: int):
    return _loans(user_id, db_version())


def loan_payments(user_id: int, loan_id: str):
    return _loan_payments(user_id, loan_id, db_version())


def holdings(user_id: int):
    return _holdings(user_id, db_version())


def holding_prices(user_id: int):
    return _holding_prices(user_id, db_version())


def audit(user_id: int, limit: int = 200):
    return _audit(user_id, db_version(), limit)


def household_expenses(household_id: int):
    return _household_expenses(household_id, db_version())


def household_members(household_id: int):
    return _household_members(household_id, db_version())


def get_settings(user_id: int):
    """Settings are one small row — always read fresh (no caching)."""
    return _db_get_settings(user_id)


def save_settings(user_id: int, updates: dict):
    """Save settings, refresh the session snapshot, and bump the cache version."""
    _db_save_settings(user_id, updates)
    st.session_state.settings = _db_get_settings(user_id)
    bump_db_version()
    return st.session_state.settings
