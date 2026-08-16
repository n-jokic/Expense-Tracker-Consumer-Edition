"""
mcp_server.py — MCP (Model Context Protocol) server for OpenClaw / AI agents.

Exposes read tools plus two audited write tools (add_expense / add_income) on
the SAME SQLCipher-encrypted database the app uses, so an OpenClaw agent can
answer "how much did I spend on groceries this month?" or log an expense.

Default transport is stdio (OpenClaw launches it locally). `--http` serves
streamable HTTP bound to 127.0.0.1 only — it trusts every local process, so
only enable it when that is acceptable. The recommended OpenClaw setup is
stdio; see the README for the exact commands.

The target account is EXPENSE_TRACKER_MCP_USERNAME (or the first account).
Writes are audit-logged with {"via": "mcp"} and bump the shared data revision
so open browser sessions pick them up on their next refresh.
"""

import os
import math
import argparse
from datetime import date, datetime, timedelta

import pandas as pd

from mcp.server.mcpserver import MCPServer

from db import (init_db, get_session, User, get_user_by_username, get_settings,
                get_expenses, get_income, get_budgets, get_savings,
                get_savings_accounts, get_recurring, get_loans,
                get_earned_milestone_ids,
                add_expense as db_add_expense, add_income as db_add_income,
                bump_data_revision)
from utils import (CATEGORIES, CAT_LIST, INCOME_TYPES, SUPPORTED_CURRENCIES,
                   MAX_AMOUNT, get_rates, to_eur)

server = MCPServer(
    name="expense-tracker",
    title="Expense Tracker",
    description=(
        "Personal finance data for the local Expense Tracker app: spending, "
        "income, budgets, savings, bills, loans, milestones, and insights. "
        "Writes (add_expense, add_income) are audit-logged."),
    version="4.0",
    instructions=(
        "Amounts are stored in EUR (amount_eur). Use list_* tools to read "
        "history before answering questions about the user's finances. Only "
        "log entries the user explicitly asked for; never guess amounts."),
)

MCP_USERNAME = (os.environ.get("EXPENSE_TRACKER_MCP_USERNAME") or "").strip().lower() or None

_USER_ID: int | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_user() -> int:
    global _USER_ID
    if _USER_ID is not None:
        return _USER_ID
    if MCP_USERNAME:
        u = get_user_by_username(MCP_USERNAME)
        if not u:
            raise RuntimeError(
                f"No account named '{MCP_USERNAME}'. Set "
                "EXPENSE_TRACKER_MCP_USERNAME to an existing account.")
    else:
        with get_session() as s:
            u = s.query(User).order_by(User.id.asc()).first()
        if not u:
            raise RuntimeError(
                "No accounts exist yet — create one in the app first "
                "(or set EXPENSE_TRACKER_MCP_USERNAME).")
    _USER_ID = int(u["id"] if isinstance(u, dict) else u.id)
    return _USER_ID


