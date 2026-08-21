# FIN-01 Pre-Implementation Inventory (read-only analysis, 2026-08-21)

> Produced by a read-only analysis subagent before any FIN-01 code changes.
> All paths relative to repo root. Every `db.*` write helper commits via its own
> `get_session()` context manager (`db.py:300-311`, commit at line 306) unless
> stated otherwise. No files were modified in producing this document.

## 1. LOAN SURCHARGE REPRESENTATION (critical)

**Answer: the surcharge is INSIDE `expenses.amount_eur` (inclusive). `loan_surcharge_eur`
is redundant metadata stored beside it — never on top of it.**

| Step | Location | What happens |
|---|---|---|
| Fee calculation | `finance.py:62-69` `calculate_early_repayment_surcharge` | Returns non-negative fee: `amount * value / 100` (percent) or flat `value` (fixed). |
| Early-payment dialog | `app_pages/loans.py:270` | `total_eur = principal_eur + surcharge_eur` |
| Early-payment write | `app_pages/loans.py:280-288` | One `add_expense`: `"amount": float(principal + surcharge)` (l.284), `"amount_eur": total_eur` (l.285, **includes fee**), plus `"loan_payment_type": "early"` and `"loan_surcharge_eur": surcharge_eur` (l.286). |
| Regular payment write | `app_pages/loans.py:421-434` | One `add_expense`: `amount_eur = ae` (full installment, l.428), `"loan_surcharge_eur": 0.0` (l.432). |
| Persistence | `db.py:1079-1120` `add_expense` | Stores `amount_eur` verbatim (l.1094) and the loan columns verbatim (ll.1096-1098). |
| Pinning test | `tests/test_db.py:97-107` | Payment with fee: `amount_eur=102.5`, `loan_surcharge_eur=2.5` → columns round-trip exactly (102.5 is principal+fee inclusive). |

How `loan_schedule` treats surcharges (`finance.py:99-235`): principal component =
`total − surcharge` (`:138` dict path, `:143` tuple path); only the principal component
reduces the balance (`:151`, applied `:174`). The surcharge is booked as interest paid
(`surcharge_paid` `:152`, merged into `total_interest_paid` `:217`) and never reduces
`remaining_balance`.

**Exact outflow formula the FIN-01 query must use:**

```
loan_payment_outflow(user) = Σ expenses.amount_eur
                             WHERE user_id = ? AND is_deleted = 0
                               AND loan_id IS NOT NULL
```

Each linked expense counts once, in full, as −external outflow. Two prohibitions:

1. Never add `loan_surcharge_eur` on top of `amount_eur` (double-count).
2. Never reclassify the principal slice of a loan payment as an allocation — the money
   is gone to the lender; there is no corresponding asset row. Conversely,
   `Loan.principal_eur` itself produces no outflow (see §2).

## 2. CASH-EFFECT CLASSIFICATION

Delete semantics legend: **soft** = `is_deleted`/`deleted_at` + `soft_delete_*`/`restore_*`;
**hard** = `s.delete(obj)` row removal.

