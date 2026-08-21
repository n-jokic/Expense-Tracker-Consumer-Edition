"""
services/finance_queries.py — canonical read/query services for finance data.

Streamlit-free: imports none of ``streamlit`` / ``queries`` — callers supply
or let the service read directly from ``db.*``. MCP, Streamlit (via queries.py
wrappers if desired), and the future AI tool registry all consume these same
functions so finance arithmetic has exactly one implementation.

Every function that touches the DB takes a ``user_id`` and reads fresh rows via
``db.get_*``; functions that only transform already-fetched DataFrames are pure
(and easily unit-tested). Amounts are in EUR (amount_eur/actual_eur) — display
conversion is the caller's responsibility.

This module also re-homes the pure analysis helpers previously in
``insights.py`` (month_over_month, top_category_this_month, unusual_expenses,
days_until_budget_depleted, savings_projection, build_narrative_stats, and the
recurring/fun-money/travel aggregations that were duplicated across the app).
``insights.py`` and ``mcp_server.py`` should delegate to these.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

import db
import finance as fin

# Inlined from utils.effective_category_budgets to keep this module
# Streamlit-free (utils imports streamlit at top; R6 will move this to domain).
def _effective_category_budgets(m_bud) -> dict:
    """Effective budget per category for a single month (budget-scope semantics)."""
    if m_bud is None or m_bud.empty:
        return {}
    df = m_bud.copy()
    df["_sub"] = df["subcategory"].fillna("").astype(str).str.strip()
    eff: dict = {}
    for cat, g in df.groupby("category"):
        subs = g[g["_sub"] != ""]
        if not subs.empty:
            eff[cat] = float(subs["budgeted_eur"].sum())
        else:
            eff[cat] = float(g["budgeted_eur"].sum())
    return eff


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Period:
    start: date
    end: date


@dataclass(frozen=True)
class SpendingComparison:
    period_a: Period
    period_b: Period
    total_a_eur: float
    total_b_eur: float
    difference_eur: float
    change_pct: float | None
    transactions_a: int
    transactions_b: int


# ── Pure helpers (no DB) ────────────────────────────────────────────────────

def _in_month(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df
    return df[(df["date"] >= pd.Timestamp(start)) & (df["date"] < pd.Timestamp(end))]


def month_bounds(month: str) -> tuple[date, date]:
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


def parse_date(d: str | None) -> date:
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


# ── Re-homed pure analysis (from insights.py) ───────────────────────────────

def month_over_month(df: pd.DataFrame, col: str, current_year: int, current_month: int) -> dict:
    """Compare current month total vs previous month for the given column."""
    if df.empty or col not in df.columns:
        return {"current": 0.0, "previous": 0.0, "change_pct": 0.0, "trend": "same"}
    prev_month = current_month - 1 if current_month > 1 else 12
    prev_year = current_year if current_month > 1 else current_year - 1
    cur = df[(df["date"].dt.year == current_year) & (df["date"].dt.month == current_month)][col].sum()
    prev = df[(df["date"].dt.year == prev_year) & (df["date"].dt.month == prev_month)][col].sum()
    cur, prev = float(cur), float(prev)
    if prev == 0:
        change_pct = 100.0 if cur > 0 else 0.0
        trend = "up" if cur > 0 else "same"
    else:
        change_pct = ((cur - prev) / prev) * 100
        trend = "up" if cur > prev else ("down" if cur < prev else "same")
    return {"current": cur, "previous": prev, "change_pct": round(change_pct, 1), "trend": trend}


def top_category_this_month(expenses_df: pd.DataFrame, year: int, month: int):
    if expenses_df.empty:
        return None
    m = expenses_df[(expenses_df["date"].dt.year == year) & (expenses_df["date"].dt.month == month)]
    if m.empty:
        return None
    grp = m.groupby("category")["amount_eur"].sum()
    cat = grp.idxmax()
    return cat, float(grp.max())


def unusual_expenses(expenses_df: pd.DataFrame, multiplier: float = 2.0) -> pd.DataFrame:
    if expenses_df.empty:
        return expenses_df.iloc[0:0]
    avgs = expenses_df.groupby("category")["amount_eur"].mean()
    def is_unusual(row):
        avg = avgs.get(row["category"], 0)
        return avg > 0 and row["amount_eur"] > avg * multiplier
    return expenses_df[expenses_df.apply(is_unusual, axis=1)].copy()


def days_until_budget_depleted(expenses_df: pd.DataFrame, total_budget_eur: float,
                               period_start: date) -> int | None:
    if total_budget_eur <= 0 or expenses_df.empty:
        return None
    today = date.today()
    days_elapsed = max((today - period_start).days + 1, 1)
    period_exp = expenses_df[
        (expenses_df["date"].dt.date >= period_start)
        & (expenses_df["date"].dt.date <= today)
    ]
    if period_exp.empty:
        return None
    spent = float(period_exp["amount_eur"].sum())
    daily_avg = spent / days_elapsed
    if daily_avg <= 0:
        return None
    remaining_budget = total_budget_eur - spent
    if remaining_budget <= 0:
        return 0
    return int(remaining_budget / daily_avg)


def savings_projection(savings_df: pd.DataFrame, goal_name: str) -> dict:
    empty = {"current_balance": 0.0, "target": 0.0, "months_to_goal": None, "projected_date": None}
    if savings_df.empty:
        return empty
    rows = savings_df[savings_df["goal_name"] == goal_name].sort_values("date")
    if rows.empty:
        return empty
    latest = rows.iloc[-1]
    balance = float(latest["balance_eur"]) if pd.notna(latest["balance_eur"]) else 0.0
    target = float(rows["target_eur"].max()) if pd.notna(rows["target_eur"].max()) else 0.0
    interest_rate = float(latest["interest_rate"]) if pd.notna(latest["interest_rate"]) else 0.0
    if target <= 0 or balance >= target:
        return {"current_balance": balance, "target": target,
                "months_to_goal": 0, "projected_date": date.today()}
    if len(rows) >= 2:
        # The first deposit row CREATES the goal (seed/opening deposit) and is
        # not representative of the ongoing monthly run-rate. Exclude it before
        # resampling so a large opening deposit doesn't inflate the projection.
        # If excluding it leaves zero rows, fall back to all-rows behaviour.
        trailing = rows.iloc[1:] if len(rows) >= 2 else rows
        monthly = (trailing.set_index("date")["deposited_eur"].resample("MS").sum().dropna())
        if monthly.empty:
            monthly_dep = float(rows["deposited_eur"].mean())
        else:
            monthly_dep = float(monthly.tail(3).mean())
    else:
        monthly_dep = float(rows["deposited_eur"].mean())
    if pd.isna(monthly_dep) or monthly_dep <= 0:
        return {"current_balance": balance, "target": target,
                "months_to_goal": None, "projected_date": None}
    monthly_rate = (interest_rate / 100) / 12
    cur_bal = balance
    months = 0
    while cur_bal < target and months < 600:
        cur_bal = cur_bal * (1 + monthly_rate) + monthly_dep
        months += 1
    if months >= 600:
        return {"current_balance": balance, "target": target,
                "months_to_goal": None, "projected_date": None}
    today = date.today()
    proj_year = today.year + (today.month - 1 + months) // 12
    proj_month = (today.month - 1 + months) % 12 + 1
    from datetime import date as _date
    proj_date = _date(proj_year, proj_month, 1)
    return {"current_balance": balance, "target": target,
            "months_to_goal": months, "projected_date": proj_date}


def build_narrative_stats(expenses_df: pd.DataFrame, settings: dict,
                          year: int, month: int) -> dict:
    mom = month_over_month(expenses_df, "amount_eur", year, month)
    stats = {"spent_eur": round(mom["current"], 2),
             "prev_spent_eur": round(mom["previous"], 2),
             "change_pct": round(mom["change_pct"], 1)}
    top = top_category_this_month(expenses_df, year, month)
    if top:
        stats["top_category"] = f"{top[0]} ({top[1]:.2f} EUR)"
    unusual = unusual_expenses(expenses_df, multiplier=2.5)
    unusual = (unusual[(unusual["date"].dt.year == year)
                       & (unusual["date"].dt.month == month)]
               if not unusual.empty else pd.DataFrame())
    if not unusual.empty:
        stats["unusual"] = [
            f"{r['description']} ({r['amount_eur']:.2f} EUR)"
            for _, r in unusual.head(3).iterrows()]
    budget = float(settings.get("monthly_budget") or 0.0)
    if budget > 0:
        stats["budget_remaining"] = round(budget - stats["spent_eur"], 2)
    return stats


# ── Canonical read services (DB) ────────────────────────────────────────────

def list_expenses(user_id: int, month: str = "current",
                  category: str | None = None, limit: int = 50) -> dict:
    df = db.get_expenses(user_id)
    start, end = month_bounds(month)
    df = _in_month(df, start, end)
    if category:
        df = df[df["category"].str.lower() == category.strip().lower()]
    total = float(df["amount_eur"].fillna(0).sum()) if not df.empty else 0.0
    df = df.sort_values("date", ascending=False).head(max(1, min(int(limit), 500)))
    return {"count": len(df), "total_eur": round(total, 2), "expenses": df,
            "month_start": start, "month_end": end}


def search_expenses(user_id: int, query: str, limit: int = 20) -> dict:
    q = (query or "").strip().lower()
    if not q:
        raise ValueError("query must not be empty")
    df = db.get_expenses(user_id)
    if df.empty:
        return {"count": 0, "expenses": df}
    mask = pd.Series(False, index=df.index)
    for col in ("description", "category", "subcategory", "notes"):
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
    out = df[mask].sort_values("date", ascending=False).head(max(1, min(int(limit), 100)))
    return {"count": len(out), "expenses": out}


def list_income(user_id: int, month: str = "current") -> dict:
    start, end = month_bounds(month)
    df = _in_month(db.get_income(user_id), start, end)
    total = float(df["actual_eur"].fillna(0).sum()) if not df.empty else 0.0
    df = df.sort_values("date", ascending=False)
    return {"count": len(df), "total_eur": round(total, 2), "income": df,
            "month_start": start, "month_end": end}


def get_expense_summary(user_id: int, month: str = "current") -> dict:
    start, end = month_bounds(month)
    expenses = _in_month(db.get_expenses(user_id), start, end)
    income = _in_month(db.get_income(user_id), start, end)
    budgets = db.get_budgets(user_id)
    if not budgets.empty:
        b = budgets[(budgets["year"] == start.year) & (budgets["month"] == start.month)]
        budget_total = float(sum(_effective_category_budgets(b).values())) if not b.empty else 0.0
    else:
        budget_total = 0.0
    spent = float(expenses["amount_eur"].fillna(0).sum()) if not expenses.empty else 0.0
    earned = float(income["actual_eur"].fillna(0).sum()) if not income.empty else 0.0
    settings = db.get_settings(user_id)
    top = top_category_this_month(expenses, start.year, start.month)
    return {
        "month": f"{start.year}-{start.month:02d}",
        "month_start": start, "month_end": end,
        "spent_eur": round(spent, 2), "income_eur": round(earned, 2),
        "net_eur": round(earned - spent, 2),
        "budget_total_eur": round(budget_total, 2),
        "budget_remaining_eur": round(budget_total - spent, 2),
        "top_category": {"category": top[0], "amount_eur": round(top[1], 2)} if top else None,
        "fun_money_eur": settings.get("fun_money") or 0.0,
        "monthly_budget_eur": settings.get("monthly_budget") or 0.0,
    }


def get_category_breakdown(user_id: int, year: int, month: int) -> dict:
    df = db.get_expenses(user_id)
    m = df[(df["date"].dt.year == year) & (df["date"].dt.month == month)] if not df.empty else df
    if m.empty:
        return {}
    return m.groupby("category")["amount_eur"].sum().to_dict()


def get_merchant_breakdown(user_id: int, year: int, month: int, n: int = 5) -> list:
    df = db.get_expenses(user_id)
    m = df[(df["date"].dt.year == year) & (df["date"].dt.month == month)] if not df.empty else df
    if m.empty:
        return []
    s = m.groupby("description")["amount_eur"].sum().nlargest(n)
    return [{"merchant": k, "amount_eur": float(v)} for k, v in s.items()]


def get_budget_vs_actual(user_id: int, year: int, month: int) -> dict:
    budgets = db.get_budgets(user_id)
    b = budgets[(budgets["year"] == year) & (budgets["month"] == month)] if not budgets.empty else budgets
    eff = _effective_category_budgets(b)
    expenses = db.get_expenses(user_id)
    m = expenses[(expenses["date"].dt.year == year) & (expenses["date"].dt.month == month)] if not expenses.empty else expenses
    out = {}
    for cat, budgeted in eff.items():
        actual = float(m[m["category"] == cat]["amount_eur"].sum()) if not m.empty else 0.0
        out[cat] = {"budgeted_eur": float(budgeted), "actual_eur": actual,
                    "remaining_eur": float(budgeted) - actual}
    return out


def get_savings_summary(user_id: int) -> dict:
    sv = db.get_savings(user_id)
    if sv.empty:
        return {"goals": [], "total_balance_eur": 0.0, "interest_total_eur": 0.0}
    sv = sv.sort_values("date")
    goals = []
    total_bal = 0.0
    interest_total = 0.0
    for name in sv["goal_name"].dropna().unique():
        rows = sv[sv["goal_name"] == name]
        last = rows.iloc[-1]
        bal = float(last.get("balance_eur") or 0.0)
        tgt = float(last.get("target_eur") or 0.0)
        total_bal += bal
        dep_sum = float(rows["deposited_eur"].sum())
        interest_total += bal - dep_sum
        goals.append({"goal_name": name, "balance_eur": bal, "target_eur": tgt,
                      "interest_rate_pct": float(last.get("interest_rate") or 0.0)})
    return {"goals": goals, "total_balance_eur": total_bal, "interest_total_eur": interest_total}


def get_locked_savings(user_id: int, asof: date | None = None) -> float:
    asof = asof or date.today()
    accs = db.get_savings_accounts(user_id)
    locked = 0.0
    for _, a in accs.iterrows():
        if a["status"] == "closed":
            continue
        if pd.isna(a["start_date"]) or pd.isna(a["maturity_date"]):
            locked += float(a["amount_eur"] or 0.0)
            continue
        end = (a["maturity_date"].date() if a["maturity_date"].date() < asof else asof)
        locked += fin.accrued_value(float(a["amount_eur"]), float(a["annual_rate"]),
                                    a["start_date"].date(), end)
    return locked


def get_debt_summary(user_id: int, asof: date | None = None) -> dict:
    asof = asof or date.today()
    loans = db.get_loans(user_id)
    total_debt = 0.0
    monthly_payments = 0.0
    debt_free_dates = []
    for _, row in loans.iterrows():
        start_date = (row["start_date"].date() if pd.notna(row["start_date"]) else date.today())
        _principal = float(row["principal_eur"]) if pd.notna(row["principal_eur"]) else 0.0
        _rate = float(row["annual_rate"]) if pd.notna(row["annual_rate"]) else 0.0
        # loan_payments are expenses linked to the loan
        pay_df = db.get_loan_payments(user_id, str(row["id"]))
        payments = []
        for _, p in pay_df.iterrows():
            if pd.isna(p.get("date")):
                continue
            payments.append({"date": p["date"].date(),
                             "amount_eur": float(p.get("amount_eur") or 0.0),
                             "surcharge_eur": float(p.get("loan_surcharge_eur") or 0.0)})
        sched = fin.loan_schedule(_principal, _rate, int(row["term_months"]),
                                  start_date, int(row["payment_day"]), payments, asof)
        if row.get("status") == "active":
            total_debt += sched["remaining_balance"]
            if sched["payoff_date"]:
                debt_free_dates.append(sched["payoff_date"])
            monthly_payments += sched["monthly_payment"]
    return {
        "total_debt_eur": round(total_debt, 2),
        "monthly_payments_eur": round(monthly_payments, 2),
        "debt_free_date": max(debt_free_dates) if debt_free_dates else None,
        "active_loan_count": int((loans["status"] == "active").sum()) if not loans.empty else 0,
    }


def get_recurring_monthly_total(user_id: int) -> float:
    df = db.get_recurring(user_id)
    if df.empty:
        return 0.0
    return float(df[df["active"] == True]["amount_eur"].sum())


def budget_runway(user_id: int, period_start: date, total_budget_eur: float | None = None) -> dict:
    """Budget depletion estimate using the user's configured monthly budgets."""
    budgets = get_budget_vs_actual(user_id, period_start.year, period_start.month)
    total = float(total_budget_eur) if total_budget_eur is not None else sum(
        row["budgeted_eur"] for row in budgets.values())
    return {"total_budget_eur": total, "period_start": period_start.isoformat(),
            "days_remaining": days_until_budget_depleted(db.get_expenses(user_id), total, period_start),
            "depleted": False}