def _clean(v):
    """Make any value JSON-safe (None for NaN, ISO strings for dates)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):  # numpy scalars
        try:
            return _clean(v.item())
        except Exception:
            pass
    return v


def _records(df: pd.DataFrame, columns: list | None = None) -> list:
    if df is None or df.empty:
        return []
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return [{k: _clean(v) for k, v in row.items()}
            for row in df.to_dict(orient="records")]


def _month_bounds(month: str) -> tuple[date, date]:
    """'current', 'last', or 'YYYY-MM' → (first_day, first_day_of_next)."""
    m = (month or "current").strip().lower()
    today = date.today()
    if m in ("current", "this", "now"):
        first = today.replace(day=1)
    elif m in ("last", "previous"):
        prev = (today.replace(day=1) - timedelta(days=1))
        first = prev.replace(day=1)
    else:
        try:
            first = datetime.strptime(m, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise ValueError("month must be 'current', 'last', or 'YYYY-MM'")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt


def _parse_date(d: str | None) -> date:
    if not d:
        return date.today()
    d = d.strip().lower()
    if d in ("today", "now"):
        return date.today()
    if d == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("date must be 'YYYY-MM-DD', 'today', or 'yesterday'")


def _user_rates() -> dict:
    return get_rates(get_settings(_resolve_user()))


def _err(e: Exception) -> dict:
    return {"ok": False, "error": str(e)}


def _in_month(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df
    return df[(df["date"] >= pd.Timestamp(start)) & (df["date"] < pd.Timestamp(end))]


# ── Read tools ────────────────────────────────────────────────────────────────

async def _expense_summary_impl(month: str) -> dict:
    uid = _resolve_user()
    start, end = _month_bounds(month)
    expenses = _in_month(get_expenses(uid), start, end)
    income = _in_month(get_income(uid), start, end)
    budgets = get_budgets(uid)
    if not budgets.empty:
        b = budgets[(budgets["year"] == start.year) & (budgets["month"] == start.month)]
        budget_total = float(b["budgeted_eur"].fillna(0).sum())
    else:
        budget_total = 0.0
    spent = float(expenses["amount_eur"].fillna(0).sum()) if not expenses.empty else 0.0
    earned = float(income["actual_eur"].fillna(0).sum()) if not income.empty else 0.0
    settings = get_settings(uid)
    from insights import top_category_this_month
    top = top_category_this_month(expenses, start.year, start.month)
    return {
        "ok": True, "month": f"{start.year}-{start.month:02d}",
        "spent_eur": round(spent, 2), "income_eur": round(earned, 2),
        "net_eur": round(earned - spent, 2),
        "budget_total_eur": round(budget_total, 2),
        "budget_remaining_eur": round(budget_total - spent, 2),
        "top_category": {"category": top[0], "amount_eur": round(top[1], 2)} if top else None,
        "fun_money_eur": settings.get("fun_money") or 0.0,
        "monthly_budget_eur": settings.get("monthly_budget") or 0.0,
    }


async def _list_expenses_impl(month: str, category: str | None, limit: int) -> dict:
    uid = _resolve_user()
    df = get_expenses(uid)
    start, end = _month_bounds(month)
    df = _in_month(df, start, end)
    if category:
        df = df[df["category"].str.lower() == category.strip().lower()]
    total = float(df["amount_eur"].fillna(0).sum()) if not df.empty else 0.0
    df = df.sort_values("date", ascending=False).head(max(1, min(int(limit), 500)))
    cols = ["date", "category", "subcategory", "description", "amount",
            "currency", "amount_eur", "notes"]
    return {"ok": True, "count": len(df), "total_eur": round(total, 2),
            "expenses": _records(df, cols)}


async def _search_expenses_impl(query: str, limit: int) -> dict:
    uid = _resolve_user()
    q = (query or "").strip().lower()
    if not q:
        return {"ok": False, "error": "query must not be empty"}
    df = get_expenses(uid)
    if df.empty:
        return {"ok": True, "count": 0, "expenses": []}
    mask = pd.Series(False, index=df.index)
    for col in ("description", "category", "subcategory", "notes"):
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
    out = df[mask].sort_values("date", ascending=False).head(max(1, min(int(limit), 100)))
    cols = ["date", "category", "subcategory", "description", "amount",
            "currency", "amount_eur", "notes"]
    return {"ok": True, "count": len(out), "expenses": _records(out, cols)}


async def _list_income_impl(month: str) -> dict:
    uid = _resolve_user()
    start, end = _month_bounds(month)
    df = _in_month(get_income(uid), start, end)
    total = float(df["actual_eur"].fillna(0).sum()) if not df.empty else 0.0
    df = df.sort_values("date", ascending=False)
    cols = ["date", "income_type", "source", "actual", "currency",
            "actual_eur", "notes"]
    return {"ok": True, "count": len(df), "total_eur": round(total, 2),
            "income": _records(df, cols)}


async def _list_budgets_impl() -> dict:
    df = get_budgets(_resolve_user())
    cols = ["year", "month", "category", "subcategory", "budgeted_eur"]
    return {"ok": True, "count": len(df), "budgets": _records(df, cols)}


async def _list_savings_goals_impl() -> dict:
    uid = _resolve_user()
    goals = []
    sv = get_savings(uid)
    if not sv.empty:
        sv = sv.sort_values("date")
        for name in sv["goal_name"].fillna("").unique():
            rows = sv[sv["goal_name"].fillna("") == name]
            if rows.empty:
                continue
            last = rows.iloc[-1]
            goals.append({
                "goal_name": name,
                "balance_eur": _clean(last.get("balance_eur")),
                "target_eur": _clean(last.get("target_eur")),
                "interest_rate_pct": _clean(last.get("interest_rate")),
            })
    accounts = get_savings_accounts(uid)
    term = _records(accounts, ["id", "account_name", "bank", "amount_eur",
                               "currency", "interest_rate_pct", "start_date",
                               "maturity_date", "status"]) if not accounts.empty else []
    return {"ok": True, "goals": goals, "term_deposits": term}


async def _list_recurring_bills_impl() -> dict:
    df = get_recurring(_resolve_user())
    cols = ["id", "category", "subcategory", "description", "amount",
            "currency", "amount_eur", "due_day", "active", "notes"]
    return {"ok": True, "count": len(df), "bills": _records(df, cols)}


async def _list_loans_impl() -> dict:
    df = get_loans(_resolve_user())
    cols = ["id", "name", "principal", "annual_rate", "term_months",
            "payment_day", "start_date", "currency", "notes"]
    return {"ok": True, "count": len(df), "loans": _records(df, cols)}


async def _get_milestones_impl() -> dict:
    ids = sorted(get_earned_milestone_ids(_resolve_user()))
    from gamification import MILESTONE_INDEX
    return {"ok": True, "count": len(ids),
            "milestones": [{"id": i, **MILESTONE_INDEX[i]} for i in ids
                           if i in MILESTONE_INDEX]}


async def _get_insights_impl() -> dict:
    uid = _resolve_user()
    today = date.today()
    expenses = get_expenses(uid)
    income = get_income(uid)
    settings = get_settings(uid)
    from insights import (month_over_month, top_category_this_month,
                          unusual_expenses, days_until_budget_depleted)
    out = {
        "ok": True,
        "spending_mom": month_over_month(expenses, "amount_eur", today.year, today.month),
        "income_mom": month_over_month(income, "actual_eur", today.year, today.month),
    }
    top = top_category_this_month(expenses, today.year, today.month)
    if top:
        out["top_category_this_month"] = {"category": top[0],
                                          "amount_eur": round(float(top[1]), 2)}
    unusual = unusual_expenses(expenses).sort_values("amount_eur", ascending=False).head(5)
    out["unusual_expenses"] = _records(unusual, ["date", "category", "description",
                                                 "amount_eur"])
    budget = float(settings.get("monthly_budget") or 0.0)
    if budget > 0:
        out["days_until_budget_depleted"] = days_until_budget_depleted(
            expenses, budget, today.replace(day=1))
    return out


# ── Write tools ───────────────────────────────────────────────────────────────

async def _add_expense_impl(amount: float, category: str, description: str = "",
                            date_str: str | None = None, subcategory: str = "",
                            currency: str = "EUR") -> dict:
    try:
        uid = _resolve_user()
        amt = float(amount)
        if not math.isfinite(amt) or amt <= 0 or amt > MAX_AMOUNT:
            raise ValueError(f"amount must be > 0 and <= {MAX_AMOUNT:g}")
        cat = (category or "").strip()
        if cat not in CAT_LIST:
            raise ValueError(f"unknown category '{cat}' — use one of: {', '.join(CAT_LIST)}")
        sub = (subcategory or "").strip()
        if sub and sub not in CATEGORIES[cat]:
            raise ValueError(f"unknown subcategory '{sub}' for {cat} "
                             f"(valid: {', '.join(CATEGORIES[cat])})")
        desc = (description or "").strip()
        if not desc:
            raise ValueError("description is required")
        cur = (currency or "EUR").strip().upper()
        if cur not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unknown currency '{cur}'")
        when = _parse_date(date_str)
        rates = _user_rates()
        ae = to_eur(amt, cur, rates)
        row = {
            "date": when, "category": cat, "subcategory": sub,
            "description": desc, "amount": amt, "currency": cur,
            "amount_eur": ae, "recurring": False, "notes": "",
            "via": "mcp",
        }
        exp_id = db_add_expense(uid, row)
        bump_data_revision(uid)
        return {"ok": True, "id": exp_id, "amount_eur": round(ae, 2),
                "date": when.isoformat(),
                "message": f"Logged expense '{desc}' ({cat}) for {amt:g} {cur}."}
    except Exception as e:
        return _err(e)


async def _add_income_impl(amount: float, income_type: str = "Other",
                           date_str: str | None = None, currency: str = "EUR",
                           notes: str = "") -> dict:
    try:
        uid = _resolve_user()
        amt = float(amount)
        if not math.isfinite(amt) or amt <= 0 or amt > MAX_AMOUNT:
            raise ValueError(f"amount must be > 0 and <= {MAX_AMOUNT:g}")
        itype = (income_type or "Other").strip()
        if itype not in INCOME_TYPES:
            raise ValueError(f"unknown income_type '{itype}' — use one of: "
                             f"{', '.join(INCOME_TYPES)}")
        cur = (currency or "EUR").strip().upper()
        if cur not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unknown currency '{cur}'")
        when = _parse_date(date_str)
        rates = _user_rates()
        ae = to_eur(amt, cur, rates)
        row = {
            "date": when, "source": itype, "income_type": itype,
            "hours": None, "rate": None,
            "budgeted": amt, "actual": amt, "currency": cur,
            "budgeted_eur": ae, "actual_eur": ae, "notes": notes or "",
            "via": "mcp",
        }
        inc_id = db_add_income(uid, row)
        bump_data_revision(uid)
        return {"ok": True, "id": inc_id, "amount_eur": round(ae, 2),
                "date": when.isoformat(),
                "message": f"Logged {itype} income of {amt:g} {cur}."}
    except Exception as e:
        return _err(e)


# ── Tool registration ─────────────────────────────────────────────────────────

@server.tool()
async def expense_summary(month: str = "current") -> dict:
    """Monthly financial summary for 'current', 'last', or 'YYYY-MM':
    spending, income, net, budget usage, top spending category, fun money."""
    try:
        return await _expense_summary_impl(month)
    except Exception as e:
        return _err(e)


@server.tool()
async def list_expenses(month: str = "current", category: str = None,
                        limit: int = 50) -> dict:
    """Expenses in a month ('current', 'last', 'YYYY-MM'), optionally filtered
    by exact category name. Includes the month's EUR total."""
    try:
        return await _list_expenses_impl(month, category, limit)
    except Exception as e:
        return _err(e)


