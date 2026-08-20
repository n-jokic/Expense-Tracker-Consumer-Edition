# Encryption & Crypto — Expense Tracker (crypto.py + db.py)

> How the database and sensitive fields stay ciphertext at rest, how the master secret is resolved, and how plaintext databases are migrated without data loss.

## 1. Purpose & Threat Model

- **Goal:** whole-file at-rest encryption for the SQLite DB (SQLCipher) + field-level encryption for SMTP/GitHub/AI secrets (Fernet). Losing the DB file alone leaks nothing (ciphertext); losing the secret alone is safe if the DB stays local.
- **Non-goal:** transport encryption (TLS), multi-user E2EE, or per-row crypto. One **master secret** protects everything.
- **Key never leaves the machine** — deliberately **never included in `.db` backups or GitHub backups** (see §13). Back it up separately.
- **Household is experimental** — encryption and key scope are per-installation, not per-household; household membership (see `data-model.md §4`) does not change the master secret or per-user ciphertext boundary.

## 2. Architecture — One Master Secret, Two Derivations

```
master secret (32 bytes) ──┬──→ SHA-256 hex → SQLCipher raw key (whole DB file)
                         └──→ Fernet key   → encrypt_str/decrypt_str (fields)
```

- DB key: `sqlcipher_key_pragma() = "\"x'{sha256(master).hexdigest()}'\"`" — SQLCipher `PRAGMA key = …`.
- Fernet key: `get_fernet_key()` — urlsafe-base64 of the same 32 bytes (with one divergence for the file path, §8).

## 3. _ENCRYPT Flag & Opt-Out

```python
_ENCRYPT = env EXPENSE_TRACKER_NO_ENCRYPT not in ("1","true","yes","on")
```

- **Enabled by default.** Set `EXPENSE_TRACKER_NO_ENCRYPT=1` to run plaintext (exotic hosts, debugging). `_sqlite_module()` then returns stdlib `sqlite3`; otherwise it returns `sqlcipher3.dbapi2` and raises with install hint if missing.
- `DATABASE_URL` (Postgres etc.) bypasses file encryption entirely — Postgres TLS/auth is the operator's job.

## 4. Precedence Chain — Overview

`get_master_bytes()` resolves in strict order; first hit wins:

| Priority | Source | Raw form | What `get_master_bytes()` returns |
|---|---|---|---|
| 1 | `EXPENSE_TRACKER_DB_KEY` env var | 64-hex / Fernet b64 / passphrase | 32 bytes (decoded or SHA-256) |
| 2 | `data/.secret_key` file (via `state_dir()`) | Fernet key bytes | raw file bytes |
| 3 | `st.secrets["encryption_key"]` | hosted string | SHA-256 digest |
| 4 | *(none found)* | — | auto-generate Fernet key, write `data/.secret_key` |

See `crypto.py:93` (`get_master_bytes`) and `crypto.py:34/54/64`.

## 5. Source 1 — ENV (`EXPENSE_TRACKER_DB_KEY`)

`_env_secret() -> bytes | None` — `crypto.py:34`:

1. `raw = env EXPENSE_TRACKER_DB_KEY.strip()` — absent/empty → `None`.
2. **64-char branch:** if `len(raw)==64` try `bytes.fromhex(raw)` → 32 bytes. On `ValueError` fall through (not hex).
3. **Base64 branch:** try `base64.urlsafe_b64decode(raw + padding)`; if decoded length 32 → return it (covers Fernet keys).
4. **Passphrase fallback:** `hashlib.sha256(raw.encode("utf-8")).digest()` — any other string becomes a stable 32-byte key.

`tests/test_crypto.py:test_master_key_env_precedence_and_passphrase_digest` pins hex vs passphrase behavior; hermetic suites set `EXPENSE_TRACKER_DB_KEY="9f2c8e6a…"` in `tests/conftest.py`.

## 6. Source 2 — File (`data/.secret_key` or DB-colocated)

`_secret_key_path()` → `_file_secret() -> bytes | None` — `crypto.py:54`:

