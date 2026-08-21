# Implementation Plan: desktop reliability repairs

## Task list

- [x] Loans: normalize payment record date use and add a regression test.
- [x] Local AI: preserve CPU setting, add diagnostics, and retry GPU failure on CPU.
- [x] Lists: replace encoded sortable labels with a shared validated card board.
- [x] Packaging: add onedir PyInstaller + Inno Setup, per-user state, and migration.
- [ ] Verify on a working Windows Python 3.12 build environment.

## Risk

The checked-in virtual environments target a missing base interpreter, so runtime and installer verification must run where Python 3.12 and Inno Setup 6 are available.

---

# Implementation Plan: financial integrity and product integration review

## Review outcome

The highest-risk defect is not any one page: income, expenses, savings, term deposits,
loans, holdings, and wishlist purchases are independent records with no canonical
unallocated-cash invariant. Multi-record actions also commit separately. This permits
money to appear, disappear, or be duplicated when opening a term deposit, deleting a
savings object, buying a linked item, or retrying after a partial failure.

The smallest correct foundation is **not another mutable balance**. Keep the existing
tables as the financial record, compute unallocated funds from canonical active rows,
and route only multi-record financial actions through atomic command functions. A
separate double-entry ledger can be added later only if the derived model proves
insufficient.

## Verified findings

### P0 — financial correctness and privacy

- There is no canonical unallocated-funds calculation or source-balance validation.
- Opening a term deposit creates a locked asset without debiting unallocated cash or a
  savings goal; closing it performs two separate commits.
- Savings/term deletion can remove value without settlement. Savings withdrawals are
  negative deposits and database-level callers can exceed the balance; read-time logic
  silently clamps negative balances to zero.
- Big purchases have no funding link. Buying performs an expense insert and status
  update in separate transactions; the free status selector can mark an item bought
  without creating an expense.
- External AI answer composition can serialize raw transaction tool results to the
  configured provider, contrary to the documented sanitized-data boundary.

### P1 — broken behavior

- Loan and savings collapse state never persists: `ui_layout` is absent from
  `UserSettings`, while both settings and panel helpers swallow the failed write.
- Recurring and wishlist boards expose group-reorder/collapse capabilities that the
  browser component does not implement.
- A loan can be manually marked paid off while its computed balance is non-zero; paid
  loans are not separated into an archive.
- Planner repair asks the model to restore missing `month`/`year` although deterministic
  inference already exists. The repair prompt omits the original question and schema.
- Transient API failures such as 503 receive no bounded retry, lose provider diagnostics,
  and can discard a deterministic result that was already computed.
- Receipt OCR is intentionally a merchant/total/date/currency extractor. It has no
  line-item model, row reconstruction, reconciliation, or real checked-in receipt corpus.

### P2 — incomplete integration and UX

- Big purchases cannot create or link to a savings target, and purchase records do not
  retain the resulting expense reference.
- Term accounts are associated by `goal_name` but rendered in a separate flat section.
- Early-withdrawal interest/penalty policy is not modeled; the normal rate is used early.
- The new ML tab has no empty state, training explanation, or readable model metrics.
- AI provider capability metadata claims native tools/JSON schema, while requests use
  prompt-only JSON over Chat Completions.
- Ask cannot return visual output even though Plotly and canonical finance queries
  already exist.

### Needs reproduction, not a speculative fix

- Current code filters `status != "bought"` before building the Big Purchases priority
  matrix and renders bought items under Archived. Add a regression test and reproduce
  against the running app/version before changing this path.
- The working tree already contains large uncommitted ML, forecasting, and OCR changes.
  Review and validate those changes before implementing overlapping tasks.

## Architecture decisions

- **Virtual unallocated pool:** one canonical query computes available cash from realized
  income/financing inflows minus expenses and allocations represented by active savings,
  term-principal, and holding-cost rows. Do not persist a second balance that can drift.
- **Explicit realization:** accrued interest is a valuation until realized. On withdrawal
  or maturity, book the interest portion as income so transfers remain zero-sum.
- **Atomic commands only where needed:** reuse existing CRUD for one-row edits; use one
  database transaction for savings settlement, term open/close, linked purchase, and
  payoff transitions.
- **No model-generated code:** AI charts use a validated allowlisted chart specification
  over canonical query results and the installed Plotly dependency.
- **Provider boundary:** filter tool results once before any external request; keep local
  providers unrestricted by that external-transmission policy where appropriate.

## Task list

### Phase 0: establish a trustworthy baseline

