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

Status: ☐ planned — harness and cases not yet written.
