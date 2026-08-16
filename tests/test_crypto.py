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
    # tempfile.mkdtemp rather than pytest's tmp_path: the sandboxed test
    # runner cannot scandir the pytest temp root.
    return tempfile.mkdtemp(prefix="expense_crypto_tests_")


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
