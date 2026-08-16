"""
github_backup.py — encrypted database backups to a free private GitHub repo.

The SQLite file is already SQLCipher ciphertext, so uploading the raw file
leaks nothing (the master key is deliberately NOT uploaded — back it up
separately). Files over 50 MB are split into parts to stay safely under
GitHub's 100 MB per-file limit; a .manifest.json (written LAST) records the
parts and their SHA-256 checksums so a restore can verify everything.

Transport is the GitHub Contents REST API via `requests` — no git CLI needed.
The fine-grained PAT is stored Fernet-encrypted in user settings and is never
included in exports.
"""

import os
import json
import base64
import hashlib
import logging
import threading
from datetime import datetime, date, timedelta

import requests

from db import (backup_db, DB_PATH, BACKUP_DIR, get_engine, get_settings,
                save_settings, get_user_by_username, get_session, User)
from crypto import decrypt_str

log = logging.getLogger("github_backup")

GH_API = "https://api.github.com"
# GitHub hard-caps files at 100 MB; 50 MB parts leave room for base64
# overhead-free uploads and keep memory use bounded.
CHUNK_SIZE = int(os.environ.get("GH_TEST_CHUNK_SIZE") or 50 * 1024 * 1024)
_BACKUPS_PREFIX = "backups"

# One upload at a time per process (background thread + manual button).
_lock = threading.Lock()


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Expense-Tracker-Backup",
    }