#### FIN-00 — Restore a runnable Python 3.12 verification environment

**Acceptance criteria:**
- The repository uses a working Python 3.12 environment; existing broken virtual
  environments are not silently reused.
- The full test suite result and the current dirty-worktree diff are recorded before any
  application change.
- Focused Streamlit tests can run with version 1.61.1.

**Verification:** `python -m pytest`; record failures separately from environment errors.

**Likely files:** environment only; no application file required. **Scope:** S.

#### FIN-01 — Lock down the money invariant with executable examples

**Acceptance criteria:**
- A canonical `unallocated_funds_eur(user_id)` calculation is defined and tested.
- Tests cover income, expense deletion/restore, goal deposit/withdrawal, term funding,
  realized interest, holding cost, and loan proceeds without double-counting.
- Legacy users can create an explicit opening-balance income adjustment; migration does
  not invent historical transfers.

**Verification:** focused finance-query tests prove each operation changes total money by
exactly its external inflow/outflow.

**Likely files:** `services/finance_queries.py`, `tests/test_finance.py`,
`tests/test_service_smoke.py`. **Scope:** M. **Dependencies:** FIN-00.

### Phase 1: repair shared state and money movements

#### FIN-02 — Persist panel layout and stop hiding failures

**Acceptance criteria:**
- `ui_layout` round-trips through a real JSON settings column and additive migration.
- Loan and savings collapse state persists across rerun/reload and remains user/area
  scoped.
- Persistence errors are logged/displayed instead of swallowed; glyph buttons have
  accessible labels.

**Verification:** layout unit test plus Streamlit toggle → rerun → reload test.

**Likely files:** `db.py`, `ui/layout_state.py`, `ui/panel.py`, one focused test.
**Scope:** M. **Dependencies:** FIN-00.

#### FIN-03 — Finish recurring category controls

**Acceptance criteria:**
- Categories can collapse and reorder; items still reorder and move across categories.
- Category controls have keyboard equivalents and `aria-expanded` state.
- Returned group order/collapse values are validated before persistence.

**Verification:** component/state tests for group collapse/order, invalid IDs, item moves,
and keyboard actions.

**Likely files:** `ui/board.py`, `app_pages/recurring.py`, `ui/layout_state.py`, one test.
**Scope:** M. **Dependencies:** FIN-02.

#### FIN-04 — Make savings and term actions zero-sum and atomic

**Acceptance criteria:**
- Goal deposits cannot exceed unallocated funds; withdrawals cannot exceed spendable goal
  principal plus realizable interest.
- Opening a term account debits its selected goal exactly once; close/withdraw returns
  principal and books realized interest exactly once in one transaction.
- Non-empty goal/account deletion settles to unallocated funds or is blocked; no silent
  clamp hides an overdraft.

**Verification:** focused command tests for success, insufficient funds, retry/idempotency,
rollback after an injected failure, delete/restore, and maturity/early withdrawal.

**Likely files:** `services/commands.py`, `db.py`, `app_pages/savings.py`,
`tests/test_savings.py`. **Scope:** M. **Dependencies:** FIN-01.

#### FIN-05 — Model early-withdrawal policy and nest term accounts

**Acceptance criteria:**
- A term account stores an optional early annual rate; the withdrawal preview clearly
  shows which rate and payout apply.
- Active/closed term accounts render once inside their goal panel; only true orphans use
  a separate section.
- Goal rename updates every linked reference without losing layout state.

**Verification:** persistence/calculation tests plus Streamlit tests for active, matured,
closed, early-withdrawn, and orphaned accounts.

**Likely files:** `db.py`, `finance.py`, `app_pages/savings.py`, one test.
**Scope:** M. **Dependencies:** FIN-02, FIN-04.

### Checkpoint: financial foundation

- Full suite passes.
- An end-to-end scenario proves: income → unallocated → goal → term account → goal or
  unallocated, with the same total before realized interest and a recorded income inflow
  for realized interest.
- Existing user data opens without destructive rewriting.

### Phase 2: integrate purchases and loans

#### FIN-06 — Link wishlist items to savings targets

**Acceptance criteria:**
- Wishlist creation offers: no target, create a target, or link an existing goal.
- The optional stable funding reference survives reload, edit, rename, and missing-goal
  cases.
- Cards show progress/funding source without duplicating savings calculations.

**Verification:** model/migration tests and UI tests for all three choices and rename/delete.

**Likely files:** `db.py`, `app_pages/big_purchases.py`, `app_pages/savings.py`, one test.
**Scope:** M. **Dependencies:** FIN-01.

