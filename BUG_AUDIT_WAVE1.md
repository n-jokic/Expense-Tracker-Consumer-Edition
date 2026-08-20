# Expense Tracker — Comprehensive Bug Audit Report

**Status: COMPLETE** — 3 waves of Q&A + hunter teams deployed, no new bug categories found after Wave 3.

## Audit Methodology

**Wave 1 — Q&A Agent Teams (6 parallel teams):**
1. app.py + UI/routing/session state
2. queries.py + db.py + finance.py (data layer)
3. auth.py + onboarding.py + utils.py (security)
4. notifications.py + gamification.py + rates.py + market_data.py (middleware)
5. bank_import.py + ocr.py + pdf_import.py (data import pipelines)
6. app_pages domain logic (budgets, forecast, loans, travel, recurring, savings)

**Wave 2 — Hunter Teams (3 targeted pattern teams):**
1. Broken dialog scopes/NameErrors across all `@st.dialog` functions
2. Non-atomic read-modify-write on JSON columns
3. Email/HTML injection in subjects and `unsafe_allow_html` sinks

**Wave 3 — Hunter Teams (2 targeted pattern teams):**
1. Missing `st.rerun()` + missing error boundaries (try/except) in dialogs/forms
2. `st.cache_data` stale data / wrong-key bugs

---

## Bugs Found — Consolidated by Severity

### 🔴 CRITICAL (app broken)

| # | Bug | Location | Description |
|---|-----|----------|-------------|
| 1 | **Recurring "Log now" dialog crashes with NameError** | `app_pages/recurring.py:84-115` (`log_template_dialog`) | The "Save changes" block (lines 101-115) was copy-pasted from `edit_template_dialog` into `log_template_dialog`. It references `c2`, `uid`, `n_amt`, `n_cur`, `n_cat`, `n_sub`, `n_desc`, `n_due`, `n_start`, `n_notes`, `n_active` — none defined in `log_template_dialog`'s scope. Opening "Log now" raises `NameError: name 'c2' is not defined`. |
| 2 | **Recurring "Edit template" dialog has no Save button** | `app_pages/recurring.py:39-82` (`edit_template_dialog`) | The edit dialog only has a Cancel button — no Save. Templates can never be edited from the UI. |
| 3 | **DB exceptions in dialogs crash the entire Streamlit page** | ~30 dialog/form handlers across `app_pages/*.py` | No `@st.dialog` or `st.form` submit handler wraps its DB write in try/except. Any DB exception (IntegrityError, DB locked, connection error) crashes the whole app page instead of showing a user-facing error. See Wave 3 full listing. |

### 🟠 HIGH (data corruption possible)

| # | Bug | Location | Description |
|---|-----|----------|-------------|
| 4 | **Audit entry written with user_id=0 for nonexistent holding → FK violation** | `db.py:1791-1793` (`add_holding_price`) | When `owner` is `None`, `log_audit(s, 0, ...)` writes `user_id=0` into `AuditLog`, violating FK to `users.id`. With `PRAGMA foreign_keys=ON`, the commit raises `IntegrityError`, losing the price snapshot. |
| 5 | **19 unguarded non-idempotent INSERT operations** | `loans.py:72,240,354`, `big_purchases.py:81,159`, `savings.py:90,123,403,579`, `log_expense.py:86,146`, `log_income.py:83,138`, `recurring.py:94,142`, `portfolio.py:73`, `dashboard.py:114` | All use fresh-UUID INSERTs. Double-click before rerun completes creates duplicate rows (duplicate expenses, double deposits, duplicate loans, duplicate households). |
| 6 | **Multi-write dialogs can strand inconsistent state** | `big_purchases.py:145` (confirm_purchase), `loans.py:211` (early_repayment), `log_expense.py:86` (receipt+recurring) | Dialog performs multiple writes (e.g., mark item bought → log expense). If the second write fails, the first is committed with no rollback → orphaned/inconsistent state. |
| 7 | **Budget "exceeded" alert suppressed after 85% threshold fires** | `notifications.py:314-337` | Dedup keyed per (category, month). Once `act_val >= bud_val * 0.85` adds the category to `alerted`, the >100% "exceeded" escalation never re-fires. |
| 8 | **Non-atomic read-modify-write on `sent_markers` (email marker race)** | `notifications.py:200-214` (`_persist_marker`) | Fresh read → mutate dict → write whole JSON column. Two concurrent async email callbacks lose a marker → duplicate emails. |
| 9 | **Non-atomic read-modify-write on `fun_bonuses` (bonus race)** | `gamification.py:429-463` (`_queue_fun_bonus`) | Same RMW pattern. Concurrent milestone awards can drop one bonus. |
| 10 | **Sidebar rate edit merges against stale session snapshot** | `app.py:125-127` | `new_rates = dict(st.session_state.settings.get("currency_rates") or {})` reads from session snapshot, not fresh DB. Concurrent rate edits from another tab are lost. |

