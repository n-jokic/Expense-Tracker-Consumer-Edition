"""
Regression tests for the shared cache revision (db.bump_data_revision /
queries.db_version): a write in one browser session must invalidate cached
readers in every other session of the same user — and every household
member's session — immediately, without waiting for cache TTLs.
"""

import pytest

import queries as q
from db import (
    init_db, create_user, delete_user_account, create_household,
    join_household, bump_data_revision, get_data_revision,
    username_exists, get_user_by_username,
)
from auth import hash_password

U1 = "revision_test_user1"
U2 = "revision_test_user2"


class _SessionState(dict):
    """dict with attribute access — mimics st.session_state's interface."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value


def _ss(**values):
    return _SessionState(values)


@pytest.fixture()
def two_users():
    init_db()
    ids = []
    for name, email in ((U1, "rev1@example.com"), (U2, "rev2@example.com")):
        if username_exists(name):
            delete_user_account(get_user_by_username(name)["id"])
        ids.append(create_user(name, email, hash_password("test1234"), name))
    yield ids
    for uid in ids:
        delete_user_account(uid)


def test_revision_persists_and_increments(two_users):
    uid, _ = two_users
    r0 = get_data_revision(uid)
    assert bump_data_revision(uid, include_household=False) == r0 + 1
    assert get_data_revision(uid) == r0 + 1


def test_second_session_sees_bump_immediately(two_users, monkeypatch):
    """Session A bumps; session B (fresh st.session_state) reads the new
    revision from the DB instead of its own stale counter."""
    uid, _ = two_users
    r0 = get_data_revision(uid)

    monkeypatch.setattr(q.st, "session_state",
                        _ss(user_id=uid, db_version=0))
    assert q.db_version() == r0
    q.bump_db_version()

    # A brand-new session for the same user...
    monkeypatch.setattr(q.st, "session_state",
                        _ss(user_id=uid, db_version=0))
    # ...sees the write immediately (no TTL wait).
    assert q.db_version() == r0 + 1


def test_household_member_bump_invalidates_all(two_users):
    uid_a, uid_b = two_users
    hh_id, code = create_household(uid_a, "Rev Test Home")
    assert join_household(uid_b, code)

    r_a, r_b = get_data_revision(uid_a), get_data_revision(uid_b)
    # A write by member A bumps member B's revision too.
    bump_data_revision(uid_a, include_household=True)
    assert get_data_revision(uid_a) == r_a + 1
    assert get_data_revision(uid_b) == r_b + 1
