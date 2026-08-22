# Tasks

- [x] Implement regression and packaging changes.
- [ ] Run the Windows installer smoke and clean-machine checks.

## Financial integrity and integration review (2026-08-21)

- [x] FIN-00 Restore a runnable Python 3.12 test environment and record the baseline.
      → `qa/baseline/fin00-baseline.md`: `.venv-fin00` (uv + workspace cache, wheels/TEMP-adjusted),
      streamlit 1.61.1, 24/24 imports, scoped suite **535 passed / 7 failed / 10 errors**
      (cross-validated by two independent runs). Classified: 16 deterministic sandbox-temp
      PermissionErrors (environment), **1 genuine pre-existing bug**:
      `tests/test_ocr_review.py::test_receipt_review_shows_uncertainty_and_reuses_result`
      → `StreamlitAPIException: Forms cannot be nested` at `app.py:120` (fix in OCR-02 scope).
      Bare `pytest` from root still blocked by undeletable foreign-session dirs under
      `data\_pytest_tmp` (ACL principals from another sandbox product); use `pytest tests`.
- [x] FIN-01 Define and test the canonical virtual unallocated-funds invariant.
      → `unallocated_funds_eur()` / `unallocated_breakdown()` in services/finance_queries.py
      with the full cash-effect classification docstring; €0.01 tolerance constant;
      read-time clamps removed from `_recompute_savings_balances` (negatives inspectable);
      `tests/test_unallocated.py` (12 scenarios incl. surcharge-single-count,
      holding double-count, −€250 verbatim, cross-user isolation). Suite: 547 passed /
      7 failed / 10 errors = baseline +12, identical failure set.
      Scope note: the daily-accrual engine swap planned for FIN-01 moves to FIN-04 —
      it couples to interest posting and would force touching summary/page KPI code
      before its numbers become load-bearing. The invariant never reads balance_eur
      (it sums deposited_eur), so clamps were the only FIN-01-relevant part.
- [x] FIN-02 Persist shared panel collapse/layout state and surface save failures.
      → RMW per area via atomic_update_setting_json; sanitize_area with known-id
      filtering; load never raises (warn+default), writes raise LayoutSaveError;
      panel chevrons get accessible labels; failures log + non-fatal st.warning.
      8 new tests; suite 555/7/10 = baseline +8, identical failure set. (`7dd5e0a`)
- [x] FIN-03 Implement accessible recurring-category collapse and reorder.
      → board emits group_order/collapsed_groups with aria-expanded + native
      keyboard buttons; values validated (permutation/subset) pre-persistence;
      recurring page persists via set_area_ids w/ LayoutSaveError→warning.
      Bonus fix: known_ids now actually threads into sanitization (was dropped).
      8 new tests; suite 563/7/10 = baseline +8, same failure set. (`a878dbf`)
- [x] FIN-04 Make savings and term-account movements zero-sum and atomic.
      → commands (deposit/withdraw/open-term/settle/monthly-posting/delete
      guards) with in-txn invariant validation; accrual_key/settlement_ref
      partial unique indexes; posted-balance read chain + pending column;
      page rewired. 14 new tests incl. both locked acceptances (3.10 / 2.25);
      suite 577/7/10 = baseline +14, identical failure set. (`c499fb0`)
- [x] FIN-05 Model early-withdrawal policy and nest term accounts under goals.
      → early_annual_rate column + calculate_term_payout (matured=full rate,
      early=agreed rate or principal-only); explicit preview text; accounts
      render once inside goal panels, true orphans separate; rename carries
      layout state; KPIs on posted+pending. 8 new tests; 585/7/10. (`b0ffbae`)
- [x] GATE B financial foundation checkpoint.
      → end-to-end lifecycle test (income→unallocated→goal→term→goal→pool)
      with total-value invariant and single realized-interest income row;
      non-destructive open proven; full suite 587/7/10, identical failure
      set. (`410d943`)
- [x] FIN-06 Link wishlist items to new or existing savings targets.
      → funding_goal_ref anchors to a Savings row id (rename-safe); 3-way
      funding choice; vanished goals render a warning. (`95127f9`)
- [x] FIN-07 Complete linked purchases atomically and prevent status bypass/duplicates.
      → buy_wishlist_item one-txn (goal debit w/ unique settlement_ref +
      expense + status stamp); refund reverses exactly; bought removed from
      free selector. 16 new tests. (`95127f9`)
- [x] FIN-08 Enforce loan payoff invariants and archive paid loans.
      → record_loan_payment single-txn with inclusive surcharge + audited
      split; overpay/archive guards; LoanError hierarchy. 14 tests. (`11a5d5c`)
- [x] AI-01 Sanitize tool results before any external provider request.
      → ai.safety boundary applied at llm.py egress; credentials/paths/
      emails redacted, counts-only logging; local providers keep context.
      33 new tests. (`828c2d4`)