- Path: when `DB_PATH` is overridden (tests, custom install) the secret is **colocated** with the DB: `dirname(abspath(DB_PATH))/.secret_key` (T2-001, P4 lock/memo fix); otherwise `os.path.join(state_dir(), ".secret_key")` — `state_dir()` respects `EXPENSE_TRACKER_DATA_DIR`, then frozen `%LOCALAPPDATA%/ExpenseTracker`, then `<repo>/data` (`app_paths.py:12`). This prevents different DBs sharing one `state_dir` secret/lock.
- If the file exists, `f.read().strip()` non-empty → return **raw bytes**.
- **Auto-generation:** `_generate_and_store_file_key()` → `Fernet.generate_key()`, `os.open(...,0o600)` + `fdopen`, fallback to plain `open` on Windows. Logs `generated new master key at …`.

## 7. Source 3 — Streamlit Secrets

`_streamlit_secret() -> bytes | None` — `crypto.py:64`:

```python
try: secret = st.secrets.get("encryption_key")
except Exception: return None
if secret: return hashlib.sha256(str(secret).encode()).digest()
```

- Docker/hosted deployments that set `.streamlit/secrets.toml: encryption_key = "…"`.
- Always a **SHA-256 digest** — matches the app's original SMTP-encryption behavior.

## 8. get_master_bytes() vs get_fernet_key() — Critical Divergence

- `get_master_bytes()` (DB key) feeds `sqlcipher_key_pragma()` as `sha256(master).hexdigest()`. For the **file case it uses raw file bytes** — the live DB was historically encrypted with that derivation; changing it would brick the DB.
- `get_fernet_key()` (field key) for the **file case returns the file content verbatim** — it *is* already a Fernet key; re-base64-encoding it would double-encode and break every stored SMTP/GitHub token. Regression pinned in `tests/test_crypto.py:test_fernet_key_file_path_is_used_verbatim`.
- Env/`st.secrets` paths: `get_fernet_key() = base64.urlsafe_b64encode(secret_bytes)` (32 bytes → valid Fernet key).
- `tests/test_crypto.py:test_fernet_roundtrip_and_tamper` and `test_notifications_use_the_same_secret` enforce round-trip and cross-module (`notifications._decrypt`) compatibility.

## 9. SQLCipher Key Pragma

```python
def sqlcipher_key_pragma() -> str:
    digest = hashlib.sha256(get_master_bytes()).hexdigest()
    return f"\"x'{digest}'\""
```

- Returns a **quoted** raw-key literal for `PRAGMA key = "x'…'"` — the `x'…'` form is SQLCipher's raw-key notation, not a SQLite blob literal.
- Applied in `_keyed_pragmas(con)` and on every pooled connection via `@event.listens_for(engine,"connect")` in `get_engine()`.

## 10. At-Rest Lifecycle — _ensure_db_encrypted()

- **Fresh DB (no file):** mark `_ENCRYPTION_DONE = True`; engine creates it encrypted via the `PRAGMA key` path — no migration.
- **Ciphertext on disk:** verify the key immediately: open with `_raw_connect()`, `SELECT count(*) FROM sqlite_master`. On `not a database / encrypted / key` errors, raise friendly `RuntimeError("The database is encrypted but the key does not match…")`.
- **Plaintext on disk:** enter migration (up to 2 attempts, see §12).
- Empty 0-byte files are treated as fresh (see `test_empty_db_file_becomes_encrypted`).

## 11. Plaintext Detection — _SQLITE_HEADER

```python
_SQLITE_HEADER = b"SQLite format 3\x00"
def _file_is_plaintext(path) -> bool:
    with open(path,"rb") as f: return f.read(16) == _SQLITE_HEADER
```

- Ciphertext SQLCipher files start with **random salt**, not the magic header — reliable discriminator. Used by migration guard and backup tests (`test_fresh_db_is_ciphertext`, `test_backup_file_is_encrypted`).

## 12. Migration & Concurrency — _migrate_plaintext_to_encrypted() + _wait_for_migration_lock()

