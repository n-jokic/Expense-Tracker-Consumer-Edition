# Data Model — Expense Tracker (db.py)

> Persistence reference for Agent 2. Covers every table, column, constraint, and data-access helper in `db.py` (2,586 lines), plus related paths in `app_paths.py` and test harnesses.

## 1. Overview & Design Principles

- **ORM:** SQLAlchemy `declarative_base()` → `Base`. All models inherit `Base`.
- **Scoping rule:** every user-owned row carries `user_id = ForeignKey("users.id")` (except `Household` itself and `HoldingPrice` which links via `holding_id`). Every query filters `WHERE user_id = :uid`.
- **IDs:** user-facing entities use `String UUID4` primary keys (expenses, income, savings, accounts, recurring, loans, holdings, big_purchases, custom_milestones, devices). Integer auto-increment for `users`, `households`, `budgets`, `audit_log`, `user_milestones`, `sync_conflicts`, `holding_prices`, `user_settings`.
- **Time:** `_utcnow() = datetime.now(timezone.utc)` for `created_at`/`updated_at`/`deleted_at`; naive UTC stored in SQLite.
- **Household is experimental** — see §4. Single `households` row per group, joined via `users.household_id`. Do not assume multi-tenant isolation beyond `user_id` + household helpers; UI treats household aggregation as opt-in beta.

## 2. Engine & Session Management

| Concern | Implementation | File:line |
|---|---|---|
| **State root** | `state_dir()` → `EXPENSE_TRACKER_DATA_DIR` or `frozen ? %LOCALAPPDATA%/ExpenseTracker : <repo>/data` | `app_paths.py:12` |
| **Paths** | `DB_PATH = env DB_PATH or <BASE_DIR>/expense_tracker.db`, `BACKUP_DIR = env BACKUP_DIR or <BASE_DIR>/backups` | `db.py:39` |
| **URL override** | `DATABASE_URL = env DATABASE_URL` — when set, Postgres etc. is used, encryption is skipped | `db.py:43` |
| **Singleton** | `_engine = None`, `_Session = None`, `Base = declarative_base()` | `db.py:45` |
| **get_engine()** | Lazy singleton. `if DATABASE_URL: create_engine(DATABASE_URL)` else `ensure_db_encrypted() + create_engine("sqlite:///{DB_PATH}", module=_sqlite_module(), connect_args={"check_same_thread": False})` + `@event.listens_for(engine,"connect") _keyed_pragmas` | `db.py:269` |
| **_engine pragmas** | `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000` (+ `PRAGMA key = …` when encrypted) — applied in `_keyed_pragmas(con)` and on every pooled connection | `db.py:75` |
| **check_same_thread** | `False` — Streamlit reruns, background backup thread, sync API and MCP server share handles | `db.py:278` |
| **_get_session_factory()** | `sessionmaker(bind=get_engine(), expire_on_commit=False)` — critical: rows are converted to dicts/DataFrames *after* `session.close()`; default `expire_on_commit=True` would raise `DetachedInstanceError` (regression covered in `tests/test_db.py`) | `db.py:288` |
| **get_session()** | `@contextmanager` → `yield session; commit() on success else rollback(); close()` | `db.py:298` |
| **_raw_connect(path)** | Raw DBAPI connection with identical key/pragmas — used by `backup_db` and `tests` that read files directly | `db.py:84` |
| **Init** | `init_db(force_migrate=False)` → `Base.metadata.create_all(engine)` + additive `_migrate(engine)` once per process (`_MIGRATED` guard) | `db.py:649` |

> Encryption at rest (SQLCipher, `_ENCRYPT`, `_SQLITE_HEADER = b"SQLite format 3\\x00"`, `.db-encrypting` lock, `_wait_for_migration_lock` 600 s) is detailed in `encryption-and-crypto.md`.

Tests override isolation *before import*: `tests/conftest.py` force-sets `DB_PATH`, `BACKUP_DIR`, `EXPENSE_TRACKER_DB_KEY="9f2c…" `(temp dir `mkdtemp`), so the suite never touches the live DB.

## 3. Full Schema Table (18 models)

> Columns shown as `name: Type (constraint)`. FK = ForeignKey, PK = PRIMARY KEY, UQ = UniqueConstraint/Index, NN = NOT NULL.

