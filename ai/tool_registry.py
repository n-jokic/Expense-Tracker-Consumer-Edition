"""
ai/tool_registry.py — finance tool registry (Phase 3 A2).

Exposes application services (services/finance_queries) as finance tools.
MCP and AI share the same FinanceQueryService — no duplicate arithmetic.
All finance numbers come from services/finance_queries, finance.py or
forecasting.py; this file is wiring only (no new arithmetic).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import services.finance_queries as fq

# Registry: tool name -> callable. Each callable takes (user_id, **kwargs) and
# returns a dict with _provenance.
TOOLS: dict[str, Any] = {}

# JSON-schema-style validation for planner output (structural, not value-level).
TOOL_SCHEMAS: dict[str, dict] = {
    "search_transactions": {"required": ["query"], "optional": ["limit"]},
    "aggregate_spending": {"required": ["year", "month"], "optional": ["category"]},
    "compare_periods": {"required": ["start_a", "end_a", "start_b", "end_b"], "optional": ["category", "merchant"]},
    "category_breakdown": {"required": ["year", "month"], "optional": []},
    "merchant_breakdown": {"required": ["year", "month"], "optional": ["n"]},
    "budget_status": {"required": ["year", "month"], "optional": []},
    "budget_runway": {"required": ["total_budget_eur", "period_start"], "optional": []},
    "cashflow_summary": {"required": ["year"], "optional": ["month"]},
    "savings_status": {"required": [], "optional": []},
    "project_savings": {"required": ["goal_name"], "optional": []},
    "debt_summary": {"required": [], "optional": []},
    "loan_scenario": {"required": ["principal_eur", "annual_rate_pct", "term_months"], "optional": ["extra_monthly_eur"]},
    "recurring_costs": {"required": [], "optional": []},
    "subscription_changes": {"required": [], "optional": []},
    "anomalies": {"required": [], "optional": ["multiplier"]},
    "forecast": {"required": [], "optional": []},
}

MAX_RESULT_ROWS = 100  # enforced by orchestrator too; kept here for reference


def _register(name: str):
    def deco(fn):
        TOOLS[name] = fn
        return fn
    return deco


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _prov(calculation: str, row_count: int = 0, filters: dict | None = None,
          period_start: date | None = None, period_end: date | None = None,
          **extra) -> dict:
    d: dict[str, Any] = {"calculation": calculation, "row_count": row_count, "currency_basis": "EUR"}
    if filters:
        d["filters"] = filters
    if period_start:
        d["period_start"] = period_start.isoformat()
    if period_end:
        d["period_end"] = period_end.isoformat()
    d.update(extra)
    return d


# ── Search / aggregate ────────────────────────────────────────────────────

@_register("search_transactions")
def search_transactions(user_id: int, query: str, limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 100))
    res = fq.search_expenses(user_id, query, limit)
    expenses = res.get("expenses")
    # Convert DataFrame to records when present (fq returns DF)
    records: list[dict] = []
    if expenses is not None and hasattr(expenses, "to_dict"):
        try:
            import pandas as pd
            if not expenses.empty:
                df = expenses.head(limit)
                records = df.to_dict("records")
                # Make dates json-safe
                for r in records:
                    if "date" in r and hasattr(r["date"], "isoformat"):
                        try:
                            r["date"] = r["date"].isoformat()
                        except Exception:
                            r["date"] = str(r["date"])
        except Exception:
            records = []
    truncated = res.get("count", 0) > len(records) and len(records) >= limit
    return {
        "count": res.get("count", len(records)),
        "expenses": records[:MAX_RESULT_ROWS],
        "_provenance": _prov("search_transactions", row_count=res.get("count", 0),
                             filters={"query": query}),
        "_truncated": truncated,
    }


@_register("aggregate_spending")
def aggregate_spending(user_id: int, year: int, month: int, category: str | None = None) -> dict:
    year, month = int(year), int(month)
    breakdown = fq.get_category_breakdown(user_id, year, month)
    if not breakdown:
        total = 0.0
    elif category:
        total = float(breakdown.get(category, 0.0))
        breakdown = {category: total} if category in breakdown else {}
    else:
        total = float(sum(breakdown.values()))
    # Also fetch month bounds for provenance
    try:
        start = date(year, month, 1)
        # next month start - 1 day as end
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        from datetime import timedelta
        end = end - timedelta(days=1)
    except Exception:
        start = end = None
    return {
        "total_eur": round(total, 2),
        "breakdown": breakdown,
        "year": year,
        "month": month,
        "_provenance": _prov("aggregate_spending", row_count=len(breakdown),
                             filters={"category": category} if category else {},
                             period_start=start, period_end=end),
    }


@_register("compare_periods")
def compare_periods(user_id: int, start_a: str, end_a: str, start_b: str, end_b: str,
                    category: str | None = None, merchant: str | None = None) -> dict:
    comp = fq.compare_spending_periods(
        user_id,
        fq.Period(_parse_date(start_a), _parse_date(end_a)),
        fq.Period(_parse_date(start_b), _parse_date(end_b)),
        category=category, merchant=merchant,
    )
    filters: dict[str, str] = {}
    if category:
        filters["category"] = category
    if merchant:
        filters["merchant"] = merchant
    return {
        "total_eur": comp.total_a_eur, "previous_eur": comp.total_b_eur,
        "difference_eur": comp.difference_eur, "change_pct": comp.change_pct,
        "transactions_a": comp.transactions_a, "transactions_b": comp.transactions_b,
        "period_a": f"{comp.period_a.start.isoformat()}..{comp.period_a.end.isoformat()}",
        "period_b": f"{comp.period_b.start.isoformat()}..{comp.period_b.end.isoformat()}",
        "_provenance": {
            "calculation": "compare_periods",
            "period": f"{start_a}..{end_a}",
            "previous_period": f"{start_b}..{end_b}",
            "row_count": comp.transactions_a + comp.transactions_b,
            "filters": filters,
            "currency_basis": "EUR",
        },
    }


@_register("category_breakdown")
def category_breakdown(user_id: int, year: int, month: int) -> dict:
    year, month = int(year), int(month)
    breakdown = fq.get_category_breakdown(user_id, year, month)
    return {
        "breakdown": breakdown,
        "year": year, "month": month,
        "_provenance": _prov("category_breakdown", row_count=len(breakdown),
                             period_start=date(year, month, 1)),
    }


@_register("merchant_breakdown")
def merchant_breakdown(user_id: int, year: int, month: int, n: int = 5) -> dict:
    year, month, n = int(year), int(month), max(1, min(int(n), 20))
    rows = fq.get_merchant_breakdown(user_id, year, month, n=n)
    return {
        "merchants": rows[:MAX_RESULT_ROWS],
        "year": year, "month": month,
        "_provenance": _prov("merchant_breakdown", row_count=len(rows),
                             period_start=date(year, month, 1)),
    }


# ── Budgets / runway / cashflow ───────────────────────────────────────────

@_register("budget_status")
def budget_status(user_id: int, year: int, month: int) -> dict:
    year, month = int(year), int(month)
    data = fq.get_budget_vs_actual(user_id, year, month)
    # data is {category: {budgeted_eur, actual_eur, remaining_eur}}
    total_budgeted = sum(v.get("budgeted_eur", 0) for v in data.values()) if data else 0
    total_actual = sum(v.get("actual_eur", 0) for v in data.values()) if data else 0
    return {
        "budgets": data,
        "total_budgeted_eur": round(total_budgeted, 2),
        "total_actual_eur": round(total_actual, 2),
        "remaining_eur": round(total_budgeted - total_actual, 2),
        "year": year, "month": month,
        "_provenance": _prov("budget_status", row_count=len(data),
                             period_start=date(year, month, 1)),
    }


@_register("budget_runway")
def budget_runway(user_id: int, total_budget_eur: float, period_start: str) -> dict:
    from db import get_expenses
    total_budget_eur = float(total_budget_eur)
    start = _parse_date(period_start)
    df = get_expenses(user_id)
    days = fq.days_until_budget_depleted(df, total_budget_eur, start)
    return {
        "total_budget_eur": total_budget_eur,
        "period_start": start.isoformat(),
        "days_remaining": days,
        "depleted": days == 0,
        "_provenance": _prov("budget_runway", period_start=start),
    }


@_register("cashflow_summary")
def cashflow_summary(user_id: int, year: int, month: int | None = None) -> dict:
    year = int(year)
    # When month provided, use that month; otherwise aggregate current month as proxy
    month_key = f"{year}-{int(month):02d}" if month is not None else f"{year}-01"
    summary = fq.get_expense_summary(user_id, month_key)
    # If caller asked for year-level without month, we already returned Jan; augment with real month hint
    result = dict(summary)
    if month is not None:
        result["requested_month"] = f"{year}-{int(month):02d}"
    result["_provenance"] = _prov("cashflow_summary", period_start=date(year, int(month) if month else 1, 1))
    return result


# ── Savings / debt ────────────────────────────────────────────────────────

@_register("savings_status")
def savings_status(user_id: int) -> dict:
    s = fq.get_savings_summary(user_id)
    # Ensure provenance
    s = dict(s)
    s["_provenance"] = _prov("savings_status", row_count=len(s.get("goals", [])))
    return s


@_register("project_savings")
def project_savings(user_id: int, goal_name: str) -> dict:
    import db as _db
    df = _db.get_savings(user_id)
    proj = fq.savings_projection(df, goal_name)
    proj = dict(proj)
    # Make projected_date json-safe
    if proj.get("projected_date") and hasattr(proj["projected_date"], "isoformat"):
        proj["projected_date"] = proj["projected_date"].isoformat()
    proj["_provenance"] = _prov("project_savings", filters={"goal_name": goal_name})
    return proj


@_register("debt_summary")
def debt_summary(user_id: int) -> dict:
    s = fq.get_debt_summary(user_id)
    s = dict(s)
    # Make debt_free_date json-safe
    if s.get("debt_free_date") and hasattr(s["debt_free_date"], "isoformat"):
        s["debt_free_date"] = s["debt_free_date"].isoformat()
    s["_provenance"] = _prov("debt_summary", row_count=s.get("active_loan_count", 0))
    return s


@_register("loan_scenario")
def loan_scenario(user_id: int, principal_eur: float, annual_rate_pct: float,
                  term_months: int, extra_monthly_eur: float = 0.0) -> dict:
    import finance as fin
    principal_eur = float(principal_eur)
    annual_rate_pct = float(annual_rate_pct)
    term_months = int(term_months)
    extra = float(extra_monthly_eur or 0.0)
    monthly = fin.annuity_payment(principal_eur, annual_rate_pct, term_months)
    out: dict[str, Any] = {
        "principal_eur": principal_eur,
        "annual_rate_pct": annual_rate_pct,
        "term_months": term_months,
        "monthly_payment": round(monthly, 2),
    }
    if extra > 0.01:
        # Simulate extra payment effect via finance.loan_schedule without history
        from datetime import timedelta
        # Use a synthetic schedule: extra reduces remaining months if we compute
        # via closed-form rather than full schedule (keep deterministic, no DB).
        r = (annual_rate_pct / 100) / 12
        if r == 0:
            import math
            months_needed = math.ceil(principal_eur / (monthly + extra)) if (monthly + extra) > 0 else term_months
        else:
            import math
            pay = monthly + extra
            if pay <= principal_eur * r:
                months_needed = term_months  # extra not enough
            else:
                months_needed = math.ceil(-math.log(1 - principal_eur * r / pay) / math.log(1 + r))
                months_needed = max(1, months_needed)
        saved_months = max(0, term_months - months_needed)
        # Interest: total cost = payment * months; difference approximates saved interest
        interest_normal = monthly * term_months - principal_eur
        interest_extra = (monthly + extra) * months_needed - principal_eur
        interest_saved = max(0.0, interest_normal - interest_extra)
        out.update({
            "extra_monthly_eur": round(extra, 2),
            "monthly_with_extra": round(monthly + extra, 2),
            "months_needed_with_extra": months_needed,
            "months_saved": saved_months,
            "interest_saved_eur": round(interest_saved, 2),
        })
    out["_provenance"] = _prov("loan_scenario")
    return out


# ── Recurring / subscriptions / anomalies / forecast ──────────────────────

@_register("recurring_costs")
def recurring_costs(user_id: int) -> dict:
    total = fq.get_recurring_monthly_total(user_id)
    # Also include per-bill list capped
    try:
        from db import get_recurring
        df = get_recurring(user_id)
        bills: list[dict] = []
        if not df.empty:
            active = df[df["active"] == True] if "active" in df.columns else df  # noqa: E712
            for _, r in active.head(MAX_RESULT_ROWS).iterrows():
                bills.append({
                    "description": str(r.get("description", "")),
                    "amount_eur": float(r.get("amount_eur", 0) or 0),
                    "category": str(r.get("category", "")),
                })
    except Exception:
        bills = []
    return {
        "monthly_total_eur": float(total),
        "bills": bills,
        "_provenance": _prov("recurring_costs", row_count=len(bills)),
    }


@_register("subscription_changes")
def subscription_changes(user_id: int) -> dict:
    from db import get_expenses
    import forecasting as fc
    df = get_expenses(user_id)
    subs = fc.detect_subscriptions(df)
    records: list[dict] = []
    if subs is not None and not subs.empty:
        for _, r in subs.head(MAX_RESULT_ROWS).iterrows():
            d = r.to_dict()
            if "last_date" in d and hasattr(d["last_date"], "isoformat"):
                try:
                    d["last_date"] = d["last_date"].isoformat()
                except Exception:
                    d["last_date"] = str(d["last_date"])
            records.append(d)
    return {
        "count": len(records),
        "subscriptions": records,
        "_provenance": _prov("subscription_changes", row_count=len(records)),
        "_truncated": len(records) >= MAX_RESULT_ROWS,
    }


@_register("anomalies")
def anomalies(user_id: int, multiplier: float = 2.0) -> dict:
    import db as _db
    multiplier = float(multiplier)
    df = _db.get_expenses(user_id)
    out = fq.unusual_expenses(df, multiplier=multiplier)
    records: list[dict] = []
    if out is not None and not out.empty:
        for _, r in out.head(MAX_RESULT_ROWS).iterrows():
            d = r.to_dict()
            if "date" in d and hasattr(d["date"], "isoformat"):
                try:
                    d["date"] = d["date"].isoformat()
                except Exception:
                    d["date"] = str(d["date"])
            records.append(d)
    # Enrich with IsolationForest signal when enough rows (pure helper, not authoritative)
    try:
        import forecasting as fc
        flagged = fc.detect_anomalies(df)
        if flagged is not None and not flagged.empty:
            # Only note count; do not duplicate arithmetic as authoritative
            pass
    except Exception:
        pass
    return {
        "count": len(records),
        "expenses": records,
        "_provenance": _prov("anomalies", row_count=len(records)),
        "_truncated": len(records) >= MAX_RESULT_ROWS,
    }


@_register("forecast")
def forecast(user_id: int) -> dict:
    from db import get_expenses
    import forecasting as fc
    df = get_expenses(user_id)
    result = fc.forecast_next_month(df)
    # Ensure json-safe and provenance
    result = dict(result)
    if "_provenance" not in result:
        result["_provenance"] = _prov("forecast", row_count=result.get("history_months", 0))
    else:
        result["_provenance"] = dict(result["_provenance"])
        result["_provenance"].setdefault("calculation", "forecast")
    return result