def project_savings_goal(user_id: int, goal_name: str) -> dict:
    return savings_projection(db.get_savings(user_id), goal_name)


def recurring_costs(user_id: int, limit: int = 100) -> dict:
    df = db.get_recurring(user_id)
    active = df[df["active"] == True] if not df.empty and "active" in df else df  # noqa: E712
    bills = [{"description": str(row.get("description", "")),
              "amount_eur": float(row.get("amount_eur", 0) or 0),
              "category": str(row.get("category", ""))}
             for _, row in active.head(limit).iterrows()]
    return {"monthly_total_eur": get_recurring_monthly_total(user_id), "bills": bills}


def subscription_changes(user_id: int, limit: int = 100) -> list[dict]:
    import forecasting as fc
    df = fc.detect_subscriptions(db.get_expenses(user_id))
    if df is None or df.empty:
        return []
    rows = []
    for _, row in df.head(limit).iterrows():
        item = row.to_dict()
        for key, value in item.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
        rows.append(item)
    return rows


def anomalies(user_id: int, multiplier: float = 2.0, limit: int = 100) -> list[dict]:
    df = unusual_expenses(db.get_expenses(user_id), multiplier=float(multiplier))
    rows = []
    for _, row in df.head(limit).iterrows():
        item = row.to_dict()
        if hasattr(item.get("date"), "isoformat"):
            item["date"] = item["date"].isoformat()
        rows.append(item)
    return rows


