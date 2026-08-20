# FIX_PLAN_AGENT — Agent-Executable Bug Fix Plan

> Source audit: `BUG_AUDIT_WAVE1.md` (38 bugs, 3 waves, 15 teams). Verified 2026-05-13 against workspace `C:\Users\Nikita\Desktop\gitProjects\Expense-Tracker-Consumer-Edition`.
> New bug found during verification: **#39 — `onboarding.py:108-125` bypass** (MEDIUM).
> Some Phase-1/2 fixes already landed this session — marked `[DONE]` below. Remaining tasks are ready to run in parallel by subagents.

## How to use this plan

- Each task is **independently assignable** to one subagent. Subagent prompt is copy-paste ready.
- All file paths are absolute or repo-relative from repo root. Use `read` before `edit`, `edit` with literal `old_string` (must match exactly — include surrounding lines for uniqueness), never invent line numbers.
- After each task: `py_compile` the touched file, run `pytest` (existing 28 test files must stay green), and satisfy the task's **Done when** check.
- Parallelism groups at bottom — don't block.

---

## Verification summary

| Audit claim | Verdict | Evidence |
|---|---|---|
| CRITICAL #1/#2 `recurring.py:39-115` both dialogs broken | **CONFIRMED** | `edit_template_dialog` ends at L81 with only Cancel; `log_template_dialog` L101 `with c2:` references sibling locals → `NameError` |
| CRITICAL #3 ~30 sinks no `try/except` | **CONFIRMED fixed** | Sampled `budgets.py`, `loans.py`, `big_purchases.py`, `savings.py`×8, `portfolio.py` — now all wrapped (see `[DONE]`) |
| HIGH #4 `db.py:1791` FK `user_id=0` | **CONFIRMED fixed** | Changed to `if owner is not None` guard |
| HIGH #5 19 double-submit INSERTs | **CONFIRMED, NOT YET FIXED** | Fresh-UUID `add_*` at `loans.py:72,240,354` etc. — still needs P2-2 |
| HIGH #6 multi-write orphan | **CONFIRMED fixed** | `big_purchases.confirm_purchase_dialog`, `loans.early_repayment_dialog` + `Log payment` popover, `log_expense.exp_form` now single `try` + compensate |
| HIGH #7 `notifications.py:314` suppression | **CONFIRMED fixed** | Now keys `f"{cat}:{level}"` |
| HIGH #8-#11 JSON RMW | **CONFIRMED, NOT YET FIXED** | `notifications._persist_marker`, `gamification._queue_fun_bonus`, `app.py:125` stale snapshot, `rates.py:107` — needs P2-4 helper |
| MEDIUM #12 IntegrityError races | **CONFIRMED fixed** | `add_budget`, `create_household`, `regenerate_invite_code`, `create_pairing_device` now catch + retry |
| MEDIUM #13 `bump_data_revision` TOCTOU | **CONFIRMED fixed** | Now tries `RETURNING` then fallback |
| MEDIUM #14/#15 NaN | **CONFIRMED fixed** | `db.py:add_holding_price` validates `isfinite`; `finance.portfolio_metrics` clamps |
| MEDIUM #16 budget scope | **CONFIRMED fixed** | `budgets.py` now uses `effective_category_budgets` + skips whole-row when subcategories exist |
| MEDIUM #17 email re-send | **CONFIRMED, NOT YET FIXED** | Marker only on `ok=True` — needs P3-4 |
| LOW #22-#38 samples | **CONFIRMED, PARTLY FIXED** | QR log, badge escape, dedup norm, PDF guard, upload cap done; currency persist + others remain |
| NEW #39 `onboarding.py` bypass | **FOUND + FIXED** | `set_onboarding_complete` was dedented outside validation — now inside `else` success path |

---

## Task index

| ID | Severity | Bugs | Status | Effort | Parallel group |
|---|---|---|---|---|---|
| P1-1 | CRITICAL | #1 #2 | **DONE** | 30m | A |
| P1-2 | CRITICAL | #3 | **DONE** | 2-3h | A |
| P1-3 | CRITICAL | #6 | **DONE** | 1h | A |
| P2-1 | HIGH | #4 #18 | **DONE** | 15m | A |
| P2-2 | HIGH | #5 | TODO | 3-4h | B |
| P2-3 | HIGH | #7 | **DONE** | 30m | B |
| P2-4 | HIGH | #8 #9 #10 #11 | TODO | 2-3h | B |
| P3-1 | MEDIUM | #12 | **DONE** | 1h | C |
| P3-2 | MEDIUM | #13 | **DONE** | 30m | C |
| P3-3 | MEDIUM | #14 #15 | **DONE** | 30m | C |
| P3-4 | MEDIUM | #16 | **DONE** | 1h | C |
| P3-5 | MEDIUM | #17 | TODO | 1-2h | C (after P2-4) |
| P3-6 | MEDIUM | #39 NEW | **DONE** | 15m | C |
| P4-1 | LOW | #22 | TODO | 15m | D |
| P4-2 | LOW | #24 | **DONE** | 30m | D |
| P4-3 | LOW | #25 | **DONE** | 30m | D |
| P4-4 | LOW | #26 | **DONE** | 15m | D |
| P4-5 | LOW | #27 | **DONE** | 5m | D |
| P4-6 | LOW | #31 | **DONE** | 10m | D |
| P4-7 | LOW | #28 #30 #32-38 | TODO | 2h | D |

