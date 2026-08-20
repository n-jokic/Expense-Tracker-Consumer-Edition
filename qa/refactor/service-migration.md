# Service migration — R2/R3/R4/R5 progress

> Operational artifact for R2 (read/query services) and R3 (command services).
> Update this file as services are extracted. Keep it honest: what moved,
> what still lives in the old place, and what's blocked.

## R2 — Canonical read/query services

Target: `services/finance_queries.py` (plus `expense_queries`, `budget_queries`,
`savings_queries`, `debt_queries` peers). Must not import Streamlit.

| Calculation | Current home(s) | Canonical home | MCP adapter | Status |
|---|---|---|---|---|
| `get_expense_summary(uid, year, month)` | `mcp_server._expense_summary_impl` | `services/finance_queries.py` | `mcp_server` → service | ☐ planned |
| `list_expenses` / `search_expenses` | `mcp_server._list_expenses_impl` / `_search_expenses_impl` | `services/` | adapter only | ☐ planned |
| `list_income` | `mcp_server._list_income_impl` | `services/` | adapter only | ☐ planned |
| `list_savings_goals` | `mcp_server._list_savings_goals_impl` | `services/` | adapter only | ☐ planned |
| `month_over_month` / `top_category_this_month` / `unusual_expenses` / `days_until_budget_depleted` / `savings_projection` / `build_narrative_stats` | `insights.py` (imports Streamlit) | `services/` or `finance.py` (pure part) | `mcp_server._get_insights_impl` → service | ☐ planned |
| `get_category_breakdown` / `get_merchant_breakdown` | 11 call sites (dashboard/insights/llm/notifications) | `services/` | consumers call service | ☐ planned |
| `get_budget_vs_actual` / `get_budget_summary` | dashboard/notifications/budgets | `services/` | consumers call service | ☐ planned |
| `get_portfolio_metrics` / `get_net_worth` | portfolio/dashboard/savings | `services/` | delegate to `finance.portfolio_metrics` | ☐ planned |
| `get_debt_summary` | dashboard/insights/loans/notifications | `services/` | wraps `finance.loan_schedule` | ☐ planned |
| `get_recurring_monthly_total` | insights/dashboard/llm | `services/` | one canonical impl | ☐ planned |

## R3 — Command services (Unit-of-Work)

Target: `services/expense_commands.py`, `income_commands.py`,
`recurring_commands.py`, `wishlist_commands.py`, `savings_commands.py`.
Invariant: one logical user command = one transaction = one audit group = one
revision bump. Return `CommandResult(changed, revision, affected_ids)`.

| Write path | Current commits | Target commits | Status |
|---|---|---|---|
| `reorder_recurring_items(moves=[...])` | 1 (already fixed T4-005+A-002) | 1 | ☑ done (no change needed) |
| `reorder_big_purchases` | N (loop) | 1 | ☐ planned |
| inline edit / bulk trash (log_expense) | N (loop) | 1 | ☐ planned |
| bank_import bulk import | N (by design — per-row independence) | N (document as intentional) | ☐ document |
| big_purchases → expense handoff | 2 (uncompensated) | 1 (or compensated) | ☐ planned |
| income + raise (`add_income` → `save_settings`) | up to 3 commits | 1 or compensated | ☐ planned |
| savings: goal rename+update, term-deposit withdraw+close | 2 | 1 or compensated | ☐ planned |
| loan payment / early repayment | 2 (compensated via soft_delete) | keep compensation, document | ☐ document |

## R4 — Validation

Target: `domain/validation.py` + `taxonomy.py` + `money.py` + `periods.py`.

- `normalize_amount`, `normalize_currency`, `validate_category`,
  `validate_category_subcategory`, `normalize_description`, `parse_period`, …

Ensure ≥2 entry paths (Streamlit + bank_import/OCR/MCP/sync/future AI) share
the canonical functions.

## R5 — Merchant normalization

Target: `domain/merchant.py` — `MerchantMatch(raw, normalized, canonical, confidence, source)`.
Deterministic cleaning (unicode, casefold, store IDs, etc.) plus a persisted
alias table `raw_pattern | canonical | learned_from_user` (added later).

## Notes

- `db.py` (2,302 lines) is intentionally **not** rewritten in Phase 1; keep it
  as the persistence façade and split into `repositories/` only after services
  have taken the pressure off it.
- `utils.py` compatibility shims stay during R6 so imports don't churn.