@server.tool()
async def search_expenses(query: str, limit: int = 20) -> dict:
    """Case-insensitive search over expense descriptions, categories, and
    notes. Returns the matching expenses with their EUR amounts."""
    try:
        return await _search_expenses_impl(query, limit)
    except Exception as e:
        return _err(e)


@server.tool()
async def list_income(month: str = "current") -> dict:
    """Income entries in a month ('current', 'last', 'YYYY-MM') with totals."""
    try:
        return await _list_income_impl(month)
    except Exception as e:
        return _err(e)


@server.tool()
async def list_budgets() -> dict:
    """All category budgets (year, month, category, subcategory, EUR amount)."""
    try:
        return await _list_budgets_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def list_savings_goals() -> dict:
    """Savings goals (latest balance/target) and term-deposit accounts."""
    try:
        return await _list_savings_goals_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def list_recurring_bills() -> dict:
    """Recurring bills/templates (category, amount, due day, active flag)."""
    try:
        return await _list_recurring_bills_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def list_loans() -> dict:
    """Loans: principal, annual rate, term, payment day, start date."""
    try:
        return await _list_loans_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def get_milestones() -> dict:
    """Gamification milestones/badges the account has already earned."""
    try:
        return await _get_milestones_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def get_insights() -> dict:
    """Month-over-month spending/income trends, top category, unusual
    expenses, and days until the monthly budget runs out."""
    try:
        return await _get_insights_impl()
    except Exception as e:
        return _err(e)


