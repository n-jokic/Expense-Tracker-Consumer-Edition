# Sync & Household — Connectivity Reference

> **Scope:** Device sync protocol (`api.py` + `sync_core.py`), persistence (`Device`, `SyncConflict`, `Household`), household UI (`app_pages/household.py`), Settings → Sync tab. Covers pairing, push/pull, conflict lifecycle, and shared-household caching.

---

## 1. Purpose & Scope

Device sync lets an offline phone PWA record expenses/income without the web app and later reconcile. Households let 2+ users share a combined budget view (not shared ownership — each row stays owned by one `user_id`). This doc is the contract for:

- Pairing a phone to a user (one-time code → bearer token).
- Pushing client changes and pulling server snapshots (v2 cursor model).
- Detecting, storing, and manually resolving conflicts in Settings → Sync.
- Household create/join/leave, invite-code sharing, and combined reads.

Out of scope: receipt OCR, LLM, payment processing. Sync is labelled **EXPERIMENTAL** in `api.py` and README — expect envelope changes.

## 2. Components & Files

| Component | File | Lines | Role |
|---|---|---|---|
| Sync API | `api.py` | 156 | FastAPI app, `/health`, `/api/pair`, `/api/sync` (v1 deprecated), `/api/v2/sync`, rate limit, `_auth`, `_bump_after_sync` |
| Sync core | `sync_core.py` | 329 | Validation, coercion, conflict check, atomic apply, snapshot |
| DB models & helpers | `db.py` | ~1179 | `Device`, `SyncConflict`, `Household`, `User`, `UserMilestone`, `create_pairing_device`, `complete_pairing`, `device_by_token`, `touch_device_sync`, `get_devices`, `revoke_device`, `add_sync_conflict`, `get_sync_conflicts`, `resolve_sync_conflict`, `apply_record_fields`, `atomic_update_setting_json`, `bump_data_revision`, household CRUD, audit |
| Household UI | `app_pages/household.py` | 158 | Create/join/leave, invite code display + regenerate, members list, combined donut + per-member table |
| Settings Sync tab | `app_pages/settings.py` | 422 (tab_sync excerpt) | Pair-code generation, device list + revoke dialog, conflict resolution (keep device / keep server), `q.bump_db_version` on every mutation |
| Data helpers | `queries.py` / `utils.py` | — | `q.household_members`, `q.household_expenses`, `q.bump_db_version`, `remap_category_subcategory`, `CATEGORIES`/`ALL_SUBCATS` |

## 3. Data Model & State

```python
class Device(Base):  # devices
    id               PK str (uuid4)
    user_id          FK users.id
    name             str default "Phone"
    pairing_code     str | None   # shown once, cleared after complete_pairing
    token_hash       str | None   # sha256(token) — raw token never stored
    token_expires_at datetime | None
    created_at       datetime (utcnow)
    last_sync_at     datetime | None  # cursor — updated by touch_device_sync on every v2 sync

class SyncConflict(Base):  # sync_conflicts
    id           PK int autoincrement
    user_id      FK users.id
    table_name   str   # one of SYNC_MODELS keys
    record_id    str
    device_value JSON  # json_safe(clean) — what device wanted
    server_value JSON  # json_safe(_serialize(server_row)) — what server held
    created_at   datetime
    resolved     bool default False

class Household(Base):  # households
    id          PK int autoincrement
    name        str
    invite_code str unique (e.g. AB12CD34) — regenerated via regenerate_invite_code
    created_at  datetime
    members     relationship User

class User(Base) (relevant fields):
    household_id FK households.id | None
    data_revision int default 0   # cache-bust counter — bumped on writes

class UserMilestone — not sync-scoped, but household gamification reads it via get_earned_milestone_ids.
```

**Syncable tables (`SYNC_MODELS`):** `expenses`, `income`, `savings`, `savings_accounts` — each row PK is a global uuid string.

**Derived state:** `user_settings.sent_markers` is NOT used by sync; sync uses `Device.last_sync_at` as its cursor (v2).

## 4. Flows (End-to-End)

### 4.1 Pairing

