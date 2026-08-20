# Caching & Revision — Expense Tracker (queries.py + db.py + github_backup.py)

> How cached readers stay fast (300 s / 120 s TTLs) while mutations become visible instantly across every session and household member.

## 1. Overview & Problem

- Streamlit reruns the script top-to-bottom on every widget interaction. Without caching, every `get_expenses` etc. would hit SQLite on every rerun.
- `st.cache_data(ttl=…)` memoizes readers, but TTL alone is stale: a write in one browser tab/window would be invisible in another tab for up to 300 s.
- **Solution:** a **shared revision counter** in the DB — every cached reader takes `version = db_version()` as a cache key. Bumping the revision *invalidates all cached readers immediately* (new key → cache miss), independent of TTL.

## 2. Architecture — Shared Revision vs TTL

```
write path:  get_session().commit()  →  bump_db_version()  →  st.session_state.db_version = new revision
read path:   key = (user_id, db_version(), …) → st.cache_data lookup → DB only on miss
```

- Revision lives in `users.data_revision: Integer default 0` — one integer per user, read by every session, bumped by every write.
- **Two invalidation signals:** revision (strong, immediate, cross-session) + TTL (weak, per-key expiry for readers that somehow missed a bump). Both are required.

## 3. DB Revision Primitives — db.py

```python
def get_data_revision(user_id: int) -> int:
    with get_session() as s:
        u = s.query(User).filter(User.id == user_id).first()
        return int(u.data_revision or 0) if u else 0

def bump_data_revision(user_id: int, include_household: bool = True) -> int:
```

- **get_data_revision** — single-row read; returns 0 for missing user (safe before login).
- **bump_data_revision** — atomically increments and returns new value. Default `include_household=True` (see §4). Additive migration: `users.data_revision` added via `_add_missing_columns`.
- **Atomicity:** tries `UPDATE users SET data_revision = COALESCE(data_revision,0)+1 WHERE id IN (…) RETURNING data_revision` (single-statement bump+return). On exception (older SQLite without RETURNING) falls back to plain `UPDATE` + separate `get_data_revision` read.
- Member expansion: when `include_household`, reads `User.household_id` then `SELECT * FROM users WHERE household_id = :hid` to collect all member IDs — every member's row is bumped.
- Used as engine-level primitive; never call directly from pages — use `queries.bump_db_version()`.

## 4. Household Propagation — include_household

- `bump_data_revision(user_id, include_household=True)` (default) — when the writer belongs to a household, **all members** share the bump: `ids = [m.id for m in household_members]`. Reads (`db_version()`) for every member then miss their caches.
- `include_household=False` — single-user bump (rare; tests use it to pin increment semantics).
- **Household is experimental:** revision propagation is the *only* household-level invalidation today; do not assume row-level household ACLs. Regression pinned in `tests/test_cache_revision.py:test_household_member_bump_invalidates_all`.

## 5. queries.py — db_version() & bump_db_version()

`queries.py` (186 lines) — cached wrappers + version glue:

```python
def db_version() -> int:
    uid = st.session_state.get("user_id")
    if uid is None: return int(st.session_state.get("db_version", 0))
    return _db_get_revision(int(uid))  # → db.get_data_revision

def bump_db_version() -> int:
    uid = st.session_state.get("user_id")
    if uid is None:
        st.session_state.db_version = int(st.session_state.get("db_version", 0)) + 1
        return st.session_state.db_version
    rev = _db_bump_revision(int(uid))  # → db.bump_data_revision
    st.session_state.db_version = rev
    return rev
```