### 🟠 MEDIUM (correctness / partial data corruption)

| # | Bug | Location | Description |
|---|-----|----------|-------------|
| 11 | **`currency_rates` refresh races under concurrency** | `rates.py:107-112` (`refresh_rates_if_due`) | Concurrent whole-dict writes to `currency_rates` clobber each other. |
| 12 | **Check-then-insert races without IntegrityError catch** | `db.py:1472-1491` (`add_budget`), `db.py:1868-1885` (`create_household`), `db.py:1888-1908` (`regenerate_invite_code`), `db.py:2192-2206` (`create_pairing_device`) | Check for collision then insert; no `IntegrityError` catch on the final INSERT → genuine race raises instead of retrying. |
| 13 | **TOCTOU read-back in `bump_data_revision`** | `db.py:902-924` | Commits UPDATE in one transaction, then re-reads revision via separate connection. Concurrent bump returns a value exceeding this caller's. |
| 14 | **NaN propagation in holding price computation** | `db.py:1771` | `value = round(qty * float(price) / rt, 4)` — NaN price/rate/qty yields NaN. NaN is truthy, so the guard passes and NaN is stored in `holding_prices.value_eur`. |
| 15 | **NaN propagation in `portfolio_metrics`** | `finance.py:273-280` | Non-finite `quantity`/`price_eur`/`cost_eur` poison `value`/`invested` aggregates. |
| 16 | **Budget progress display violates scope semantics** | `app_pages/budgets.py:110-131` | Per-row progress bars count ALL subcategory spend against the whole-category row, disagreeing with `effective_category_budgets` and `test_budget_scope.py`. |
| 17 | **Email re-send on every session when SMTP fails** | `notifications.py:316-337, 545-552` | Persisted marker only written in async `on_done` callback AFTER confirmed delivery. Failing SMTP → marker never persisted → re-toast/re-email every session. |
| 18 | **`add_holding_price` rate=0 edge case** | `db.py:1761-1794` | `None` qty/rate stores `rate=0.0`/`value_eur=0.0`; `value_eur` never validated as finite/positive. |
| 19 | **`save_settings` silently drops unknown keys** | `queries.py:181-186`; `db.py:1827-1844` | Logs warning but returns success; caller believes save worked. |
| 20 | **`rates_are_stale` compares calendar days, not age** | `rates.py:76-90` | Rate stamped late on day N is "stale" from day N+3 even if <3 days old. |
| 21 | **SMTP key-mismatch degrades silently to empty password** | `notifications.py:333,396,462,548` + `crypto.py:152-162` | `decrypt_str` returns `""` on failure. Empty password passed to SMTP silently → auth fails in worker thread, no user-visible error. |

### 🟡 LOW / defense-in-depth

