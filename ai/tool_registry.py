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
    "budget_runway": {"required": ["period_start"], "optional": ["total_budget_eur"]},
    "cashflow_summary": {"required": ["year", "month"], "optional": []},
    "savings_status": {"required": [], "optional": []},
    "project_savings": {"required": ["goal_name"], "optional": []},
    "debt_summary": {"required": [], "optional": []},
    "loan_scenario": {"required": ["principal_eur", "annual_rate_pct", "term_months"], "optional": ["extra_monthly_eur"]},
    "recurring_costs": {"required": [], "optional": []},
    "subscription_changes": {"required": [], "optional": []},
    "anomalies": {"required": [], "optional": ["multiplier"]},
    "forecast": {"required": [], "optional": []},
    "purchase_scenario": {"required": ["purchase_eur", "year", "month"], "optional": []},
    "spending_series": {"required": [], "optional": ["months"]},
    # #26 E3 mutations — dry_run/confirm accepted on all of them
    "add_expense": {"required": ["description", "amount_eur"],
                    "optional": ["category", "subcategory", "date", "notes", "dry_run", "confirm"]},
    "add_income": {"required": ["source", "amount_eur"],
                   "optional": ["income_type", "date", "notes", "dry_run", "confirm"]},
    "update_expense": {"required": ["expense_id", "updates"],
                       "optional": ["dry_run", "confirm"]},
    "delete_expense": {"required": ["expense_id"],
                       "optional": ["dry_run", "confirm"]},
    "add_recurring_template": {"required": ["description", "amount_eur"],
                               "optional": ["category", "subcategory", "due_day", "dry_run", "confirm"]},
    "update_recurring_template": {"required": ["template_id", "updates"],
                                  "optional": ["dry_run", "confirm"]},
    "delete_recurring_template": {"required": ["template_id"],
                                  "optional": ["dry_run", "confirm"]},
    "link_purchase_to_goal": {"required": ["purchase_id", "goal_ref"],
                              "optional": ["dry_run", "confirm"]},
    "unlink_purchase_from_goal": {"required": ["purchase_id"],
                                  "optional": ["dry_run", "confirm"]},
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


