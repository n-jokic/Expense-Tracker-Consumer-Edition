# Expense Tracker Consumer Edition — 11-Item Fix & Feature Plan

> Tracking doc. Tick items as phases complete. Created at planning time; each
> item carries acceptance criteria. Execution order: A1 → A2 → B1 → B2 → B3 →
> C1 → C2 → D1 → D2 → E (E may start in parallel with D once A1/A2 land).

## Ground rules
- Q&A decisions locked in: one-tap edited **in the dashboard panel**; tap-time price adjust with instant default; pool overview includes **budgets + goals + emergency fund + upcoming-bills reserve**; OCR = **full pipeline rework**; raises = **history with effective dates**; recurring = **full drag-and-drop**; repayment fee booked **as expense shown on loan**; drained goals **auto-archive (restorable)**; auto-allocation runs **on every income log**, targets = **goals, emergency fund, remainder rule, extra loan payments**.
- Per-phase: `python -m pytest` stays green; manual smoke per affected tab recorded here.

## Key discoveries (shape the plan)
- Unallocated-funds math **already exists** (FIN-01): `services/finance_queries.py:100-139` (`unallocated_breakdown`, `unallocated_funds_eur`) — never rendered anywhere.
- Drag-and-drop + collapsible board **already exists** for Recurring (`app_pages/recurring.py:295-327` → `ui/board.py grouped_board`, persisted via `ui/layout_state.py`). Item 7 = *verify-at-runtime + consistency*; new dep only if runtime verification proves the board broken.
- Goal-link dropdown **already exists** but gated behind a 3-way radio (`big_purchases.py:140-165`).
- Early-repayment fee booking already matches chosen design (`services/commands.py:790-882`); missing piece = at-repayment-time override (`loans.py:248-251` uses stored values only).
- Crash roots: `r_cur` used at `log_expense.py:182`, assigned at :224 (widget order); `pct` at `savings.py:705` clamps top only.
- OCR roots (verified): optional-decimals regex admits phone numbers (`total_extractor.py:18`); hyphenated "Sub-total" gains +5 while dodging −4 (`:12,:13,:106`); "AMOUNT" alone not a key; currency regex `(…|€|…)` can never match symbols/glued codes, `user_locale` never passed (`currency_extractor.py:8-35`, `service.py:56`); min-max normalization collapses single date candidate to 0.2 conf (`date_extractor.py:75-81`).
- Goals have **no status field** — `goal_name` groups of `savings` rows; soft delete + Trash/Restore exist (`db.py:1653-1674`, `savings.py:906-940`) → reuse for archive.
- Settings persistence pattern ready-made: JSON columns on `user_settings` + `_SETTINGS_DEFAULTS` + `_add_missing_columns` + `atomic_update_setting_json` (`db.py:2161-2214`).
- "Emergency Fund" is a default **goal name** (`domain/taxonomy.py:32`), no special entity → treated as a normal goal.

---

## Phase A — Bug fixes (crashes first)

### [x] A1. NameError `r_cur` (item 3)
- `app_pages/log_expense.py`: hoist receipt-review widgets (currency `r_cur`, category `r_cat`, other `rcpt_*` inputs, :218-228) **above** the item-review block (:131-216). Keep identical keys/session state.
- Acceptance: OCR on test image completes with zero exceptions; kept rows convert with selected currency.

### [x] A2. Progress-bar crash + clamp sweep (item 6)
- Add `utils.progress_ratio(value, target) -> float` clamped [0.0, 1.0] (None/≤0 target → 0.0).
- Replace unclamped sites: `savings.py:705/716`; `dashboard.py:397/399`, `:420/425`; `forecast.py:159-161`; `travel.py:84/94`; `budgets.py:63/66`, `:171/174`; `rewards.py:66/70`.
- Clamped-from-negative shows hint text (e.g. "overdrawn"); write-side negative producers stay as-is (intentional no-clamp ledger policy, `db.py:1499-1500`).
- Acceptance: negative-balance goal renders 0% + hint, no exception.

### [x] A3. Recurring tab runtime verification + consistency (item 7)
- Run app (global Python 3.12 + Streamlit 1.61), exercise board: drag card/group, collapse, Alt+arrows, reload → verify `group_order`/`collapsed_groups` persist in `ui_layout`.
- Replace silent `except Exception:` fallback (`recurring.py:324-325`) with logging + visible warning.
- Visual pass: chrome aligned with other tabs.
- **Decision gate**: only if `grouped_board` cannot work here → add `streamlit-sortables` and re-wire; otherwise no new dep.
- Acceptance: drag reorder persists across rerun; collapse persists; keyboard path works.

## Phase B — Small features

