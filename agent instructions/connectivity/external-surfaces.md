# External Surfaces — Connectivity Reference

> **Scope:** Every outward-facing surface that syncs or exposes data outside the web process: MCP server (`mcp_server.py`), GitHub encrypted backup (`github_backup.py`), and the hosting edge (`Dockerfile` + `compose.yaml` + `Caddyfile`). Trust-boundary maps, auth models, and deployment caps.

---

## 1. Purpose & Scope

These surfaces let *other systems* touch the otherwise local, SQLCipher-encrypted SQLite DB:

- **MCP server** — local AI agents (OpenClaw, Claude Desktop, etc.) read live data and log expenses/income audited.
- **GitHub backup** — ciphertext DB off-site to a free private GitHub repo, chunked + manifest-verified.
- **Hosting edge** — Docker + Caddy expose only the Streamlit app (loopback-bound) with automatic TLS, keeping the DB and sync API off the public surface unless explicitly proxied.

Plus: what is NOT exposed (holdings history, raw DB key, tokens).

## 2. Components & Files

| Surface | File | Lines | Entrypoint |
|---|---|---|---|
| MCP server | `mcp_server.py` | 574 | `python mcp_server.py` (stdio) or `--http --port 8510` → `server.run(transport="streamable-http", host="127.0.0.1")` |
| GitHub backup | `github_backup.py` | 457 | `github_backup.py list|restore|test --user NAME` CLI; `maybe_auto_backup` called from `app.py` on page load; `run_github_backup(user_id)` from Settings "Back up to GitHub now" |
| Dockerfile | `Dockerfile` | 26 | `FROM python:3.12-slim` + tesseract-ocr, `useradd appuser`, `HEALTHCHECK /_stcore/health`, `CMD streamlit run app.py` |
| Compose | `compose.yaml` | 30 | `app` loopback `127.0.0.1:8501:8501`, `caddy` on 80/443, `app-data` volume |
| Caddyfile | `Caddyfile` | 11 | `expenses.example.com { reverse_proxy app:8501; encode gzip }` |
| Crypto shim | `crypto.py` | — | `encrypt_str/decrypt_str` (Fernet) for `gh_token_enc`, `sqlcipher_key_pragma` for DB-at-rest |
| DB backup shim | `db.py` | — | `backup_db(force=True) → local ciphertext path | None`, `get_engine`, `get_session` |

## 3. Data Model & State

**On disk (host):**

- `state_dir()/expense_tracker.db` — SQLCipher ciphertext (WAL mode, key from master secret `state_dir()/.secret_key` or `EXPENSE_TRACKER_DB_KEY`). Header is NOT `SQLite format 3\x00` but random salt; verified by `_file_is_plaintext` check and key-test on open.
- `state_dir()/backups/expense_tracker_*.db` — local WAL-safe snapshots (checkpoint + `sqlite3.backup`).
- `state_dir()/.db-encrypting` — migration lock file if plaintext→encrypted conversion in progress.

**GitHub remote:**

- `backups/YYYY-MM-DD/{<dbfile>.part001 … .partNN, <dbfile>.manifest.json}` — parts are `CHUNK_SIZE=50 MB` raw ciphertext concatenated; manifest last written, contains `{version, original_name, original_size, db_sha256, parts:[{file, sha256}]}` (all SHA-256 hex).

**MCP in-memory:**

- `_USER_ID: int | None` cached after first `_resolve_user`; `MCP_USERNAME = env(EXPENSE_TRACKER_MCP_USERNAME) or None`; `server = MCPServer(name="expense-tracker", version="4.0", title="Expense Tracker")`.

**Settings (UserSettings) for GitHub:**

- `gh_repo` `owner/name` (stripped), `gh_token_enc` Fernet ciphertext, `gh_backup_enabled` bool, `gh_retention_days` 1–90 (default 14), `gh_last_backup_at` datetime, `gh_last_status` ok|error, `gh_last_error` ≤500 chars.

## 4. Flows (End-to-End)

