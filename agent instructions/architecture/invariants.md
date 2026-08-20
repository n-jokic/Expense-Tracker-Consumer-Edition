# Invariants — Rules That Must Stay True

> **Scope:** global + cross-domain invariants. Subsystem-local invariants live in their own docs (linked). Violating any rule here silently corrupts data or security.

---

## 1. Global Invariants (span ≥2 domains)

| # | Invariant | Why brittle | Where enforced |
|---|-----------|-------------|----------------|
| G1 | **User-scoped every read/write:** `db.*(user_id, ...)` filters `user_id` in the WHERE clause; `queries.*(user_id, version)` keys on user_id; MCP resolves via `EXPENSE_TRACKER_MCP_USERNAME` or first user | Forgetting the predicate leaks cross-user data (household aggregate is explicit opt-in via Household members) | `db.py: all get/add/update`, `queries.py`, `sync_core` (user-scoped existence check, no oracle), `mcp_server._resolve_user` |
| G2 | **Bump on every successful write:** `q.bump_db_version() → db.bump_data_revision(user_id, include_household=True)` after each `add/update/delete/restore/save_settings` | Stale `st.cache_data ttl 120-300` in other sessions/household; budgets/alerts read old expenses | Every write page, `sync_core atomic apply`, `mcp_server add_expense/add_income`, `api sync apply`, `github_backup` via queries |
| G3 | **Dual amount storage:** original `amount+currency` + `amount_eur` (`to_eur` at write, `fmt` at read) — rates at read time only affect display | Changing conversion direction mutates history; EUR aggregates (budget progress, forecast) depend on _eur | `utils.to_eur/to_display/fmt`, every `add_expense/add_income` |
| G4 | **SQLCipher pragma on every connection:** `PRAGMA key = sqlcipher_key_pragma()` + `journal_mode WAL + foreign_keys ON + busy_timeout 5000` | Open a raw `sqlite3` without pragmas → DB appears corrupted; WAL needed for concurrent Streamlit reruns | `db._keyed_pragmas` called from `_raw_connect` + engine connect event |
| G5 | **Master secret never leaves device:** `data/.secret_key` + `DB_PATH` ciphertext upload via GitHub manifest excludes secret; Fernet tokens remain encrypted at rest | Uploading secret to GitHub or including in export destroys encryption boundary | `crypto.py`, `github_backup._put_file` manifest, `db.BACKUP_DIR` note |
| G6 | **Taxonomy remapped on read, write, and wire:** legacy `Food & Dining→Groceries/Dining Out` etc. remapped in `db._migrate` at startup, `sync_core.validate_fields` on sync push, and on import normalization | Adding a category requires updating all three sites or legacy rows desync | `utils.TAXONOMY_MIGRATION + remap_category_subcategory`, `db._migrate`, `sync_core`, `bank_import` |
| G7 | **Sync field whitelist + caps:** `FIELD_SCHEMAS` rejects unknown fields (not Drop), `MAX_CHANGES 500`, `SNAPSHOT_LIMIT 5000`, `STR_MAX 500`; `since` is server-issued `last_sync_at` not client-chosen | Client could inject columns or bypass conflict detection with future since | `sync_core.validate_fields + coerce_fields`, `api.SyncRequest` Pydantic max_length |
| G8 | **Subcategory sentinel `""`:** UI "—" ↔ stored `""` — bare category budgets use `subcategory=""` and `UniqueConstraint(user,year,month,cat,subcat)` | Storing "—" breaks budget unique constraint and join logic | `log_expense: subcat if != "—" else ""`, `db.Budgetuq`, `recurring` |
| G9 | **No client-trusted clock:** `_throttled` window, rate staleness (`3d`), price staleness (`1d`), budget period all use server `date.today() / datetime.now(timezone.utc)` | Client clock skew would bypass throttles and alert dedupe | `auth._throttled`, `rates.rates_are_stale`, `market_data maybe_refresh`, `notifications sent_markers` |
| G10 | **X-Forwarded-For not trusted:** login throttle key is hardcoded `"local"` — single bucket for home LAN, 60s lockout after 5 bad guesses | Trusting XFF lets attacker rotate header to bypass throttle | `auth._client_key` comment + `MAX_ATTEMPTS/WINDOW` |
| G11 | **Failure memoization includes None:** rate/price fetches cache `None` for 30m (`_fetch_cached ttl1800`) so broken network doesn't hammer APIs on every rerun | Forgetting to cache failures → page load latency spikes under outage; caching forever → stale rates | `rates._fetch_cached`, `market_data maybe_refresh` failure path |
| G12 | **Last known survives failure:** any fetch failure keeps persisted `currency_rates` / `holdings.last_price` untouched | Overwriting with null would blank charts and budget conversions | `rates.refresh_rates_if_due fresh is None→ return`, `market_data update only on price>0` |
| G13 | **Migration lock staleness 600s:** `.db-encrypting` removed if older than 600s; waited out up to 600s otherwise — double migrator is blocked not raced | Two migrators would write same temp files and corrupt DB | `db._wait_for_migration_lock` |