def forecast(user_id: int) -> dict:
    import forecasting as fc
    return dict(fc.forecast_next_month(db.get_expenses(user_id)))


def loan_scenario(principal_eur: float, annual_rate_pct: float, term_months: int,
                  extra_monthly_eur: float = 0.0) -> dict:
    """Deterministic repayment comparison; no database mutation or model arithmetic."""
    principal, rate, term, extra = float(principal_eur), float(annual_rate_pct), int(term_months), float(extra_monthly_eur or 0)
    monthly = fin.annuity_payment(principal, rate, term)
    out = {"principal_eur": principal, "annual_rate_pct": rate, "term_months": term,
           "monthly_payment": round(monthly, 2)}
    if extra <= 0:
        return out
    monthly_rate = rate / 1200
    payment = monthly + extra
    if monthly_rate == 0:
        months = math.ceil(principal / payment)
    elif payment <= principal * monthly_rate:
        months = term
    else:
        months = max(1, math.ceil(-math.log(1 - principal * monthly_rate / payment) / math.log(1 + monthly_rate)))
    normal_interest = monthly * term - principal
    extra_interest = payment * months - principal
    out.update({"extra_monthly_eur": round(extra, 2), "monthly_with_extra": round(payment, 2),
                "months_needed_with_extra": months, "months_saved": max(0, term - months),
                "interest_saved_eur": round(max(0, normal_interest - extra_interest), 2)})
    return out


