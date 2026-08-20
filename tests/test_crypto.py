"""
At-rest encryption tests: fresh DBs are created as ciphertext, existing
plaintext DBs migrate in place (crash-safe), backups stay encrypted, wrong
keys fail loudly, and the shared master key drives both SQLCipher and Fernet.
"""

import os
import sqlite3
import tempfile

import pytest

import db as db_module
import crypto
from db import init_db, _raw_connect, _file_is_plaintext


def _tmpdir() -> str:
    # Workspace-write sandbox: system temp is blocked for SQLite/SQLCipher
    # opens. Use data/_pytest_tmp so temp DB files are writable.
    import pathlib, uuid
    base = pathlib.Path(__file__).resolve().parent.parent / "data" / "_pytest_tmp"
    base.mkdir(parents=True, exist_ok=True)
    p = str(base / f"expense_crypto_tests_{uuid.uuid4().hex[:6]}")
    os.makedirs(p, exist_ok=True)
    return p


def test_fresh_db_is_ciphertext_and_keyed_open_works():
    init_db()
    path = db_module.DB_PATH
    assert os.path.exists(path)
    assert not _file_is_plaintext(path), "fresh DB must not start with the SQLite header"
    con = _raw_connect(path)
    try:
        n = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        assert n > 0
    finally:
        con.close()


def test_wrong_key_cannot_open_the_db(monkeypatch):
    # Encrypt a scratch DB with the suite key, then try to open it with a
    # different key — SQLCipher must reject the read.
    other = os.path.join(_tmpdir(), "other.db")
    con = _raw_connect(other)
    try:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
    finally:
        con.close()
    assert not _file_is_plaintext(other)

    monkeypatch.setenv("EXPENSE_TRACKER_DB_KEY", "00" * 32)
    with pytest.raises(Exception, match="not a database"):
        _raw_connect(other)


def test_plaintext_db_migrates_in_place(monkeypatch):
    plain = os.path.join(_tmpdir(), "plain.db")
    con = sqlite3.connect(plain)
    try:
        con.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO things (name) VALUES ('alpha'), ('beta')")
        con.commit()
    finally:
        con.close()
    assert _file_is_plaintext(plain)

    monkeypatch.setattr(db_module, "DB_PATH", plain)
    monkeypatch.setattr(db_module, "_ENCRYPTION_LOCK",
                        os.path.join(os.path.dirname(plain), "lock"))
    db_module._migrate_plaintext_to_encrypted()

    assert os.path.exists(plain)
    assert not _file_is_plaintext(plain), "migration must produce ciphertext"
    con = _raw_connect(plain)
    try:
        rows = con.execute("SELECT name FROM things ORDER BY name").fetchall()
        assert rows == [("alpha",), ("beta",)]
    finally:
        con.close()
    # No lock or temp leftovers.
    assert not os.path.exists(db_module._ENCRYPTION_LOCK)
    assert not os.path.exists(plain + ".migrating")


def test_migration_failure_leaves_plaintext_intact(monkeypatch):
    plain = os.path.join(_tmpdir(), "keep.db")
    con = sqlite3.connect(plain)
    try:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(db_module, "DB_PATH", plain)
    monkeypatch.setattr(db_module, "_ENCRYPTION_LOCK",
                        os.path.join(os.path.dirname(plain), "lock"))
    # Make cipher_migrate impossible by pointing the DBAPI at a stub that
    # raises mid-migration: the original file must remain untouched.
    class _Boom:
        def connect(self, *a, **k):
            raise RuntimeError("boom")
    monkeypatch.setattr(db_module, "sqlcipher_dbapi", _Boom())

    with pytest.raises(RuntimeError, match="Failed to encrypt the database"):
        db_module._migrate_plaintext_to_encrypted()

    assert _file_is_plaintext(plain), "failed migration must not touch the original"
    con = sqlite3.connect(plain)
    try:
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 0
    finally:
        con.close()
    assert not os.path.exists(db_module._ENCRYPTION_LOCK)
    assert not os.path.exists(plain + ".migrating")


def test_backup_file_is_encrypted():
    from db import backup_db, DB_PATH, BACKUP_DIR
    init_db()
    path = backup_db(force=True)
    assert path and os.path.exists(path)
    assert not _file_is_plaintext(path), "backups must remain ciphertext"
    con = _raw_connect(path)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        con.close()
    assert path.startswith(BACKUP_DIR)


def test_master_key_env_precedence_and_passphrase_digest(monkeypatch):
    hexkey = "ab" * 32
    monkeypatch.setenv("EXPENSE_TRACKER_DB_KEY", hexkey)
    assert crypto.get_master_bytes() == bytes.fromhex(hexkey)

    monkeypatch.setenv("EXPENSE_TRACKER_DB_KEY", "my-passphrase")
    import hashlib
    assert crypto.get_master_bytes() == hashlib.sha256(b"my-passphrase").digest()