### 4.1 MCP stdio (recommended, OpenClaw-local)

```
OpenClaw (local)                          mcp_server.py                         SQLite (SQLCipher)
  | spawns: python mcp_server.py (stdio)     init_db(); _resolve_user() → user_id
  | JSON-RPC over stdin/stdout                                          |
  |  tools/list → server registers 14 tools (10 read, 2 write, 2 meta) |
  |  tools/call {name:"list_expenses", arguments:{month:"current"}}     |
  |                                      ──────────────────────────────>|
  |                                      uid = _resolve_user()           |
  |                                      df = get_expenses(uid)         |
  |                                      _in_month / filter / _records   |
  |                                      _clean (NaN→None, dates→ISO)   |
  | <─────────────────────────── {ok:true, count, total_eur, expenses:[...]}|
  |  tools/call {name:"add_expense", arguments:{amount:12.5, category:"Groceries", description:"Milk"}}
  |                                      validations (amount finite<=MAX_AMOUNT, cat∈CAT_LIST, sub∈CATEGORIES[cat], desc non-empty ≤500, currency∈SUPPORTED) |
  |                                      rates = _user_rates() (get_rates from that user's settings)
  |                                      ae = to_eur(amt, cur, rates)   |
  |                                      db_add_expense(uid, {via:"mcp", ...}) → id + log_audit(via:mcp) |
  |                                      bump_data_revision(uid)        |
  | <─────────────────────────── {ok:true, id, amount_eur, date, message} |
```

### 4.2 MCP HTTP (streamable-http, localhost-only)

`python mcp_server.py --http --port 8510` → `server.run(transport="streamable-http", host="127.0.0.1", port=8510)`. Same tools, transport is HTTP but bound to loopback — any local process can call it; no auth layer beyond local trust.

### 4.3 GitHub backup (manual or auto)

```
Trigger: Settings "Back up to GitHub now" / maybe_auto_backup(user_id, settings) on each page load
  | run_github_backup(user_id)                                     |
  | _lock.acquire(blocking=False) else return {status:"skipped"}   |
  | _resolve_config(user_id) → (repo, token=decrypt_str(gh_token_enc))
  |   validate repo contains "/" and non-empty parts → else RuntimeError "owner/name"
  | _default_branch(token, repo) → GET /repos/{repo} → default_branch or error |
  | local = backup_db(force=True) → fresh ciphertext path | None → else error "Local backups unavailable"
  | _split_file(local) → parts=[(name, bytes)] size CHUNK_SIZE, manifest={db_sha256, parts_sha256}
  | day = date.today().isoformat(); base = basename(local); stamp = now "%Y-%m-%d %H:%M"
  | for name, blob in parts:  _put_file(token, repo, "backups/{day}/{name}", blob, branch, msg)  // PUT /repos/{repo}/contents/{path} base64-encoded
  | _put_file(token, repo, "backups/{day}/{base}.manifest.json", json.dumps(manifest), branch, msg) // LAST — folder only "restorable" once manifest exists
  | retention = clamp(int(settings.gh_retention_days or 14), 1, 90)
  | _prune_old(token, repo, branch, retention) → lists backups/ prefix, deletes day-folders with name < cutoff (YYYY-MM-DD) and valid date
  | _record_status(ok) → save_settings {gh_last_status:"ok", gh_last_error:None, gh_last_backup_at: now}
  | return {status:"ok", backup:base, parts, pruned_files, repo}
  | finally: _lock.release
  On exception: _record_status(error, msg[:500]), log.warning, return {status:"error", message}
```

**Auto path:** `maybe_auto_backup(user_id, settings)` — if `gh_backup_enabled` and `now - gh_last_backup_at ≥ 24h` (or never ran), spawns `Thread(target=run_github_backup, daemon=True)` — never blocks UI.

**CLI restore:**