```
Web app (Settings → Sync)                    Phone PWA                    API (/api/pair)
  | create_pairing_device(user_id) → (dev_id, code)                         |
  | st.session_state.pair_code = code  "valid for 10 minutes"               |
  | code displayed + Clear button           user types code + device_name    |
  |                                            POST /api/pair {code, device_name}
  |                                                        ──────────────────>|
  |                                           _pair_rate_limited(ip)? 429    |
  |                                           complete_pairing(code, name)   |
  |                                           → token (random), token_hash stored, pairing_code cleared, token_expires_at set
  |                                           device_by_token(token) → {user_id, id}
  |<──────────────────────────────────────── token + user_id                 |
  | Phone stores bearer token (localStorage)                                  |
```

Single-use code; second use of same code → 400. Code expiry enforced by comparing naive vs aware datetimes regression-tested in `tests/test_sync.py::test_pairing_flow_roundtrip`.

### 4.2 Sync push + pull (v2 — current)

```
Phone                          API /api/v2/sync (Bearer token)              sync_core
  | POST {since?, changes[]}  // client since IGNORED in v2                  |
  | Authorization: Bearer <token>                                             |
  |──> _auth(header) → device_by_token → dev {id, user_id, last_sync_at}     |
  |    init_db()                                                              |
  |    since = parse_since(dev.last_sync_at)  // server-issued cursor        |
  |    result = apply_changes(user_id, changes, since) ─────────────────>    |
  |            for ch in changes[:MAX_CHANGES]:                               |
  |              validate_fields(table, fields) → clean/errors               |
  |              if errors → failed                                           |
  |              else _apply_update(user_id, table, id, clean, since)        |
  |                   // ONE session: query scoped to user_id+id             |
  |                   // if server_updated > since AND fields_differ → conflict (add_sync_conflict, not applied)
  |                   // else setattr + log_audit → updated:true             |
  |              if not found → create_record (scoped existence check, uuid remap if foreign id)
  |    _bump_after_sync(user_id, result) → bump_data_revision(include_household=True) if applied|conflicts
  |    touch_device_sync(dev.id) → last_sync_at = now                        |
  |    snapshot(user_id, since) → {expenses, income, savings, savings_accounts} filtered by updated_at > since, ordered, limit SNAPSHOT_LIMIT
  |<── {applied:[{id,table,status,(new_id)}], conflicts:[{id,table}], failed:[{id,table,error}], snapshot, truncated}
  | Phone merges snapshot, records new_ids for remapped creates               |
```

### 4.3 Sync v1 (deprecated)

`POST /api/sync` — identical except `since = parse_since(req.since)` (client-supplied). Left for compat; tests assert v2 uses server cursor regardless of client `since` (`tests/test_api.py::test_v2_uses_server_cursor_not_client_since`). Deprecation warning returned by FastAPI.

### 4.4 Conflict resolution (Settings → Sync)

1. `get_sync_conflicts(user_id, resolved=False)` lists unresolved rows.
2. UI shows two JSON panes: **Device value** vs **Server value**.
3. *Keep device*: `apply_record_fields(user_id, table, record_id, device_value)` then `resolve_sync_conflict(user_id, id)` + `q.bump_db_version()`.
4. *Keep server*: `resolve_sync_conflict(user_id, id)` + bump.
5. Exports include `sync_conflicts` sheet (filtered to unresolved).

### 4.5 Household lifecycle

```
Create: household.py form → create_household(user_id, name) → (hh_id, invite_code); session household_id set; q.bump_db_version() (include_household=True so other members invalidate caches)
Join:   join_household(user_id, code) → lookup households.invite_code; sets user.household_id; bump.
Leave:  _confirm_leave_household dialog → q.bump_db_version() BEFORE leave_household (other members only reachable while still member) → household_id = None.
Regenerate code: regenerate_invite_code(user_id) → new unique code, old invalidated; bump.
Reads:  household_members(hh_id), household_expenses(hh_id) — combined donut + per-member table; caption clarifies "CURRENT members only — expenses logged while member stay on that account".
```

## 5. API / Interface Contract

| Endpoint | Method | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| `/health` | GET | none | — | `{"status":"ok"}` | — |
| `/api/pair` | POST | none (IP rate limited) | `PairRequest{code: str max 20, device_name="Phone"}` | `{"token","user_id"}` | 400 invalid/expired code, 429 rate limited |
| `/api/sync` | POST | Bearer | `SyncRequest{since?: str, changes: Change[] max 500}` | `{applied, conflicts, snapshot}` | 401 missing/invalid token |
| `/api/v2/sync` | POST | Bearer | same (client `since` ignored) | `{applied, conflicts, failed, snapshot, truncated}` | 401, 422 if `>MAX_CHANGES` (pydantic max_length) |