@_register("spending_series")
def spending_series(user_id: int, months: int = 12) -> dict:
    """AI-04: read-only monthly spending series (canonical totals only).

    Every value comes from the same aggregate the numbers pages use
    (fq.get_category_breakdown) — charts can never invent figures."""
    try:
        months = max(1, min(int(months), 24))
    except (TypeError, ValueError):
        months = 12
    today = date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    series = []
    for yy, mm in reversed(keys):
        total = float(sum(fq.get_category_breakdown(user_id, yy, mm).values()))
        series.append({"month": f"{yy:04d}-{mm:02d}", "amount_eur": round(total, 2)})
    start = date(keys[-1][0], keys[-1][1], 1)
    end = date(today.year, today.month, 1)
    return {
        "series": series,
        "_provenance": _prov("spending_series", row_count=len(series),
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
def budget_runway(user_id: int, period_start: str, total_budget_eur: float | None = None) -> dict:
    start = _parse_date(period_start)
    result = fq.budget_runway(user_id, start, total_budget_eur)
    result["depleted"] = result["days_remaining"] == 0
    result["_provenance"] = _prov("budget_runway", period_start=start)
    return result


@_register("cashflow_summary")
def cashflow_summary(user_id: int, year: int, month: int) -> dict:
    year = int(year)
    month = int(month)
    month_key = f"{year}-{month:02d}"
    summary = fq.get_expense_summary(user_id, month_key)
    result = dict(summary)
    result["requested_month"] = month_key
    result["_provenance"] = _prov("cashflow_summary", period_start=date(year, month, 1))
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
    proj = fq.project_savings_goal(user_id, goal_name)
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
    out = fq.loan_scenario(principal_eur, annual_rate_pct, term_months, extra_monthly_eur)
    out["_provenance"] = _prov("loan_scenario")
    return out


# ── Recurring / subscriptions / anomalies / forecast ──────────────────────

@_register("recurring_costs")
def recurring_costs(user_id: int) -> dict:
    result = fq.recurring_costs(user_id, MAX_RESULT_ROWS)
    result["_provenance"] = _prov("recurring_costs", row_count=len(result["bills"]))
    return result


@_register("subscription_changes")
def subscription_changes(user_id: int) -> dict:
    records = fq.subscription_changes(user_id, MAX_RESULT_ROWS)
    return {
        "count": len(records),
        "subscriptions": records,
        "_provenance": _prov("subscription_changes", row_count=len(records)),
        "_truncated": len(records) >= MAX_RESULT_ROWS,
    }


@_register("anomalies")
def anomalies(user_id: int, multiplier: float = 2.0) -> dict:
    records = fq.anomalies(user_id, multiplier, MAX_RESULT_ROWS)
    return {
        "count": len(records),
        "expenses": records,
        "_provenance": _prov("anomalies", row_count=len(records)),
        "_truncated": len(records) >= MAX_RESULT_ROWS,
    }


@_register("forecast")
def forecast(user_id: int) -> dict:
    result = fq.forecast(user_id)
    # Ensure json-safe and provenance
    result = dict(result)
    if "_provenance" not in result:
        result["_provenance"] = _prov("forecast", row_count=result.get("history_months", 0))
    else:
        result["_provenance"] = dict(result["_provenance"])
        result["_provenance"].setdefault("calculation", "forecast")
    return result


@_register("purchase_scenario")
def purchase_scenario(user_id: int, purchase_eur: float, year: int, month: int) -> dict:
    result = fq.purchase_scenario(user_id, purchase_eur, year, month)
    result["_provenance"] = _prov("purchase_scenario", period_start=date(int(year), int(month), 1))
    return result# ── Mutation tools (#26 E3) ──────────────────────────────────────────────
# Every mutation goes through services.commands (audit + revision + undo
# token). dry_run=True validates and previews without writing. Mutations
## above the user's confirm threshold return needs_confirmation unless
# confirm=True — the ask.py confirm card supplies that flag.

MUTATION_TOOLS: set[str] = {
    "add_expense", "add_income", "update_expense", "delete_expense",
    "add_recurring_template", "update_recurring_template",
    "delete_recurring_template", "link_purchase_to_goal",
    "unlink_purchase_from_goal",
}


def _mutation_guard(user_id: int, amount_eur: float | None) -> dict | None:
    """Rate-cap check shared by all mutation tools (None => not blocked)."""
    from services.commands import mutation_rate_limited
    if mutation_rate_limited(int(user_id)):
        return {
            "ok": False,
            "error": ("Agent mutation limit reached (20 per 24h). "
                      "Do it manually or wait — this protects your data."),
            "_provenance": _prov("mutation_guard"),
        }
    return None


def _mutation_result(user_id: int, command: str, res, preview: dict) -> dict:
    import services.commands as _C
    from ai import orchestrator as _orch
    if not getattr(res, "changed", False):
        return {"ok": True, "changed": False,
                "_provenance": _prov("mutation:" + command)}
    try:
        _C.record_agent_mutation(int(user_id))
    except Exception:
        pass
    out = {"ok": True, "changed": True,
           "_provenance": _prov("mutation:" + command),
           "stored": preview}
    tok = getattr(res, "undo_token", None)
    if tok is not None:
        # real token travels to the UI via result["mutations"]; the value
        # the MODEL sees is redacted by ai.safety (undo_* keys).
        out["undo_token"] = tok.token_id
        out["undo_description"] = tok.description
        try:
            _orch.note_mutation(int(user_id), tok.token_id,
                                tok.description, command,
                                dict(preview))
        except Exception:
            pass
    return out


@_register("add_expense")
def tool_add_expense(user_id: int, description: str, amount_eur: float,
                     category=None, subcategory: str = "", date=None,
                     notes: str = "", dry_run: bool = False,
                     confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, float(amount_eur))
    if blocked:
        return blocked
    preview = {"description": str(description),
               "amount_eur": round(float(amount_eur), 2),
               "date": str(date or "today"),
               "category": str(category or "auto")}
    if C.mutation_requires_confirmation(int(user_id), float(amount_eur)) \
            and not confirm and not dry_run:
        return {"needs_confirmation": True, "command": "add_expense",
                "args": {"description": str(description),
                         "amount_eur": float(amount_eur),
                         "category": category,
                         "subcategory": str(subcategory),
                         "date": str(date) if date else None,
                         "notes": str(notes)},
                "preview": preview,
                "_provenance": _prov("mutation:add_expense")}
    res = C.add_expense(int(user_id), description=str(description),
                        amount_eur=float(amount_eur), category=category,
                        subcategory=str(subcategory or ""), date=date,
                        notes=str(notes or ""), dry_run=bool(dry_run))
    return _mutation_result(user_id, "add_expense", res, preview)


@_register("add_income")
def tool_add_income(user_id: int, source: str, amount_eur: float,
                    income_type: str = "Other", date=None,
                    notes: str = "", dry_run: bool = False,
                    confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, float(amount_eur))
    if blocked:
        return blocked
    preview = {"source": str(source),
               "amount_eur": round(float(amount_eur), 2),
               "date": str(date or "today")}
    if C.mutation_requires_confirmation(int(user_id), float(amount_eur)) \
            and not confirm and not dry_run:
        return {"needs_confirmation": True, "command": "add_income",
                "args": {"source": str(source),
                         "amount_eur": float(amount_eur),
                         "income_type": str(income_type),
                         "date": str(date) if date else None,
                         "notes": str(notes)},
                "preview": preview,
                "_provenance": _prov("mutation:add_income")}
    res = C.add_income(int(user_id), source=str(source),
                       amount_eur=float(amount_eur),
                       income_type=str(income_type), date=date,
                       notes=str(notes or ""), dry_run=bool(dry_run))
    return _mutation_result(user_id, "add_income", res, preview)


@_register("update_expense")
def tool_update_expense(user_id: int, expense_id: str, updates: dict,
                        dry_run: bool = False,
                        confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if C.mutation_requires_confirmation(
            int(user_id),
            float(updates.get("amount_eur") or 0) +
            C.confirm_threshold_eur(int(user_id))) \
            and not confirm and not dry_run:
        return {"needs_confirmation": True, "command": "update_expense",
                "args": {"expense_id": str(expense_id),
                         "updates": dict(updates)},
                "preview": {"expense_id": str(expense_id),
                            "updates": dict(updates)},
                "_provenance": _prov("mutation:update_expense")}
    res = C.update_expense(int(user_id), str(expense_id), dict(updates),
                           dry_run=bool(dry_run))
    return _mutation_result(user_id, "update_expense", res,
                            {"expense_id": str(expense_id),
                             "updates": dict(updates)})


@_register("delete_expense")
def tool_delete_expense(user_id: int, expense_id: str,
                        dry_run: bool = False,
                        confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if not confirm and not dry_run:
        # deletes always confirm — the threshold does not apply to them.
        return {"needs_confirmation": True, "command": "delete_expense",
                "args": {"expense_id": str(expense_id)},
                "preview": {"expense_id": str(expense_id),
                            "action": "soft-delete (undoable)"},
                "_provenance": _prov("mutation:delete_expense")}
    res = C.delete_expense(int(user_id), str(expense_id),
                           dry_run=bool(dry_run))
    return _mutation_result(user_id, "delete_expense", res,
                            {"expense_id": str(expense_id)})


@_register("add_recurring_template")
def tool_add_recurring_template(user_id: int, description: str,
                                amount_eur: float, category=None,
                                subcategory: str = "", due_day=None,
                                dry_run: bool = False,
                                confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, float(amount_eur))
    if blocked:
        return blocked
    preview = {"description": str(description),
               "amount_eur": round(float(amount_eur), 2)}
    if C.mutation_requires_confirmation(int(user_id), float(amount_eur)) \
            and not confirm and not dry_run:
        return {"needs_confirmation": True,
                "command": "add_recurring_template",
                "args": {"description": str(description),
                         "amount_eur": float(amount_eur),
                         "category": category,
                         "subcategory": str(subcategory),
                         "due_day": due_day},
                "preview": preview,
                "_provenance": _prov("mutation:add_recurring_template")}
    res = C.add_recurring_template(
        int(user_id), description=str(description),
        amount_eur=float(amount_eur), category=category,
        subcategory=str(subcategory or ""), due_day=due_day,
        dry_run=bool(dry_run))
    return _mutation_result(user_id, "add_recurring_template", res, preview)


@_register("update_recurring_template")
def tool_update_recurring_template(user_id: int, template_id: str,
                                   updates: dict,
                                   dry_run: bool = False,
                                   confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if not confirm and not dry_run:
        return {"needs_confirmation": True,
                "command": "update_recurring_template",
                "args": {"template_id": str(template_id),
                         "updates": dict(updates)},
                "preview": {"template_id": str(template_id),
                            "updates": dict(updates)},
                "_provenance": _prov("mutation:update_recurring_template")}
    res = C.update_recurring_template(int(user_id), str(template_id),
                                      dict(updates))
    return _mutation_result(user_id, "update_recurring_template", res,
                            {"template_id": str(template_id),
                             "updates": dict(updates)})


@_register("delete_recurring_template")
def tool_delete_recurring_template(user_id: int, template_id: str,
                                   dry_run: bool = False,
                                   confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if not confirm and not dry_run:
        return {"needs_confirmation": True,
                "command": "delete_recurring_template",
                "args": {"template_id": str(template_id)},
                "preview": {"template_id": str(template_id),
                            "action": "soft-delete (undoable)"},
                "_provenance": _prov("mutation:delete_recurring_template")}
    res = C.delete_recurring_template(int(user_id), str(template_id))
    return _mutation_result(user_id, "delete_recurring_template", res,
                            {"template_id": str(template_id)})


@_register("link_purchase_to_goal")
def tool_link_purchase_to_goal(user_id: int, purchase_id: str,
                               goal_ref: str, dry_run: bool = False,
                               confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if not confirm and not dry_run:
        return {"needs_confirmation": True,
                "command": "link_purchase_to_goal",
                "args": {"purchase_id": str(purchase_id),
                         "goal_ref": str(goal_ref)},
                "preview": {"purchase_id": str(purchase_id),
                            "goal_ref": str(goal_ref)},
                "_provenance": _prov("mutation:link_purchase_to_goal")}
    res = C.link_purchase_to_goal(int(user_id), str(purchase_id),
                                  str(goal_ref))
    return _mutation_result(user_id, "link_purchase_to_goal", res,
                            {"purchase_id": str(purchase_id),
                             "goal_ref": str(goal_ref)})


@_register("unlink_purchase_from_goal")
def tool_unlink_purchase_from_goal(user_id: int, purchase_id: str,
                                   dry_run: bool = False,
                                   confirm: bool = False) -> dict:
    from services import commands as C
    blocked = _mutation_guard(user_id, None)
    if blocked:
        return blocked
    if not confirm and not dry_run:
        return {"needs_confirmation": True,
                "command": "unlink_purchase_from_goal",
                "args": {"purchase_id": str(purchase_id)},
                "preview": {"purchase_id": str(purchase_id),
                            "action": "clear funding link"},
                "_provenance": _prov("mutation:unlink_purchase_from_goal")}
    res = C.unlink_purchase_from_goal(int(user_id), str(purchase_id))
    return _mutation_result(user_id, "unlink_purchase_from_goal", res,
                            {"purchase_id": str(purchase_id)})
