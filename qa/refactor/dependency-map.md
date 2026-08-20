# Dependency map — refactor orchestrator graph

> Operational artifact for the coordinator. Edges are "must happen before".

```
R0 baseline (inventory.md + 4 sub-inventories)
 │
 ├──── R1 reactive UI (settings_ai, log_expense receipt, savings, loans)
 │      no dependencies beyond R0
 │
 ├──── R2 canonical read services (services/finance_queries.py, etc.)
 │        │  extracts mcp_server.py finance + insights.py pure helpers
 │        └──── MCP migration (mcp_server.py → service adapter)
 │
 ├──── R3 command services (services/*_commands.py, Unit-of-Work)
 │      touches db.py write paths, audit, revision bump
 │
 ├──── R4 validation (domain/validation.py, taxonomy.py, money.py, periods.py)
 │      consumed by Streamlit / bank_import / OCR / MCP / sync / future AI
 │
 └──── R5 merchant normalization (domain/merchant.py)
          │
          ▼
       R6 utils cleanup (domain/money, taxonomy, periods + ui/* + infra/*)
          │
          ▼
       PHASE-1 QA gate (pytest + reactive checks + architecture checks)
          │
          ▼
       Phase 2 — UI system (U1 Panel, U2 layout_state, U3 GroupedBoard v2)
          │
          ▼
       Phase 3 — Financial copilot (A1 providers … A8 evaluation)
          │
          ▼
       Phase 4 — ML (M1 registry … M6 subscription detection)
          │
          ▼
       Phase 5 — OCR (O1 RapidOCR core … O13 benchmark)
```

## File ownership (one subagent owns a file at a time)

| Work package | Owned files (write) | Read-only inspection |
|---|---|---|
| R1 | `app_pages/settings_ai.py`, `app_pages/log_expense.py`, `app_pages/savings.py`, `app_pages/loans.py`, `app_pages/portfolio.py`, `onboarding.py`, `app_pages/log_income.py` | `qa/refactor/reactive-state-audit.md`, `utils.py`, `queries.py` |
| R2 | `services/finance_queries.py`, `services/expense_queries.py`, `services/budget_queries.py`, `services/savings_queries.py`, `services/debt_queries.py`, `mcp_server.py` (adapter only) | `db.py`, `finance.py`, `insights.py`, `queries.py`, `utils.py` |
| R3 | `services/expense_commands.py`, `services/income_commands.py`, `services/recurring_commands.py`, `services/wishlist_commands.py`, `services/savings_commands.py` | `db.py`, `queries.py`, `app_pages/*.py` |
| R4 | `domain/validation.py`, `domain/taxonomy.py`, `domain/money.py`, `domain/periods.py` | `utils.py`, `bank_import.py`, `ocr.py`, `mcp_server.py` |
| R5 | `domain/merchant.py` | `bank_import.py`, `ocr.py`, `utils.py` |
| R6 | `domain/*`, `ui/styles.py`, `ui/formatting.py`, `infra/exporting.py`, `infra/networking.py`, `utils.py` (shim) | all of the above |

## Cross-cutting rules (enforced by coordinator)

1. One subagent owns a file at a time.
2. Agents may inspect anything but only modify owned files.
3. Cross-cutting changes are serialized.
4. Every task starts from current HEAD.
5. Every task ends with targeted tests.
6. Every phase ends with full `pytest`.
7. No later phase starts until the previous gate passes.
8. Compatibility shims preferred over mass rewrites.
9. No new domain calculations in `app_pages/`, `mcp_server.py`, or `ai/`.
10. No reusable UI component performs DB mutations.
11. No ML/LLM calculation becomes authoritative financial arithmetic.
12. Every new model/heuristic gets an evaluation fixture.