| # | Bug | Location | Description |
|---|-----|----------|-------------|
| 22 | **Sidebar display-currency change never persisted** | `app.py:108-112` | Sidebar selectbox writes `st.session_state.dc` only — never calls `save_settings({"default_currency": ...})`. Lost on next login. |
| 23 | **Float arithmetic for money (no Decimal)** | `finance.py`, `db.py:1213-1230`, `utils.py:268-287` | Repeated float multiply/divide drifts on large sums. |
| 24 | **No upload size limit** | `bank_import.py:402`, `pdf_import.py:467` | Whole file loaded to memory. |
| 25 | **Malformed/corrupt PDF → unhandled crash** | `pdf_import.py:473`, `bank_import.py:412-413` | `pdfplumber.open` not wrapped in try/except. |
| 26 | **Dedup key whitespace-sensitive** | `bank_import.py:315, 601-605` | Descriptions differing only by whitespace → duplicates. |
| 27 | **QR exception swallow has no logging** | `app.py:144-154` | `except Exception` around QR block swallows error with no log. |
| 28 | **Unused `rate` parameter in email builder** | `notifications.py:99-101` | `build_budget_alert_email(..., rate)` never references `rate` in body. |
| 29 | **Open registration by default** | `auth.py:68-82` | `ALLOW_REGISTRATION` unset → enabled. |
| 30 | **Device tokens use `uuid.uuid4()` not `secrets`** | `db.py:2218` | Lower entropy / non-CSPRNG. |
| 31 | **Latent stored-XSS in gamification badge sidebar** | `gamification.py:604-608` | Unescaped f-string into HTML `title="..."` + body under `unsafe_allow_html=True`. Safe today (hardcoded data) but becomes XSS if custom milestone titles enter this path. |
| 32 | **Global login-throttle bucket** | `auth.py:45-65` | All clients share one bucket per username → username DoS. |
| 33 | **Non-async SMTP test email** | `notifications.py:620-632` | Test email uses `send_email` (blocking), not `send_email_async`. |
| 34 | **`get_lan_urls` keyed only by port** | `utils.py:684` | Cache keyed only by port (LOW — URLs are machine-static). |
| 35 | **Manual price refresh serves cached prices** | `market_data.py:80` | Portfolio "Refresh prices" uses `cached=True` → may serve ≤30-min-old cached prices. |
| 36 | **Rate cache serves stale failure for 30 min** | `rates.py:70-73` | `_fetch_cached` caches failures (None) for 30 min. |
| 37 | **bcrypt cost not pinned** | `auth.py:26,91-98` | No rehash-on-cost-change. |
| 38 | **`total_interest_remaining` under-reports** | `finance.py:209, 189-190` | Forced to 0 when `monthly <= bal*r` (perpetual no-payoff case). |

---

## ✅ Verified Correct (no bug found)

- All data queries parameterized (SQLAlchemy ORM + `text()` with bound params) — no SQL injection
- Transaction handling correct — all write paths commit; no missed `conn.commit()`
- SQLite threading handled (`check_same_thread=False` + WAL + `busy_timeout`)
- Currency rate engine correctly rejects zero/NaN/negative rates; RSD guaranteed present
- Formula injection defense (`_xl_safe`) applied uniformly to all string columns at export
- Milestone idempotency (INSERT OR IGNORE + unique index; atomic conditional UPDATE)
- Background thread safety in `market_data.py` (`_refresh_lock`, per-call sessions)
- Weekly-summary window math correct (no off-by-one)
- All `st.markdown` f-strings without `unsafe_allow_html` are auto-escaped
- Custom component `draggable_card_board` uses `textContent`/`setAttribute` (no `innerHTML` with data)
- `app_pages/settings_ai.py` intentionally not a route (sub-component, not navigable page)
- `st.cache_data` version-based invalidation correct; copy-on-read prevents cache mutation
- ETS forecasting model, anomaly detection, loan amortization, travel currency conversion — all correct
- No deprecated `st.experimental_*` APIs anywhere
- MCP server has proper input validation (amount type, category enum, length limits)

---

## Files Inspected

### Source files (root)
`app.py`, `auth.py`, `onboarding.py`, `utils.py`, `crypto.py`, `app_paths.py`, `db.py` (2489 lines), `queries.py`, `finance.py`, `rates.py`, `market_data.py`, `notifications.py`, `gamification.py`, `mcp_server.py`, `sync_core.py`, `api.py`, `bank_import.py`, `ocr.py`, `pdf_import.py`, `github_backup.py`, `insights.py`, `forecasting.py`

