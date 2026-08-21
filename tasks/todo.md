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
- [ ] FIN-01 Define and test the canonical virtual unallocated-funds invariant.
- [ ] FIN-02 Persist shared panel collapse/layout state and surface save failures.
- [ ] FIN-03 Implement accessible recurring-category collapse and reorder.
- [ ] FIN-04 Make savings and term-account movements zero-sum and atomic.
- [ ] FIN-05 Model early-withdrawal policy and nest term accounts under goals.
- [ ] FIN-06 Link wishlist items to new or existing savings targets.
- [ ] FIN-07 Complete linked purchases atomically and prevent status bypass/duplicates.
- [ ] FIN-08 Enforce loan payoff invariants and archive paid loans.
- [ ] AI-01 Sanitize tool results before any external provider request.
- [ ] AI-02 Repair missing planner arguments deterministically.
- [ ] AI-03 Retry transient provider failures and preserve deterministic answers/diagnostics.
- [ ] AI-04 Add validated Plotly chart answers, then native OpenAI/Claude adapters.
- [ ] OCR-01 Add real fixtures, line-item extraction, and total reconciliation.
- [ ] OCR-02 Add row-level receipt review and atomic multi-item save.
- [ ] ML-01 Finish empty/candidate/active ML settings states and verify existing work.
- [ ] QA-01 Fix isolated derived-EUR sync creates, comma-thousands CSV parsing, and stale QA docs.

### Checkpoints

- [ ] After FIN-00–FIN-05: full suite passes and the income → unallocated → goal → term lifecycle balances exactly.
- [ ] After FIN-06–FIN-08: linked purchase and loan archive flows pass end to end.
- [ ] After AI-01–AI-04: external prompt privacy, outage handling, and chart safety tests pass.
- [ ] After OCR-01–ML-01: real-receipt benchmark and all ML UI states pass.
