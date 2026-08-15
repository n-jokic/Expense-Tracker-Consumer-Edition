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
)


def db_version() -> int:
    return int(st.session_state.get("db_version", 0))


def bump_db_version() -> int:
    """Invalidate all cached reads. Call after any DB mutation."""
    st.session_state.db_version = db_version() + 1
    return st.session_state.db_version


# ── Cached readers ────────────────────────────────────────────────────────────

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
def _budgets(user_id: int, version: int):
    return get_budgets(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _recurring(user_id: int, version: int):
    return get_recurring(user_id)


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


def budgets(user_id: int):
    return _budgets(user_id, db_version())


def recurring(user_id: int):
    return _recurring(user_id, db_version())


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