| # | Table (`__tablename__`) | Columns (type) | Constraints | user_id scoped? | Soft-delete? |
|---|---|---|---|---|---|
| 1 | `households` | `id: Integer PK AUTOINCREMENT`, `name: String NN`, `invite_code: String UQ`, `created_at: DateTime` | PK `id`, UQ `invite_code` | ❌ (owner via `users.household_id`) | ❌ (row deleted when last member leaves) |
| 2 | `users` | `id: Integer PK AUTOINCREMENT`, `username: String UQ NN`, `email: String UQ NN`, `password_hash: String NN`, `display_name: String`, `household_id: Integer FK→households.id nullable`, `is_admin: Boolean default False`, `created_at: DateTime`, `onboarding_complete: Boolean default False`, `data_revision: Integer default 0` | PK, UQ username/email, FK household | N/A (is the scope) | ❌ (hard delete via `delete_user_account`) |
| 3 | `expenses` | `id: String PK UUID`, `user_id: Integer FK→users.id NN`, `date: Date`, `category: String`, `subcategory: String default ""`, `description: String`, `amount: Float`, `currency: String default EUR`, `amount_eur: Float`, `recurring: Boolean`, `rec_template_id: String nullable`, `loan_id: String nullable`, `loan_payment_type: String default "regular"`, `loan_surcharge_eur: Float`, `notes: String default ""`, `suggest_*: String/Float/Int/Bool×8 telemetry`, `is_deleted: Boolean default False`, `deleted_at: DateTime`, `created_at/updated_at: DateTime` | PK, FK user | ✅ | ✅ `is_deleted`+`deleted_at` |
| 4 | `income` | `id: String PK UUID`, `user_id: Integer FK NN`, `date: Date`, `source: String`, `income_type: String default "Other"`, `hours/rate: Float nullable`, `budgeted/actual: Float`, `currency: String`, `budgeted_eur/actual_eur: Float`, `notes: String`, `is_deleted/deleted_at/created_at/updated_at` | PK, FK user | ✅ | ✅ |
| 5 | `savings` | `id: String PK UUID`, `user_id: Integer FK NN`, `date: Date`, `goal_name: String`, `target_eur/deposited/deposited_eur/interest_rate/balance_eur: Float`, `currency: String`, `notes: String`, `is_deleted/deleted_at/created_at/updated_at` | PK, FK user | ✅ | ✅ |
| 6 | `savings_accounts` | `id: String PK UUID`, `user_id: Integer FK NN`, `goal_name: String NN`, `name: String`, `amount/amount_eur/annual_rate: Float`, `currency: String`, `start_date/maturity_date: Date`, `status: String default "active"` (active|closed), `notes: String`, `is_deleted/deleted_at/created_at/updated_at` | PK, FK user | ✅ | ✅ |
| 7 | `budgets` | `id: Integer PK AUTOINCREMENT`, `user_id: Integer FK NN`, `year/month: Integer`, `category/subcategory: String`, `budgeted_eur: Float` | PK, FK user, **UQ`(user_id,year,month,category,subcategory)` → `uq_budget_scope`** | ✅ | ❌ (hard delete) |
| 8 | `recurring` | `id: String PK UUID`, `user_id: Integer FK NN`, `category/subcategory/description/amount/currency/amount_eur/notes: …`, `due_day: Integer nullable`, `start_month: String nullable YYYY-MM`, `active: Boolean`, `sort_order: Integer` | PK, FK user | ✅ | ❌ (active flag) |
| 9 | `audit_log` | `id: Integer PK AUTOINCREMENT`, `user_id: Integer FK NN`, `action: String`, `table_name: String`, `record_id: String`, `details: Text`, `timestamp: DateTime default _utcnow`, `ip_address: String nullable` | PK, FK user | ✅ (per-user log) | ❌ (append-only) |
| 10 | `big_purchases` | `id: String PK UUID`, `user_id: Integer FK NN`, `name: String`, `category: String default "Other"`, `price/currency/price_eur/usage_hours: …`, `importance: Integer 1-5`, `status: String default "wishlist"` (wishlist|saving|bought), `sort_order: Integer`, `notes: String`, `created_at: DateTime` | PK, FK user | ✅ | ❌ |
| 11 | `loans` | `id: String PK UUID`, `user_id: Integer FK NN`, `name/principal/currency/principal_eur/annual_rate/start_date/term_months/payment_day/status/notes: …`, `early_repayment_surcharge_type: String default "fixed"`, `early_repayment_surcharge_value: Float`, `created_at: DateTime` | PK, FK user | ✅ | ❌ (hard delete) |
| 12 | `holdings` | `id: String PK UUID`, `user_id: Integer FK NN`, `symbol: String normalized upper`, `name: String`, `quantity/cost_total/cost_eur/last_price: Float`, `currency: String`, `last_price_date: DateTime`, `created_at: DateTime` | PK, FK user | ✅ | ❌ |
| 13 | `holding_prices` | `id: Integer PK AUTOINCREMENT`, `holding_id: String FK→holdings.id NN`, `date: Date default today`, `price/quantity/rate/value_eur: Float` | PK, FK holding | ✅ via holding FK | ❌ |
| 14 | `devices` | `id: String PK UUID`, `user_id: Integer FK NN`, `name: String default "Phone"`, `pairing_code: String nullable`, `token_hash: String nullable`, `token_expires_at: DateTime nullable`, `created_at: DateTime`, `last_sync_at: DateTime nullable` | PK, FK user, **partial UQ `pairing_code WHERE pairing_code IS NOT NULL`** | ✅ | ❌ |
| 15 | `user_milestones` | `id: Integer PK AUTOINCREMENT`, `user_id: Integer FK NN`, `milestone_id: String NN`, `earned_at: DateTime` | PK, FK user, **UQ`(user_id,milestone_id)` → `uq_user_milestones`** | ✅ | ❌ |
| 16 | `custom_milestones` | `id: String PK UUID`, `user_id: Integer FK NN`, `title: String NN`, `metric: String NN`, `target/reward: Float NN`, `achieved_at: DateTime nullable`, `created_at: DateTime` | PK, FK user | ✅ | ❌ |
| 17 | `sync_conflicts` | `id: Integer PK AUTOINCREMENT`, `user_id: Integer FK NN`, `table_name/record_id: String NN`, `device_value/server_value: JSON`, `created_at: DateTime`, `resolved: Boolean default False` | PK, FK user | ✅ | ❌ (resolved flag) |
| 18 | `user_settings` | `id: Integer PK AUTOINCREMENT`, `user_id: Integer UQ NN`, `exchange_rate: Float`, `default_currency: String`, `monthly_budget: Float`, `currency_rates: JSON`, `rates_updated_at: DateTime`, `salary_*: Float/String/Int/Bool×4`, `bill_reminder_days/weekly_summary/...: …`, `hourly_rate: Float`, `fun_*/travel_*/sent_markers: Float/JSON`, `email_alerts/alert_email/smtp_*: …`, `smtp_password_enc: String (Fernet)`, `gh_*: Boolean/String/Int/DateTime×6`, `ai_*: String/Int×6` (**ai_api_key_enc Fernet**) | PK, **UQ`user_id`** (one row per user) | N/A | ❌ |