@server.tool()
async def add_expense(amount: float, category: str, description: str = "",
                      date: str = None, subcategory: str = "",
                      currency: str = "EUR") -> dict:
    """Log an expense (audit-logged, visible in the app). `amount` must be
    positive; `category` must be one of the app's categories (see
    list_expenses output or use search to check); `date` is 'YYYY-MM-DD',
    'today', or omitted. Returns the new record id."""
    return await _add_expense_impl(amount, category, description, date,
                                   subcategory, currency)


@server.tool()
async def add_income(amount: float, income_type: str = "Other",
                     date: str = None, currency: str = "EUR",
                     notes: str = "") -> dict:
    """Log income (audit-logged, visible in the app). `income_type` is one of
    Salary, Hourly, 'Bonus / Raise', Freelance, Investment, Rental, Other."""
    return await _add_income_impl(amount, income_type, date, currency, notes)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python mcp_server.py",
        description="Expense Tracker MCP server (OpenClaw / MCP clients).")
    parser.add_argument("--http", action="store_true",
                        help="serve streamable HTTP on 127.0.0.1 (default: stdio)")
    parser.add_argument("--port", type=int, default=8510,
                        help="HTTP port (default: 8510)")
    args = parser.parse_args()

    init_db()
    _resolve_user()  # fail fast with a clear message before serving

    if args.http:
        server.run(transport="streamable-http", host="127.0.0.1", port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
