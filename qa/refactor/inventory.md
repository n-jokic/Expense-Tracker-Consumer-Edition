# Refactor inventory (R0) — characterization baseline

> No behavior changes were made in R0. Each sub-inventory was produced by a
> dedicated inspection agent (no file ownership conflicts) and lives in its own
> file:

- `qa/refactor/reactive-state-audit.md` — every `st.form` and whether a widget
  inside that same form drives another widget's existence/choices/label/bounds.
- `qa/refactor/write-path-inventory.md` — every mutating user action, its DB
  call chain, commit count, audit/bump coverage and atomicity.
- `qa/refactor/read-calc-inventory.md` — every finance calculation, its
  implementation sites (Streamlit/MCP/insights/LLM/forecasting), duplication,
  and its canonical-service home.
- `qa/refactor/utils-inventory.md` — every symbol exported by `utils.py`,
  classified by concern and mapped to its post-R6 destination.

This document is the **index + cross-cutting summary** those four inventories
support. It does not duplicate them.

---

## 1. Repo snapshot at R0

- **HEAD at inventory time:** `462efbe` (docs-only tip;
  `dab118a` was the last code change, the 26-finding remediation wave).
- **Primary code under inspection** (7 files + `app_pages/`):
  `app`, `auth`, `bank_import`, `crypto`, `db` (2,302 lines),
  `finance`, `forecasting`, `gamification`, `insights`, `llm`, `market_data`,
  `mcp_server` (586 lines), `notifications`, `ocr`, `pdf_import`, `queries`,
  `rates`, `sync_core`, `utils` (731 lines).
- **Tests considered:** every file in `tests/` plus the `test_app_ui.py`
  / `test_app_smoke.py` and cache-revision / categorizer suites cited in the
  spec.

## 2. Cross-cutting findings

### 2.1 Reactive Streamlit state

28 forms total; **8 flagged** (see `reactive-state-audit.md` §1). The tight loop is:

```
provider/category/currency selectbox (inside form) → other widgets' existence/choices/label
```

Only in a Streamlit form is this actually a defect: widget values inside a
form are not re-rendered until submit. The P0s are `ai_form` (provider
→ local/API branch, the canonical case) and `receipt_form` (`r_cat` → `r_sub`
choices); the remaining 6 follow the same pattern.

### 2.2 Atomicity of mutating paths

Every `db.add_*`/`update_*`/`soft_delete_*` function is one `get_session()`
commit (its `log_audit` row in the same commit). The two non-obvious cases
are `add_budget` (`flush` inside, but still one commit) and
`q.save_settings` (2 commits: settings + `bump_data_revision`).

Loop-of-commits handlers (see `write-path-inventory.md` §summary):
`log_expense` inline edit / bulk trash, `bank_import` bulk import,
`big_purchases` reorder, `market_data` portfolio refresh,
`sync_core` change batch. Recurring reorder was already consolidated to
**one** transaction/single bump (T4-005+A-002); `big_purchases` reorder
had not.

Highest-value *uncompensated* two-commit sequence: `big_purchases.py`
"Confirm & log expense" — `add_expense` then `update_big_purchase(status="bought")`.
Loan payment/early-repayment are the same two-commit sequence but **do**
compensate (`soft_delete_expense` of the just-created row on failure).

### 2.3 Read/calculation duplication

The duplication rank (see `read-calc-inventory.md` §7) is led by:

1. category `groupby(...).sum()` (~11 sites);
2. "sum this month's expenses" (~6 sites — dashboard/MCP/insights/LLM/notifications);
3. `sum(effective_category_budgets)` (MCP + gamification + forecast);
4. budget vs actual comparison at `NEAR_LIMIT_THRESHOLD` (dashboard/notifications/budgets);
5. portfolio value holdings→EUR (portfolio/dashboard/savings).

`mcp_server.py` re-implements `_expense_summary_impl`, `_list_expenses_impl`
(`total_eur` sum), `_list_income_impl` (`total_eur` sum), and
`_list_savings_goals_impl` (latest per-goal balance/target) inline.
Everything else MCP currently does is plumbing/formatting (`_clean`,
`_records`, `_month_bounds`) or thin delegation to `insights.py`/`llm.py`
that re-fetches data the canonical stats builder already has.

The layering blocker is **Streamlit at module top**: `insights.py`,
`utils.py`, `forecasting.py`, `queries.py` all `import streamlit`, so a
service used by `mcp_server.py` must not transitively import Streamlit.