`Change{ table: str, id: str, fields: dict }` — `fields` is validated per-table.

**Headers:** `Authorization: Bearer <raw token>` — token hashed (sha256) in DB; `device_by_token` hashes presented token and checks `token_expires_at`.

**Snapshots:** always include `snapshot.{expenses,income,savings,savings_accounts}` as arrays of serialized rows (dates as ISO strings), plus `truncated: bool` when any table hit `SNAPSHOT_LIMIT`.

## 6. Validation & Caps

| Cap / Rule | Value | Where | Effect |
|---|---|---|---|
| Pair rate limit | 5 attempts / 10 min per IP | `api.py: _PAIR_WINDOW_S=600, _PAIR_MAX_ATTEMPTS=5, _pair_attempts dict + lock` | 429 with "try again in 10 minutes"; dict cleared when >1000 IPs |
| Pair code length | max 20 | `PairRequest.code: Field(max_length=20)` | Rejects oversized codes |
| Max changes per call | 500 | `sync_core.MAX_CHANGES=500` + `SyncRequest.changes max_length` | 422 if >500; slicing `[:MAX_CHANGES]` as defence in depth |
| Snapshot limit | 5000 rows / table | `SNAPSHOT_LIMIT=5000` | `snapshot(limit=...)` caps per-table; truncated flag true if hit |
| String length | 500 | `STR_MAX=500` | `validate_fields` rejects `len>500` for str fields |
| Protected fields | `id, user_id, created_at, updated_at` | `PROTECTED` tuple | Rejected with "is server-managed" |
| REQUIRED_FIELDS | `savings_accounts: (goal_name,)` | `REQUIRED_FIELDS` | Creates without required field → failed, never IntegrityError |
| Pair code validity | ~10 minutes | `create_pairing_device` sets expiry; `complete_pairing` checks | Expired → 400 |
| Token validity | configurable window | `Device.token_expires_at` | Expired → 401 (`tests/test_api.py::test_expired_token_rejected`) |

**Field schemas (`FIELD_SCHEMAS`):** each table maps field → `date|str|float|bool`; unknown fields REJECTED (never dropped). Type coercion: `date` via `date.fromisoformat(str(v)[:10])`, `str` via `str(v)`, `float` via `float(v)` + `math.isfinite` check, `bool` via explicit `("1","true","yes","on")`/`("0","false","no","off")`/`0/1` — `bool("false")==True` is NOT used. `status` enum: `active|closed` for savings_accounts. Category/subcategory validated against `CATEGORIES`/`ALL_SUBCATS` AFTER legacy remap. Business guards: `amount/budgeted/actual/amount/annual_rate` `>0 && <=MAX_AMOUNT` (744h cap for hours), `target_eur/balance_eur <=MAX_SAVINGS_TARGET`, `interest_rate/annual_rate 0..100`, `loan_surcharge_eur >=0 && <=MAX_AMOUNT`, `currency ∈ SUPPORTED_CURRENCIES`, `loan_payment_type ∈ regular|early`. Derived `*_eur` fields are server-recomputed via `to_eur(amount,currency,get_rates(get_settings(uid)))` — client `amount_eur/budgeted_eur/actual_eur/deposited_eur/amount_eur` is overwritten on create/update (validate_fields with rates + _apply_update/create_record recompute). Expenses whitelist now includes `loan_payment_type, loan_surcharge_eur` (INTEGRATION-C-001).

**Legacy remap:** `remap_category_subcategory` rewrites old taxonomy names before validation (e.g. `"Food & Dining"→"Groceries"`, `"Entertainment"→...`), validated by `tests/test_sync.py::test_validate_rewrites_legacy_*`.

## 7. Trust Boundaries & Threat Model