| Model (db.py) | Money-relevant columns | Delete | Classification |
|---|---|---|---|
| **Income** (377-395) | `budgeted`, `actual`, `currency`, `budgeted_eur`, `actual_eur`, `hours`, `rate`, `income_type` | **Soft** (l.392; 1351; 1362) | **+external inflow on `actual_eur` only.** `budgeted_eur` is intent/planning. Readers sum `actual_eur` (`finance_queries.py:278`, `:295`); UI sets budgeted=actual for Salary/Hourly (`log_income.py:100-104, 163-181`); MCP writes both equal (`mcp_server.py:372-377`). |
| **Expense** (340-374) | `amount`, `currency`, `amount_eur`, `recurring`, `rec_template_id`, `loan_id`, `loan_payment_type`, `loan_surcharge_eur` | **Soft** (ll.371-372; 1261; 1272; deleted filtered by default in `get_expenses` 1069-1076 and `get_loan_payments` 1899-1907) | **−external outflow on `amount_eur`**, including rows with `loan_id` set (full amount). `loan_surcharge_eur` is subset-metadata → 0 incremental. |
| **Savings** (398-414) | `goal_name`, `target_eur` (intent), `deposited`/`deposited_eur`, `interest_rate`, `balance_eur` (**derived on read**, not authoritative — recomputed in `get_savings` 1379-1387) | **Soft** (ll.411-412; 1484; 1495) | **±allocation**: positive `deposited_eur` moves pool→goal; negative rows are withdrawals goal→pool (`savings.py:151-156`; UI overdraft guard `savings.py:489-491`; balance clamped ≥0 at `db.py:1430/1443`). |
| **SavingsAccount** (417-436) | `goal_name`, `amount`/`amount_eur`, `annual_rate`, `start_date`, `maturity_date`, `status` ("active"\|"closed") | **Soft** (ll.433-434; 1640; 1653) | **±allocation (active only)**; `closed` rows excluded from locked sums (`finance_queries.py:368`, `savings.py:531`). ⚠️ Lifecycle asymmetric: opening writes nothing else (`savings.py:675-680`), withdrawal closes AND logs full accrued payout as a goal deposit (`savings.py:267-274`). |
| **Budget** (439-453) | `year`, `month`, `category`, `subcategory`, `budgeted_eur` | **Hard** (1722-1729) | Not part of the cash model (planning ceiling; unique scope ll.441-446). |
| **Recurring** (456-470) | `amount`/`amount_eur`, `due_day`, `start_month`, `active` | No delete helper; deactivate via `active=False` (1765-1778) | Not part of the cash model (template). Becomes −outflow only when logged as an Expense (`recurring.py:140-143`). |
| **BigPurchase** (485-499) | `price`/`price_eur`, `usage_hours`, `importance`, `status` | **Hard** (1830-1837) | `wishlist`/`saving` = not cash (intent). `bought` realized through separate Expense row (`big_purchases.py:190-197`); row itself valuation-only; later edits/deletes never touch that expense. |
| **Loan** (502-518) | `principal`/`principal_eur`, `annual_rate`, `start_date`, `term_months`, `payment_day`, `status`, early-repayment surcharge fields | **Hard** (1889-1896) | **Taking a loan creates NO cash record today**: `add_loan` (1856-1874, called `loans.py:93`) inserts only the Loan row. Payments arrive exclusively as linked Expenses. Proceeds = unrecorded financing inflow; repayments fully recorded −outflows. |
| **Holding** (521-533) | `quantity`, `cost_total`, `cost_eur` (basis incl. fees — `portfolio.py:72-75`), `last_price`, `last_price_date` | **Hard**, cascading (1957-1965 deletes HoldingPrice snapshots too) | **±allocation on `cost_eur`** + **0 valuation-only** on market value (`finance_queries.py:519-539`, `finance.py:278-308`). Buying logs no expense. |

Non-money models outside the formula: Household (316), User (325), AuditLog (473),
HoldingPrice (536, snapshots/valuation), Device (547), milestones (559/567),
SyncConflict (581), UserSettings (593), MlModel (649).

## 3. MONEY-CHANGING CALLERS

"Commits" counts `get_session` transactions (each helper self-commits, `db.py:306`).

### app_pages\