### 2.4 `utils.py` decomposition target

57 exported symbols classified (see `utils-inventory.md`):

- **domain** (35): full currency engine, `CATEGORIES`/`TAXONOMY_MIGRATION`,
  date math, `effective_category_budgets`, planning/FX pools, travel pools.
- **UI** (9): `draggable_card_board`, `help_expander`, `inject_mobile_css`, …
  plus the colour palettes.
- **formatting** (4), **exporting** (3), **networking** (5), **legacy** (1:
  `BACKUP_RETENTION_DAYS`).

No dead symbols and no merchant-normalization symbols (that concern lives in
the ingestion layer, as intended). The R6 migration map in the companion file
should be treated as authoritative.

---

## 3. What R1–R6 must do (and must not)

**Must do**

- **R1** — fix the 8 same-form dependencies (see §2.1 and the per-form detail
  in `reactive-state-audit.md`); measure success with the render-based tests
  the spec calls for (switching provider/category/currency inside a form and
  asserting the sibling widget re-renders *before* submit).
- **R2** — extract the MCP inline aggregations plus the `insights.py` pure
  helpers (`month_over_month`, `top_category_this_month`, `unusual_expenses`,
  `days_until_budget_depleted`, `savings_projection`, `build_narrative_stats`)
  into a Streamlit-free `services/finance_queries.py` (plus `services/` peers);
  make MCP an adapter (`@server.tool` wrappers + `_clean`/`_records`) with no
  finance arithmetic.
- **R3** — consolidate multi-record writes to one transaction / one audit
  group / one revision bump; give pages a `CommandResult(changed, revision,
  affected_ids)` rather than SQLAlchemy details.
- **R4** — a canonical `domain/validation.py` (and `taxonomy`/`money`/`periods`
  peers) that Streamlit / bank-import / OCR / MCP / sync / future AI all call,
  so an OCR or LLM proposal is validated like any other entry.
- **R5** — a `domain/merchant.py` deterministic normalizer (`MerchantMatch`)
  that bank-import / OCR / recurring detection / analytics reuse.
- **R6** — split `utils.py` per the classification (§2.4), leaving temporary
  compatibility re-exports in `utils.py` so no PR touches 30 files.

**Must not**

- rewrite `db.py` as a 2,300-line big-bang (`repositories/` can come later,
  after services have taken the pressure off `db.py`);
- add finance arithmetic in `app_pages/`, `mcp_server.py`, or `ai/`;
- let reusable UI components write to the DB, or let ML/LLM numbers become
  authoritative financial arithmetic;
- replace `LogisticRegression` without a baseline evaluation, run
  `PaddleOCR-VL` as the normal receipt path, or gate OCR quality on character
  accuracy.

---

## 4. Phase gates (copied from the goal spec, verbatim)

**Phase 1 gate.**

```
pytest: PASS

Reactive:
  Local → API immediate
  API → Local immediate
  receipt Category → Subcategory immediate
  savings Goal selection immediate
  all other identified dependent controls immediate

Architecture:
  MCP reads consume canonical service functions
  no new finance arithmetic in MCP
  important multi-record writes atomic
  shared UI components cannot write to DB
  validation functions shared by ≥2 entry paths
  current behavior otherwise unchanged
```

The coordinator must not start Phase 2 until the Phase 1 gate is demonstrated.

**Phase 2, Phase 3, Phase 4, Phase 5 gates** follow the same structure
(ui-migration / llm-evaluation / ml-evaluation / ocr-evaluation artifacts,
respectively).

---

## 5. Remaining operational artifacts (the coordinator maintains)

Per the goal spec:

```
qa/refactor/
  inventory.md          ← this file
  dependency-map.md     ← dependency graph implied by the orchestrator
  reactive-state-audit.md
  service-migration.md  ← R2/R3/R4/R5 service extraction progress
  ui-migration.md       ← Phase 2 board/panel/layout migrations
  llm-evaluation.md     ← Phase 3 advisor eval (cases.yaml, accuracy)
  ml-evaluation.md      ← Phase 4 eval (categorizer/anomaly/forecast)
  ocr-evaluation.md     ← Phase 5 field-accuracy benchmark
```

The three migration/evaluation files not yet written (`dependency-map.md`,
`service-migration.md`, `ui-migration.md`, and the three evaluation files)
must be created by the phases that own them; empty stubs with an ownership
note are preferred to absent files.
