"""
Tests for the login user-enumeration timing guard in auth.login_user.

The user-miss path must run an equivalent-cost bcrypt verification (against a
lazily-initialised dummy hash) so a probe for a non-existent user takes ~the
same time as a wrong-password attempt, closing the timing oracle.
"""

import pytest

import auth


@pytest.fixture(autouse=True)
def _reset_login_state():
    """Clear throttle buckets and the cached dummy hash between tests."""
    auth._attempts.clear()
    auth._dummy_password_hash = None
    yield
    auth._attempts.clear()
    auth._dummy_password_hash = None


@pytest.fixture
def _wrap_verify_password(monkeypatch):
    """Wrap auth.verify_password so calls are counted (real impl still runs)."""
    real = auth.verify_password
    calls = {"count": 0}

    def _counting(plain, hashed):
        calls["count"] += 1
        return real(plain, hashed)

    monkeypatch.setattr(auth, "verify_password", _counting)
    return calls


@pytest.fixture
def existing_user(monkeypatch):
    """A known existing user row, with the DB lookup stubbed out."""
    from auth import hash_password
    user = {"id": 1, "username": "real_user", "password_hash": hash_password("Correct123")}
    monkeypatch.setattr(auth, "get_user_by_username", lambda name: user if name == "real_user" else None)
    return user


def test_nonexistent_user_runs_bcrypt_once(_wrap_verify_password, existing_user):
    ok, user, msg = auth.login_user("no_such_user", "anything123")
    assert ok is False
    assert user is None
    # The generic message is identical to the wrong-password path.
    assert msg == "Incorrect username or password."
    # Exactly one bcrypt verification (the dummy) ran on the user-miss path.
    assert _wrap_verify_password["count"] == 1


def test_wrong_password_runs_bcrypt_once(_wrap_verify_password, existing_user):
    ok, user, msg = auth.login_user("real_user", "WrongPassword123")
    assert ok is False
    assert user is None
    assert msg == "Incorrect username or password."
    # One real verification against the stored hash.
    assert _wrap_verify_password["count"] == 1


def test_dummy_hash_is_lazily_initialised(_wrap_verify_password, existing_user):
    """The first miss computes the dummy hash; a second, distinct miss reuses it."""
    # Fresh import state → no dummy hash yet.
    assert auth._dummy_password_hash is None

    auth.login_user("ghost_one", "whatever123")
    assert auth._dummy_password_hash is not None
    h1 = auth._dummy_password_hash

    # A second, different non-existent user reuses the same cached hash.
    auth.login_user("ghost_two", "whatever123")
    assert auth._dummy_password_hash is h1