### App pages (19 files)
`app_pages/budgets.py`, `log_expense.py`, `log_income.py`, `savings.py`, `loans.py`, `recurring.py`, `big_purchases.py`, `rewards.py`, `dashboard.py`, `portfolio.py`, `travel.py`, `forecast.py`, `insights_view.py`, `audit_log.py`, `settings.py`, `settings_ai.py`, `household.py`, `ask.py`, `bank_import_view.py`

### Test files (28 files)
`test_formula_injection.py`, `test_budget_scope.py`, `test_recurring.py`, `test_currency.py`, `test_rates.py`, `test_rate_validation.py`, `test_crypto.py`, `test_notifications.py`, `test_custom_milestones.py`, `test_fun_travel.py`, `test_forecasting.py`, `test_forecast.py`, `test_portfolio_snapshots.py`, `test_taxonomy_migration.py`, `test_app_smoke.py`, `test_app_ui.py`, `test_backup.py`, `test_api.py`, `test_mcp.py`, `test_ocr.py`, `test_pdf_import.py`, `test_gamification.py`, `test_bank_import.py`, `test_big_purchases.py`, `test_savings.py`, `test_db.py`, `test_entry_editing.py`, `test_insights.py`, `test_income.py`, `test_rate_validation.py`, `test_launcher.py`, `test_llm.py`, `test_sync.py`, `test_categorizer_cache.py`, `test_cache_revision.py`, `test_qr.py`, `test_market_data.py`

---

## Actionable Fix Plan

### Phase 1 — CRITICAL (unblock app functionality)

