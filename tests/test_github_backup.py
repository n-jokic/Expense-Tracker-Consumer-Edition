"""
GitHub backup tests: chunked upload ordering (parts before manifest),
SHA-256 verified restore, retention pruning, encrypted token storage, error
recording, and the once-a-day auto-backup trigger. All HTTP is mocked — the
suite never touches the network.
"""

import os
import json
from datetime import datetime, date, timedelta

import pytest

import github_backup as gb
from crypto import encrypt_str, decrypt_str
from db import init_db, create_user, delete_user_account, username_exists, \
    get_user_by_username, get_settings, save_settings, add_expense
from auth import hash_password

TEST_USERNAME = "gh_backup_test_user"
TEST_EMAIL    = "gh_backup_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "GH Backup Tester")
    yield uid
    delete_user_account(uid)


def _tmpfile(name: str, data: bytes) -> str:
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="gh_backup_tests_"), name)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ── Splitting & verification ──────────────────────────────────────────────────

def test_split_merge_roundtrip_single_and_multi_part(monkeypatch):
    data = os.urandom(50)
    path = _tmpfile("one.db", data)
    monkeypatch.setattr(gb, "CHUNK_SIZE", 4096)
    parts, manifest = gb._split_file(path)
    assert len(parts) == 1
    assert gb._merge_parts(parts, manifest) == data

    big = os.urandom(4096 * 2 + 17)
    path2 = _tmpfile("big.db", big)
    parts, manifest = gb._split_file(path2)
    assert len(parts) == 3
    assert manifest["original_size"] == len(big)
    assert gb._merge_parts(parts, manifest) == big