| Caller | Operation | Records | Commits |
|---|---|---|---|
| `big_purchases.py:33` | update_big_purchase (status select) | 1 | 1 |
| `big_purchases.py:103` | add_big_purchase | 1 | 1 |
| `big_purchases.py:190`+`:197` (confirm buy) | add_expense **then** update_big_purchase(bought) | 2 | **2 (non-atomic)** |
| `big_purchases.py:248` / `:318` / `:350` / `:398` / `:412` | update/delete big purchase | 1 | 1 |
| `dashboard.py:141` | add_expense (quick-add) | 1 | 1 |
| `log_expense.py:176` | add_expense (scanned receipt) | 1 | 1 |
| `log_expense.py:249`+`:256` | add_recurring **then** add_expense | 2 | **2** (+ compensating `update_recurring` on failure ll.267-271) |
| `log_expense.py:462` / `:487` | bulk commands (services.commands) | multi | **1** each |
| `log_expense.py:515` | restore_expense | 1 | 1 |
| `log_income.py:99` / `:194`(+settings `:201`) | add_income (+ settings write) | 1–2 | 1–2 |
| `loans.py:93` | add_loan (no cash rows) | 1 | 1 |
| `loans.py:204` | update_loan (edit terms) | 1 | 1 |
| `loans.py:280`+`:296` (early repayment) | add_expense **then** update_loan(paid_off) | 2 | **2** (+ compensating soft_delete_expense `:302`) |
| `loans.py:421`+`:443` (regular payment) | add_expense **then** update_loan(paid_off) | 2 | **2** (+ compensating `:447`) |
| `loans.py:483` | update_loan (status toggle) | 1 | 1 |
| `portfolio.py:91` | add_holding | 1 | 1 |
| `recurring.py:140` | add_expense (log template) | 1 | 1 |
| `savings.py:106` / `:151` / `:494` | add_savings (deposit / negative withdrawal / form) | 1 | 1 |
| `savings.py:267`+`:274` (term withdraw) | add_savings(payout) **then** update_savings_account(closed) | 2 | **2 (non-atomic)** |
| `savings.py:326` / `:351` | update / soft-delete savings account | 1 | 1 |
| `savings.py:675` | add_savings_account (open term; **no offsetting record**) | 1 | 1 |
| `onboarding.py:119` | add_expense | 1 | 1 |

(`update_expense` imported in `log_expense.py:14` but never called there — edits go
through the bulk command.)

### Other surfaces

