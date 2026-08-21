# QA Audit + Remediation — INTERIM LEDGER (2026-08-21)

> Coordinator ledger for the post-remediation audit & fix campaign. Supersedes nothing; complements qa/reports/final-qa-bug-map.md (2026-08-20).
> Status when written: 7/8 fixes landed & verified; 11 validators in flight (H6 x3, FIN x4, ING x4).

## A. CONFIRMED FINDINGS -> FIX STATUS

| ID | Finding | Location | Sev | Verdict | Fix |
|----|---------|----------|-----|---------|-----|
| AUTH-001 | Login user-enumeration timing oracle (miss path skips bcrypt; ~162ms delta, measured) | auth.py login_user | HIGH | CONFIRMED (2x independent, probe) | LANDED: lazy dummy-hash verify on miss; tests/test_auth_login_timing_guard.py; verified exit 0 |
| UI-001 | ui_layout settings key silently dropped (no column; save returns True) -> layout persistence no-op | db.py UserSettings/_SETTINGS_DEFAULTS/_migrate, ui/layout_state.py | MEDIUM | CONFIRMED (probe + schema) | LANDED: JSON column + default + migration entry; tests/test_layout_persistence.py 4 tests; 15/15 |
| AI-001 | Raw tool results embedded into LLM prompt (legacy llm path sanitizes, orchestrator path did not) | ai/orchestrator.py _compose_answer + planner | MEDIUM | HIGH-CONFIDENCE (household cross-member claim falsified) | LANDED: ai/safety.sanitize_tool_result walker at both embed sites; tests/test_ai_safety.py 15 tests |
| NOTIF-005 | Budget alert email hardcodes EUR, ignores DC/rates (dead rate param) | notifications.py build_budget_alert_email | LOW | CONFIRMED (code) | LANDED: fmt(.,DC,rates) + signature change; test_notifications 11/11 |
| SYNC-ENUM | income_type accepted by sync with any string (MCP rejects; breaks Hourly/Salary branching) | sync_core.py validate_fields | MEDIUM | CONFIRMED (probe, e2e) | LANDED: INCOME_TYPES enum gate; test_sync 25/25, test_mcp 15/15 |
| SYNC-RESURRECT | Device can un-delete tombstoned rows via is_deleted=false when cursor >= deletion (no conflict) | sync_core.py _apply_update | MEDIUM | HIGH-CONFIDENCE (snapshot tombstone shipping itself = INTENDED, rejected as defect) | LANDED: undelete->conflict gate; tests/test_sync_resurrection.py 5 tests; 30/30; comment accuracy fixed by coordinator |
| INF-LEDGER | inf via batch editor poisons ledger sums (pd.isna misses inf; unbounded NumberColumn; sink unguarded) | log_expense.py + services/commands.py | HIGH | CONFIRMED (empirical e2e) | LANDED (both layers): _valid_amount gate + bounded NumberColumn; bulk sink finite/cap validation + rejected counter; final tests pending fixer report |
| H6-1 | settings_ai provider selectbox stale after external write | app_pages/settings_ai.py | - | REJECTED (no external writer exists; latent hardening lead) | - |
| H6-2 | Edit-dialog fixed widget keys -> stale carry-over across entries (AppTest-proven data corruption on Save) | big_purchases/savings(x2)/log_income/loans (27+11 keys row-scoped) | HIGH | CONFIRMED | FIXED+VERIFIED (source-guard + AppTest sim; loans pass done by coordinator) |
| H6-4 | recurring _persist_grouped_order eats clicks on rerun (RerunException unwinds before action handlers; one-shot self-healing window) | app_pages/recurring.py | LOW-MED | CONFIRMED (Streamlit runtime proof) | FIXED+VERIFIED (gate persist on 'if not action'; spy tests) |
| FIN-1 | Negative-amortization loan reports false payoff date (max(remaining,1) clamp defeated honest-zero branch; total_interest_remaining lied) | finance.py loan_schedule + loans.py caption | HIGH | CONFIRMED (probe) | FIXED+VERIFIED (clamp removed; None = never-at-this-payment; regression test) |
| FIN-2 | rates.get(cur,1.0) silent 1:1 fallback for unknown currency | domain/money.py | - | REJECTED (all ingresses gated by SUPPORTED_CURRENCIES; hardening lead: enforce validate_currency at persistence) | - |
| FIN-4 | savings_projection includes opening deposit in monthly run-rate ('Goal in ~Nmo' inflated) | services/finance_queries.py | MEDIUM | CONFIRMED (dashboard avg_dep variant rejected as by-design) | FIX RUNNING |
| FIN-5 | forecast NaN/inf escapes 'is None' guard -> garbled '€nan' display from legacy/corrupt rows (new-write paths hardened) | forecasting.py _candidate_prediction/forecast_next_month + app_pages/forecast.py | LOW | CONFIRMED (repro) | FIXED+VERIFIED (_finite() candidates + fallback guards + page guard; regression tests) |
| ING-C1 | _NOISE_RE per-cell on descriptions eats merchants ('TOTAL ENERGY DRINK'->'Bank transaction') | pdf_import.py parse_table_rows | HIGH | CONFIRMED (git intent: line-level only) | FIXED+VERIFIED (clause removed; 30->38 tests) |
| ING-C2 | Page footers: bare '2' becomes amount / noise line clears pending_tx losing whole tx | pdf_import.py parse_text_lines | HIGH | CONFIRMED (probes) | FIXED+VERIFIED (_is_real_amount_token threshold; noise lines skip without clearing) |
| ING-A | total-extractor scoring (cash-over-total; date phantoms; adjacency/largest boosts outrank penalized totals) + _AMT_RE truncation ('1.234'->1.23) | ingestion/receipt/total_extractor.py | HIGH x4 | CONFIRMED | FIXED+VERIFIED (date-strip; boost reservation for total lines; penalties exclude largest/adjacency boosts; deterministic tiebreak; regex '?'; 39 tests + coordinator probe all four repros OK) |
| ING-B1 | NaN description from cleared data_editor cell stored as literal 'nan' | bank_import.py _save_edited_row | MEDIUM | CONFIRMED (float('nan') truthy) | FIXED+VERIFIED (log_expense-style coercion; 14 new tests) |
| ING-B2 | Signed thousands '-1.234'/'1,234'/'-1,234' parsed as decimals (1000x error); pdf_import divergence | bank_import.py _to_numeric_locale | HIGH | CONFIRMED (parity matrix) | FIXED+VERIFIED (sign-strip both dialects + _pure_comma_thousands; parity test) |
| ING-B3 | Generic CSV picks Currency column -> silent TOTAL import failure for any non-'amount' column name (Value/Betrag/Iznos...) | bank_import.py normalize_bank_csv generic branch | HIGH | CONFIRMED (no mapping UI exists) | FIXED+VERIFIED (alias search incl. localized names before last-column fallback) |
| ING-D1 | pass2 flat token join biases pick-better comparison; line_conf keyed by line_id read by split-index loses OCR bonuses | ingestion/receipt/service.py raw_text2 | MEDIUM | CONFIRMED (probes) | FIXED+VERIFIED (_tokens_to_text groups by line_id both passes) |
| ING-D2 | guess_merchant pre-fills form while warning says 'could not read' (contradiction) | ingestion/receipt/service.py _to_compat_dict | HIGH | CONFIRMED (probe) | FIXED+VERIFIED (fallback becomes FieldCandidate conf=0.0 via dataclasses.replace; low-confidence warning) |

