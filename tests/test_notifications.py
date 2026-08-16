"""
Regression tests for notifications.py: marker persistence must merge fresh
DB state (never a stale snapshot), markers must be persisted only after
confirmed delivery, the weekly summary must fire every Monday, and the email
must use the user's currency (not a hardcoded EUR/117.0 rate).
"""

import ssl
from datetime import date, timedelta

import pytest

import notifications
from db import (
    init_db, create_user, delete_user_account, get_settings,
    username_exists, get_user_by_username,
)
from auth import hash_password

TEST_USERNAME = "notif_test_user"
TEST_EMAIL    = "notif_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Notif Tester")
    yield uid
    delete_user_account(uid)


def _markers(user_id: int) -> dict:
    return (get_settings(user_id) or {}).get("sent_markers") or {}


def test_persist_marker_reads_fresh_db_state(test_user):
    """A stale in-memory snapshot must not clobber markers persisted by an
    earlier checker in the same page load."""
    month = "2025_6"
    notifications._persist_marker(test_user, "bill", month, "row-1")
    # The caller's snapshot is stale — it predates row-1 — but the function
    # must read fresh state and merge rather than overwrite.
    notifications._persist_marker(test_user, "budget", month, "Food")
    m = _markers(test_user)
    assert set(m[f"bill_{month}"]) == {"row-1"}
    assert set(m[f"budget_{month}"]) == {"Food"}

    # Same kind twice keeps both items.
    notifications._persist_marker(test_user, "bill", month, "row-2")
    assert set(_markers(test_user)[f"bill_{month}"]) == {"row-1", "row-2"}


def test_marker_on_delivery_only_persists_on_success(test_user):
    cb = notifications._marker_on_delivery(test_user, "loan", "2025_6", "loan-7")
    cb(False, "SMTP timeout")
    assert _markers(test_user) == {}
    cb(True, "OK")
    assert set(_markers(test_user)["loan_2025_6"]) == {"loan-7"}


class _PinnedDate(date):
    _today = date(2025, 6, 2)  # a Monday

    @classmethod
    def today(cls):
        return cls._today


def _weekly_settings(**overrides):
    s = {
        "weekly_summary": True,
        "email_alerts": True,
        "alert_email": "me@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_user": "me@example.com",
        "smtp_port": 587,
        "default_currency": "EUR",
    }
    s.update(overrides)
    return s


def test_weekly_summary_sends_every_monday(monkeypatch, test_user):
    """Regression: last week's send must NOT suppress this week's send."""
    monkeypatch.setattr(notifications, "date", _PinnedDate)
    monkeypatch.setattr(notifications.st, "session_state",
                        {"display_name": "Tester"})
    calls = []

    def fake_send(*args, **kwargs):
        calls.append(args)
        on_done = kwargs.get("on_done")
        if on_done:
            on_done(True, "OK")

    monkeypatch.setattr(notifications, "send_email_async", fake_send)

    import pandas as pd
    empty = pd.DataFrame()

    # Sent LAST Monday (7 days ago) -> this Monday must still send.
    notifications.check_and_send_weekly_summary(
        test_user, empty,
        _weekly_settings(weekly_summary_last_sent="2025-05-26"))
    assert len(calls) == 1

    # Sent THIS Monday already -> skip.
    notifications.check_and_send_weekly_summary(
        test_user, empty,
        _weekly_settings(weekly_summary_last_sent="2025-06-02"))
    assert len(calls) == 1

    # And the "last sent" marker lands in the DB only after delivery.
    assert (get_settings(test_user) or {}).get("weekly_summary_last_sent") == date(2025, 6, 2)


def test_weekly_summary_uses_default_currency(monkeypatch, test_user):
    """Regression: the checker read the non-existent `display_currency` key,
    so every weekly email fell back to EUR. With default_currency=RSD the
    email must be formatted in dinars."""
    monkeypatch.setattr(notifications, "date", _PinnedDate)
    monkeypatch.setattr(notifications.st, "session_state",
                        {"display_name": "Tester"})
    captured = {}

    def fake_send(*args, **kwargs):
        captured["args"] = args
        kwargs["on_done"](True, "OK")

    monkeypatch.setattr(notifications, "send_email_async", fake_send)
    import pandas as pd
    week = pd.DataFrame({"date": [pd.Timestamp(2025, 6, 1)],
                         "category": ["Food & Dining"], "amount_eur": [100.0]})
    notifications.check_and_send_weekly_summary(
        test_user, week,
        _weekly_settings(default_currency="RSD",
                         currency_rates={"RSD": 117.0, "EUR": 1.0}))
    html = captured["args"][-1]
    assert "11,700 din" in html
    assert "€100.00" not in html


def test_weekly_summary_failed_send_not_marked(monkeypatch, test_user):
    monkeypatch.setattr(notifications, "date", _PinnedDate)
    monkeypatch.setattr(notifications.st, "session_state",
                        {"display_name": "Tester"})

    def fake_send(*args, **kwargs):
        kwargs["on_done"](False, "boom")

    monkeypatch.setattr(notifications, "send_email_async", fake_send)
    import pandas as pd
    notifications.check_and_send_weekly_summary(
        test_user, pd.DataFrame(), _weekly_settings())
    assert (get_settings(test_user) or {}).get("weekly_summary_last_sent") is None


def test_weekly_email_uses_user_currency():
    import pandas as pd
    rows = pd.DataFrame({
        "category": ["Food & Dining"],
        "amount_eur": [100.0],
    })
    html = notifications.build_weekly_summary_email(
        "Tester", rows, {"RSD": 117.0, "EUR": 1.0}, "RSD")
    assert "11,700 din" in html
    assert "€100.00" not in html


def test_send_email_verifies_tls_certificates(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            captured["args"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            captured["context"] = context

        def login(self, *a):
            pass

        def sendmail(self, *a):
            pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, err = notifications.send_email(
        "smtp.example.com", 587, "u", "p", "to@example.com", "S", "<b>hi</b>")
    assert ok is True and err == "OK"
    ctx = captured["context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