#### FIN-07 — Buy a linked item in one transaction

**Acceptance criteria:**
- The only path to `bought` validates funds, debits the selected goal/unallocated source,
  creates one linked expense, marks the item bought, and bumps revision once.
- Retry cannot duplicate the expense; failure rolls the whole action back.
- Reversal/refund restores the source with audit history; arbitrary status changes cannot
  bypass the command.

**Verification:** command tests with retry and injected partial failures; UI regression
proves bought items leave the active matrix and remain archived.

**Likely files:** `services/commands.py`, `db.py`, `app_pages/big_purchases.py`,
`tests/test_big_purchases.py`. **Scope:** M. **Dependencies:** FIN-04, FIN-06.

#### FIN-08 — Enforce loan payoff and archive semantics

**Acceptance criteria:**
- A loan enters `paid_off` only when computed remaining balance is within tolerance, unless
  a separately audited override is explicitly designed.
- Paid loans render under Archived and retain payment/audit history; reopening restores
  active calculations.
- Loan proceeds and payments participate consistently in the unallocated-funds model.

**Verification:** schedule/status command tests and Streamlit active/archive/reopen test.

**Likely files:** `services/commands.py`, `services/finance_queries.py`,
`app_pages/loans.py`, one test. **Scope:** M. **Dependencies:** FIN-01.

### Phase 3: AI safety and reliability

#### AI-01 — Sanitize external tool results at one boundary

**Acceptance criteria:**
- External prompts contain only allowlisted fields needed for the answer; IDs and account
  metadata are removed and descriptions are capped.
- Local and external privacy behavior is documented accurately.
- Logs never contain API keys, full prompts, or raw financial rows.

**Verification:** intercepted-request test asserts forbidden fields never reach the body.

**Likely files:** `ai/safety.py`, `ai/orchestrator.py`, `README.md`, `tests/test_ai_eval.py`.
**Scope:** M. **Dependencies:** FIN-00.

#### AI-02 — Repair planner arguments deterministically

**Acceptance criteria:**
- Missing `year`/`month` is filled from the original question/current date before model
  repair; explicit and “last month” dates remain correct.
- Repair includes tool schema and original question; unresolved ambiguity becomes a clear
  clarification message.
- Provider capabilities no longer claim unimplemented native tools/schema.

**Verification:** missing month, explicit month, last month, invalid type, and ambiguous
date tests.

**Likely files:** `ai/router.py`, `ai/orchestrator.py`, `ai/prompts.py`, `tests/test_ai_eval.py`.
**Scope:** M. **Dependencies:** AI-01.

#### AI-03 — Handle transient provider outages without losing computed answers

**Acceptance criteria:**
- 429/500/502/503/504 receive at most two bounded retries honoring a capped `Retry-After`;
  permanent 4xx responses do not retry.
- Provider diagnostics reach Ask; persistent 503 reports temporary unavailability.
- A deterministic tool result still produces a deterministic answer when composition fails.

**Verification:** mocked transient-success, persistent-503, 401, timeout, and deterministic
fallback tests.

**Likely files:** `llm.py`, `ai/orchestrator.py`, `app_pages/ask.py`, `tests/test_llm.py`.
**Scope:** M. **Dependencies:** AI-01.

#### AI-04 — Add safe chart answers, then native providers

**Acceptance criteria:**
- A read-only series tool and validated line/bar/pie chart spec render via existing Plotly;
  invalid specs fall back to text/table and cannot execute code or HTML.
- Direct OpenAI uses a provider-specific structured API path; native Claude support is a
  separate adapter with its own authentication/capabilities. Existing OpenAI-compatible
  endpoints remain supported.
- Provider connection tests disclose exactly what data may leave the device.

**Verification:** chart values equal canonical tool results; invalid-spec/security tests;
mocked provider contract tests.

**Likely files:** `ai/tool_registry.py`, `ai/providers/`, `ai/orchestrator.py`,
`app_pages/ask.py`, focused tests. Split provider adapters into separate changes if this
exceeds five files. **Scope:** M per slice. **Dependencies:** AI-02, AI-03.

### Phase 4: OCR and ML usability

#### OCR-01 — Build a measurable receipt line-item pipeline

**Acceptance criteria:**
- Privacy-safe real receipt fixtures cover subtotal/VAT/discount/cash/change, rotation,
  poor lighting, Serbian Latin/Cyrillic, and multi-quantity rows.
- Each line item has description, amount, optional quantity/unit price, polygon, and
  confidence; summary rows are classified separately.