```
github_backup.py list [--user NAME]     → enumerates backups/<day>/*manifest.json
github_backup.py restore <stamp> [--user NAME] [--out PATH] [--replace]
  _find_manifest(token, repo, branch, stamp_prefix) → fetches manifest blob: GET {url} → base64 decode JSON
  _download(token, repo, branch, day, manifest) → for each part: GET /repos/{repo}/contents/backups/{day}/{file}?ref={branch}
    if payload.content present → base64 decode; elif download_url → GET raw (for >1 MB parts); _merge_parts verifies part SHA-256 + whole db_sha256
  write to out (default BACKUP_DIR/restored_<name>) atomically via .tmp then os.replace
  if --replace: _guard_replace (checks -wal/-shm not present), then _replace_db (keeps pre_restore_... copy, os.replace onto DB_PATH)
```

### 4.4 Hosting edge (Docker + Caddy)

```
Internet → Caddy (80/443, auto TLS) → reverse_proxy app:8501 (Streamlit)
         compose network only — app port 8501 is 127.0.0.1:8501 on host, not internet-reachable
         Sync API 8502 + MCP HTTP 8510 are NOT in compose / Caddyfile by default — keep internal or add explicit route
         app-data volume holds SQLCipher DB + state_dir
```

## 5. API / Interface Contract

| Surface | Transport | Entry | Auth | Reads | Writes |
|---|---|---|---|---|---|
| MCP stdio | stdin/stdout JSON-RPC | `server.run(transport="stdio")` | none — local process trust | `expense_summary, list_expenses, search_expenses, list_income, list_budgets, list_savings_goals, list_recurring_bills, list_loans, get_milestones, get_insights, ask_data` | `add_expense`, `add_income` (audited) |
| MCP HTTP | streamable-http on 127.0.0.1:8510 | `--http --port N` | none — loopback trust | same | same |
| GitHub | `requests` → `GH_API=https://api.github.com` | `_api(token, method, url)` | `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` | `_default_branch, _list_dir, _find_manifest, _download` | `_put_file (PUT /contents/{path})`, `_delete_file (DELETE /contents/{path})` for prune |
| Streamlit | HTTP via Caddy | `expenses.example.com` | session auth (password hash) in `auth.py` | app pages | mutations via UI |
| Sync API | HTTP 0.0.0.0:8502 | `python api.py` | Bearer device token | snapshot | apply_changes |

**MCP tool shapes (all return `{"ok": true/false, ...}`):**

- `expense_summary(month="current"|"last"|"YYYY-MM") → {month, spent_eur, income_eur, net_eur, budget_total_eur, budget_remaining_eur, top_category, fun_money_eur, monthly_budget_eur}`
- `list_expenses(month, category?, limit capped 500) → {count, total_eur, expenses:[{date,category,subcategory,description,amount,currency,amount_eur,notes}]}`
- `search_expenses(query, limit capped 100) → {count, expenses:[...]}` — case-insensitive contains over description/category/subcategory/notes; empty query → `{ok:false, error:"query must not be empty"}`
- `list_income(month) → {count, total_eur, income:[...]}`
- `list_budgets() → {count, budgets:[{year,month,category,subcategory,budgeted_eur}]}`
- `list_savings_goals() → {goals:[{goal_name,balance_eur,target_eur,interest_rate_pct}], term_deposits:[...]}`
- `list_recurring_bills() → {count, bills}`, `list_loans() → {count, loans}`, `get_milestones() → {count, milestones:[{id, ...MILESTONE_INDEX}]}`
- `get_insights() → {spending_mom, income_mom, top_category_this_month, unusual_expenses (head 5), days_until_budget_depleted?, narrative?}` — AI narrative optional, gracefully missing
- `ask_data(question) → {ok, answer} or {ok:false, error:"AI assistant is not configured..."}` — read-only free-form over own data
- `add_expense(amount, category, description, date?, subcategory?, currency?) → {ok, id, amount_eur, date, message} or {ok:false, error}`
- `add_income(amount, income_type?, date?, currency?, notes?) → {ok, id, amount_eur, date, message}`