def purchase_scenario(user_id: int, purchase_eur: float, year: int, month: int) -> dict:
    """Read-only affordability snapshot for a proposed purchase."""
    purchase = float(purchase_eur)
    if purchase <= 0:
        raise ValueError("purchase_eur must be positive")
    cashflow = get_expense_summary(user_id, f"{int(year)}-{int(month):02d}")
    savings = get_savings_summary(user_id)
    free_cash = float(cashflow["net_eur"])
    savings_balance = float(savings["total_balance_eur"])
    return {
        "purchase_eur": round(purchase, 2),
        "projected_free_cash_before_purchase": round(free_cash, 2),
        "projected_after_purchase": round(free_cash - purchase, 2),
        "savings_balance_eur": round(savings_balance, 2),
        "savings_after_purchase_eur": round(savings_balance - purchase, 2),
        "affordable_from_monthly_cashflow": free_cash >= purchase,
    }


def get_portfolio_metrics(user_id: int) -> dict:
    """Portfolio metrics via finance.portfolio_metrics after EUR conversion."""
    df = db.get_holdings(user_id)
    if df.empty:
        return fin.portfolio_metrics([])
    # Need rates to convert last_price -> last_price_eur
    # Reuse utils.get_rates on the user's settings.
    from utils import get_rates  # keep near call site to limit import coupling
    rates = get_rates(db.get_settings(user_id))
    holdings = []
    for _, h in df.iterrows():
        cur = str(h.get("currency") or "EUR")
        price = float(h.get("last_price") or 0.0)
        price_eur = price
        if cur != "EUR" and price > 0:
            r = rates.get(cur, 1.0) or 1.0
            price_eur = price / r
        holdings.append({"quantity": float(h.get("quantity") or 0.0),
                         "last_price_eur": price_eur,
                         "cost_eur": float(h.get("cost_eur") or 0.0)})
    return fin.portfolio_metrics(holdings)