- **Before login** (`user_id is None`): falls back to a **session-local counter** in `st.session_state.db_version` — no DB read, no cross-session propagation (no user yet).
- **After login:** delegates to DB primitives; also mirrors the new revision into `st.session_state.db_version` (fast local read, but not authoritative — next `db_version()` re-reads the DB so a *second session* sees a first session's bump immediately).

## 6. Cached Readers — st.cache_data Wiring

Every reader is a thin cached trampoline:

```python
@st.cache_data(ttl=300, show_spinner=False)
def _expenses(user_id: int, version: int, include_deleted: bool):
    return get_expenses(user_id, include_deleted)

def expenses(user_id: int, include_deleted=False):
    return _expenses(user_id, db_version(), include_deleted)
```

- Public helpers (`expenses, income, savings, savings_accounts, budgets, recurring, big_purchases, loans, loan_payments, holdings, holding_prices, audit, household_expenses, household_members`) all call `db_version()` at call time — the `version` argument is *only* a cache key, not forwarded beyond the key.

## 7. TTL Strategy — 300 s vs 120 s

| TTL | Readers | Rationale |
|---|---|---|
| **300 s** (5 min) | `_expenses, _income, _savings, _savings_accounts, _budgets, _recurring, _big_purchases, _loans, _loan_payments, _audit, _household_*` | Ledger/budget data changes infrequently; 300 s bounds miss cost if a bump were lost, but revision invalidation makes TTL irrelevant on happy path |
| **120 s** (2 min) | `_holdings, _holding_prices` | Market data / price snapshots refresh more often; shorter TTL keeps prices reasonably fresh even without a manual bump from background price fetch |

- TTL is *defense in depth* — revision invalidation is authoritative. Even if `bump_db_version` were forgotten on a new write path, the worst staleness is TTL.
- `show_spinner=False` — cache hits render instantly.

## 8. Cache Key Design — (user_id, version, …)

- Effective key per reader: `(user_id, version=db_version(), …extra)`.
- Extra discriminators: `include_deleted: bool`, `limit: int` (audit), `loan_id: str`, `household_id: int`.
- **Cross-user isolation:** different `user_id` → different key (no leakage). **Cross-session invalidation:** different `version` → miss; same `user_id` in two tabs shares the *DB-backed* revision, so Tab B's next read misses Tab A's bump even though they have independent `st.session_state`.
- Household propagation makes version bumps shared across member IDs too (distinct keys, but each member's key changes on any member's write).

## 9. Write Path — Always Bump After Commit

**Contract:** every mutation must `bump_db_version()` *after* `get_session()` commits. UI pages that call `db.add_*/update_*/soft_delete*` directly must follow with `queries.bump_db_version()`. Audit logging happens inside the session; bumping happens outside.

```
with get_session() as s:
    s.add(obj)
    log_audit(s, user_id, …)
# commit happens on context exit
bump_db_version()  # ← queries wrapper, bumps DB + session_state
```

- `bump_db_version` is safe pre-login (local counter) and post-login (DB-backed). Forgetting it is the #1 staleness bug — catch in code review.
- `get_data_revision` / `bump_data_revision` use separate connections (not `get_session`) so they work even while another session holds a transaction (SQLite `busy_timeout=5000` + WAL).

## 10. Household — Experimental

> `Household` and household-scoped reads are **experimental beta** (see `data-model.md §4`). Only `users.household_id`, `create_household`, `join/leave_household`, `get_household_*` exist; no row-level household ACL or merge logic.

- **Revision is household-aware:** `bump_data_revision(include_household=True)` bumps all members, so household-aggregate pages (`household_expenses`) invalidate for everyone.
- **Reads are household-scoped:** `household_expenses(household_id)` / `household_members(household_id)` are cached with `version=db_version()` (same shared revision — no extra key needed).
- Do not cache household membership itself without the version key — `household_id` can change via join/leave; membership caches must also take version.

## 11. Settings — Uncached Special Case

```python
def get_settings(user_id: int):
    return _db_get_settings(user_id)  # always fresh

def save_settings(user_id: int, updates: dict):
    _db_save_settings(user_id, updates)
    st.session_state.settings = _db_get_settings(user_id)
    bump_db_version()
    return st.session_state.settings
```

- Settings is one small row — **never cached** (`queries.get_settings` is a direct pass-through). `queries.save_settings` is a **wrapper** that (a) writes via `db.save_settings`, (b) refreshes `st.session_state.settings` snapshot, (c) calls `bump_db_version()` so ledger caches invalidate when rates/currency/budget settings change.

## 12. Local Backups — backup_db & Retention

- **Path:** `backup_db(force=False)` in `db.py:2195` → `BACKUP_DIR = env BACKUP_DIR or <BASE_DIR>/backups` (`tests/conftest.py` overrides to temp dir).
- **SQLite-only, WAL-safe, ciphertext-preserving:** returns `None` when `engine.dialect != "sqlite"` or DB missing; else `src = _raw_connect(DB_PATH)`, `dst = _raw_connect(tmp)`, `src.backup(dst)` (uses raw keyed connections — backup file is already SQLCipher ciphertext).
- **Atomic writes:** copy lands in `<dest>.tmp`, then `os.replace(tmp, dest)`; no stray `.tmp` leftovers (`tests/test_backup.py:test_backup_is_atomic_no_tmp_files`).
- **One-per-day throttle:** marker file `<BACKUP_DIR>/.last_backup` stores `date.today().isoformat()`; non-forced `backup_db()` returns `None` when marker == today. `force=True` always takes a fresh timestamped snapshot (`expense_tracker_YYYY-MM-DD_HHMMSS_<6hex>.db`) and **must capture same-day changes** (regression `test_force_backup_captures_same_day_changes`).
- **Retention:** `BACKUP_RETENTION_DAYS = 30` (from `utils`; fallback 30) — prunes backups where `(today - parsed_date).days > retention` by filename date prefix. GitHub path (`github_backup.py`) has separate retention (14 days, capped 1-90) operating on remote GitHub folders.
- **Marker is best-effort:** failure to write `.last_backup` does not turn a successful backup into an exception.

## 13. Testing, Gotchas & Migration Notes

**Regression suites:**

- `tests/test_cache_revision.py`: three cases pinned — `test_revision_persists_and_increments` (bump returns `r0+1`), `test_second_session_sees_bump_immediately` (Session A bumps, fresh `st.session_state` for same user in Session B reads new revision without TTL wait), `test_household_member_bump_invalidates_all` (`include_household=True` bumps both members). Uses `_SessionState(dict)` shim that mimics `st.session_state` attribute access.
- `tests/test_backup.py`: force-captures-same-day, atomicity, prune-old-files.
- `tests/test_crypto.py:test_fernet_key_file_path_is_used_verbatim` ensures `get_fernet_key` is not double-encoded — relevant because backup file paths and caches are independent of the key derivation choice.

**Gotchas:**

- **Forgot to bump:** new mutation paths that write via `db.*` without `queries.bump_db_version()` stay stale until TTL — add bump or wrap via `queries.*` helpers; grep for `add_|update_|soft_delete|save_settings` without a nearby `bump` in review.
- **Expire-on-commit:** `_get_session_factory(expire_on_commit=False)` — do not revert to `True`; `tests/test_db.py:test_expense_roundtrip_after_session_close` would raise `DetachedInstanceError`.
- **WAL / busy_timeout:** `PRAGMA busy_timeout=5000` + `journal_mode=WAL` lets bump + cached read serialize under concurrent access (background thread + UI thread + sync API). Do not remove pragmas or switch off WAL.

**State paths recap (`app_paths.py`):**

- `state_dir()` = `EXPENSE_TRACKER_DATA_DIR` or (frozen) `%LOCALAPPDATA%/ExpenseTracker` or `<repo>/data` — `DB_PATH`, `BACKUP_DIR`, `data/.secret_key`, `.db-encrypting` lock, and backups all live under this root unless overridden by env (tests override `DB_PATH/BACKUP_DIR`; users may set `EXPENSE_TRACKER_DATA_DIR` or `DB_PATH`).