### [x] B1. Early-repayment fee % editable at repayment time (item 8)
- `loans.py early_repayment_dialog` (:237-294): surcharge-mode selectbox (`fixed`/`percent`) + value input, defaulting to loan's stored values, live caption recompute.
- Optional "Save as default for this loan" checkbox → persists via existing loan-update path.
- Booking unchanged (`record_loan_payment` fee-inclusive expense + audited split).
- Acceptance: % override reflected in total/split/notes; default-save round-trips.

### [x] B2. Goal linking via real dropdown (item 9)
- `big_purchases.py:140-165`: replace 3-way radio with one selectbox **"Savings target"**: `[(no target — pay from unallocated), (➕ create new target…)] + sorted(goal names)`; "create new…" reveals name + target inputs inside existing form.
- Adjust validation (:172-175) and `fund_src/fund_ref` resolution (:186-199). Edit dialog unchanged.
- Acceptance: link/unlink/create-new flows save with correct stable ref; no typing of existing goal names anywhere.

### [x] B3. Auto-archive drained goal after purchase (item 10)
- Hook in `buy_wishlist_item` (`services/purchase_commands.py:235-359`) post-debit pre-commit: remaining principal ≤ €0.005 AND no other non-bought purchases sharing `funding_goal_ref` AND no active term accounts → soft-delete goal rows + audit `BUY`/`auto_archived_goal`.
- Symmetric restore in `refund_wishlist_item` (:362-417): if refunded item's goal resolves to nothing alive → restore its soft-deleted rows + audit `AUTO_RESTORE`.
- Restorable via existing Trash expander (`savings.py:906-940`).
- Acceptance: buy drains single-linked goal → goal archived; refund brings it back.

## Phase C — Medium features

### [ ] C1. Fully configurable one-tap presets (items 1 + tap-time price)
- New JSON column `user_settings.quick_presets` (`{id,label,amount,currency,category,subcategory,description}` list) via defaults+migration+whitelist. Empty ⇒ built-in three.
- Dashboard panel: header **✏️ Edit** action (`panel(actions=…)`, `ui/panel.py:81`) toggles inline editor (label, amount, currency, category/subcategory, description, remove, add preset); saves via `save_settings`.
- Tap-time adjust: primary button logs instantly at preset amount (dedupe extracted into shared helper); small ✎ opens prefilled `st.dialog` → Log. Convert via `to_eur` at save.
- Update `tests/test_app_smoke.py:232-244`.
- Acceptance: add/edit/remove/reorder presets persists; instant tap + ✎-adjust paths both dedupe-safe.

### [ ] C2. Raise history with effective dates (item 5)
- New `SalaryRaise` model (id, user_id FK, amount, currency, effective_date, income_id nullable, note, created_at) — table auto-created by create_all; accessor `salary_raises(user_id)`.
- Raise flow (`log_income.py:200-205`): insert history row (effective_date = income date) AND overwrite `salary_amount`. Existing readers (forecast, hourly fallback, gamification, big-purchases) keep reading setting — zero regressions.
- Render raise list inside "My fixed salary" expander.
- Assumption: raise applies immediately going forward; no retroactive recomputation.
- Acceptance: applying raise inserts row, updates setting, forecast unaffected, list renders.

## Phase D — Dashboard transparency & automation

### [ ] D1. "Where your money goes" panel (item 2)
- New `PanelSpec(id="dash_allocation")` in personal-panel cluster:
  - Headline metric **Unallocated now** (`unallocated_funds_eur`).
  - Breakdown from `unallocated_breakdown()`: per-goal allocations (`get_savings_summary`), locked terms, holdings — expandable rows.
  - Planning layers labelled outside FIN-01: monthly budgets total vs spent (`effective_category_budgets`) + **upcoming-bills reserve** (active recurring due this month minus logged, reusing `rec_template_id` month-gate pattern from `recurring.py:114-135`).
- Read-only consumer of existing services; no invariant changes.
- Acceptance: numbers reconcile with FIN-01 tests; panel collapses/persists like others.

### [ ] D2. %-auto-allocation of income (item 11)
- `user_settings.auto_alloc_rules` JSON: `{enabled, targets:[{type:"goal"|"loan", ref, pct}]}` (+ migration trio as C1).
- New service `apply_auto_allocations(user_id, income_amount_eur, income_date)` called from both income-save paths (`log_income.py` one-tap :87-111, form :194-205) after insert, before `bump_db_version`:
  - goal targets → `deposit_to_goal` (pool-validating);
  - loan targets → `record_loan_payment(payment_type="early", surcharge=0)` capped at remaining balance;
  - requested > unallocated pool → **pro-rata scale down** + toast; per-rule failures never abort income save;
  - remainder rule = display-only caption (100 − Σpct)%.
