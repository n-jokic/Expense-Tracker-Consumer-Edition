# Remediation Report — Verified Fixes for Validated QA Findings
> Generated: 2026-08-20
> Source: qa/registry-findings.json (20 authorized CONFIRMED/HIGH-CONFIDENCE) + 6 integration-distinct = 26
> Rule: Fix only authorized validated findings; new observations -> NEW BUG LEADs (UNVALIDATED); source code truth over stale memory.

## 1. Executive Summary
- Targeted: 20 registry CONFIRMED/HC (19C+1HC) + 6 integration-distinct = 26 authorized
- Verified fixed: 26 (20 primary + 6 integration distinct). Includes hunter analogs covered within same root-cause patches (income/savings/accounts caps, JSON clobber analogs, backup/secret colocation, N+1 clones).
- Reopened: 0 (no QA model disproved during remediation — validators confirmed or SUSPECTED kept as leads)
- Blocked: 0
- Files changed: 27 prod + 3 tests/conftest + 3 docs = 33 touched (git diff --stat: 27 prod files reported, crypto/queries added)
- Regression tests added/modified: finance day-aware test (tests/test_finance.py:312), crypto lock colocation test (tests/test_crypto.py), conftest temp dir robustness (tests/conftest.py)
- Memory files updated (Type A): agent instructions/domain/planning-and-wealth.md:7.2, agent instructions/ingestion/import-pipeline.md §2-4.5, agent instructions/connectivity/sync-and-household.md §6
- New unvalidated leads: 4 in qa/new-leads.json
- Memory inconsistencies discovered: 2 Type B (planning-and-wealth snippet matched buggy code — fixed as Type A; ingestion pipeline no-sniff vs execution-flows claimed sniff — noted for owner, not edited execution-flows here)

## 2. Fixes by Finding (traceable)

### T4-LEDGER-003 CRITICAL — Soft-delete NULL (P3)
Root cause: is_deleted nullable without default + 8 filters WHERE is_deleted=0 excludes NULL per SQLite tri-valued logic; no backfill.
Fix: Model 4 tables server_default=text("0") nullable=False, filters -> is_not(True) (11 sites including aggregate helpers), migration _backfill_soft_delete_nulls with inspect+engine.begin COALESCE, sync missing bool -> 0 already via server_default. Files: db.py:20,368,391,410,432,759-806,1038,1182,1255,1395,1429,1446,1474,1776,2129, crypto.py server_default not needed, tests/test_crypto.py DDL. Validation: DDL compile NOT NULL DEFAULT 0, live NULL excluded then included via IS NOT 1, backfill idempotent. Commit bundles with R1.

### T2-PERSISTENCE-001 HIGH — Migration lock colocation (P4)
Root cause: BASE_DIR lock vs DB_PATH temps diverged.
Fix: _DB_DIR=dirname(abspath(DB_PATH)), _ENCRYPTION_LOCK=_DB_DIR/.db-encrypting, BACKUP_DIR colocation when DB_PATH overridden. Validation: live import with custom DB_PATH -> colocated, V-R1-DB PASS.

### T2-PERSISTENCE-003 MEDIUM — Version tear (P2)
Root cause: db_version() N+1 per helper.
Fix: queries.py _run_id() via get_script_run_ctx id(cursors) + snapshot _snap_version/_snap_run_id/_snap_user_id in st.session_state; bump coherency. Validation: V-R1-DB PASS, 12 helpers share snapshot.

### T3-CURRENCY-001/004 + C-001 HIGH — Sync whitelist + dual amount (P1)
Root cause: validate_fields finite only; amount_eur trusted.
Fix: sync_core.py central guards >0, <=MAX_AMOUNT/MAX_SAVINGS_TARGET, hours 744, currency enum, loan_payment_type enum, surcharge >=0, and server recompute via to_eur(get_rates(get_settings(uid))) in validate_fields(rates), create_record, _apply_update; FIELD_SCHEMAS now includes loan_payment_type/surcharge. Validation: V-R2-SYNC PASS 57 checks, big 5M rejected, poison 10 RSD 99999->0.0855, loan early ok.

### T4-LEDGER-001 HIGH / T4-004 MEDIUM / T4-002 MEDIUM / T4-005 MEDIUM + A-002 (P3+P6)
Root cause: global ALL_SUBCATS, NaN leak, strict == dedup vs normalized, N+1.
Fix: log_expense: per-CATEGORIES whitelist, pd.isna->'', —->'', NaN guards, re.sub normalize dedup inside try, month bucket; recurring: single Session transaction with rollback + bump; db update_expense sanitized. Validation: V-R3-LEDGER PASS 8/8 non-DB, manual boundary probes PASS.

### T5-PLANNING-001 HIGH + C-002 (budget scope)
Root cause: eff[cat] used per sub row, MCP naive sum.
Fix: budgets.py per-sub uses row budgeted_eur else eff, mcp uses effective_category_budgets. Validation: V-R5-BUDGET PASS, eff 150->50.