| Boundary | Untrusted → Trusted | Threat | Mitigation |
|---|---|---|---|
| Internet → `/api/pair` | Anonymous IP can try codes | Brute-force enumeration of 6-char codes | Per-IP 5/10min limit, in-memory (+ capped at 1000 IPs). Short codes still brute-forceable locally — codes are random, not sequential. |
| Internet → `/api/v2/sync` | Bearer token holder | Stolen token, forged `since` to skip conflict detection | Token hashed at rest; server-issued cursor (`dev.last_sync_at`) — client `since` ignored in v2. `parse_since` naive-UTC normalisation avoids tz oracle. |
| Sync changes → DB rows | Arbitrary JSON fields | Mass assignment, column injection, cross-account read/update | Whitelist (`FIELD_SCHEMAS`) + `PROTECTED` reject; unknown fields error; record lookup scoped to `(id, user_id)`; missing foreign row → `create_record` with id-remap (see below). |
| Cross-account id probing | Attacker guesses another user's uuid | Existence oracle via error vs remap | `create_record` checks `existing = query(id==rid).first()`; if exists AND `user_id != caller` → fresh uuid, returns `new_id` to caller, never reveals existence nor crashes. Update path scopes to `user_id` — B cannot edit A's row (`tests/test_sync.py::test_cross_user_ids_*`). |
| String bombs / NaN | Huge strings, `NaN/Inf` floats | DB bloat, JSON breakage | `STR_MAX 500`, `math.isfinite` reject, p1. |
| Snapshot exfiltration | Authenticated user pulls all data | Bounded but sensitive | `SNAPSHOT_LIMIT 5000` + auth scoping per user_id; loopback-only deployment via Caddy (see §11). |
| Household invite codes | Anyone with code can join | Code leak → unwanted join | Codes unique (DB index), regeneratable; joining does not expose household expenses beyond combined view. Exports strip `invite_code`. |

## 8. Authentication, Authorization & Secrets

- **Pairing secret:** random code, stored plaintext in `devices.pairing_code` until consumed (cleared on success). Not hashed — short-lived (10 min). Rotation via regeneration is for household codes, not pairing codes (pairing code is one-time).
- **Device token:** random bearer, SHA-256 hashed in `token_hash`; raw token only in API response and on phone. `device_by_token` hashes input and constant-time compares; also checks `token_expires_at`.
- **Device revocation:** `revoke_device(user_id, device_id)` deletes row — immediate 401 on next call; UI via `revoke_device_dialog` (confirm dialog).
- **Authorization scope:** every DB helper takes `user_id` first; queries always include `user_id` filter. No household role — any member can view combined expenses; future: no "admin" vs "member" split (all reads equal).
- **No credential export:** `app_pages/settings.py` Data tab strips `invite_code`, device tokens never appear in exports.

## 9. Concurrency, Atomicity & Ordering

- **Compare-and-update atomicity:** `_apply_update` does `query` → `_norm_dt(updated_at) > since` → `fields_differ` → `setattr + log_audit` inside a single `with get_session() as s:` (commit/rollback scope). No TOCTOU: a concurrent web edit between read and write would be caught on the DB row's `updated_at` only if it happens inside the same session — SQLite WAL + `busy_timeout=5000` serialises.
- **Audit:** every applied create/update emits `log_audit(s, user_id, "CREATE"/"UPDATE", table, id, {...fields, via:"sync"})` inside the same transaction (via field in details → provenance).
- **Idempotency:** creating with an id already owned by same user → `return False` (no duplicate). Caller should handle `status: failed` — phone should generate fresh uuid on next attempt.
- **Ordering:** `snapshot` orders by `updated_at ASC` so clients can apply in causal order; server clock is authoritative.
- **Conflict recording:** `add_sync_conflict` called inside failed-update branch; `device_value` and `server_value` are `json_safe`-converted (dates → ISO strings) — regression-tested for date fields (`tests/test_sync.py::test_conflict_with_date_field_is_json_safe`).
- **Cache invalidation:** `_bump_after_sync` → `bump_data_revision(user_id, include_household=True)` increments `users.data_revision` for user + all household members (if household_id set). Web app pollers compare revision to cached values — no polling misses. Household create/join/leave also bump BEFORE leaving (see §4.5).

## 10. Error Handling, Observability & Audit

| Case | Behaviour | Observability |
|---|---|---|
| Unknown table / missing id | → `failed: {error: "unknown table or missing id"}` | No exception, loop continues |
| Validation error | → `failed: {error: "; ".join(errors)}` — includes field name + reason | Client surfaces per-field |
| IntegrityError on create (NOT NULL missed) | caught → `return False` → `status:"failed"` | Logged, call does not crash whole batch |
| Conflict detected | → `conflicts` + `SyncConflict` row | UI + exports + `get_sync_conflicts` |
| Oversized payload (>500) | FastAPI 422 (pydantic) | Client must chunk |
| Invalid token / missing Bearer | 401 `Missing token` / `Invalid or expired token` | No logging of token value |
| Pairing 429 | Raises HTTPException 429 | In-memory counter, not persisted |
| DB busy (WAL) | SQLite busy_timeout retries 5s | Falls back to 500 on persistent failure |