- Item totals reconcile to the selected total within a documented tolerance; mismatches
  require review instead of silent import.

**Verification:** item precision/recall and reconciliation benchmark runs on actual images;
subtotal is never selected when an explicit total is present.

**Likely files:** `ingestion/receipt/models.py`, one new line-item extractor,
`ingestion/receipt/service.py`, `tests/test_ocr_benchmark.py`, fixture manifest/images.
**Scope:** M slices. **Dependencies:** FIN-00.

#### OCR-02 — Add row-level receipt review

**Acceptance criteria:**
- Users can accept, edit, or reject each extracted item and see low-confidence/mismatch
  warnings.
- Saving multiple items uses one atomic bulk command and retains the source receipt total.
- Optional vision/Paddle fallback is explicit opt-in; images are never silently uploaded.

**Verification:** Streamlit review test plus atomic-save rollback test.

**Likely files:** `app_pages/log_expense.py`, `services/commands.py`, OCR service/model,
one test. **Scope:** M. **Dependencies:** OCR-01.

#### ML-01 — Finish the ML settings experience

**Acceptance criteria:**
- Empty, candidate, active, and multiple-version states explain what exists and what the
  user can do next.
- Metrics are formatted, model activation is explicit, and automatic training behavior is
  accurately described.
- Existing uncommitted registry/forecasting changes are reviewed and tested rather than
  reimplemented.

**Verification:** registry unit tests and Streamlit tests for each state.

**Likely files:** `app_pages/settings.py`, `ml/registry.py`, one DB helper area, one test.
**Scope:** M. **Dependencies:** FIN-00.

### Phase 5: carry-over hardening

#### QA-01 — Close validated residual data-integrity leads

**Acceptance criteria:**
- Sync create rejects isolated derived-EUR fields that lack their base amount/currency.
- CSV parsing handles comma-thousands consistently with PDF parsing.
- UI/refactor documentation stops claiming layout/group capabilities are complete before
  their tests pass.

**Verification:** focused sync/import regression tests and documentation check.

**Likely files:** `sync_core.py`, `bank_import.py`, two focused tests, QA docs.
**Scope:** M. **Dependencies:** FIN-00.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Legacy history is incomplete | Derived unallocated funds may begin negative | Show it explicitly; allow an audited opening-balance income adjustment, never invent history |
| Existing direct writes bypass commands | Pool/source invariants can still be violated | Inventory callers; route only money-changing multi-write flows through the canonical commands and test every caller |
| Goal names are mutable identifiers | Links/layout state break on rename | Add stable goal identity as part of FIN-06, or update all references atomically as the interim minimum |
| Interest creates apparent money | Zero-sum rule is violated | Treat accrued interest as valuation and book realized interest as income at settlement |
| Current dirty ML/OCR work overlaps plan | Changes may be overwritten or duplicated | Review and checkpoint the existing diff before implementation |
| No runnable Python in this environment | Review claims cannot be runtime-verified | Complete FIN-00 before accepting any implementation task |

## Open product decisions

- Confirm whether wishlist funding may draw from a locked term account before maturity, or
  only from liquid goal/unallocated balances. Recommended default: liquid balances only;
  require the explicit early-withdrawal flow first.
- Confirm whether loan proceeds should appear as financing inflow in unallocated cash.
  Recommended default: yes, paired with the liability so net worth does not increase.
- Confirm whether deleting a purchase means refund/reversal or hiding a mistaken record.
  Recommended default: soft-delete mistakes; use an explicit refund for real-world returns.

---

# Locked financial-model decisions (design review, supersedes open items above)

## Environment recipe (FIN-00, applies to every later venv/build task)

The DSH workspace-write sandbox denies operations outside the session workspace and
denies parts of uv's sdist build isolation inside it. Working recipe for creating a
Python 3.12 environment here:

```powershell
uv venv .venv-fin00 --python 3.12
$env:UV_CACHE_DIR = "$PWD\.tmp\uv-cache"        # keep uv cache inside the workspace
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.tmp\uv-python"
$env:UV_LINK_MODE = "copy"
uv pip install --only-binary :all: --python .venv-fin00\Scripts\python.exe `
    -r requirements.txt -r requirements-dev.txt