---

## P1 — CRITICAL — DONE (no further work; verify only)

### P1-1 — `recurring.py` structural defect — #1 #2 — DONE

- **Files:** `app_pages/recurring.py`
- **What was done:** Removed orphaned `with c2:` Save block L101-115 from `log_template_dialog`; restored Save into `edit_template_dialog` (adds `c2` Save button, computes `n_eur`, wraps `update_recurring` in `try/except` + `bump` + `rerun`). `log_template_dialog` now self-contained with `try/except` on `add_expense`.
- **Verify:** `py_compile app_pages/recurring.py` ok. Manual: open Recurring page, click Log now → no NameError, creates expense; click Edit → Save persists.
- **Tests to add (optional next):** `tests/test_recurring_dialogs.py` via `AppTest` — assert no `at.exception` for each dialog.

### P1-2 — Universal error boundaries — #3 — DONE

- **Files touched:** `app_pages/savings.py`, `loans.py`, `big_purchases.py`, `budgets.py`, `log_expense.py`, `log_income.py`, `portfolio.py`, `household.py`, `settings.py`, `settings_ai.py`, `travel.py`, `rewards.py`, `dashboard.py`, `app.py`, `onboarding.py` (all sinks)
- **Pattern applied:**
  - Inside `@st.dialog` function: `try: <DB call> except Exception as e: st.error(f"Couldn't save: {e}"); return`
  - At module top-level (inside `if saved:` / `if st.button:`): `try: ... except Exception as e: st.error(... ) else: bump+success+rerun` (no `return` — `return` outside function is `SyntaxError`)
- **Verify:** `py_compile` all pages ok. Fault inject: `monkeypatch db.add_expense to raise IntegrityError` in any dialog/form → `AppTest` shows `st.error` not `at.exception`.

### P1-3 — Multi-write atomicity — #6 — DONE

- **Files:** `app_pages/big_purchases.py:confirm_purchase_dialog` (now `add_expense` → `update_big_purchase` in one `try`), `app_pages/loans.py:early_repayment_dialog` (captures `exp_id`, compensates via `soft_delete_expense` on second-write failure), `loans.py:Log payment` popover (same), `log_expense.py:exp_form` (captures `rec_id`, marks inactive on failure)
- **Verify:** Patch `update_big_purchase` to raise after `add_expense` → no orphan expense remains; patch `update_loan` → expense rolled back.

### P2-1 — FK + NaN guards — #4 #14 — DONE

- **Files:** `db.py:add_holding_price` — guard `if owner is not None` before `log_audit`; validate `qty/price/rt` with `math.isfinite` and `rt != 0`; `finance.py:portfolio_metrics` clamps each component with `isfinite`.
- **Verify:** Call `add_holding_price` with orphan holding_id → no IntegrityError; with `float('nan')` → `ValueError`.

---

## P2 — HIGH — TODO (ready to run now)

### P2-2 — Double-submit guards — #5 — TODO — HIGH — 3-4h