**Notes on the table:**

- Every `*_COLS` list in `db.py` (e.g. `_EXP_COLS`, `_INC_COLS`) is the canonical column order returned by `_to_df` / DataFrames.
- `is_deleted` + `deleted_at` appear on **expenses, income, savings, savings_accounts** only (trash/restore pattern). All readers accept `include_deleted: bool`.
- `budgets` dedupes to the *newest* row per scope (MAX id) and then enforces `uq_budget_scope`; `add_budget` is an upsert.
- `user_settings.user_id unique` — `save_settings` creates the row on first write; unknown keys are *warned not swallowed*.

## 4. Household — Experimental

- Model: `Household(id, name, invite_code UQ, created_at)` ↔ `User.household_id FK`.
- Created via `create_household(user_id, name) → (id, code)` with cryptographically secure `secrets.choice` (A-Z0-9, 8 chars, 5 collision retries). Invite code is upper-cased on join.
- Lifecycle: `join_household(user_id, code)`, `leave_household(user_id)` (orphaned household auto-deleted), `regenerate_invite_code(user_id)`, `get_household_by_member`, `get_household_members`, `get_household_expenses`.
- **Experimental flag:** no row-level tenancy — household reads are explicit joins (`get_household_expenses` joins `Expense ⨝ User WHERE household_id`). Cache layer must propagate invalidations (see `caching-and-revision.md`). Do not build security assumptions on household isolation.

## 5. Expenses & ML Telemetry