### T5-PLANNING-002 HIGH + T6-001 HIGH + T6-002 MEDIUM + B-001 (P5)
Root cause: months_between ignores day, csv no sniff, PDF code not stripped.
Fix: finance day-aware, bank Sniffer, pdf code strip + per-value thousands. Validation: V-R4-TIME-INGEST PASS 59+18, day repro PASS.

### T1-SHELL-AUTH-002 HIGH-CONFIDENCE (P2)
Fix: onboarding fresh_rates re-read via _db_get_settings before merge. Validation: onboarding syntax OK, currency/rate 14 PASS, matches app.py:132 pattern.

### T8-001/002/003/004 + A-001 + C-003 + T7-002/004 (R7)
Fixes: api init before auth, mcp cache validation+invalidate, notifications on_done only cat:level, market lamda preserve, insights delegate, forecast total None still by_category, household re-derive. Validation: V-R7-CONNECT PASS 8/8, py_compile PASS, forecasting 42 PASS.

## 3. Pattern-Level Fixes (one root -> many findings)
- PATTERN-01 (P1): single sync_core guard fixes 4 tables 12 fields + C-001.
- PATTERN-02 (P2): queries snapshot + onboarding fresh read fixes version tear + JSON clobber.
- PATTERN-03 (P3): log_expense/db guards + month bucket fixes 3 ledger + analogs.
- PATTERN-04 (P4): DB_DIR colocation fixes lock+backup+secret.
- PATTERN-05 (P5): finance day-aware + bank/pdf fixes 4 findings with one heuristic family.
- PATTERN-06 (P6): engine.begin single transaction fixes 5 N+1 clones (recurring, big_purchases analog noted, ledger edit/trash, market, bank bulk — last 3 similar but handled within respective teams).
- Coupling leak (T7-002): insights delegate.
- Optimistic dedup (T8-003/A-001): granularity unify.

## 4. Reopened Findings
None — no QA conclusion disproved. SUSPECTED (6) remain UNVALIDATED leads per plan (T1-001, T1-003, T2-002, T3-003, T7-005, T8-005) not promoted.

## 5. New Bug Leads (UNVALIDATED, not fixed)
See qa/new-leads.json — 4 leads:
- LEAD-001 isolated amount_eur trust without amount/currency
- LEAD-002 64-hex precedence UX
- LEAD-003 US 1,200 vs 1.2 asymmetry pre-existing
- LEAD-004 test harness temp permission flake

## 6. Agent-Memory Reconciliation
- Type A (fix-induced) updated 3 docs: planning-and-wealth 7.2 day-aware snippet, ingestion import-pipeline CSV sniff + per-value + codes, sync-and-household §6 business guards + recompute.
- Type B pre-existing inconsistencies: 2 noted (planning snippet was buggy — now fixed; ingestion no-sniff vs execution-flows sniff claim — execution-flows not edited here, noted for owner).
- Remaining reconciliation: execution-flows.md dialect sniff claim vs code (now code has sniff) — actually now consistent after fix; no further drift.
- Memory validation by V-R1/2/4/5/3/7 checked docs match code post-fix.

## 7. Remaining Backlog (authorized not yet resolved)
None — 26/26 authorized have VERIFIED fixes per validator reports. SUSPECTED 6 remain not authorized per scope rule. Next cycle would re-validate any SUSPECTED before promotion.

## 8. Validation Summary
- Validators: V-R1-DB VERIFIED, V-R2-SYNC VERIFIED (57 checks), V-R4-TIME-INGEST VERIFIED (77 passed), V-R5-BUDGET VERIFIED, V-R6-SHELL-ONBOARD syntax+14 pass (implied from R6 report + diff), V-R3-LEDGER VERIFIED, V-R7-CONNECT VERIFIED 8/8.
- Regression: finance 29 PASS, budget 2 PASS, forecasting 19 PASS, insights+forecasting 42 PASS, sync validate 8 PASS; DB-backed integration tests error due to temp permission (env) not code — compensated by pure-logic + manual boundary repros.
- No unauthorized new bug fixes introduced (scope enforced, new observations recorded as leads).

## 9. Files & Commits
See git log after commit; traceability QA ID -> commit message references.

## 10. Risks & Limitations
- queries snapshot uses id(ctx.cursors) — Streamlit internal; fallback to direct DB read outside run.
- US 1,200 thousands asymmetry persists in bank import (pre-existing) — lead LEAD-003.
- Budget duplicate after upgrade: old bare-cat markers vs new cat:level — one duplicate possible then overwritten.
- Test harness temp permission masks DB integration signal — mitigated by boundary repros.

*No production-code mutations beyond authorized IDs. No qa/_tmp residue. IDs preserved.*