**Lock file:** `_ENCRYPTION_LOCK = <_DB_DIR>/.db-encrypting` where `_DB_DIR = dirname(abspath(DB_PATH))` — colocation with the DB file (T2-001). Exclusive creation via `os.open(O_CREAT|O_EXCL|O_WRONLY,0o600)`, content = PID. Similarly `BACKUP_DIR` and `.last_backup` marker are cololated with `_DB_DIR` when `DB_PATH` is overridden (otherwise `<BASE_DIR>/backups`).

- **_wait_for_migration_lock(timeout_s=600)** — busy-waits while the lock exists. If the lock is **>600 s stale** (`mtime` >600 s ago) it is removed (crashed migrator). Returns `True` when the caller may proceed, `False` when the lock is *freshly held* at the deadline — caller must NOT start its own migration (same temp files `*.migrating`, `*.enc-new`).
- **Retry loop:** `_ensure_db_encrypted()` tries up to 2 attempts; `FileExistsError("database encryption is already running…")` causes a re-check after the other process finishes. Once another process produces ciphertext, waiting callers see `not _file_is_plaintext` and return without migrating.
- **Migration steps** (crash-safe, verified, atomic swap):
  1. `PRAGMA wal_checkpoint(TRUNCATE)` then WAL-safe plaintext copy via `src.backup(dst)` (count `orig_tables`).
  2. `sqlcipher_export('main','plaintext')`: attach plaintext DB with empty key, export schema+data into fresh keyed DB (`PRAGMA key` + `journal_mode=WAL`), verify `table_count == orig_tables` and `not _file_is_plaintext(tmp_enc)`, `PRAGMA wal_checkpoint(TRUNCATE)` to fold WAL into the main file.
  3. `os.replace(tmp_enc, DB_PATH)`, delete `-wal/-shm` leftovers and `tmp_plain`. On any exception, temps are removed and the **original plaintext file is left intact** (`test_migration_failure_leaves_plaintext_intact`).
- Staleness threshold = timeout = **600 s** — slow-but-alive migrations are waited out, not raced.

## 13. Field-Level Encryption, Backup Safety & Recovery

**Field crypto:**

```python
def encrypt_str(plain: str) -> str:
    if not plain: return ""
    return Fernet(get_fernet_key()).encrypt(plain.encode()).decode()

def decrypt_str(enc: str) -> str:
    if not enc: return ""
    try: return Fernet(...).decrypt(enc.encode()).decode()
    except Exception as e:
        log.warning("cannot decrypt … (key mismatch?): %s", e)
        return ""
```

- Used for `UserSettings.smtp_password_enc, gh_token_enc, ai_api_key_enc` and SMTP/notification paths. Tampered tokens return `""` (not an exception). `github_backup.py:_resolve_config` and `notifications._decrypt` call `decrypt_str`.

**Backups stay ciphertext:**

- `db.py:backup_db` copies via `_raw_connect` (reads ciphertext) + `src.backup(dst)` — the backup file is itself **ciphertext**; `tests/test_crypto.py:test_backup_file_is_encrypted` and `_file_is_plaintext(backup) == False`. GitHub backups upload the raw file (`github_backup.py:_split_file`), never the secret.

**Never upload the secret:**

- `github_backup.py` header: *"The master key is deliberately NOT uploaded — back it up separately."* `crypto.py` module doc echoes: *"The secret is NEVER included in database backups or GitHub backups."*

**Recovery:** if the ciphertext persists but the key is lost → `_ensure_db_encrypted` raises *"key does not match. Set EXPENSE_TRACKER_DB_KEY or restore data/.secret_key from your backup."* Restore the file key or set `EXPENSE_TRACKER_DB_KEY` to the same value before restarting. Wrong keys fail loudly (`test_wrong_key_cannot_open_the_db`) rather than silently corrupting.

**Pragmas on every connection (`_keyed_pragmas`):** `PRAGMA key` (if encrypted), `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000` — applied to engine connections and `_raw_connect` equally.