def get_net_worth(user_id: int, asof: date | None = None) -> dict:
    asof = asof or date.today()
    sv_summary = get_savings_summary(user_id)
    locked = get_locked_savings(user_id, asof)
    portfolio = get_portfolio_metrics(user_id)
    debt = get_debt_summary(user_id, asof)
    savings_val = sv_summary["total_balance_eur"] + locked
    net = savings_val + portfolio["value"] - debt["total_debt_eur"]
    return {"savings_eur": round(savings_val, 2),
            "locked_eur": round(locked, 2),
            "portfolio_eur": round(portfolio["value"], 2),
            "debt_eur": debt["total_debt_eur"],
            "net_worth_eur": round(net, 2)}


def compare_spending_periods(
    user_id: int,
    period_a: Period,
    period_b: Period,
    category: str | None = None,
    merchant: str | None = None,
) -> SpendingComparison:
    def _sum_period(df: pd.DataFrame, p: Period) -> tuple[float, int]:
        m = df[(df["date"].dt.date >= p.start) & (df["date"].dt.date <= p.end)]
        if category:
            m = m[m["category"] == category]
        if merchant:
            # description contains merchant (case-insensitive)
            m = m[m["description"].fillna("").str.contains(merchant, case=False, na=False)]
        return float(m["amount_eur"].sum()) if not m.empty else 0.0, int(len(m))

    df = db.get_expenses(user_id)
    total_a, n_a = _sum_period(df, period_a)
    total_b, n_b = _sum_period(df, period_b)
    diff = total_a - total_b
    if total_b > 0:
        pct = (diff / total_b) * 100
    else:
        pct = None
    return SpendingComparison(period_a=period_a, period_b=period_b,
                              total_a_eur=round(total_a, 2),
                              total_b_eur=round(total_b, 2),
                              difference_eur=round(diff, 2),
                              change_pct=round(pct, 2) if pct is not None else None,
                              transactions_a=n_a, transactions_b=n_b)