All records passed through `_clean` (NaN→None, np scalars unwrapped, dates→ISO, recusive). Limit params clamped via `max(1, min(int(limit), 500/100))`.

## 6. Validation & Caps

| Surface | Cap / Rule | Value | Notes |
|---|---|---|---|
| MCP add_expense amount | `>0 and <= MAX_AMOUNT and finite` (bool rejected) | MAX_AMOUNT from utils (e.g. 1e9) | NaN/Inf → ValueError, returned as `{ok:false, error}` |
| MCP category | `∈ CAT_LIST` (current taxonomy) | e.g. Groceries, Housing & Utilities | Unknown → error with valid list |
| MCP subcategory | empty or `∈ CATEGORIES[cat]` | after legacy remap | Wrong pair → error with valid subs |
| MCP description | non-empty, len ≤500 | — | Empty → error; >500 → error |
| MCP currency | `∈ SUPPORTED_CURRENCIES` | EUR, USD, RSD, ... | Upper-normalised |
| MCP date | `"YYYY-MM-DD" / today / yesterday / None` | — | Other format → ValueError |
| MCP list limits | expenses capped 500, search capped 100 | via `head(...)` | Prevents oversized JSON-RPC response |
| GitHub part size | `CHUNK_SIZE = 50 * 1024 * 1024` (env `GH_TEST_CHUNK_SIZE` for tests) | 50 MB | Stays safely under GitHub 100 MB per-file hard cap; base64 overhead avoided (raw Content API uses base64 but chunks small enough) |
| GitHub retention | 1–90 days after clamp (`max(1, min(input, 90))`), default 14 | `_prune_old` cutoff = today - retention | Invalid int → fallback 14 |
| GitHub manifest | SHA-256 per part + whole DB | `_merge_parts` verifies; `db_sha256` mismatch → RuntimeError "Database checksum mismatch" | Missing part → RuntimeError |
| GitHub lock | `threading.Lock non-blocking` | one upload at a time | Concurrent `run_github_backup` → skipped, not queued |
| GitHub error | message truncated 500 chars | `gh_last_error[:500]` | Stored via `save_settings` |
| Docker non-root | `useradd appuser`, `USER appuser`, /app writable | — | DB + state written as appuser |
| Compose volumes | `app-data:/app/data`, `caddy_data:/data` | — | State persists across restarts |
| HTTP binding | Streamlit `127.0.0.1:8501`, MCP HTTP `127.0.0.1:8510` | — | Not internet-reachable without Caddy route |
| Caddy | `encode gzip`, auto TLS on 80/443 | — | No rate limiting by default — add `rate_limit` directive if needed |
| MCP month parsing | "current"/"last"/"YYYY-MM" only | `_month_bounds` | Invalid → ValueError returned as ok:false |
| Local backups | WAL checkpoint before `sqlite3.backup` | atomic local snapshot | If not SQLite (DATABASE_URL set) → backup_db returns None → "Local backups unavailable" |

## 7. Trust Boundaries & Threat Model

