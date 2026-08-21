"""
ai/tool_registry.py — finance tool registry (Phase 3 A2).

Exposes application services (services/finance_queries) as finance tools.
MCP and AI share the same FinanceQueryService — no duplicate arithmetic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import services.finance_queries as fq

# Registry: tool name -> callable. Each callable takes (user_id, **kwargs) and
# returns a dict with _provenance (see ai/schemas.py).
TOOLS: dict[str, Any] = {}


def _register(name: str):
    def deco(fn):
        TOOLS[name] = fn
        return fn
    return deco


@_register("search_transactions")
def search_transactions(user_id: int, query: str, limit: int = 20) -> dict:
    res = fq.search_expenses(user_id, query, limit)
    res["_provenance"] = {"calculation": "search_expenses", "row_count": res.get("count", 0)}
    return res


@_register("aggregate_spending")
def aggregate_spending(user_id: int, year: int, month: int, category: str | None = None) -> dict:
    exp = fq.get_category_breakdown(user_id, year, month)
    total = sum(v for k, v in exp.items() if category is None or k == category) if category else sum(exp.values())
    return {"total_eur": float(total), "breakdown": exp, "_provenance": {"calculation": "aggregate_spending", "row_count": len(exp)}}


@_register("compare_periods")
def compare_periods(user_id: int, start_a: str, end_a: str, start_b: str, end_b: str,
                    category: str | None = None, merchant: str | None = None) -> dict:
    from datetime import datetime

    def _parse(s: str) -> date:
        return datetime.strptime(s, "%Y-%m-%d").date()

    comp = fq.compare_spending_periods(
        user_id,
        fq.Period(_parse(start_a), _parse(end_a)),
        fq.Period(_parse(start_b), _parse(end_b)),
        category=category, merchant=merchant,
    )
    return {
        "total_eur": comp.total_a_eur, "previous_eur": comp.total_b_eur,
        "difference_eur": comp.difference_eur, "change_pct": comp.change_pct,
        "transactions_a": comp.transactions_a, "transactions_b": comp.transactions_b,
        "_provenance": {"calculation": "compare_spending_periods", "row_count": comp.transactions_a + comp.transactions_b},
    }


@_register("category_breakdown")
def category_breakdown(user_id: int, year: int, month: int) -> dict:
    exp = fq.get_category_breakdown(user_id, year, month)
    return {"breakdown": exp, "_provenance": {"calculation": "category_breakdown", "row_count": len(exp)}}


@_register("merchant_breakdown")
def merchant_breakdown(user_id: int, year: int, month: int, n: int = 5) -> dict:
    rows = fq.get_merchant_breakdown(user_id, year, month, n=n)
    return {"merchants": rows, "_provenance": {"calculation": "merchant_breakdown", "row_count": len(rows)}}


@_register("budget_status")
def budget_status(user_id: int, year: int, month: int) -> dict:
    return fq.get_budget_vs_actual(user_id, year, month)


@_register("budget_runway")
def budget_runway(user_id: int, total_budget_eur: float, period_start: str) -> dict:
    from datetime import datetime
    import pandas as pd
    from db import get_expenses

    start = datetime.strptime(period_start, "%Y-%m-%d").date()
    df = get_expenses(user_id)
    days = fq.days_until_budget_depleted(df, total_budget_eur, start)
    return {"days_remaining": days, "_provenance": {"calculation": "budget_runway"}}


@_register("cashflow_summary")
def cashflow_summary(user_id: int, year: int) -> dict:
    summary = fq.get_expense_summary(user_id, f"{year}-01")
    # simplified — real impl aggregates monthly; keep stub for registry shape
    return {**summary, "_provenance": {"calculation": "cashflow_summary"}}


@_register("savings_status")
def savings_status(user_id: int) -> dict:
    s = fq.get_savings_summary(user_id)
    s["_provenance"] = {"calculation": "savings_status"}
    return s


@_register("project_savings")
def project_savings(user_id: int, goal_name: str) -> dict:
    # Reuse savings_projection helper via service
    from db import get_savings
    import db as _db

    df = _db.get_savings(user_id)
    proj = fq.savings_projection(df, goal_name)
    return {**proj, "_provenance": {"calculation": "project_savings"}}


@_register("debt_summary")
def debt_summary(user_id: int) -> dict:
    s = fq.get_debt_summary(user_id)
    s["_provenance"] = {"calculation": "debt_summary"}
    return s


@_register("recurring_costs")
def recurring_costs(user_id: int) -> dict:
    total = fq.get_recurring_monthly_total(user_id)
    return {"monthly_total_eur": total, "_provenance": {"calculation": "recurring_costs"}}


@_register("anomalies")
def anomalies(user_id: int, multiplier: float = 2.0) -> dict:
    import db as _db

    df = _db.get_expenses(user_id)
    out = fq.unusual_expenses(df, multiplier=multiplier)
    return {"count": len(out), "expenses": out.head(20).to_dict("records") if not out.empty else [], "_provenance": {"calculation": "anomalies", "row_count": len(out)}}