- Core fields: `date, category, subcategory, description, amount, currency, amount_eur, recurring, rec_template_id, loan_id, loan_payment_type ("regular"|"early"), loan_surcharge_eur, notes`.
- **Soft-delete:** `soft_delete_expense` sets `is_deleted=True, deleted_at=_utcnow()`; `restore_expense` clears both. `get_expenses(include_deleted)` filters `is_deleted == False` by default.
- **ML columns (measurement-first, nullable):** `suggest_source (classifier|keywords)`, `suggest_confidence`, `suggest_model_version`, `suggest_merchant`, `suggest_accepted: Bool`, plus subcategory mirror `suggest_subcategory*, suggest_subcategory_confidence, suggest_subcategory_source, suggest_subcategory_accepted`. Populated on write, never required for read.
- `_EXP_COLS` is the DataFrame contract; `_parse_dates` coerces `date, created_at, deleted_at`.

## 6. Income (with Legacy Type Migration)

- Fields: `income_type ("Salary"|"Hourly"|"Bonus / Raise"|"Freelance"|"Investment"|"Rental"|"Other")`, `hours, rate`, `budgeted/actual + _eur`, `currency, notes`.
- `_LEGACY_INCOME_TYPES` maps old `source` labels (e.g. `"Primary Salary" → "Salary"`) on read via `_fill_income_types(df)`.
- Same soft-delete pattern as expenses.

## 7. Savings & SavingsAccount

- `Savings` (goal ledger): `goal_name, target_eur, deposited, currency, deposited_eur, interest_rate, balance_eur (derived), notes`.
- `SavingsAccount` (term deposit under a goal): `goal_name NN, name, amount, currency, amount_eur, annual_rate (monthly compounding), start_date, maturity_date, status (active|closed)`.
- **Goal-wide helpers:** `rename_savings_goal` (cross-table rename with case-insensitive clash check), `update_savings_goal`, `soft_delete_savings_goal` (trashes entries + accounts together).
- Balances are **derived chains** recomputed on read by `_recompute_savings_balances` (monthly compounding, clamped ≥0, negative deposits allowed as withdrawals).

## 8. Budgets & Recurring

- **Budget** = one row per `(user_id, year, month, category, subcategory)`; `subcategory=""` means whole category. `add_budget` is an **upsert** (find → update else insert → on race retry). Deletion is hard (`delete_budget`).
- **Recurring** templates: `category, subcategory, description, amount(_eur), currency, due_day (1-31 or None), start_month ("YYYY-MM" or None), notes, active, sort_order`. `update_recurring` auto-clears subcategory when category changes to an incompatible set (via `utils.CATEGORIES`).

## 9. AuditLog & BigPurchase

- **AuditLog** (append-only): every `add_*/update_*/soft_delete*/etc.` calls `log_audit(session, user_id, action, table, record_id, details, ip)` where `details = json.dumps(dict, default=_json_default)`. Readers: `get_audit_log(user_id, limit=200)`.
- **BigPurchase**: `name, category, price(_eur), currency, usage_hours (per month), importance (1-5), status (wishlist|saving|bought), sort_order, notes, created_at`. Hard delete.

## 10. Loans, Holdings & Prices

- **Loan**: `name, principal(_eur), currency, annual_rate (%), start_date, term_months, payment_day, status (active|paid_off), early_repayment_surcharge_type/value, notes`. Payments are *expenses* with `loan_id` link; `get_loan_payments(user_id, loan_id)` filters `Expense WHERE loan_id = :id AND is_deleted == False`.
- **Holding**: brokerage position `symbol (upper normalized), quantity, cost_total/_eur, last_price/date`.
- **HoldingPrice** (daily snapshot): `holding_id FK, date, price, quantity, rate, value_eur (= qty*price/rate)`. `add_holding_price` upserts one per `(holding_id, date)`; `get_holding_prices(user_id)` joins via holding ownership and orders by date. Deleting a holding cascades its prices (`delete_holding`).

## 11. Devices, Milestones, Sync Conflicts