| Boundary | Threat | Severity | Mitigation |
|---|---|---|---|
| MCP caller → DB (local agent is fully trusted) | Local agent (or malware) logs abusive expenses/income, exfiltrates all data | High if host shared | **Documented trust model:** stdio assumes local machine is single-user/trusted; HTTP variant explicitly "trusts every local process — only enable when acceptable." No ACL per agent — any local caller gets full read + two writes on the resolved account. Mitigate by: stdio default, loopback-only HTTP, MCP_USERNAME scoping to one account, audit trail (`via:mcp`), data_revision bump (browser sessions notice immediately). |
| MCP loopback → network | User forwards 8510 to Internet, exposing data | High | Default port not in compose/Caddyfile; README warns "stdio recommended — see README for exact commands." No network auth — loopback is sole defence. |
| GitHub PAT → GitHub API | PAT leaks (logs, screen share, backup repo public) | Critical | Token stored `gh_token_enc` Fernet-encrypted; never in exports (`settings` sheet pops `gh_token_enc`); never logged; `User-Agent: Expense-Tracker-Backup`. PAT should be fine-grained scoped to single private repo, contents:write only. |
| DB ciphertext → GitHub | Ciphertext upload still sensitive (offline brute-force against weak master secret) | Medium | Ciphertext is SQLCipher (random salt, key from master secret). Master secret (`data/.secret_key`) is NOT uploaded — "back it up separately" (README). Weak/empty key → brute-force feasible — advise strong auto-generated key. |
| DATABASE_URL → GitHub | Postgres URL not encrypted — backup leaks nothing if using Postgres | Low | Comment in `github_backup.py`: DATABASE_URL not encrypted — backup path returns None on non-SQLite so nothing uploaded; local backup is only for SQLite. |
| Github repo visibility | User creates public repo → ciphertext world-readable | Medium | Docs say "free private GitHub repository" repeatedly; no code enforces — user responsibility. |
| GitHub backup swap | Partial upload visible → corrupt restore | Low | Parts written before manifest; manifest written LAST → backup folder only restorable once manifest exists; SHA-256 per part + whole file verified on restore. |
| MCP amount injection | Opportunistic oversized amount exhausts storage | Low | `MAX_AMOUNT` finite cap + per-field validation; DB column is Float not unbounded text. |
| MCP category spoof | Invalid category breaks downstream taxonomy migrator | Low | Server-side whitelist + legacy remap reject — never persisted. |
| Streamlit → Caddy | Caddy misconfig exposes 8501 directly to Internet | Medium | `compose.yaml` binds `127.0.0.1:8501:8501`; Caddy is only public entrypoint. Verify `docker compose ps` shows 127.0.0.1 binding. |
| API 8502 → Internet | Same — sync API not in compose, accidental `docker run -p 8502:8502` exposes bearer tokens | Medium | Keep 8502 internal or front with Caddy TLS + IP allowlist; bearer tokens over plaintext HTTP leak. |

## 8. Authentication, Authorization & Secrets

- **MCP:** no credential — identity is `MCP_USERNAME` (env) or "first account by id ASC" (fallback). Resolution fails fast before serving if user not found. Writes audit as `via:mcp` + bump so other sessions refresh.
- **GitHub:** fine-grained PAT (classic not recommended) with contents write; encrypted at rest. Transport is Bearer + GitHub API versioning; repo determined from `gh_repo` or env `GH_REPO`/`GH_TOKEN` for CLI. Branch via `GET /repos/{repo}` (default branch). Content encoding: body `{message, content: base64(blob), branch}` for puts; base64 decode + SHA-256 on restore. Concurrent uploads serialised by process-local lock.
- **Streamlit/Caddy:** Streamlit session auth via hashed password (`auth.py: hash_password`); Caddy terminates TLS (auto-certificate) then proxies plaintext to app over compose network.
- **SQLCipher key:** not per-surface — one master secret encrypts DB, Fernet tokens, and backup ciphertext key. Stored `data/.secret_key` mode 0600; alternative `EXPENSE_TRACKER_DB_KEY` env.

## 9. Concurrency, Atomicity & Ordering

- **MCP:** each tool call is sequential within JSON-RPC; no internal lock — SQLAlchemy session per call. Two concurrent agents calling `add_expense` both succeed (separate ids) and each bumps revision.
- **GitHub:** `_lock.acquire(blocking=False)` — second caller returns immediately `skipped` (not queued). Auto-trigger (`maybe_auto_backup`) spawns daemon thread — fire-and-forget; manual trigger same path but from Settings thread named `gh-backup-manual`. Upload order: parts first (any order) then manifest last — atomicity by convention (manifest existence = completeness).
- **Restore ordering:** manifest discovery iterates `backups/` dirs alphabetically, then manifests sorted — newest by timestamp prefix matching (`stamp_prefix` argument). Large (>1 MB) file fallback: if `payload.content` missing but `download_url` present → GET raw (GitHub omits content for large files). SHA-256 verified twice.
- **Hosting:** no DB concurrency beyond SQLite WAL + global engine; multiple writers (web, MCP, sync API) share WAL with `busy_timeout=5000`.

