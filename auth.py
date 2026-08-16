"""
auth.py — Authentication module for Expense Tracker v3.
Handles registration, login, session management via bcrypt + SQLite.
"""

import os
import re
import logging
from collections import defaultdict, deque
from datetime import datetime

import streamlit as st
import bcrypt

from db import (
    create_user, get_user_by_username, username_exists, email_exists,
    update_user_password, init_db
)

logger = logging.getLogger(__name__)


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        # Log for the operator; never echo the raw error to the UI.
        logger.warning("Password verification failed: %s", e)
        return False


# ── Login throttling (in-memory; per client + username) ──────────────────────

_attempts = defaultdict(deque)
MAX_ATTEMPTS   = 5
WINDOW_SECONDS = 60


def _client_key() -> str:
    try:
        fwd = st.context.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
    except Exception:
        pass
    return "local"


def _throttled(key: str) -> bool:
    now = datetime.now()
    dq = _attempts[key]
    while dq and (now - dq[0]).total_seconds() > WINDOW_SECONDS:
        dq.popleft()
    if len(dq) >= MAX_ATTEMPTS:
        return True
    dq.append(now)
    return False


def _registration_enabled() -> bool:
    """Open registration for family LAN use; disable it when hosting publicly.

    Defaults to ENABLED when nothing is configured; set ALLOW_REGISTRATION=false
    (env var or st.secrets) to close sign-ups.
    """
    val = os.environ.get("ALLOW_REGISTRATION")
    if val is None:
        try:
            val = st.secrets.get("ALLOW_REGISTRATION")
        except Exception:
            val = None
    if val is None:
        return True
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# ── Validation helpers ────────────────────────────────────────────────────────

def _valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _valid_password(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if len(password.encode("utf-8")) > 72:
        return False, "Password must be at most 72 bytes (bcrypt limit)."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    return True, ""


# ── Core auth functions ───────────────────────────────────────────────────────

def register_user(username: str, email: str, password: str, display_name: str) -> tuple[bool, str]:
    username = username.strip().lower()          # normalise to lowercase
    email    = email.strip().lower()
    password = password.strip()                  # remove accidental leading/trailing whitespace

    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if not all(c.isalnum() or c == "_" for c in username):
        return False, "Username can only contain letters, numbers, and underscores."
    if not _valid_email(email):
        return False, "Please enter a valid email address."

    ok, msg = _valid_password(password)
    if not ok:
        return False, msg

    if username_exists(username):
        return False, "That username is already taken. Please choose another."
    if email_exists(email):
        return False, "An account with that email already exists."

    pw_hash = hash_password(password)
    create_user(username, email, pw_hash, display_name or username)
    return True, "Account created successfully! You can now log in."


def login_user(username: str, password: str) -> tuple[bool, dict | None, str]:
    username = username.strip().lower()          # normalise — matches registration
    password = password.strip()                  # remove accidental whitespace

    throttle_key = f"{_client_key()}|{username}"
    if _throttled(throttle_key):
        return False, None, "Too many attempts. Please wait a minute and try again."

    user = get_user_by_username(username)

    if not user:
        return False, None, "Incorrect username or password."
    if not verify_password(password, user["password_hash"]):
        return False, None, "Incorrect username or password."

    # successful logins don't count towards the lockout budget
    _attempts.pop(throttle_key, None)
    return True, user, "Welcome back!"


def change_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str]:
    from db import get_session, User
    old_password = old_password.strip()
    new_password = new_password.strip()
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        if not u:
            return False, "User not found."
        if not verify_password(old_password, u.password_hash):
            return False, "Current password is incorrect."

    ok, msg = _valid_password(new_password)
    if not ok:
        return False, msg

    update_user_password(user_id, hash_password(new_password))
    return True, "Password changed successfully."


def logout():
    for key in ["authenticated", "user_id", "username", "display_name",
                "household_id", "onboarding_complete", "onboarding_step",
                "settings", "db_version", "dc", "rates"]:
        st.session_state.pop(key, None)
    st.cache_data.clear()


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_login_page():
    st.markdown("""
    <div style="max-width:420px;margin:60px auto 0 auto;text-align:center;">
        <h1 style="font-size:2.4rem;margin-bottom:4px;">💰 Expense Tracker</h1>
        <p style="color:#888;margin-bottom:32px;">Track smarter. Save better.</p>
    </div>
    """, unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        if _registration_enabled():
            tab_login, tab_register = st.tabs(["🔑 Login", "✨ Create Account"])
        else:
            tab_login = st.tabs(["🔑 Login"])[0]

        # ── Login tab ─────────────────────────────────────────────────────────
        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", placeholder="your_username")
                password = st.text_input("Password", type="password", placeholder="••••••••")
                submitted = st.form_submit_button("Login →", width="stretch", type="primary")

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    ok, user, msg = login_user(username, password)
                    if ok:
                        st.session_state.authenticated       = True
                        st.session_state.user_id             = user["id"]
                        st.session_state.username            = user["username"]
                        st.session_state.display_name        = user["display_name"]
                        st.session_state.household_id        = user["household_id"]
                        st.session_state.onboarding_complete = user["onboarding_complete"]
                        st.session_state.onboarding_step     = 0
                        st.rerun()
                    else:
                        st.error(f"🔒 {msg}")

        # ── Register tab ──────────────────────────────────────────────────────
        if _registration_enabled():
            with tab_register:
                with st.form("register_form"):
                    r_display = st.text_input("Your name", placeholder="e.g. Sandra")
                    r_username = st.text_input("Username", placeholder="min. 3 characters")
                    r_email    = st.text_input("Email address", placeholder="you@example.com")
                    r_pass     = st.text_input("Password", type="password",
                                               placeholder="min. 8 chars, include a number")
                    r_confirm  = st.text_input("Confirm password", type="password", placeholder="••••••••")
                    submitted_r = st.form_submit_button("Create Account →",
                                                        width="stretch", type="primary")

                if submitted_r:
                    if r_pass != r_confirm:
                        st.error("Passwords don't match. Please try again.")
                    else:
                        ok, msg = register_user(r_username, r_email, r_pass, r_display)
                        if ok:
                            st.success(f"✅ {msg} Please log in.")
                        else:
                            st.error(f"⚠️ {msg}")

        st.markdown("<br><p style='text-align:center;color:#aaa;font-size:12px;'>"
                    "Your data is stored on the computer running this app.</p>", unsafe_allow_html=True)


def require_auth() -> bool:
    """Returns True if user is authenticated, else renders login page."""
    init_db()
    if st.session_state.get("authenticated"):
        return True
    render_login_page()
    return False