Audit log (`audit_log` table) records every sync CREATE/UPDATE with `via:sync` for later forensics. Exports include last 10k audit rows.

## 11. Configuration & Deployment Surfaces

- **Run modes:** Web app `streamlit run app.py --server.address 0.0.0.0` (via Dockerfile); Sync API `python api.py` on `0.0.0.0:8502` (separate process, optional TLS via `EXPENSE_TRACKER_TLS=1` + `state_dir()/certs/cert.pem + key.pem`).
- **Docker:** `compose.yaml` exposes Streamlit only on `127.0.0.1:8501`; `api.py` not in compose — run manually or extend compose with port mapping. Caddy (`Caddyfile`) reverse-proxies `expenses.example.com → app:8501` with auto-TLS; 8502 must be separately reverse-proxied if exposed (not in default Caddyfile — keep internal or add route).
- **DB path:** `DB_PATH = env(DB_PATH) or state_dir()/expense_tracker.db`; tests override before import. `DATABASE_URL` → Postgres via SQLAlchemy (SQLCipher off). SQLite WAL + FK + busy_timeout + encryption key pragma on every connection (`_keyed_pragmas`).
- **EXPERIMENTAL flag:** documented in `api.py` header and Settings Sync caption — "under active development — see README for caveats." Treat sync envelope as unstable; version via `/api/v2/sync` path.
- **Health:** `GET /health` for probes; Dockerfile HEALTHCHECK hits `/_stcore/health` not sync API.

## 12. Tests & Verification

| Suite | Command | What it proves |
|---|---|---|
| `tests/test_sync.py` (22 tests) | `pytest tests/test_sync.py -v` | `parse_since`, `coerce_fields`, `fields_differ`, create/update/conflict/failed paths, legacy remap, cross-account isolation, conflict JSON safety, pairing roundtrip, validation rejects, id remap, snapshot truncation |
| `tests/test_api.py` (8 tests) | `pytest tests/test_api.py -v` | health, pair success/single-use/junk, rate limit 429, 401 on missing/bogus/expired token, create+snapshot, v2 cursor ignores client `since`, payload cap 422, unknown field rejected |
| Household | `pytest tests/test_db.py -k household -v` | household create/join/leave/regenerate (via db layer) |
| Manual | `python api.py & curl -H "Authorization: Bearer …" POST /api/v2/sync` | Live token + snapshot truncated flag |

Run full gate: `pytest tests/test_sync.py tests/test_api.py -q --tb=short`.

## 13. Pitfalls, TODOs & Guidance for Agents

**Do:**

- Validate every new syncable column by adding it to `FIELD_SCHEMAS` with correct type + STR_MAX + enum checks — otherwise `validate_fields` will reject silently from client perspective.
- Remap legacy category names in `validate_fields` before enum check, and extend `TAXONOMY_MIGRATION` + `_migrate_taxonomy` together.
- Scope every sync query to `user_id`; use `create_record` remap pattern for id collisions.
- Call `bump_data_revision(include_household=True)` after any mutation that household reads depend on; include `q.bump_db_version()` in UI after household changes.
- Use `json_safe` on any JSON field going into `SyncConflict` — dates crash JSON without ISO conversion.
- Keep v1 endpoint deprecated but not removed until phone PWA ships v2-only.

**Don't:**

- Don't trust `req.since` in new code — v2 uses `dev.last_sync_at` only.
- Don't drop unknown fields — REJECT with an explicit error (security contract).
- Don't store raw bearer tokens in exports, logs, or session state.
- Don't bump revision AFTER leaving household — other members become unreachable once `household_id` cleared.
- Don't assume pairing codes are hashed — they are transient plaintext but short-lived.
- Don't increase `MAX_CHANGES`/`SNAPSHOT_LIMIT` without measuring SQLite WAL contention on low-RAM hosts.

**TODO / Known gaps:**

- Pair rate limiter is in-memory — restarts reset counters; no distributed limit behind multiple replicas.
- Invite codes are random but short — consider entropy audit if households become public.
- Sync is not end-to-end encrypted — bearer tokens over plaintext HTTP leak if not behind TLS (require `EXPENSE_TRACKER_TLS` or Caddy).
- `SyncConflict` has no expiry — long-lived unresolved conflicts accumulate; add prune or auto-resolve later.
- Soft deletes (`is_deleted`) are synced as normal field updates — ensure phone PWA respects tombstone semantics.