## 10. Error Handling, Observability & Audit

| Surface | Failure | Handling | Observability |
|---|---|---|---|
| MCP read | empty table | returns `{ok:true, count:0, ...[]}` | no error |
| MCP write validation fail | ValueError | → `{ok:false, error: str(e)}` (never raises to client) | error string lists valid categories/currencies |
| MCP unknown user | `_resolve_user` raises RuntimeError | startup fails "No account named …" or "No accounts exist yet…" | clear message before serving |
| MCP insights with no budget | returns object without `days_until_budget_depleted` | graceful | — |
| GitHub: no repo/code | raise RuntimeError "GitHub backup is not configured (no repository)" or "must look like owner/name" | caught → `{status:"error", message}` + `_record_status("error", msg)`; no upload | `gh_last_status: error`, `gh_last_error` shown in Settings |
| GitHub: 401/4xx | `_check(resp)` raises RuntimeError "GitHub API {code}: {msg}" | same + logged warning | Settings shows last error |
| GitHub: DB not SQLite | `backup_db` returns None | RuntimeError "Local backups are unavailable" | error recorded |
| GitHub: lock held | non-blocking acquire fails | `{status:"skipped", message:"A backup is already running."}` | no error recorded |
| GitHub: stale lock / Windows file in use | `_replace_db` catches PermissionError → RuntimeError "Stop the app and the sync API first" | keeps pre-restore copy; live DB untouched | CLI prints error |
| Docker health | Streamlit unreachable 30s interval | HEALTHCHECK fails; compose `restart: unless-stopped` | `docker inspect` shows health |
| Caddy | upstream app down | 502 Bad Gateway | Caddy logs |

**Audit:** MCP writes call `log_audit(s, user_id, "CREATE", "expenses"|"income", id, {via:"mcp", ...})` inside the same session as the insert. GitHub status persisted in `UserSettings` (last attempt always recorded). Sync API audit also logs (`via:sync`).

## 11. Configuration & Deployment Surfaces

```yaml
# compose.yaml
services:
  app:
    build: .
    ports: ["127.0.0.1:8501:8501"]
    volumes: [app-data:/app/data]
    environment: [ALLOW_REGISTRATION]  # false by default; registration off
    restart: unless-stopped
  caddy:
    image: caddy:2-alpine
    ports: ["80:80","443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro", caddy_data:/data]
    restart: unless-stopped
```

**Dockerfile:** `python:3.12-slim`, `tesseract-ocr` for receipt OCR, non-root `appuser`, `/app/data` owned.

**Caddyfile:** one site `expenses.example.com { reverse_proxy app:8501; encode gzip }` — replace domain; Caddy manages TLS + 80→443 redirect automatically. Add sync/MCP routes explicitly if needed (not included).

**Env vars:** `EXPENSE_TRACKER_DB_KEY` or `data/.secret_key` (master secret), `EXPENSE_TRACKER_NO_ENCRYPT=1` (opt out SQLCipher), `DATABASE_URL` (Postgres alternative), `ALLOW_REGISTRATION`, `GH_TEST_CHUNK_SIZE` (tests), `MCP_USERNAME / EXPENSE_TRACKER_MCP_USERNAME` (MCP).

**Manual commands:**

```bash
# MCP stdio (recommended)
python mcp_server.py
# MCP HTTP (local only)
python mcp_server.py --http --port 8510
# GitHub
python github_backup.py list --user alice
python github_backup.py restore 2026-08-16_143022 --user alice --out ./restored.db
python github_backup.py restore 2026-08-16_143022 --replace  # guard requires WAL cleared
python github_backup.py test --user alice  # branch resolution check
```

## 12. Tests & Verification