def test_merge_rejects_tampered_and_missing_parts(monkeypatch):
    big = os.urandom(9000)
    path = _tmpfile("t.db", big)
    monkeypatch.setattr(gb, "CHUNK_SIZE", 4096)
    parts, manifest = gb._split_file(path)

    # Tamper with one part.
    name, blob = parts[0]
    tampered = (name, blob[:-1] + bytes([blob[-1] ^ 0xFF]))
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        gb._merge_parts([tampered] + parts[1:], manifest)

    # A missing part is also fatal.
    with pytest.raises(RuntimeError, match="Missing part"):
        gb._merge_parts(parts[1:], manifest)

    # And a wrong db-level checksum (parts fine, manifest lying).
    bad_manifest = dict(manifest)
    bad_manifest["db_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="Database checksum mismatch"):
        gb._merge_parts(parts, bad_manifest)


# ── Full run (mocked GitHub) ──────────────────────────────────────────────────

def _fake_gh(monkeypatch, files=None, uploads=None, deletes=None,
             branch="main", error=None):
    files = files if files is not None else {}
    uploads = uploads if uploads is not None else []
    deletes = deletes if deletes is not None else []

    def fake_branch(token, repo):
        if error:
            raise RuntimeError(error)
        return branch

    def fake_put(token, repo, path, content, br, message):
        uploads.append(path)
        files[path] = content

    def fake_list(token, repo, path, br):
        if path == "backups":
            return [{"name": n, "type": "dir"} for n in files.get("_dirs", [])]
        return [{"name": os.path.basename(p), "type": "file", "path": p,
                 "sha": "x"} for p in files if p.startswith(path + "/")]

    def fake_delete(token, repo, path, sha, br, message):
        deletes.append(path)

    monkeypatch.setattr(gb, "_default_branch", fake_branch)
    monkeypatch.setattr(gb, "_put_file", fake_put)
    monkeypatch.setattr(gb, "_list_dir", fake_list)
    monkeypatch.setattr(gb, "_delete_file", fake_delete)
    return files, uploads, deletes


def test_run_github_backup_uploads_parts_then_manifest(test_user, monkeypatch):
    add_expense(test_user, {"date": date(2025, 6, 1), "category": "Food & Dining",
                            "description": "gh test", "amount": 5.0,
                            "currency": "EUR", "amount_eur": 5.0})
    save_settings(test_user, {"gh_repo": "me/backup",
                              "gh_token_enc": encrypt_str("pat123"),
                              "gh_retention_days": 14})
    files, uploads, deletes = _fake_gh(monkeypatch)
    monkeypatch.setattr(gb, "CHUNK_SIZE", 1024)  # force multiple parts

    res = gb.run_github_backup(test_user)
    assert res["status"] == "ok"
    assert res["parts"] >= 2
    assert uploads, "no files were uploaded"
    assert uploads[-1].endswith(".manifest.json")
    assert all(p.endswith(".manifest.json") is False for p in uploads[:-1])

    # Manifest is a valid, restorable contract for everything uploaded.
    manifest = json.loads(files[uploads[-1]])
    assert manifest["version"] == 1
    for entry in manifest["parts"]:
        assert f"backups/{date.today().isoformat()}/{entry['file']}" in uploads

    s = get_settings(test_user)
    assert s["gh_last_status"] == "ok"
    assert s["gh_last_backup_at"] is not None
    assert s["gh_last_error"] is None


def test_prune_deletes_only_old_day_folders(monkeypatch):
    files, uploads, deletes = _fake_gh(monkeypatch, files={
        "_dirs": ["2020-01-01", date.today().isoformat()],
        "backups/2020-01-01/a.db": b"x",
        "backups/2020-01-01/b.db": b"y",
    })
    n = gb._prune_old("t", "repo", "main", 14)
    assert n == 2
    assert sorted(deletes) == ["backups/2020-01-01/a.db",
                               "backups/2020-01-01/b.db"]


def test_prune_keeps_future_and_malformed_names(monkeypatch):
    future = (date.today() + timedelta(days=30)).isoformat()
    files, uploads, deletes = _fake_gh(monkeypatch, files={
        "_dirs": [future, "not-a-date"],
        "backups/" + future + "/a.db": b"x",
    })
    assert gb._prune_old("t", "repo", "main", 14) == 0
    assert deletes == []


# ── Error paths ───────────────────────────────────────────────────────────────

def test_missing_config_records_error(test_user, monkeypatch):
    files, uploads, deletes = _fake_gh(monkeypatch)
    res = gb.run_github_backup(test_user)  # nothing configured
    assert res["status"] == "error"
    assert "repository" in res["message"].lower()
    assert uploads == []
    assert get_settings(test_user)["gh_last_status"] == "error"


def test_bad_repo_shape_rejected(test_user, monkeypatch):
    save_settings(test_user, {"gh_repo": "no-slash-here",
                              "gh_token_enc": encrypt_str("pat")})
    files, uploads, deletes = _fake_gh(monkeypatch)
    res = gb.run_github_backup(test_user)
    assert res["status"] == "error"
    assert "owner/name" in res["message"]


def test_api_401_records_error(test_user, monkeypatch):
    save_settings(test_user, {"gh_repo": "me/backup",
                              "gh_token_enc": encrypt_str("pat")})
    files, uploads, deletes = _fake_gh(monkeypatch,
                                       error="GitHub API 401: Bad credentials")
    res = gb.run_github_backup(test_user)
    assert res["status"] == "error"
    assert "401" in res["message"]
    assert uploads == []
    s = get_settings(test_user)
    assert s["gh_last_status"] == "error" and "401" in (s["gh_last_error"] or "")


def test_concurrent_runs_are_skipped(test_user, monkeypatch):
    save_settings(test_user, {"gh_repo": "me/backup",
                              "gh_token_enc": encrypt_str("pat")})
    files, uploads, deletes = _fake_gh(monkeypatch)
    # Hold the lock to simulate an in-flight run.
    assert gb._lock.acquire(blocking=False)
    try:
        res = gb.run_github_backup(test_user)
        assert res["status"] == "skipped"
        assert uploads == []
    finally:
        gb._lock.release()


# ── Token handling & auto-trigger ─────────────────────────────────────────────

def test_token_stored_encrypted_only(test_user):
    save_settings(test_user, {"gh_token_enc": encrypt_str("github_pat_abc")})
    s = get_settings(test_user)
    assert s["gh_token_enc"] != "github_pat_abc"
    assert "github_pat_abc" not in json.dumps(s, default=str)
    assert decrypt_str(s["gh_token_enc"]) == "github_pat_abc"


class _FakeThreading:
    """Minimal stand-in for the threading module: records Thread() starts."""

    def __init__(self):
        self.started = False

    class Thread:
        def __init__(self, *args, **kwargs):
            pass  # the owning fake sets the flag in maybe_auto_backup tests

    def start(self):
        pass


def test_maybe_auto_backup_gates(monkeypatch):
    fake = _FakeThreading()

    def _spy_thread(target=None, args=(), **kw):
        fake.started = True
        return fake  # .start() is called on the returned object

    fake.Thread = _spy_thread
    monkeypatch.setattr(gb, "threading", fake)
    now = datetime.now()

    gb.maybe_auto_backup(1, {"gh_backup_enabled": False})
    assert fake.started is False

    gb.maybe_auto_backup(1, {"gh_backup_enabled": True,
                             "gh_last_backup_at": now})
    assert fake.started is False

    gb.maybe_auto_backup(1, {"gh_backup_enabled": True,
                             "gh_last_backup_at": now - timedelta(hours=25)})
    assert fake.started is True

    # A string timestamp is accepted too (settings roundtrip through JSON).
    fake.started = False
    gb.maybe_auto_backup(1, {"gh_backup_enabled": True,
                             "gh_last_backup_at":
                                 (now - timedelta(hours=25)).isoformat()})
    assert fake.started is True
