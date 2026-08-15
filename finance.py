"""
finance.py — Pure financial math: loan amortization and portfolio metrics.
No I/O or Streamlit dependencies; fully unit-tested.
"""

import calendar
import math
from datetime import date


def annuity_payment(principal: float, annual_rate_pct: float, term_months: int) -> float:
    """Standard amortized monthly payment for a fixed-rate loan."""
    if principal <= 0 or term_months <= 0:
        return 0.0
    r = (annual_rate_pct / 100) / 12
    if r == 0:
        return principal / term_months
    return principal * r * (1 + r) ** term_months / ((1 + r) ** term_months - 1)


def _first_due(start: date, payment_day: int) -> date:
    """First due date: the first occurrence of payment_day on or after the
    loan start, clamped to the month's length (31st in February -> 28/29).

    If payment_day already passed in the start month (e.g. loan starts Jan 31
    with payment day 1), the first due falls in the next month — the loan
    never accrues a phantom month before it exists.
    """
    if payment_day >= start.day:
        anchor = start
    else:
        anchor = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    last = calendar.monthrange(anchor.year, anchor.month)[1]
    return date(anchor.year, anchor.month, min(payment_day, last))


def _next_due(start: date, payment_day: int, k: int) -> date:
    """The (k+1)-th payment due date: k months after the first due date,
    clamped to each month's length."""
    first = _first_due(start, payment_day)
    total = first.month - 1 + k
    year  = first.year + total // 12
    month = total % 12 + 1
    last  = calendar.monthrange(year, month)[1]
    return date(year, month, min(payment_day, last))


def loan_schedule(principal: float, annual_rate_pct: float, term_months: int,
                  start_date: date, payment_day: int,
                  payments: list, asof: date | None = None) -> dict:
    """Simulate a loan month by month against its ACTUAL payment history.

    payments: list of (date, amount_eur). Interest accrues on the running
    balance each month; missed or partial payments extend the payoff date.

    Returns: monthly_payment, remaining_balance, remaining_months, payoff_date,
    total_interest_paid, total_interest_remaining, months_paid, total_cost.
    """
    monthly = annuity_payment(principal, annual_rate_pct, term_months)
    r = (annual_rate_pct / 100) / 12
    asof = asof or date.today()

    # Attribute payments to the accrual month they fall in: a payment made
    # on any day between two due dates counts towards the earlier due date's
    # month (users rarely pay on the exact payment_day).
    by_due = {}
    for p_date, amt in payments:
        if p_date is None:
            continue
        k = (p_date.year - start_date.year) * 12 + (p_date.month - start_date.month)
        due = _next_due(start_date, payment_day, max(k, 0))
        by_due[due] = by_due.get(due, 0.0) + float(amt or 0.0)

    bal = float(principal)
    interest_paid = 0.0
    months_paid = 0
    payoff = None
    k = 0
    while bal > 0.005 and k < 1200:
        k += 1
        due = _next_due(start_date, payment_day, k - 1)
        if due > asof:
            break
        interest_due = bal * r
        interest_paid += interest_due
        paid = by_due.get(due, 0.0)
        bal = bal + interest_due - paid
        months_paid += 1
        if bal <= 0.005:
            bal = 0.0
            payoff = due
            break

    remaining_months = 0
    if bal > 0.005:
        if r == 0:
            # ceil, not round: a €149 balance with €100 payments still needs
            # 2 payments (one full + one partial), never 1.
            remaining_months = int(math.ceil(bal / monthly - 1e-9)) if monthly > 0 else 0
        else:
            if monthly > bal * r:
                remaining_months = int(math.ceil(
                    -math.log(1 - bal * r / monthly) / math.log(1 + r)))
            else:
                # payment doesn't even cover interest; no finite payoff
                remaining_months = 0
        remaining_months = max(remaining_months, 1)
        if remaining_months:
            # k was incremented for the month that failed the asof check,
            # so the next unprocessed month index is k - 1.
            payoff = _next_due(start_date, payment_day,
                               (k - 1) + remaining_months - 1)

    interest_remaining = (monthly * remaining_months - bal) if remaining_months else 0.0

    return {
        "monthly_payment": round(monthly, 2),
        "remaining_balance": round(bal, 2),
        "remaining_months": remaining_months,
        "payoff_date": payoff,
        "total_interest_paid": round(interest_paid, 2),
        "total_interest_remaining": round(max(interest_remaining, 0.0), 2),
        "months_paid": months_paid,
        "total_cost": round(principal + interest_paid + max(interest_remaining, 0.0), 2),
    }


# ── Portfolio math ────────────────────────────────────────────────────────────

def portfolio_metrics(holdings: list) -> dict:
    """Aggregate portfolio value/gain from holding dicts.

    Each holding: {quantity, last_price_eur, cost_eur}.
    """
    value = 0.0
    invested = 0.0
    live_count = 0
    for h in holdings:
        qty = float(h.get("quantity") or 0.0)
        price_eur = float(h.get("last_price_eur") or 0.0)
        value += qty * price_eur
        invested += float(h.get("cost_eur") or 0.0)
        if price_eur > 0:
            live_count += 1
    gain = value - invested
    gain_pct = (gain / invested * 100) if invested > 0 else 0.0
    return {
        "value": value,
        "invested": invested,
        "gain": gain,
        "gain_pct": gain_pct,
        "live_count": live_count,
    }