```

- Plain `py -3.12 -m venv` fails on this machine (ensurepip exits 1 inside venv creation).
- Bare `uv pip install` fails: default cache sits outside the workspace; with a
  workspace cache it still fails building the one sdist in the tree
  (`antlr4-python3-runtime` via rapidocr→omegaconf) — its wheel exists, hence
  `--only-binary :all:` resolves everything.
- Never modify/delete `.venv`, `.venv-clean`, `.venv-managed`, `.venv-repair`.


These decisions are final for the implementation. Every service, command, and test must
follow them; `services/finance_queries.py` documents the operative classification.

## Canonical invariant

```
unallocated_funds_eur(user_id) =
      Σ posted external inflows        (income.actual_eur, non-deleted)
    + Σ financing inflows              (non-deleted loans.principal_eur)
    − Σ external outflows              (expenses.amount_eur, non-deleted; once each)
    − Σ allocations                    (net posted savings principal
                                        + active term principal
                                        + open holdings cost_eur)
```

- Soft-deleted rows are excluded explicitly per table. Negative results are displayed
  verbatim; no `max(balance, 0)` may hide an invalid state. Legacy-user concerns are
  dropped (app pre-release, no users), but migrations stay additive/idempotent.
- Money: Decimal-cents internally, quantize to €0.01 at boundaries, equality
  tolerance €0.01 (never 1e-6/1e-9 on user-facing money).

## Savings interest — daily accrual, monthly payout

- Interest accrues **daily** on each goal's posted balance at `annual_rate/100/365`
  (ACT/365 fixed; a money event changes the accrual balance starting on its event date).
- At each completed calendar-month boundary the accrued amount is **posted**: one income
  row (`income_type="Interest"`, notes marker `savings-interest:<goal>:<YYYY-MM>`) plus
  one savings credit row, written by an idempotent command (stable per-goal/per-month key
  + partial unique index). Posting runs inside every money-moving savings/term/purchase
  command and on savings/dashboard page load; it writes only when a month has completed.
- Posted interest becomes spendable goal principal. The current month's accrual is
  display-only valuation and is never spendable, never in the invariant.
- Posting is invariant-neutral (income +X and allocation +X cancel); total economic value
  rises by exactly X. Example acceptance numbers (3.65 % = €0.10/day per €1,000):
  €1,000 held all of January posts €3.10; withdraw €500 on Jan 15 → January posts
  14×0.10 + 17×0.05 = €2.25, exactly once.

## Term deposits — single payout at the end of the term

- Term interest pays out **exactly once, at settlement** — normally the maturity date.
  No interim postings; accrued term interest is valuation-only until settlement.
- The payout amount is **fixed by the product math at term end**: settling a matured term
  pays `maturity_value(...)` computed to the maturity date, even if settlement happens
  later. Accrual display caps at `min(maturity_date, today)`.
- Early closure exists only as the explicit early-withdrawal workflow (never implicit,
  never a wishlist funding path): `calculate_term_payout(term, as_of, "early")` accrues
  to the break date at `early_annual_rate` (NULL → normal rate) and returns `is_early`.
- Opening a term from a goal is zero-sum (goal −X, term +X); settlement to goal books
  `income += interest` exactly once and `goal += principal + interest`.

## Holdings — cost basis in, sale workflow out

- Open holdings contribute `cost_eur` as allocation (holdings create no expense row;
  exactly one representation). Deleting an open holding is mistake-correction only.
- Realized disposal goes through `sell_holding(...)`: proceeds booked as income, realized
  gain computed automatically, user-entered tax rate booked as a tax expense (gain > 0
  only), holding marked sold, cost basis released. Unallocated moves by exactly
  `realized_gain − tax`; losses book no tax and reduce cash honestly.

## Loans — principal/interest split and cash participation

- Loan principal is a financing inflow (+unallocated) paired with the liability in net
  worth. Payments are expense rows carrying `loan_id` — the single cash outflow
  representation; the loan table is never subtracted again.
- Payments record `loan_principal_eur` / `loan_interest_eur` components (schedule-
  derived) so interest paid over time is reportable; `principal + interest (+ surcharge,
  per the FIN-01 representation check) == amount_eur` is pinned by test.
- `paid_off` only when `|remaining balance| ≤ €0.01`, computed canonically from recorded
  splits (fallback: schedule).

## Market data

- Exchange-rate and stock-ticker refresh intervals are user-configurable settings
  (NULL = current defaults); fetch paths honor them against existing timestamps.

## Wishlist purchases

- Funding source is explicit (`unallocated | savings_goal`); buying is one atomic
  command (validate → debit source → one linked expense → bought stamp → revision bump);
  retries are idempotent via a stable expense reference; refunds are explicit
  compensating commands that preserve the original expense and audit history.
