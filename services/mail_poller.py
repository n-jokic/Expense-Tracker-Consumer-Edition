"""
services/mail_poller.py — #26 E6 local IMAP poller.

Fetches UNSEEN emails from a user-configured IMAP inbox (app password stored
Fernet-encrypted in UserSettings), parses each through the SAME
mail_ingestion.parse_email_text pipeline as the paste flow, and returns
staging candidates tagged with their source email. Nothing books silently:
candidates go to the same Accept/Discard cards as pasted text.

Offline-safe by contract: any network/credentials problem degrades to an
empty candidate list plus a human-readable status string.
"""

from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header


def is_configured(settings: dict | None) -> bool:
    s = settings or {}
    return bool(str(s.get("imap_host") or "").strip()
                and str(s.get("imap_user") or "").strip()
                and str(s.get("imap_app_password_enc") or "").strip())


def fetch_unseen_emails(host: str, user: str, password: str,
                        limit: int = 10, timeout_s: int = 15) -> list[dict]:
    """Log in, fetch up to `limit` UNSEEN messages (marking them Seen),
    and return [{subject, from_, text}]. Raises on connection errors --
    callers catch and surface the message."""
    conn = imaplib.IMAP4_SSL(str(host), timeout=timeout_s)
    try:
        conn.login(str(user), str(password))
        conn.select("INBOX", readonly=False)
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return []
        ids = (data[0] or b"").split()
        out: list[dict] = []
        for num in ids[-limit:]:
            status, msg_data = conn.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = str(make_header(decode_header(
                msg.get("Subject", "") or "")))
            sender = str(make_header(decode_header(
                msg.get("From", "") or "")))
            body = _extract_text(msg)
            # mark as read so the next poll does not re-offer it
            conn.store(num, "+FLAGS", "\\Seen")
            out.append({"subject": subject, "from_": sender,
                        "text": subject + "\n" + body})
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _extract_text(msg) -> str:
    """Best-effort plain-text body (first text/plain part; falls back to
    a stripped text/html)."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace")
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    import re as _re
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace")
                    return _re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return str(msg.get_payload() or "")


def poll_for_candidates(user_id: int, limit: int = 10) -> tuple[list[dict], str]:
    """Read this user's IMAP settings, pull unseen mail, and return
    (candidates, status_message). Never raises for configuration/network
    problems -- those come back as ([], reason)."""
    import crypto
    from db import get_settings
    from services.mail_ingestion import parse_email_text

    settings = get_settings(user_id) or {}
    if not is_configured(settings):
        return [], ("Not configured -- add your IMAP host, user and app "
                    "password in Settings first.")
    try:
        password = crypto.decrypt_str(
            str(settings["imap_app_password_enc"]))
    except Exception:
        return [], ("Stored app password could not be decrypted -- "
                    "re-save it in Settings.")
    try:
        mails = fetch_unseen_emails(
            settings["imap_host"], settings["imap_user"], password,
            limit=limit)
    except Exception as exc:
        return [], "Could not reach {}: {}".format(
            settings["imap_host"], exc)
    candidates: list[dict] = []
    for m in mails:
        for cand in parse_email_text(m["text"]):
            cand["source_email"] = m["subject"]
            candidates.append(cand)
    if not mails:
        return [], "No unseen emails found."
    if not candidates:
        return [], ("Checked {} new email(s) -- no order amounts "
                    "recognized.").format(len(mails))
    return candidates, "Found candidates in {} new email(s).".format(len(mails))
