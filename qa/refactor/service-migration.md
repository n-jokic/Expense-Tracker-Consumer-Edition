# Service migration — R2/R3/R4/R5 progress

## R2 — Canonical read/query services

Target: `services/finance_queries.py` (plus `expense_queries`, `budget_queries`,
`savings_queries`, `debt_queries` peers). Must not import Streamlit.

| Calculation | Current home(s) | Canonical home | MCP adapter | Status |
|---|---|---|---|---|
| `get_expense_summary(uid, year, month)` | `mcp_server._expense_summary_impl` | `services/finance_queries.py` | `mcp_server` → service | ✅ done |
| `list_expenses` / `search_expenses` | `mcp_server._list_expenses_impl` / `_search_expenses_impl` | `services/` | adapter only | ✅ done |
| `list_income` | `mcp_server._list_income_impl` | `services/` | adapter only | ✅ done |
| `list_savings_goals` | `mcp_server._list_savings_goals_impl` | `services/` | adapter only | ✅ done |
| `month_over_month` / `top_category_this_month` / `unusual_expenses` / `days_until_budget_depleted` / `savings_projection` / `build_narrative_stats` | `insights.py` (imports Streamlit) | `services/finance_queries.py` (pure) + insights shim | `mcp_server._get_insights_impl` → service | ✅ done |
| `get_category_breakdown` / `get_merchant_breakdown` | 11 call sites (dashboard/insights/llm/notifications) | `services/` | consumers call service | ✅ service done, call sites deferred |
| `get_budget_vs_actual` / `get_budget_summary` | dashboard/notifications/budgets | `services/` | consumers call service | ✅ service done |
| `get_portfolio_metrics` / `get_net_worth` | portfolio/dashboard/savings | `services/` | delegate to `finance.portfolio_metrics` | ✅ done |
| `get_debt_summary` | dashboard/insights/loans/notifications | `services/` | wraps `finance.loan_schedule` | ✅ done |
| `get_recurring_monthly_total` | insights/dashboard/llm | `services/` | one canonical impl | ✅ done |

R2 service is Streamlit-free (inlined `_effective_category_budgets`).

## R3 — Command services (Unit-of-Work)

Target: `services/commands.py` (ItemMove + reorder_* / bulk_*).
Invariant: one logical user command = one transaction = one audit group = one
revision bump. Return `CommandResult(changed, revision, affected_ids)`.

| Write path | Current commits | Target commits | Status |
|---|---|---|---|
| `reorder_recurring_items(moves=[...])` | 1 (canonical) | 1 | ✅ done (wired in recurring.py) |
| `reorder_big_purchases` | N (loop) | 1 | ✅ done (wired in big_purchases.py) |
| inline edit / bulk trash (log_expense) | N (loop) | 1 | ✅ done (wired in log_expense.py) |
| bank_import bulk import | N (by design — per-row independence) | N | intentional, documented |
| big_purchases → expense handoff | 2 (uncompensated) | 1 (or compensated) | deferred (Phase 1 remaining) |
| income + raise (`add_income` → `save_settings`) | up to 3 commits | 1 or compensated | deferred |
| savings: goal rename+update, term-deposit withdraw+close | 2 | 1 or compensated | deferred |
| loan payment / early repayment | 2 (compensated via soft_delete) | keep compensation | documented |

## R4 — Validation

Domain: `domain/validation.py` + `taxonomy.py` + `money.py` + `periods.py`.
- `validate_category`, `validate_category_subcategory`, `validate_income_type`,
  `validate_currency`, `normalize_description`, `validate_amount`,
  `normalize_currency`/`normalize_amount`.
Wired: `mcp_server` write tools + `bank_import._save_edited_row`.
≥2 entry paths share canonical functions (MCP + bank import) ✅.

## R5 — Merchant normalization

Target: `domain/merchant.py` — `MerchantMatch(raw, normalized, canonical, confidence, source)`.
`normalize_merchant(raw)` / `match_merchant(raw, known_canonicals)`.
Examples: LIDL SRBIJA DOO #0183 BEOGRAD / LIDL-183 / LIDL PROD 0183 → normalized "lidl" → canonical "Lidl" ✅.

## Notes

- `db.py` (2,302 lines) is intentionally **not** rewritten in Phase 1; keep it
  as the persistence façade and split into `repositories/` only after services
  have taken the pressure off it.
- `utils.py` compatibility shims stay during R6 so imports don't churn.
- `ui/formatting.py`, `ui/styles.py`, `infra/exporting.py`, `infra/networking.py` seeded (R6 preview).