- **Context:** 19 HIGH sinks still use fresh-UUID INSERT without re-read guard. Only `savings.py:withdraw_account_dialog` (`fresh_accs` re-read) is correct.
- **Files (HIGH sinks):** `loans.py:72` loan_form `add_loan`, `loans.py:240` early_repayment `add_expense`, `loans.py:354` Log payment `add_expense`, `big_purchases.py:81` bp_form, `big_purchases.py:159` confirm_purchase, `savings.py:90` deposit, `savings.py:123` withdraw, `savings.py:403` sav_form, `savings.py:579` savacc, `log_expense.py:86` receipt_form, `log_expense.py:146` exp_form, `log_income.py:83` Log salary, `log_income.py:138` inc_form, `recurring.py:94` log_template_dialog, `recurring.py:142` rec_form, `portfolio.py:73` hold_form, `dashboard.py:114` quick-add. Medium: `budgets.py:96`, `household.py:51`, `rewards.py:137`, `settings.py:344`, `onboarding.py:108`.
- **Subagent prompt (copy-paste):**
  ```
  Fix double-submit (Bug #5) in <FILE>. Read the file fully.
  For each sink listed above, add a re-read-before-write guard:
  - Immediately before the INSERT, fresh DB read (e.g. q.expenses/get_loans/get_savings_accounts) filtered to the key that would collide (date+category+amount, loan_id+date, goal_name+date, etc.).
  - If a matching row already exists (or status already flipped), set st.session_state toast "Already saved" and st.rerun() without inserting.
  - Keep existing try/except error boundary; guard goes INSIDE the try before the INSERT.
  - For dashboard quick-add: add st.session_state one-shot flag `qa_<desc>_<yyyymmdd>` plus the DB re-read.
  Verify py_compile and keep pytest green.
  ```
- **Sharding (run in parallel):** P2-2a `savings.py`+`portfolio.py`, P2-2b `loans.py`, P2-2c `log_expense`+`log_income`+`recurring`, P2-2d `big_purchases`+`dashboard`+`budgets`+`household`+`rewards`.
- **Done when:** Double `AppTest.button.click()` / direct double call creates exactly one row (query COUNT == 1).
- **Tests to add:** `tests/test_double_submit.py` — parametrized over each sink, call handler twice assert 1 row.

### P2-4 — Atomic JSON RMW helper — #8 #9 #10 #11 — TODO — HIGH — 2-3h