## B. REJECTED (falsified / intentional) — do not re-raise

- create_record same-user collision "stuck retry loop" — retry routes through _apply_update and heals; race yields one failed entry.
- Equal-timestamp sync conflict edge (strict >) — implements documented "edited AFTER" rule; needs microsecond collision.
- Snapshot ships soft-deleted tombstones — INTENDED deletion-propagation design (sync-and-household.md:260); guarded by regression test (e).
- H6-3 dashboard household_id staleness — claimed partner-removal flow does not exist; only self-service leave_household which self-invalidates + bumps household revisions. (Lead: dashboard could re-derive via _household_id.)
- FIN-3 zero-cost-basis gain_pct=0 — percentage mathematically undefined; display-choice edge (lead only).
- FIN-6 duplicate loan names allowed — refinancing-legitimate; UX lead only.
- ROUTER-001 naive brace-matching JSON fallback — only reached on already-malformed JSON; allow-list caps impact (lead only).
- MCP _invalidate_user_cache dead code — existence check covers delete/recreate; env swap impossible in-process (hardening note).

## C. KNOWN-OPEN (pre-existing backlog, re-confirmed)

- LEAD-001: sync CREATE with only *_eur stored verbatim (no recompute) — re-confirmed by e2e probe this cycle; cap still enforced (<=MAX_AMOUNT); requires paired device.
- LEAD-003: bank_import parses US "1,200" as 1.2 (pdf handles correctly) — re-confirmed still broken.
- LEAD-004 (NEW, HIGH): deprecated /api/sync v1 endpoint still accepts client-supplied 'since' -> conflict-detection bypass (api.py ~106-119; v2 uses server cursor). Report-only finding from IMP-C; needs validation + fix decision (disable or delegate to v2 logic).
- LEAD-005 (NEW, systemic): logging is never configured anywhere — all module logger.warning/error calls are dropped by default (no basicConfig/handler in app.py/api.py). IMP-C; operability lead.
- LEAD-006 (NEW, SUSPECTED): recurring.py edit_template_dialog has 9 widgets + buttons with NO key= at all (auto-generated positional keys -> possible stale-state collision, H6-2 class); household.py leave buttons likewise keyless. REF-D analysis-only — validate before fixing.
- LEAD-007 (NEW): dual flash protocols — loans/savings/big_purchases use ('severity', msg) tuples, recurring/portfolio/travel use flat strings -> latent TypeError if keys migrate. REF-D.
- Refactor/improvement campaign COMPLETE (report-only): 7 reports filed (REF-A data layer, REF-B ingestion incl. critical _AMT_RE find absorbed into ING-A, REF-C finance/insights, REF-D pages/UI, IMP-A UX consistency, IMP-B product, IMP-C ops). Consolidated summary due at close.

## D. HOUSEKEEPING

- DONE: grep_results.txt and qa/_tmp_audit/verify_inf_probe.py deleted; tests/test_probe_*.py + tests/baseline_out.txt already absent.
- qa/_tmp_audit/* validator/fixer probes kept as evidence (per owner).
- Pre-existing test-env noise (NOT from this campaign): test_llm.py tmp_path PermissionErrors; test_ask_page_error_does_not_pollute_history setup WinError 5; github_backup temp-permission failures (4); pytest cache-dir teardown warnings.

## E. VALIDATION RULES USED

Reachable causal chain required; independent validator per material candidate; intent adjudication against agent instructions/ docs; probes under qa/_tmp_audit/; production code touched only by scoped fix teams.