- [x] AI-02 Repair missing planner arguments deterministically.
      → repair_missing_dates fills year/month from question before any model
      round; coercion; ambiguity → clarification; schema-aware repair prompt;
      capabilities no longer claim native tools. (`b79a1d0`)
- [x] AI-03 Retry transient provider failures and preserve deterministic answers/diagnostics.
      → 429/5xx retried ≤2× with capped Retry-After; permanent 4xx never;
      persistent 503 → temporary-unavailability diagnostic; sanitized
      payload resent identically. (`b79a1d0`)
- [x] AI-04 Add validated Plotly chart answers, then native OpenAI/Claude adapters.
      → spending_series tool + validate_chart_spec whitelist (data always
      canonical rows; no code/HTML); ask.py re-validates before plotting;
      OpenAI json_object planner path; native Claude adapter w/ own auth +
      retries; API-family setting + egress disclosure. (`77cd6d4`)
- [x] OCR-01 Add real fixtures, line-item extraction, and total reconciliation.
      → line_item_extractor (signed amounts, qty×unit rows, both decimal
      formats); reconcile() reports delta/ok within €0.01; 5 real rendered
      receipt images + manifest expectations incl. mismatch case. (`f6f27b3`)
- [x] OCR-02 Add row-level receipt review and atomic multi-item save.
      → per-row keep/edit UI + low-confidence/mismatch gates with explicit
      confirm; save_receipt_items one-txn all-or-nothing w/ retained
      receipt total; nested receipt form removed (recorded bug fixed in
      isolation); cloud fallback default-OFF opt-in. (`f7e9012`)
- [x] ML-01 Finish empty/candidate/active ML settings states and verify existing work.
      → db deactivate/discard (audited, non-destructive); Settings ML tab
      EMPTY/CANDIDATE/ACTIVE/history states; retrain-while-active creates a
      new candidate, never mutating the active row; 9 state-machine tests.
      (`9cdc783`)
- [x] QA-01 Fix isolated derived-EUR sync creates, comma-thousands CSV parsing, and stale QA docs.
      → sync CREATE rejects *_eur without its base amount (per-change
      failure, never applied); legit creates stay server-computed and a
      client lie is overwritten — both pinned; CSV parser pinned equal to
      the PDF parser on comma/dot-thousands + US/EU mixed forms; README
      stale claims refreshed. Root cause of the last AppTest flake found:
      bare-mode page import leaked an open form context into later runs →
      amount guard moved to domain/validation.is_valid_amount. Full suite
      GREEN: 748 passed / 0 failed / 0 errors. (`316fb01`, `0ef2b23`)
- [x] PKG-01 Package as Windows executable and per-user state dirs.
      → spec ships services/ai/ingestion/ml/domain/ui/infra as loose files
      (pages run from disk; their imports are invisible to analysis);
      smoke gate strict on streamlit+sqlcipher3+app.py, tolerant on the
      optional llama_cpp runtime; bundle audit + logic tests (3);
      pyinstaller 6.16.0 clean build exit 0 with all new modules in
      _internal. Inno Setup stage skipped (not installed); exe execution
      blocked by host Application Control → verified at logic level.
      Per-user state dirs pre-existing and documented
      (app_paths.state_dir: EXPENSE_TRACKER_DATA_DIR → frozen
      %LOCALAPPDATA%\ExpenseTracker). Full suite 751/0/0. (`884e7a0`)

### Checkpoints

- [x] After FIN-00–FIN-05: full suite passes and the income → unallocated → goal → term lifecycle balances exactly. (Gate B, `410d943`)
- [x] After FIN-06–FIN-08: linked purchase and loan archive flows pass end to end. (648/7/10 combined-tree run)
- [x] After AI-01–AI-04: external prompt privacy, outage handling, and chart safety tests pass. (Gate C — sanitizer 33T, retries 9T incl. persistent-503 + sanitized-resend, charts 16T incl. injection/canonical-value pins; full run 696/7/10)
- [x] After OCR-01–ML-01: real-receipt benchmark and all ML UI states pass.
      (5-image benchmark green since `f6f27b3`; ML states via `9cdc783`;
      superseding duplicate line above closed)
### Advisor / ML / UI improvement wave (research.md)

- [x] Phase L wave 1 (b202584): planner date+schema context pack; routing fixes
      (eval hits 54->61, hijacks 8->1 allowed coach divergence); validated pie/bar
      chart answers for breakdown fast-routes; dead answer_query path deleted.
      Gate: AI subset 75 passed (+8 documented env errors), safety/providers 56,
      MCP 15. Smoke: ask page renders, suggestions route deterministically.
- [x] L3 proposal confirm (d1acf31): audited set_budget command with full
      server-side re-validation; ask.py Confirm pops the stored proposal before
      apply (double-click safe); proposal pins year/month at detection time.
      Gate: tests/test_proposal_confirm.py 11/11.
- [x] L6 ask-page fragment SKIPPED deliberately (rationale in research.md).