- **Files:** `db.py` (new helper), `notifications.py:200-214` `_persist_marker`, `gamification.py:429-463` `_queue_fun_bonus`, `app.py:125` sidebar rate edit, `rates.py:107-112` `refresh_rates_if_due`
- **Action:**
  1. Add `db.py:merge_settings_json(user_id, key, patch_dict)` — single `engine.begin()` transaction: `SELECT key`, `json.loads` (handle str vs dict), `current.update(patch)`, `json.dumps`, `UPDATE user_settings SET key=:val WHERE user_id=:uid`. For nested list markers (`sent_markers`) use same helper with full dict patch.
  2. Replace call sites:
     - `notifications._persist_marker`: `fresh = _db_get_settings` + mutate + `_db_save_settings` → `db.merge_settings_json(user_id, "sent_markers", {kind+"_"+month_key: sorted_items})` (keep `_fresh_markers` read as before)
     - `gamification._queue_fun_bonus`: fresh read → `merge_settings_json` for `fun_bonuses`
     - `app.py:125`: `new_rates = dict(st.session_state.settings.get("currency_rates") or {})` → `from db import get_settings; new_rates = dict(get_settings(user_id).get("currency_rates") or {})` then `merge_settings_json` or `q.save_settings` is now fresh
     - `rates.py:107`: `current = dict(settings.get("currency_rates") or {})` → re-read fresh inside helper or ensure caller passes fresh `settings`
  3. Fallback: if `json1` not available, use `BEGIN IMMEDIATE` loop (already in helper's `engine.begin()` with row lock semantics).
- **Done when:** `ThreadPoolExecutor(10).map(_persist_marker)` loses zero keys; same for `_queue_fun_bonus`.
- **Tests to add:** `tests/test_json_rmw_atomic.py` — concurrent threads assert all markers present.

---

## P3 — MEDIUM — TODO (P3-5 after P2-4)

### P3-5 — Email re-send on failure — #17 — TODO — MEDIUM — 1-2h

- **Files:** `notifications.py:316-337`, `200-226`, `545-552`
- **Action:** Persist a "sent this interval" marker **immediately** before `send_email_async` (period dedup), keep a separate `delivered` flag set only in `on_done(ok=True)`. On `ok=False` log `warning` and rely on period dedup to avoid spam while still retrying next period. Change `_marker_on_delivery` to update `delivered` not `sent`.
- **Done when:** Mock `send_email_async` to fail → marker present immediately, second session run does not re-toast.
- **Tests to add:** `tests/test_notifications_resend.py`

### P3-1/P3-2/P3-3/P3-4/P3-6 — DONE

- Already fixed (see verification table). No further work; add tests if bandwidth allows: `test_check_then_insert.py`, `test_bump_revision.py` (concurrent bumps), `test_nan_guards.py`, `test_budget_scope.py` AppTest, `test_onboarding.py` (Bug #39).

---

## P4 — LOW — TODO (parallel, no dependencies)

### P4-1 — Persist sidebar display currency — #22 — TODO — 15m

- **File:** `app.py:108-112`
- **Action:** After `st.session_state.dc = DC`, if `DC != settings.get("default_currency")`: `try: q.save_settings(user_id, {"default_currency": DC}) except Exception as e: st.error(...)`
- **Prompt:** `Read app.py, add save_settings for dc_sidebar change as above, py_compile, pytest.`
- **Done when:** Sidebar currency change survives re-login (DB `default_currency` matches).

### P4-7 — Remaining LOWs — #28 #30 #32-38 — TODO — 2h

- **Files & actions:**
  - `notifications.py:99` `build_budget_alert_email(..., rate)` — drop unused `rate` param or actually use it: `to_display(spent_eur, DC, rates)` instead of hard-coded `€`. Document choice.
  - `db.py:2218` `create_pairing_device` — `uuid.uuid4().hex` → `secrets.token_urlsafe(32)` then `sha256` (already used elsewhere).
  - `auth.py:45-65` global throttle — document as intentional LAN trade-off or add per-IP file/DB limiter if off-LAN hosting planned (leave as-is if LAN-only; add comment).
  - `rates.py:76-90` `rates_are_stale` calendar-day — document or switch to hours (`total_seconds() < 3*86400`).
  - `crypto.py` decrypt failure already logs — add user-visible `st.warning` in `notifications.py` when `smtp_password_enc` present but `_decrypt` returns `""`.
  - `utils.py:684` `get_lan_urls` keyed only by port — TTL 60 is fine; no code change, add comment.
  - `market_data.py:80` manual refresh uses `cached=True` — change portfolio Refresh button to `cached=False` or document.
  - `rates.py:70` failure cache 30m — document as intentional; no change unless UX complaint.
  - `auth.py:26` bcrypt cost — `bcrypt.gensalt(rounds=12)` and add `needs_rehash` check on login (optional).
  - `finance.py:209` `total_interest_remaining` under-report — document as "max calculable" or compute with `monthly*remaining_months - bal` even when `remaining_months==0` is 0 by definition.
- **Prompt (shard):** `Read <FILE>, apply the single-line fix above, py_compile, pytest.`

### P4-2..P4-6 — DONE

- P4-2 upload cap `bank_import.py` 20MB ✓, P4-3 PDF guard `pdf_import.py` ✓, P4-4 dedup whitespace `_re.sub` ✓, P4-5 QR `logger.warning` ✓, P4-6 badge `html.escape` ✓

---

## Public API / schema notes

- No migration needed for P1-3 + P2-4 (uses existing JSON columns, `json1` fallback to `BEGIN IMMEDIATE`).
- `queries.bump_db_version()` remains sole cache invalidation — all writers already call it (audit confirmed except benign settings paths — preserved).
- At-rest encryption (`crypto.sqlcipher_key_pragma`) unchanged; no key rotation.

## Test & acceptance checklist (run after each phase)

```bash
py_compile all edited pages
pytest -q
# New harnesses (add as you go):
pytest tests/test_double_submit.py tests/test_json_rmw_atomic.py tests/test_notifications_resend.py -v
```

- `AppTest` for `recurring.py` both dialogs: no `NameError`, edit persists.
- Fault injection on every P1-2 sink: `monkeypatch db.add_* to raise IntegrityError` → `st.error` not `at.exception`.
- Concurrency harness for P2-4 / P3-2 loses zero keys / returns distinct revisions.
- Budgets page with whole + subcategory rows matches `effective_category_budgets` (no double count).

## Execution order & parallelism

```
Group A (already done): P1-1 P1-2 P1-3 P2-1 P2-3 P3-1..P3-4 P3-6 P4-2..P4-6
Group B (run now, parallel): P2-2 (shard 4 ways)  +  P2-4  +  P4-1
Group C (after B):           P3-5  +  P4-7
```

Estimated remaining effort: P2-2 ~3-4h (mechanical, shardable), P2-4 ~2-3h, P3-5 ~1-2h, P4-1 ~15m, P4-7 ~2h.

## Risks & mitigations

- Large mechanical changes (P1-2, P2-2) miss a sink → `grep -rn "add_\|update_\|delete_\|save_settings\|soft_delete"` and parametrized tests.
- `json1` absent → fallback `BEGIN IMMEDIATE` in helper.
- `st.rerun()` in AppTest → assert `at.exception is None` not rerun count.
- `return` outside function at module level is SyntaxError — fixed pattern uses `try/except/else` (already applied).

## Assumptions & non-goals

- Trusted single-machine / LAN deployment — open registration + global throttle + cleartext HTTP + self-signed TLS stay as documented trade-offs.
- No `Decimal` migration for money (Bug #23) — only `isfinite` guards.
- No server-side session token, no new CSV/HTML exporters.