- Dashboard `PanelSpec(id="dash_auto_alloc")` editor: enable toggle, target rows (type selectbox, live goals/loans selectbox, pct, remove), add-row, Σ%>100 warning, remainder caption, last-run summary. Help text states loan targets move real money as early repayments at log time.
- Acceptance: rules persist; salary log triggers allocations; pro-rata scaling verified; FIN-01 preserved.

## Phase E — OCR pipeline rework (item 4)
Engine/preprocessing (RapidOCR, cache, warp) stay; extraction + confidence reworked:
1. [ ] **Totals** (`total_extractor.py`): mandatory decimal-cents regex; exclude tel/fax/url lines; grand-total keys incl. exact `amount`, `balance due`, `grand total`, `ukupno`; penalties match `sub-total`/`sub total`/`subtotal`/`medjuzbirka`; de-dup equal-value candidates merging reasons. Ticket receipt ⇒ **84.80 top**, 76.80 second, phones gone.
2. [ ] **Currency** (`currency_extractor.py`): rebuild pattern without impossible `` boundaries; thread `user_locale`/default currency through `service.analyze_receipt` (:56) so 0.3-conf fallback activates instead of `[]`.
3. [ ] **Dates** (`date_extractor.py:75-81`): single candidate skips min-max normalization — absolute score mapping (≥LOW_CONF when structurally valid).
4. [ ] **Shared confidence**: one normalize helper in `confidence.py` replacing three copied formulas (fixes totals stuck under 0.80 pass-2 gate).
5. [ ] **Line items** (`line_item_extractor.py`): stricter row grammar (trailing amount required; tel/address/header lines excluded); keep accept/edit/reject UX + mismatch warning.
6. [ ] **Page wiring** (`log_expense.py`): pass default currency into analysis; "assumed <DC>" badge when currency came from fallback.
7. [ ] **Regression tests**: extractor fixtures incl. ticket's raw text asserting total ranking, phone exclusion, currency detection/fallback, date confidence ≥ threshold, reconcile behaviour.

## Tests & acceptance (per phase)
- `python -m pytest` green; new tests: quick-presets round-trip + dedupe; raise-history insert + readers unaffected; auto-alloc pro-rata/loan-cap/FIN-01; buy→auto-archive + refund→auto-restore; progress clamps; OCR fixture suite.
- Manual smoke checklist per affected tab recorded below as phases land.

## Out of scope / deferred
- Write-side guards against legitimately-negative goal ledgers (display clamps ship; ledger policy intentional).
- PaddleOCR advanced stack; OCR preprocessing changes; schedulers beyond the chosen on-income trigger.

## Progress log
- [x] Plan stored (this file).
- [x] A1 done — rcpt_cat/rcpt_cur hoisted above item review; tests/test_ocr_review*.py + test_app_smoke.py: 16 passed.
- [x] A2 done — utils.progress_ratio added; clamped savings/dashboard/forecast/travel/budgets/rewards; overdrawn hint on goals. tests/test_progress_ratio.py (5) + smoke/ui suites: 43 passed. Note: test_ask_page_error_does_not_pollute_history errors at fixture setup on ANY tree (pytest temp-dir PermissionError under this sandbox) — pre-existing, unrelated.
- [x] B3 done — buy drains goal + no other unbought links + no active terms => same-tx soft-delete (AUTO_ARCHIVE audit); refund restores only AUTO_ARCHIVE'd goals, never resurrects the purchase's own debit or user-deleted goals. tests/test_purchase_auto_archive.py 5/5; purchase+unallocated+smoke: 32+8 passed. Gotcha documented in test helper: outside Streamlit runtime, queries.db_version() is a session-local counter so ttl-cached readers need explicit .clear() in tests.
- [x] B2 done — add-form funding is one selectbox (unallocated / create-new / existing goals); create-new reveals name+target inputs; resolution + validation rebranched; no stale radio refs. purchase+smoke+ui suites: 32 passed (1 env setup error, pre-existing).
- [x] B1 done — early-repayment dialog: fee mode/value widgets pre-filled from loan terms, live surcharge recompute, optional "Save as this loan's default fee" via update_loan. Loan + smoke suites: 42 passed.
- [x] A3 done — **root cause found & fixed**: (a) grouped_board declared `collapsed_groups`/`group_order` state keys without matching on_*_change callbacks → Streamlit rejected EVERY invocation, silent fallback meant drag/collapse never worked (also broke Big-Purchases board); added the missing callbacks. (b) both module-level component caches went stale vs the per-runtime bidi registry ("Component not registered") → register per render in ui/board.py; utils.draggable_card_board now delegates to the canonical board. Fallback is now loud (logged + visible warning). tests/test_recurring_board.py: canonical path renders, no fallback fired, layout persists. Full tests/: **742 passed**; all 6 failed + 11 errors are sandbox PermissionError-at-setup family (verified environmental). No streamlit-sortables needed — decision gate resolved by fixing the existing board.
