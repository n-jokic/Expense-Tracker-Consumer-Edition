"""
C2 (item 5): salary-raise history. Raises belong to the fixed salary and
increase it from their effective date onward; every raise leaves one
SalaryRaise row (single transaction with the settings bump + audit).
"""

from datetime import date, timedelta

import pytest

import db
from auth import hash_password

U = "c2_salary_raise_user"
E = "c2_salary_raise@example.com"
D1 = date(2025, 5, 1)


@pytest.fixture()
def user():
    db.init_db()
    if db.username_exists(U):
        db.delete_user_account(db.get_user_by_username(U)["id"])
    uid = db.create_user(U, E, hash_password("test1234"), "C2 Tester")
    yield uid
    db.delete_user_account(uid)


def test_record_raise_writes_history_and_bumps_settings(user):
    uid = user
    db.save_settings(uid, {"salary_amount": 2000.0, "salary_currency": "EUR",
                           "salary_active": True})
    db.record_salary_raise(
        uid, amount=2400.0, currency="EUR", amount_eur=2400.0,
        effective_date=D1, note="annual review")
    hist = db.get_salary_raises(uid)
    assert len(hist) == 1
    assert hist[0]["amount"] == 2400.0 and hist[0]["currency"] == "EUR"
    assert hist[0]["effective_date"] == D1
    assert hist[0]["note"] == "annual review"
    s = db.get_settings(uid)
    assert s["salary_amount"] == 2400.0
    assert s["salary_active"] is True


def test_history_orders_newest_first(user):
    uid = user
    db.save_settings(uid, {"salary_amount": 1000.0})
    db.record_salary_raise(uid, amount=1100.0, currency="EUR",
                           amount_eur=1100.0, effective_date=date(2024, 1, 1))
    db.record_salary_raise(uid, amount=1300.0, currency="EUR",
                           amount_eur=1300.0, effective_date=date(2025, 1, 1))
    db.record_salary_raise(uid, amount=1200.0, currency="EUR",
                           amount_eur=1200.0, effective_date=date(2024, 6, 1))
    hist = db.get_salary_raises(uid)
    amounts = [h["amount"] for h in hist]
    assert amounts == [1300.0, 1200.0, 1100.0]


def test_currency_conversion_stored(user):
    """The db layer stores amount_eur verbatim — callers own conversion."""
    uid = user
    db.save_settings(uid, {"salary_amount": 900.0})
    db.record_salary_raise(uid, amount=1000.0, currency="USD",
                           amount_eur=900.0, effective_date=D1, note="USD raise")
    hist = db.get_salary_raises(uid)
    assert abs(hist[0]["amount_eur"] - 900.0) < 1e-6
    # Settings store the raise in its OWN currency.
    assert db.get_settings(uid)["salary_amount"] == 1000.0
    assert db.get_settings(uid)["salary_currency"] == "USD"


def test_delete_user_cascades_raises(user):
    uid = user
    db.save_settings(uid, {"salary_amount": 500.0})
    db.record_salary_raise(uid, amount=600.0, currency="EUR",
                           amount_eur=600.0, effective_date=D1)
    assert len(db.get_salary_raises(uid)) == 1
    db.delete_user_account(uid)
    # Recreate a shell user with the same id is impossible; instead verify
    # the raw table no longer has rows for that id.
    from db import get_session, SalaryRaise
    with get_session() as s:
        assert s.query(SalaryRaise).filter(SalaryRaise.user_id == uid).count() == 0
