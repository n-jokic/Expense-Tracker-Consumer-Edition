# LLM / Advisor evaluation — Phase 3

> Operational artifact for Phase 3 (A8). The advisor must not be released
> while `hallucinated-number rate` is non-zero.

## Tool registry (A2)

Finance tools exposed to the LLM are **application services**, not DB
functions:

`search_transactions`, `aggregate_spending`, `compare_periods`,
`category_breakdown`, `merchant_breakdown`, `budget_status`, `budget_runway`,
`cashflow_summary`, `savings_status`, `project_savings`, `debt_summary`,
`loan_scenario`, `recurring_costs`, `subscription_changes`, `anomalies`,
`forecast` — all delegated to `services/finance_queries.py`.

## Evaluation harness (A8)

Create `tests/ai_eval/cases.yaml`:

```yaml
- question: "How much did I spend on groceries last month?"
  expected_tool: aggregate_spending
  expected: { category: Groceries, period: previous_month }

- question: "Was Lidl more expensive this spring than last spring?"
  expected_tool: compare_periods
  expected: { merchant: Lidl }
```

Test categories:

| Category | Count |
|---|---|
| Simple aggregations | 20 |
| Date comparisons | 15 |
| Merchant/category queries | 15 |
| Savings scenarios | 10 |
| Debt scenarios | 10 |
| Recurring/subscription queries | 10 |
| Follow-ups | 10 |
| Ambiguous questions | 5 |
| Impossible/unanswerable | 5 |

Measures: tool accuracy, argument accuracy, numeric accuracy, unanswerable
correctness, hallucinated-number rate, local-model parse success, API-model
success.

## Provenance (A3) + Orchestrator (A4/A5) + Safety (A7)

- **Provenance**: every tool result carries `_provenance {calculation, period, previous_period, row_count, filters, currency_basis, truncated}` (see `ai/schemas.py` `ToolProvenance`). UI renders it in an expandable Sources block — "Based on N transactions [calculation]" and filter chips. Truncation flag when `MAX_RESULT_ROWS=100` caps.
- **Orchestrator**: `ai/orchestrator.py` bounded loop — sanitize → mutation check → deterministic fast-route (no LLM) → planner loop ≤4 tool calls (one repair attempt per call) → answer composer (LLM if configured, else deterministic template). `MAX_TOOL_CALLS=4`, `MAX_RESULT_ROWS=100`.
- **Router**: `ai/router.py` deterministic patterns for common questions (spent/budget/subscriptions/debt/forecast) → `infer_deterministic_args` from question + date. Local Gemma outputs constrained `{tool, arguments}` → `parse_local_tool_json` → `validate_tool_call` (schema). `ask.py` now calls `orchestrate` and shows tool provenance + proposal box (Confirm change disabled, A7 read-only).
- **Safety (A7)**: read-only tools, `ALLOWED_MUTATIONS` empty; `check_mutation_proposal` detects "Set my X budget to N" → UI proposal requiring confirmation button (never auto-executed). `validate_no_sql` blocks DROP/DELETE/UPDATE/INSERT patterns.

## Measures (hallucinated-number is release blocker)

- Deterministic answers are stringified tool results only — no invented numbers (template `_deterministic_answer`). LLM composer prompt instructs "use ONLY the numbers in TOOL RESULTS". Tests assert answer contains only tool numbers (e.g. `test_hallucinated_number_guard` asserts `42` present, `99.99` absent).
- Harness: `pytest tests/test_ai_eval.py` (17 tests) covers cases file, registry, router contracts, safety, orchestrator paths and hallucination guard.

Status: ☑ done — `tests/ai_eval/cases.yaml` has 100 cases (20/15/15/10/10/10/10/5/5), `tests/test_ai_eval.py` has 17 tests (all green), `app_pages/ask.py` uses `orchestrate` with Sources expander and proposal box, `ai/tool_registry.py` exposes 16 canonical tools on `services/finance_queries.py`.