---

## 2. Subsystem Invariants (pointer — details in owned docs)

- **Shell:** onboarding gate default `False` never skips; sidebar rate re-reads fresh rates before save (race fix). → `app-shell/*`
- **Persistence:** `_ENCRYPT` opt-out via `EXPENSE_TRACKER_NO_ENCRYPT=1/true/yes/on`; Postgres via `DATABASE_URL` never encrypted. → `persistence/encryption-and-crypto.md`
- **Currency:** `MAX_AMOUNT` caps every numeric input; Excel injection guard `=" + @` stripped. → `domain/currency-and-taxonomy.md`
- **Ledger:** soft-delete only (`is_deleted + deleted_at`), audit inside same session, template edit never rewrites history, orphan recycle on partial is_rec. → `domain/transactions-and-recurring.md`
- **Planning:** Budget overlap never summed (`effective_category_budgets` picks tightest scope); loan interest booking on due-date-passed OR payment-applied; holdings `quantity*last_price_eur` in portfolio. → `domain/planning-and-wealth.md`
- **Ingestion:** review-first (no auto-persist), Tesseract 30s thread timeout, categorizer hash-cache invalidated on new expenses. → `ingestion/*`
- **Intelligence:** ETS missing month → None not interpolation; anomalies need 20 rows; LLM provider falls back to "none" on missing deps/keys. → `intelligence/*`
- **Connectivity:** pairing rate limit 5/600s per IP, email subject CR/LF stripped + body escaped, STARTTLS CERT_REQUIRED, `on_done` marker only after delivery. → `connectivity/*`

---

## 3. Dangerous Coupling — Do Not Bypass

| Trap | What breaks |
|------|-------------|
| Raw SQLAlchemy write without `log_audit` | Audit hole + missing `updated_at` trigger (if any) |
| Forgetting `bump_data_revision` | Stale caches across sessions/household; budget bar lies |
| Opening DB with `sqlite3` not ``sqlcipher3 + pragmas` | Appears as "file is not a database" |
| Adding a category in one place only | Legacy rows + sync peers + import fallbacks desync |
| Trusting client-provided `since` not server-issued | Sync conflict bypass |
| Using client `new Date()` for budget/rate period math | Server/client period mismatch, dedupe leak |
| Caching fetch success longer than failure | Rates drift vs outage thundering herd — intentional asymmetry (1800s success vs 1800s failure memo, staleness 3d/1d separately) |

---

## 4. Transaction & Concurrency Notes

- **Sessions:** `get_session()` contextmanager commits on exit, rolls back on exception; do not nest two writes expecting atomic — use one session per logical operation (sync_core does atomic compare-and-update in ONE session intentionally).
- **WAL:** enables concurrent reads during a write — required because Streamlit reruns overlap. `busy_timeout=5000` prevents SQLITE_BUSY crashes.
- **Household revision propagation:** `bump_data_revision(include_household=True)` bumps all household members' revisions so their caches invalidate on shared writes.
