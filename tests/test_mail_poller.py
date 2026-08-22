"""#26 E6 — local IMAP poller: config gating, crypto roundtrip,
candidate parsing of fetched-email text. Network calls stay offline."""
import pytest

from db import (create_user, delete_user_account, get_settings, get_user_by_username,
                init_db, save_settings, username_exists)
from services.mail_poller import is_configured, poll_for_candidates

U = "imap_user_test"


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, "imap_test@example.com", "x" * 20, "IMAP Tester")
    yield uid
    delete_user_account(uid)


def test_unconfigured_returns_empty_with_status(user):
    cands, status = poll_for_candidates(user)
    assert cands == [] and status
    assert not is_configured(get_settings(user))


def test_password_encrypts_into_settings_and_roundtrips(user):
    from crypto import decrypt_str, encrypt_str
    enc = encrypt_str("app-password-123")
    save_settings(user, {"imap_host": "imap.test.io",
                         "imap_user": "me@test.io",
                         "imap_app_password_enc": enc})
    s = get_settings(user)
    assert is_configured(s)
    assert decrypt_str(s["imap_app_password_enc"]) == "app-password-123"
    # unreachable host degrades to a status message, never an exception
    cands, status = poll_for_candidates(user)
    assert cands == [] and status


def test_fetched_email_text_parses_to_candidates():
    from services.mail_ingestion import parse_email_text
    text = ("Order confirmation\n\nThanks for your order on 2026-08-21.\n\n"
            "1 x Desk lamp 24.90\nTotal 24.90\n")
    cands = parse_email_text(text if isinstance(text, str) else "".join(text))
    assert cands and cands[0]["amount_eur"] > 0