- **Device** (phone pairing): `pairing_code (short-lived), token_hash (sha256), token_expires_at, last_sync_at`. Flow: `create_pairing_device → code (6-char, partial-UQ)` → `complete_pairing(code, name, token)` atomically claims via `UPDATE … WHERE pairing_code = :code` (prevents double-claim). `TOKEN_LIFETIME_DAYS = 90` sliding window via `touch_device_sync`.
- **UserMilestone** (persistent badge): deduped by `UQ(user_id, milestone_id)`; `record_milestones` uses `INSERT OR IGNORE` — only first caller inserts. `_enforce_milestone_uniqueness` dedupes legacy doubles.
- **CustomMilestone**: user-created goals `title, metric ∈ {expenses_count, expenses_eur, income_eur, savings_balance, streak_days, categories_count}, target (finite >0), reward (finite ≥0), achieved_at`. Validation in `add_custom_milestone`; `mark_custom_milestone_achieved` uses conditional `UPDATE … WHERE achieved_at IS NULL` so only one tab wins.
- **SyncConflict**: `table_name, record_id, device_value JSON, server_value JSON, resolved Bool`. `add_sync_conflict`, `get_sync_conflicts(resolved)`, `resolve_sync_conflict`, `apply_record_fields` (coerces ISO date strings back to `date`/`datetime`).

## 12. UserSettings — One Row Per User

Canonical defaults in `_SETTINGS_DEFAULTS` (`db.py:1838`):

- **Rates:** `exchange_rate, default_currency, currency_rates JSON, rates_updated_at`.
- **Notifications:** `salary_*, bill_reminder_days, weekly_summary, hourly_rate, fun_money/categories/bonus/bonuses, travel_budget/categories, sent_markers, email_alerts, smtp_*, smtp_password_enc (Fernet)`.
- **Backups:** `gh_backup_enabled, gh_repo ("owner/name"), gh_token_enc (Fernet), gh_retention_days (14), gh_last_*`.
- **AI:** `ai_provider ("none"|"local"|"api"), ai_local_model, ai_local_gpu_layers, ai_api_base/model, ai_api_key_enc (Fernet)`.
- `get_settings(user_id)` returns defaults when no row; `save_settings` ignores `id/user_id` writes and warns on unknown keys.

## 13. Data Access Patterns, Migrations & Soft-Delete

**Reader/writer surface (representative; see §3 for full list):**

- *Read (DataFrame or dict):* `get_expenses/income/savings/savings_accounts/budgets/recurring/big_purchases/loans/loan_payments/holdings/holding_prices/settings/audit_log/household_*/earned_milestone_ids/custom_milestones/sync_conflicts/devices/device_by_token`.
- *Create:* `add_expense/income/savings/savings_account/budget/recurring/big_purchase/loan/holding/holding_price/custom_milestone/sync_conflict/create_user/create_household/create_pairing_device`.
- *Update:* `update_expense/income/savings/savings_account/loan/holding/big_purchase/recurring/save_settings/atomic_update_setting_json/apply_record_fields/mark_custom_milestone_achieved/touch_device_sync/record_milestones`.
- *Delete/restore:* `soft_delete_*/restore_* (+ goal variants), delete_budget/holding/loan/big_purchase/custom_milestone/revoke_device/delete_user_account/leave_household/resolve_sync_conflict`.
- **Every mutator** calls `log_audit` and (via `queries.py` for UI paths) `bump_db_version` — cache invalidation is not optional.

**Additive migrations** (`_migrate` → `_add_missing_columns` via `ALTER TABLE ADD COLUMN` per missing column, Postgres + SQLite compatible):

- `user_settings` (currency, fun/travel, salary, notifications, backups, AI), `income` (income_type/hours/rate/updated_at), `recurring` (due_day/start_month/sort_order), `big_purchases` (sort_order), `expenses` (loan/ML/updated_at), `loans` (surcharge), `users` (data_revision), `holding_prices` (quantity/rate/value_eur), `devices` (token_expires_at), plus taxonomy rewrites (`_migrate_taxonomy`, `_migrate_budgets_taxonomy`, `_migrate_settings_taxonomy`), budget scope dedupe (`_enforce_budget_scopes`), pairing-code partial index (`_enforce_pairing_code_uniqueness`), milestone uniqueness (`_enforce_milestone_uniqueness`).

**Soft-delete contract:**

- Filter default: `WHERE is_deleted == False`.
- Trash views: `get_*(include_deleted=True)` returns everything.
- `deleted_at = _utcnow()` on soft-delete, `None` on restore.
- Budgets/loans/holdings use hard delete; devices/milestones use status/resolved flags.

**Test harnesses:** `tests/test_db.py` covers round-trip, soft-delete/restore, detached-instance read, and loan payment metadata; `tests/conftest.py` isolation is forced (not `setdefault`) so ambient `DB_PATH` cannot point the suite at the live DB.