def _check(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        msg = resp.json().get("message", resp.text[:200])
    except Exception:
        msg = resp.text[:200]
    raise RuntimeError(f"GitHub API {resp.status_code}: {msg}")


def _api(token: str, method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 60)
    resp = requests.request(method, url, headers=_headers(token), **kwargs)
    _check(resp)
    return resp


def _default_branch(token: str, repo: str) -> str:
    resp = _api(token, "GET", f"{GH_API}/repos/{repo}")
    branch = resp.json().get("default_branch")
    if not branch:
        raise RuntimeError(f"Could not determine the default branch of {repo}")
    return branch


def _put_file(token: str, repo: str, remote_path: str, content: bytes,
              branch: str, message: str) -> None:
    body = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    _api(token, "PUT", f"{GH_API}/repos/{repo}/contents/{remote_path}", json=body)


def _delete_file(token: str, repo: str, remote_path: str, sha: str,
                 branch: str, message: str) -> None:
    _api(token, "DELETE", f"{GH_API}/repos/{repo}/contents/{remote_path}",
         json={"message": message, "sha": sha, "branch": branch})


def _list_dir(token: str, repo: str, remote_path: str, branch: str) -> list:
    try:
        resp = _api(token, "GET",
                    f"{GH_API}/repos/{repo}/contents/{remote_path}?ref={branch}")
    except RuntimeError as e:
        if "404" in str(e):
            return []
        raise
    data = resp.json()
    return data if isinstance(data, list) else [data]


# ── Splitting & manifest ──────────────────────────────────────────────────────

def _split_file(path: str) -> tuple[list, dict]:
    """Split a file into CHUNK_SIZE parts. Returns (parts, manifest) where
    parts = [(part_name, bytes)] and manifest is the restore contract."""
    with open(path, "rb") as f:
        data = f.read()
    base = os.path.basename(path)
    n = max(1, -(-len(data) // CHUNK_SIZE))
    parts = []
    for i in range(n):
        chunk = data[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
        parts.append((f"{base}.part{i + 1:03d}", chunk))
    manifest = {
        "version": 1,
        "original_name": base,
        "original_size": len(data),
        "db_sha256": hashlib.sha256(data).hexdigest(),
        "parts": [{"file": name, "sha256": hashlib.sha256(blob).hexdigest()}
                  for name, blob in parts],
    }
    return parts, manifest


def _merge_parts(parts: list, manifest: dict) -> bytes:
    """Verify every part's SHA-256 and concatenate; then verify the whole."""
    by_name = {name: blob for name, blob in parts}
    out = bytearray()
    for entry in manifest["parts"]:
        blob = by_name.get(entry["file"])
        if blob is None:
            raise RuntimeError(f"Missing part: {entry['file']}")
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            raise RuntimeError(f"Checksum mismatch for {entry['file']}")
        out.extend(blob)
    if hashlib.sha256(bytes(out)).hexdigest() != manifest["db_sha256"]:
        raise RuntimeError("Database checksum mismatch — the download is corrupt")
    return bytes(out)


# ── Backup run ────────────────────────────────────────────────────────────────

def _resolve_config(user_id: int, settings: dict | None = None) -> tuple[str, str]:
    """Return (repo, token) or raise a user-facing error."""
    s = settings or get_settings(user_id)
    repo = (s.get("gh_repo") or "").strip()
    token = decrypt_str(s.get("gh_token_enc") or "")
    if not repo:
        raise RuntimeError("GitHub backup is not configured (no repository).")
    if "/" not in repo or any(not part for part in repo.split("/")):
        raise RuntimeError("The repository must look like 'owner/name'.")
    if not token:
        raise RuntimeError("GitHub token is missing — enter it in Settings.")
    return repo, token


def _record_status(user_id: int, status: str, error: str = "") -> None:
    try:
        save_settings(user_id, {
            "gh_last_status": status,
            "gh_last_error": (error[:500] if error else None),
            "gh_last_backup_at": datetime.now(),
        })
    except Exception as e:  # never let status bookkeeping crash the caller
        log.warning("could not record GitHub backup status: %s", e)


def _prune_old(token: str, repo: str, branch: str, retention_days: int) -> int:
    """Delete backup day-folders older than the retention window."""
    if retention_days <= 0:
        return 0
    cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
    removed = 0
    for entry in _list_dir(token, repo, _BACKUPS_PREFIX, branch):
        name = entry.get("name", "")
        if entry.get("type") != "dir" or not (len(name) == 10 and name[4] == "-"):
            continue
        if name >= cutoff:
            continue
        try:
            date.fromisoformat(name)
        except ValueError:
            continue
        for child in _list_dir(token, repo, f"{_BACKUPS_PREFIX}/{name}", branch):
            if child.get("type") != "file":
                continue
            try:
                _delete_file(token, repo, child["path"], child["sha"], branch,
                             f"backup retention: remove {child['name']}")
                removed += 1
            except Exception as e:
                log.warning("could not delete %s: %s", child.get("path"), e)
    return removed


def run_github_backup(user_id: int, settings: dict | None = None) -> dict:
    """Take a fresh local backup and upload it (chunked) to GitHub.

    Returns {"status": "ok", ...} or {"status": "error", "message": ...}.
    Thread-safe: a concurrent run returns immediately without uploading.
    """
    if not _lock.acquire(blocking=False):
        return {"status": "skipped", "message": "A backup is already running."}
    try:
        return _run_github_backup_locked(user_id, settings)
    except Exception as e:
        _record_status(user_id, "error", str(e))
        log.warning("GitHub backup failed: %s", e)
        return {"status": "error", "message": str(e)}
    finally:
        _lock.release()


def _run_github_backup_locked(user_id: int, settings: dict | None) -> dict:
    repo, token = _resolve_config(user_id, settings)
    branch = _default_branch(token, repo)

    # The local snapshot IS the upload payload: SQLCipher ciphertext.
    local = backup_db(force=True)
    if not local:
        raise RuntimeError("Local backups are unavailable (not a SQLite database).")

    parts, manifest = _split_file(local)
    day = date.today().isoformat()
    base = os.path.basename(local)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Parts first, manifest LAST: a backup folder only becomes restorable
    # once the manifest exists.
    for name, blob in parts:
        _put_file(token, repo, f"{_BACKUPS_PREFIX}/{day}/{name}", blob, branch,
                  f"expense-tracker backup {stamp}")
    manifest_blob = json.dumps(manifest, indent=2).encode("utf-8")
    _put_file(token, repo, f"{_BACKUPS_PREFIX}/{day}/{base}.manifest.json",
              manifest_blob, branch, f"expense-tracker backup {stamp} (manifest)")

    s = settings or get_settings(user_id)
    try:
        retention = int(s.get("gh_retention_days") or 14)
    except (TypeError, ValueError):
        retention = 14
    retention = max(1, min(retention, 90))
    pruned = _prune_old(token, repo, branch, retention)

    _record_status(user_id, "ok")
    return {"status": "ok", "backup": base, "parts": len(parts),
            "pruned_files": pruned, "repo": repo}


def maybe_auto_backup(user_id: int, settings: dict) -> None:
    """Daily automatic upload from app.py — best effort, never blocks the UI.

    Enabled when gh_backup_enabled is set and the last successful run is
    older than 24 hours (or never ran)."""
    if not settings.get("gh_backup_enabled"):
        return
    last = settings.get("gh_last_backup_at")
    if last is not None:
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last)
            except ValueError:
                last = None
        if last is not None and (datetime.now() - last).total_seconds() < 24 * 3600:
            return
    threading.Thread(target=run_github_backup, args=(user_id,),
                     name="gh-backup", daemon=True).start()


# ── Restore CLI ───────────────────────────────────────────────────────────────

def _cli_user(username: str | None):
    if username:
        u = get_user_by_username(username.strip().lower())
        if not u:
            raise RuntimeError(f"No user named '{username}'.")
        return u
    with get_session() as s:
        u = s.query(User).order_by(User.id.asc()).first()
    if not u:
        raise RuntimeError("No users in the database — create an account first.")
    return {"id": u.id, "username": u.username}


def _cli_config(user_id: int | None) -> tuple[str, str]:
    repo = os.environ.get("GH_REPO", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not repo and user_id is not None:
        repo = (get_settings(user_id).get("gh_repo") or "").strip()
    if not token and user_id is not None:
        token = decrypt_str(get_settings(user_id).get("gh_token_enc") or "")
    if not repo or "/" not in repo:
        raise RuntimeError("Set GH_REPO (owner/name) or configure Settings first.")
    if not token:
        raise RuntimeError("Set GH_TOKEN (fine-grained PAT) or configure Settings first.")
    return repo, token


def _find_manifest(token: str, repo: str, branch: str, stamp_prefix: str) -> tuple[dict, str]:
    for entry in _list_dir(token, repo, _BACKUPS_PREFIX, branch):
        if entry.get("type") != "dir":
            continue
        for child in _list_dir(token, repo, f"{_BACKUPS_PREFIX}/{entry['name']}", branch):
            name = child.get("name", "")
            if name.endswith(".manifest.json") and name.startswith(stamp_prefix):
                # The Contents API returns a metadata wrapper with the file
                # base64-encoded in its `content` field — not the raw JSON.
                resp = _api(token, "GET", child["url"])
                payload = resp.json()
                raw = payload.get("content") or ""
                manifest = json.loads(base64.b64decode(raw).decode("utf-8"))
                return manifest, entry["name"]
    raise RuntimeError(f"No backup manifest found matching '{stamp_prefix}'.")


def _download(token: str, repo: str, branch: str, day: str,
              manifest: dict) -> bytes:
    parts = []
    for entry in manifest["parts"]:
        resp = _api(token, "GET",
                    f"{GH_API}/repos/{repo}/contents/{_BACKUPS_PREFIX}/{day}/{entry['file']}"
                    f"?ref={branch}")
        payload = resp.json()
        content = payload.get("content")
        if content:
            blob = base64.b64decode(content)
        elif payload.get("download_url"):
            # The Contents API omits `content` for files > 1 MB — fetch the
            # raw file through download_url instead (parts are 50 MB).
            raw = _api(token, "GET", payload["download_url"])
            blob = raw.content
        else:
            raise RuntimeError(f"Could not download {entry['file']} "
                               "(no content and no download_url)")
        parts.append((entry["file"], blob))
    return _merge_parts(parts, manifest)


def _cli_list(args: list) -> None:
    user = _cli_user(args.user) if getattr(args, "user", None) else None
    repo, token = _cli_config(user["id"] if user else None)
    branch = _default_branch(token, repo)
    found = False
    for entry in _list_dir(token, repo, _BACKUPS_PREFIX, branch):
        if entry.get("type") != "dir":
            continue
        day = entry["name"]
        manifests = [c["name"] for c in
                     _list_dir(token, repo, f"{_BACKUPS_PREFIX}/{day}", branch)
                     if c.get("name", "").endswith(".manifest.json")]
        for m in sorted(manifests):
            print(f"{day}  {m.removesuffix('.manifest.json')}")
            found = True
    if not found:
        print("No backups found in the repository.")


def _cli_restore(args: list) -> None:
    if getattr(args, "replace", False):
        _guard_replace()
    user = _cli_user(args.user) if getattr(args, "user", None) else None
    repo, token = _cli_config(user["id"] if user else None)
    branch = _default_branch(token, repo)
    manifest, day = _find_manifest(token, repo, branch, args.stamp)
    data = _download(token, repo, branch, day, manifest)
    out = getattr(args, "out", None)
    if not out:
        out = os.path.join(BACKUP_DIR, f"restored_{manifest['original_name']}")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = f"{out}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out)
    print(f"Restored (SHA-256 verified) -> {out}")
    if getattr(args, "replace", False):
        _replace_db(out, manifest["original_name"])


def _guard_replace() -> None:
    for suffix in ("-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            raise RuntimeError(
                f"{DB_PATH + suffix} exists — stop the app and the sync API "
                "first (restoring over a live database corrupts it).")


def _replace_db(restored: str, original_name: str) -> None:
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Restore replacement only applies to SQLite databases.")
    keep = os.path.join(BACKUP_DIR,
                        f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{original_name}")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(DB_PATH, "rb") as src, open(keep, "wb") as dst:
        dst.write(src.read())
    try:
        os.replace(restored, DB_PATH)
    except OSError as e:
        # Windows: the file is still open by a running app/API process.
        raise RuntimeError(
            f"Could not replace the database ({e}). Stop the app and the "
            "sync API first, then retry — your previous database is "
            f"untouched and a copy was kept at {keep}.") from e
    print(f"Database replaced. The previous file was kept at: {keep}")
    print("Restart the app (and the sync API) now.")


def _cli_test(args: list) -> None:
    user = _cli_user(args.user) if getattr(args, "user", None) else None
    repo, token = _cli_config(user["id"] if user else None)
    branch = _default_branch(token, repo)
    print(f"OK — connected to {repo} (branch: {branch}).")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python github_backup.py",
        description="Encrypted Expense Tracker backups on GitHub.")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="list remote backups")
    p_list.add_argument("--user", help="read settings of this account")

    p_restore = sub.add_parser("restore", help="download and verify a backup")
    p_restore.add_argument("stamp", help="backup timestamp prefix (YYYY-MM-DD_HHMMSS)")
    p_restore.add_argument("--user", help="read settings of this account")
    p_restore.add_argument("--out", help="output file (default: BACKUP_DIR)")
    p_restore.add_argument("--replace", action="store_true",
                           help="replace the live DB after verifying")

    p_test = sub.add_parser("test", help="check repo/token connectivity")
    p_test.add_argument("--user", help="read settings of this account")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            _cli_list(args)
        elif args.command == "restore":
            _cli_restore(args)
        elif args.command == "test":
            _cli_test(args)
        else:
            parser.print_help()
            return 1
    except (RuntimeError, OSError) as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