| Surface | Findings |
|---|---|
| `mcp_server.py` | `_add_expense_impl` → `db_add_expense` `:346` (1 commit + bump `:347`); `_add_income_impl` → `db_add_income` `:379` (+ bump `:380`); wrappers `:495`/`:507`. Single-record. |
| `bank_import.py` | `add_expense` in `_save_edited_row` (`:340`, call `:444-465`); per-row loop `:734-742` → **N commits**, single `bump_db_version()` after `:745`. |
| `api.py` | No direct calls; delegates to `sync_core.apply_changes` (`:113-114`, `:136-137`). |
| `sync_core.py` | **Bypasses every helper** — direct ORM writes against `SYNC_MODELS = {"expenses","income","savings","savings_accounts"}` (`:42-43`): `create_record` `:415-431`; `_apply_update` atomic compare-write `:445-483`; `apply_changes` up to 500 changes, **one transaction per change** (`:499-527`). Soft delete arrives as `is_deleted` field (FIELD_SCHEMAS `:56-82`). Server-side `*_eur` recompute overwrites client values (`_recompute_derived_eur` `:147+`, applied `:411`, `:455`). Loans/holdings/big purchases/budgets/recurring not syncable. |
| `pdf_import.py`, `ingestion\` | No DB writes. |
| `queries.py` | Read-only wrappers + `save_settings` (`:234-238`). |

## 4. READ-SIDE CLAMPS AND DERIVED BALANCES

| Location | What it does |
|---|---|
| `db.py:1390-1444` `_recompute_savings_balances` | Balance chain rebuilt on read: NaN guard `:1417`; monthly compounding between deposits using earlier row's rate `:1426-1428`; **clamp `max(round(bal,4), 0.0)` per row `:1430`**; tail accrual to `asof` with same clamp `:1438-1443`. Used by `get_savings` `:1387` with `asof=today`. |
| `queries.py:101-226` | All `db.get_*` readers wrapped in `@st.cache_data(ttl=300/120)` keyed `(user_id, db_version)`; invalidation via `bump_db_version` `:77-96`. |
| `finance_queries.py:342-360` `get_savings_summary` | Last row's (clamped) balance per goal; `interest_total = bal − Σdeposited_eur` `:357`. |
| `…:363-376` `get_locked_savings` | Active term accounts; `accrued_value` capped at `min(maturity, asof)` `:373-375`; missing dates → raw `amount_eur`. |
| `…:379-410` `get_debt_summary` | Active loans: payments from linked Expenses (`:390-397`) → `fin.loan_schedule` (`:398-399`); `total_debt = Σ remaining_balance` `:401`. |
| `…:519-539` / `…:542-554` | Portfolio metrics; net worth = savings balances + locked + portfolio − debt. ⚠️ Adds goal balances **and** term amounts. |
| `app_pages/savings.py:516-559` | Page-level duplicate KPI math (headline = `total_balance + locked_eur` `:554`). |
| `app_pages/loans.py:317-339` | total_debt from schedules; repaid_pct clamped 0–100. |
| Cosmetic display clamps (not balance logic) | `budgets.py:65`, `travel.py:89`, `rewards.py:172`. |
| Write-side boundary | `sync_core._recompute_derived_eur` `:147+` normalizes synced `*_eur` before storage. |

## 5. EXISTING TEST COVERAGE

| File | Pins |
|---|---|
| `test_finance.py` | Amortization engine; surcharge = interest-not-principal (`:37-49`, `:84-95`); fee modes; missed/partial/off-due-day payments; Feb clamping; payoff; negative amortization honesty (`:324`); term math (`:310-320`); portfolio metrics (`:342`). |
| `test_savings.py` | Chain recompute behaviors incl. clamps (`:102-126`); goal-wide update/rename/trash (`:150-225`); account CRUD/restore (`:227`); sync (`:250-322`). |
| `test_db.py` | Expense soft-delete/restore (`:56`); income roundtrip (`:71-79`); **surcharge-inclusive payment metadata** (`:82-107`). |
| `test_entry_editing.py` | Savings chain forward-only recompute (`:80`); loan-term edit isolation (`:95`); big-purchase edits (`:111`); inf/NaN rejection (`:130-205`). |
| `test_service_smoke.py` | finance_queries shapes incl. `get_savings_summary` (`:115-135`); MCP delegation equality (`:218-250`). |
| `test_big_purchases.py` | Ordering + bought filtering. |
| `test_portfolio_snapshots.py` | HoldingPrice snapshot completeness. |
| `test_cache_revision.py` | Revision-based cache invalidation. |
| `test_budget_scope.py` | Budget scope semantics. |
| `test_app_smoke.py` / `test_app_ui.py` | Seed loans/savings/terms/holdings/purchases; page smoke runs. |
| `test_income.py` | income_type mapping. |
| `test_mcp.py` | MCP tools incl. term seeding (`:197-198`). |

**Coverage gap:** nothing pins `get_debt_summary`, `get_locked_savings`,
`get_net_worth` end-to-end; no `unallocated_funds` concept exists anywhere yet.

## Risks for FIN-01

- Surcharge double-count (inclusive representation) — outflow must be `Σ amount_eur`.
- Loan proceeds have no cash record — financing inflow must read `loans.principal_eur`.
- Term lifecycle asymmetry — naive allocations double-count CD money after withdrawal.
- Multi-commit paired writes exist in the wild — query must tolerate half-states.
- Sync bypasses helpers — compute purely from committed columns.
- Mixed delete semantics — soft vs hard deletes; deleting a loan orphans payment
  expenses (dangling `loan_id`); deleting a holding destroys cost history.
- Read-side derivation + TTL caching — honor `bump_data_revision` discipline.
- Intent vs realized columns — classify strictly (`actual_eur`, negative deposits,
  planning artifacts).
