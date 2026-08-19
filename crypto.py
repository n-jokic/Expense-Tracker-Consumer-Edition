"""
crypto.py — shared secret management for Expense Tracker.

One master secret protects everything at rest:

  • the whole SQLite database (SQLCipher raw key = SHA-256 of the secret), and
  • Fernet-encrypted fields (SMTP password, GitHub backup token).

Precedence for the secret:

  1. EXPENSE_TRACKER_DB_KEY environment variable
     (64 hex characters, a urlsafe-base64 Fernet key, or any passphrase —
     passphrases are SHA-256-hashed to 32 bytes);
  2. data/.secret_key — a Fernet key file, auto-generated on first use;
  3. st.secrets["encryption_key"] (Docker / hosted deployments) — SHA-256
     digest, matching the app's original SMTP-encryption behavior.

WARNING: losing the secret (key file deleted AND env var unset) makes the
database and every encrypted field unreadable. Back the key up separately —
it is deliberately NEVER included in database backups or GitHub backups.
"""

import os
import base64
import hashlib
import logging
from app_paths import state_dir

log = logging.getLogger("crypto")

_ENV_KEY = "EXPENSE_TRACKER_DB_KEY"


def _env_secret() -> bytes | None:
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        return None
    raw = raw.strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    try:
        b = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(b) == 32:
            return b
    except Exception:
        pass
    # Anything else (e.g. a human passphrase) becomes a 32-byte key.
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _file_secret() -> bytes | None:
    key_path = os.path.join(state_dir(), ".secret_key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            data = f.read().strip()
        if data:
            return data
    return None


def _streamlit_secret() -> bytes | None:
    try:
        import streamlit as st
        secret = st.secrets.get("encryption_key")
    except Exception:
        return None
    if secret:
        return hashlib.sha256(str(secret).encode("utf-8")).digest()
    return None


def _generate_and_store_file_key() -> bytes:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    key_path = os.path.join(state_dir(), ".secret_key")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    # Best-effort restrictive permissions (meaningful on POSIX; Windows
    # relies on the per-user %APPDATA%-equivalent project folder).
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    except OSError:
        with open(key_path, "wb") as f:
            f.write(key)
    log.info("generated new master key at %s", key_path)
    return key


def get_master_bytes() -> bytes:
    """The 32-byte master secret, resolved with the documented precedence.

    NOTE: this value feeds the SQLCipher database key only. For the file
    case it is the RAW .secret_key content (a Fernet key string), so the
    DB key stays stable for installs whose database was encrypted with an
    earlier version. Fernet encryption uses get_fernet_key() instead.
    """
    secret = _env_secret()
    if secret is not None:
        return secret
    secret = _file_secret()
    if secret is not None:
        return secret
    secret = _streamlit_secret()
    if secret is not None:
        return secret
    return _generate_and_store_file_key()


def get_fernet_key() -> bytes:
    """A valid Fernet key derived from the same master secret.

    - env var:   urlsafe-base64 of the 32-byte normalized secret;
    - st.secrets: urlsafe-base64 of the SHA-256 digest (matches the app's
      original SMTP-encryption behavior exactly);
    - .secret_key file: the file content IS a Fernet key already — returned
      as-is (re-encoding would double-base64 it and break every stored
      SMTP password / GitHub token).
    """
    secret = _env_secret()
    if secret is not None:
        return base64.urlsafe_b64encode(secret)
    secret = _file_secret()
    if secret is not None:
        return secret
    secret = _streamlit_secret()
    if secret is not None:
        return base64.urlsafe_b64encode(secret)
    return _generate_and_store_file_key()  # a Fernet key by construction


def sqlcipher_key_pragma() -> str:
    """The raw-key PRAGMA value for SQLCipher (SHA-256 of the master secret).

    Returns the value as a SQL string literal:  "x'<64 hex chars>'"
    (SQLCipher requires the raw key in quotes — the x'...' form is not a
    SQLite blob literal here but the key notation itself)."""
    digest = hashlib.sha256(get_master_bytes()).hexdigest()
    return f"\"x'{digest}'\""


def encrypt_str(plain: str) -> str:
    if not plain:
        return ""
    from cryptography.fernet import Fernet
    return Fernet(get_fernet_key()).encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_str(enc: str) -> str:
    if not enc:
        return ""
    from cryptography.fernet import Fernet
    try:
        return Fernet(get_fernet_key()).decrypt(enc.encode("utf-8")).decode("utf-8")
    except Exception as e:
        # e.g. the key was replaced (secrets vs file): the stored value can no
        # longer be decrypted — log loudly instead of failing silently.
        log.warning("cannot decrypt stored secret (key mismatch?): %s", e)
        return ""