def test_fernet_key_file_path_is_used_verbatim(monkeypatch):
    # Regression: get_fernet_key once base64-encoded the .secret_key file
    # content AGAIN (double-encoding), producing an invalid Fernet key and
    # silently breaking every stored SMTP password / GitHub token on the
    # default file-key path. The file content is already a Fernet key.
    from cryptography.fernet import Fernet
    fk = Fernet.generate_key()
    monkeypatch.setattr(crypto, "_env_secret", lambda: None)
    monkeypatch.setattr(crypto, "_file_secret", lambda: fk)
    assert crypto.get_fernet_key() == fk
    token = Fernet(crypto.get_fernet_key()).encrypt(b"x")
    assert Fernet(fk).decrypt(token) == b"x"
    # The SQLCipher derivation stays on the raw file bytes (the live DB was
    # encrypted with that derivation — it must not change).
    import hashlib
    assert crypto.sqlcipher_key_pragma() == f"\"x'{hashlib.sha256(fk).hexdigest()}'\""


def test_fernet_roundtrip_and_tamper():
    token = crypto.encrypt_str("github_pat_secret")
    assert token and token != "github_pat_secret"
    assert crypto.decrypt_str(token) == "github_pat_secret"
    assert crypto.decrypt_str(token[:-4] + "AAAA") == ""  # tampered → empty
    assert crypto.encrypt_str("") == "" and crypto.decrypt_str("") == ""


def test_notifications_use_the_same_secret():
    import notifications
    token = crypto.encrypt_str("smtp-pass-123")
    assert notifications._decrypt(token) == "smtp-pass-123"


def test_ensure_db_encrypted_reports_wrong_key_clearly(monkeypatch):
    # A ciphertext DB created with the suite key, then opened with a
    # DIFFERENT key: _ensure_db_encrypted must fail with the friendly
    # message, not a raw DatabaseError.
    other = os.path.join(_tmpdir(), "other.db")
    con = _raw_connect(other)
    try:
        con.execute("CREATE TABLE t (x)")
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(db_module, "DB_PATH", other)
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", False)
    monkeypatch.setenv("EXPENSE_TRACKER_DB_KEY", "00" * 32)
    with pytest.raises(RuntimeError, match="key does not match"):
        db_module._ensure_db_encrypted()
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", True)


def test_empty_db_file_becomes_encrypted(monkeypatch):
    # A 0-byte DB file (e.g. created by a broken download) must be treated
    # as a fresh database and opened keyed — no crash, ciphertext on disk.
    empty = os.path.join(_tmpdir(), "empty.db")
    with open(empty, "wb") as f:
        f.write(b"")
    monkeypatch.setattr(db_module, "DB_PATH", empty)
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", False)
    db_module._ensure_db_encrypted()
    assert db_module._ENCRYPTION_DONE is True
    con = _raw_connect(empty)
    try:
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        con.close()
    assert not _file_is_plaintext(empty)
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", True)


def test_ensure_db_encrypted_yields_when_another_process_migrates(monkeypatch):
    # A concurrent process holding a FRESH lock means WE must not start our
    # own migration (same temp paths) — fail with a clear message instead.
    plain = os.path.join(_tmpdir(), "busy.db")
    con = sqlite3.connect(plain)
    try:
        con.execute("CREATE TABLE t (x)")
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(db_module, "DB_PATH", plain)
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", False)
    monkeypatch.setattr(db_module, "_wait_for_migration_lock",
                        lambda timeout_s=120: False)
    with pytest.raises(RuntimeError, match="Another process"):
        db_module._ensure_db_encrypted()
    assert _file_is_plaintext(plain)  # untouched
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", True)


def test_lock_race_retries_when_other_process_finishes(monkeypatch):
    # Losing the O_EXCL lock race once, then seeing the other process's
    # ciphertext, must converge without a second migration attempt.
    plain = os.path.join(_tmpdir(), "race.db")
    con = sqlite3.connect(plain)
    try:
        con.execute("CREATE TABLE t (x)")
        con.commit()
    finally:
        con.close()
    state = {"migrations": 0}

    def fake_migrate():
        state["migrations"] += 1
        raise FileExistsError("database encryption is already running in another process")

    def fake_plain(path):
        return state["migrations"] == 0  # plaintext until the other finishes

    monkeypatch.setattr(db_module, "DB_PATH", plain)
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", False)
    monkeypatch.setattr(db_module, "_migrate_plaintext_to_encrypted", fake_migrate)
    monkeypatch.setattr(db_module, "_wait_for_migration_lock",
                        lambda timeout_s=120: True)
    monkeypatch.setattr(db_module, "_file_is_plaintext", fake_plain)
    db_module._ensure_db_encrypted()
    assert state["migrations"] == 1
    assert db_module._ENCRYPTION_DONE is True
    monkeypatch.setattr(db_module, "_ENCRYPTION_DONE", True)