**1. Fix recurring.py dialog structure (Bugs #1, #2)**
- **File:** `app_pages/recurring.py`
- **Action:** Remove the orphaned "Save changes" block (lines 101-115) from `log_template_dialog`. Keep only the self-contained "Log it" handler (lines 94-100). Move the "Save changes" block (with `update_recurring`) back into `edit_template_dialog` where its variables are defined, and add a proper Save button there.
- **Effort:** 30 min
- **Test:** Add `test_recurring_dialogs` to `tests/test_app_ui.py` using AppTest to verify both dialogs open without NameError.

**2. Add universal try/except error boundaries to all dialog/form DB writes**
- **Files:** All `app_pages/*.py` dialog and form handlers (~30 sinks)
- **Action:** Wrap every DB write call in `try: ... except Exception as e: safe_error(str(e)); return` (or `st.error`). Use the `safe_error` helper already imported in most pages. This prevents DB exceptions from crashing the Streamlit page.
- **Effort:** 2-3 hours (mechanical)
- **Test:** Error boundary unit tests for each dialog.

**3. Fix multi-write dialog partial-failure (Bug #6)**
- **Files:** `app_pages/big_purchases.py:145`, `app_pages/loans.py:211`, `app_pages/log_expense.py:86`
- **Action:** Wrap the entire multi-write sequence in a single try/except. Write the "leaf" data first (add_expense), then the status update (update_big_purchase/update_loan). If any step fails, the transaction should roll back (or manually undo the first write).
- **Effort:** 1-2 hours

### Phase 2 — HIGH (data integrity)

**4. Fix `add_holding_price` audit FK violation (Bug #4)**
- **File:** `db.py:1791-1793`
- **Action:** When `owner is None`, skip the `log_audit` call (or log with a sentinel like NULL user_id if the schema allows). Never write `user_id=0`.
- **Effort:** 15 min

**5. Add double-submit guards to all non-idempotent INSERT dialogs (Bug #5)**
- **Files:** All 19 sinks listed in Bug #5
- **Action:** Port the `savings.py:227-231` re-read-before-write guard pattern: before each `add_expense`/`add_income`/`add_savings`/etc., re-read the relevant state from the DB and abort if the record already exists or is already in the target state. Add `st.session_state` one-shot flags for additional protection.
- **Effort:** 3-4 hours

**6. Fix budget alert suppression (Bug #7)**
- **File:** `notifications.py:314-337`
- **Action:** Key dedup on `(category, threshold_level)` so the "exceeded" (>100%) state can fire separately from the "near limit" (85%) state. Or explicitly allow an escalation email when `over` transitions from False to True.
- **Effort:** 30 min

**7. Fix non-atomic JSON read-modify-write (Bugs #8, #9, #10, #11)**
- **Files:** `notifications.py:200-214`, `gamification.py:429-463`, `app.py:125-127`, `rates.py:107-112`
- **Action:** Create a DB-side atomic JSON-merge helper (using `UPDATE ... SET col = <merged JSON>` in a single `engine.begin()` transaction, or `BEGIN IMMEDIATE`). Replace all four RMW patterns with this helper. For `app.py`, change to a fresh DB read before merging.
- **Effort:** 2-3 hours

### Phase 3 — MEDIUM (correctness)

**8. Fix check-then-insert races (Bug #12)**
- **File:** `db.py:1472-1491`, `db.py:1868-1908`, `db.py:2192-2206`
- **Action:** Catch `IntegrityError` on the final INSERT and retry (or use `INSERT OR IGNORE`/`INSERT ... ON CONFLICT`).
- **Effort:** 1 hour

**9. Fix NaN propagation (Bugs #14, #15)**
- **Files:** `db.py:1771`, `finance.py:273-280`
- **Action:** Add `math.isfinite()` guards before storing/aggregating NaN values.
- **Effort:** 30 min

**10. Fix budget progress scope semantics (Bug #16)**
- **File:** `app_pages/budgets.py:110-131`
- **Action:** Use `effective_category_budgets` (the authoritative scope logic from utils.py) instead of raw category sums for the per-row progress display.
- **Effort:** 1 hour

**11. Fix email re-send on failure (Bug #17)**
- **File:** `notifications.py`
- **Action:** Persist a "sent" marker immediately (dedupe for the period), separate from a "delivered" flag for retry bookkeeping.
- **Effort:** 1-2 hours

**12. Fix TOCTOU in `bump_data_revision` (Bug #13)**
- **File:** `db.py:902-924`
- **Action:** Return the new value from the same UPDATE transaction (RETURNING or set in the same `engine.begin()` block) instead of a re-read via a separate connection.
- **Effort:** 30 min

### Phase 4 — LOW (hardening)

**13. Persist sidebar display-currency change (Bug #22)**
- **File:** `app.py:108-112`
- **Action:** Call `q.save_settings({"default_currency": dc})` when the sidebar currency changes.
- **Effort:** 15 min

**14. Add upload size limit (Bug #24)**
- **Files:** `bank_import.py:402`, `pdf_import.py:467`
- **Action:** Add `max_upload_size` / file size check before processing.
- **Effort:** 30 min

**15. Wrap `pdfplumber.open` in try/except (Bug #25)**
- **Files:** `pdf_import.py:473`, `bank_import.py:412-413`
- **Action:** Catch exceptions and show a user-friendly error.
- **Effort:** 30 min

**16. Normalize bank import dedup keys (Bug #26)**
- **File:** `bank_import.py:315, 601-605`
- **Action:** Strip whitespace before dedup comparison.
- **Effort:** 15 min

**17. HTML-escape gamification badge output (Bug #31)**
- **File:** `gamification.py:604-608`
- **Action:** Wrap `m["desc"]` and `m["title"]` in `html.escape(..., quote=True)`.
- **Effort:** 10 min

**18. Remaining LOW items (Bugs #23, #27-#38)**
- Float→Decimal migration (`#23`): selective migration at accumulation boundaries
- Log QR exceptions (`#27`): add `logger.warning` in the except block
- Use `rate` parameter in email builder (`#28`): implement display-currency conversion
- Pin bcrypt cost + rehash (`#37`): set `rounds=12` in `gensalt()`
- Other LOW items: 1-2 hours each

---

## Termination Criterion

**New agent runs stopped reporting new bugs after Wave 3.** The three Wave 2/3 hunter patterns (non-atomic RMW, dialog scope, injection, double-submit, cache coherency, error boundaries) each returned findings that were either confirmed instances of the targeted pattern or verified-clean. No new bug CATEGORIES emerged. The audit is complete.