| Suite | Command | Coverage |
|---|---|---|
| MCP server | `pytest tests/test_mcp.py -v` | month/date parsing, user resolution (env/default/missing), read tools (summary/lists/search/budgets/savings/bills/loans/milestones/insights+budget), ask_data no-provider error, write validation (unknown cat/sub, bad amount, empty desc, >500, unknown currency), audit + bump_data_revision on write, insight reuse with budget, error wrapped as `{ok:false}` |
| GitHub backup | `pytest tests/test_github_backup.py -v` (18) | split/merge roundtrip, gap check, SHA-256 mismatch, manifest wrapper regression, missing manifest, >1 MB download_url fallback, chunk size wiring, repo shape rejection, 401 error recording, encrypted token storage, concurrent skip lock, find_manifest+download roundtrip, missing-raises, file-in-use guard keeps original, auto-backup gates (disabled / <24h skip / >25h run / string timestamp) |
| Hosting smoke | `pytest tests/test_app_smoke.py -v` etc. | app loads, DB init, no crash |
| Manual MCP | `python mcp_server.py` then JSON-RPC via stdio client | live read/write + revision bump visible in app |
| Manual GitHub | Configure Settings → GitHub → Save + "Back up to GitHub now" → check Settings caption + repo `backups/YYYY-MM-DD/` | ciphertext parts + manifest last + retention prune |
| Manual Docker | `docker compose up -d && curl http://127.0.0.1:8501/_stcore/health` | 8501 loopback reachable, 8502 not |
| Manual TLS | `python api.py` with `EXPENSE_TRACKER_TLS=1` + certs present → https on 8502 | cert/key loading from `state_dir()/certs` |

Run combined: `pytest tests/test_mcp.py tests/test_github_backup.py -q --tb=short`.

## 13. Pitfalls, TODOs & Guidance for Agents

**Do:**

- Add new MCP read tools by copying the pattern: `_resolve_user` → scoped query → `_records` → `_clean` → `{"ok":true, ...}`; handle empty→`[]` not error.
- Guard new MCP writes with the same validation tower: `MAX_AMOUNT`, `CATEGORIES`, `SUPPORTED_CURRENCIES`, `_parse_date`, amount finite checks, `bump_data_revision` + `log_audit(via:mcp)`.
- For new GitHub-exposed settings, add the key to the export strip list in `app_pages/settings.py` Data tab.
- Keep MCP HTTP bound to `127.0.0.1` — never `0.0.0.0` without an explicit auth layer.
- Use fine-grained PAT scoped to single repo; rotate by re-saving `gh_token_enc` in Settings.
- Verify `_resolve_config` shape check (`"/" in repo and all parts non-empty`) when adding new repo-like inputs.
- After adding a new holding field, update `.snapshot.json` value precomputation so historic EUR values stay stable.

**Don't:**

- Don't add MCP tools that mutate budgets, holdings, or settings directly — current surface is intentionally narrow (2 writes).
- Don't log `gh_token_enc` decrypted value or put it in error messages.
- Don't upload plaintext DB even "temporarily" — ciphertext only; key never leaves host.
- Don't change `CHUNK_SIZE` above 50 MB without testing against GitHub's 100 MB hard cap + base64 size growth.
- Don't bypass `_merge_parts` SHA-256 on restore — that's the only integrity check against GitHub bit rot.
- Don't expose `Device.token_hash` or raw tokens via any new surface.

**TODO / Known gaps:**

- MCP has no authorization beyond "local process" — a malicious local extension can read all categories and log fake expenses. Future: per-tool allowlists or user confirmation gate.
- GitHub backup has no encryption-at-rest verification beyond SHA-256 — master secret strength not checked.
- Compose has no `api` service — sync API must be run manually or added as a separate service with its own Caddy route + TLS.
- Retry for market or billing emails is synchronous on next page load — no circuit breaker if SMTP provider is down for hours.
- Holding snapshot `quantity`/`rate` frozen at refresh time is correct for history, but the daily snapshot row for today keeps appending if `refresh_prices_if_due(force=True)` called repeatedly — add daily dedupe if schedule tightened.
