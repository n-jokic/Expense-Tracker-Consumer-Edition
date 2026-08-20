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


def derive_hourly_rate(income_rows, salary_eur: float = 0.0) -> tuple[float, str]:
    """Return a weighted hourly income rate, or the salary fallback.

    ``income_rows`` may be a pandas DataFrame or an iterable of mappings. Only
    Hourly rows with positive hours and finite EUR actuals contribute. The
    weighted calculation avoids letting a small one-off entry outweigh the
    user's actual recorded workload.
    """
    if hasattr(income_rows, "to_dict"):
        income_rows = income_rows.to_dict("records")

    total_hours = 0.0
    total_actual_eur = 0.0
    for row in income_rows or []:
        if str(row.get("income_type", "")) != "Hourly":
            continue
        hours_raw = row.get("hours")
        actual_raw = row.get("actual_eur")
        if hours_raw is None or actual_raw is None:
            continue
        try:
            hours = float(hours_raw)
            actual_eur = float(actual_raw)
        except (TypeError, ValueError):
            continue
        if hours > 0 and math.isfinite(hours) and math.isfinite(actual_eur):
            total_hours += hours
            total_actual_eur += actual_eur

    if total_hours > 0:
        return total_actual_eur / total_hours, "income"

    try:
        salary_eur = float(salary_eur or 0.0)
    except (TypeError, ValueError):
        salary_eur = 0.0
    if salary_eur > 0 and math.isfinite(salary_eur):
        return salary_eur / 160.0, "salary"
    return 0.0, "none"


def calculate_early_repayment_surcharge(amount: float, mode: str,
                                        value: float) -> float:
    """Calculate a non-negative surcharge in the same currency as ``amount``."""
    amount = max(float(amount or 0.0), 0.0)
    value = max(float(value or 0.0), 0.0)
    if mode == "percent":
        return amount * value / 100.0
    return value


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

    Payments may be legacy ``(date, amount_eur)`` tuples or mappings with
    ``date``, ``amount_eur`` (total paid), and optional ``surcharge_eur``.
    Surcharges count as interest but only the principal component reduces the
    balance. Interest accrues on the running balance each month; a month's
    interest is booked when its due date has passed OR when a payment is
    applied to it — whichever comes first. Missed or partial payments extend
    the payoff date.

    Returns: monthly_payment, remaining_balance, remaining_months, payoff_date,
    total_interest_paid, scheduled_interest_paid, total_surcharge_paid,
    total_interest_remaining, next_payment_interest, next_payment_principal,
    months_paid, total_cost.
    """
    monthly = annuity_payment(principal, annual_rate_pct, term_months)
    r = (annual_rate_pct / 100) / 12
    asof = asof or date.today()

    # Attribute payments by CALENDAR month relative to the first due date:
    # a payment lands in the bucket of the month it was made in (users rarely
    # pay on the exact payment_day), and each bucket's due date is the
    # clamped payment day for that month. Measuring the month index from the
    # FIRST due date — not the loan start — keeps payments in the right
    # bucket even when the first due rolls into the month after the start
    # (e.g. start Jan 31 with payment day 1 → first due Feb 1).
    by_due = {}
    surcharge_paid = 0.0
    first_due = _first_due(start_date, payment_day)
    for payment in payments:
        if isinstance(payment, dict):
            p_date = payment.get("date")
            total = float(payment.get("amount_eur", 0.0) or 0.0)
            surcharge = max(float(payment.get("surcharge_eur", 0.0) or 0.0), 0.0)
            principal_paid = payment.get("principal_eur")
            if principal_paid is None:
                principal_paid = total - surcharge
            principal_paid = max(float(principal_paid or 0.0), 0.0)
        else:
            p_date, total = payment[0], float(payment[1] or 0.0)
            surcharge = max(float(payment[2] or 0.0), 0.0) if len(payment) > 2 else 0.0
            principal_paid = max(total - surcharge, 0.0) if len(payment) > 2 else total
        if p_date is None:
            continue
        if p_date > asof:
            continue
        k = ((p_date.year - first_due.year) * 12
             + (p_date.month - first_due.month))
        due = _next_due(start_date, payment_day, max(k, 0))
        by_due[due] = by_due.get(due, 0.0) + principal_paid
        surcharge_paid += surcharge

    bal = float(principal)
    interest_paid = 0.0
    months_paid = 0
    payoff = None

    # Simulate through the CURRENT calendar month (relative to the first due)
    # so a payment logged this month reduces the balance immediately — even
    # before that month's payment day has arrived. A month's interest is
    # booked once: when its due date has passed, or at the moment a payment
    # is applied to it (payments in future months are never applied).
    cur_k = (asof.year - first_due.year) * 12 + (asof.month - first_due.month)
    k = 0
    while bal > 0.005 and k <= max(cur_k, 0) and k < 1200:
        due = _next_due(start_date, payment_day, k)
        bucket_pay = by_due.get(due, 0.0)
        if due <= asof or bucket_pay > 0.005:
            interest_due = bal * r
            interest_paid += interest_due
            bal += interest_due
            months_paid += 1
        bal -= bucket_pay
        if bal <= 0.005:
            bal = 0.0
            payoff = due
            break
        k += 1

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
            # k = last simulated month index + 1. The current month's payment
            # slot is still owed ONLY when its due date hasn't arrived AND no
            # payment was applied to it — if a payment already landed there,
            # the remaining events start with the next month (k).
            last_idx = k - 1
            due_last = _next_due(start_date, payment_day, last_idx)
            if due_last > asof and by_due.get(due_last, 0.0) <= 0.005:
                next_idx = last_idx
            else:
                next_idx = last_idx + 1
            payoff = _next_due(start_date, payment_day,
                               next_idx + remaining_months - 1)

    interest_remaining = (monthly * remaining_months - bal) if remaining_months else 0.0
    next_interest = min(max(bal * r, 0.0), monthly) if bal > 0.005 else 0.0
    next_principal = min(max(monthly - next_interest, 0.0), bal) if bal > 0.005 else 0.0
    total_interest = interest_paid + surcharge_paid

    return {
        "monthly_payment": round(monthly, 2),
        "remaining_balance": round(bal, 2),
        "remaining_months": remaining_months,
        "payoff_date": payoff,
        "total_interest_paid": round(total_interest, 2),
        "scheduled_interest_paid": round(interest_paid, 2),
        "total_surcharge_paid": round(surcharge_paid, 2),
        "total_interest_remaining": round(max(interest_remaining, 0.0), 2),
        "next_payment_interest": round(next_interest, 2),
        "next_payment_principal": round(next_principal, 2),
        "months_paid": months_paid,
        "total_cost": round(principal + total_interest + max(interest_remaining, 0.0), 2),
    }


# ── Term deposit math ─────────────────────────────────────────────────────────

def months_between(start: date, end: date) -> int:
    """Whole calendar months from start to end (0 when end is not later).

    Day-aware: a partial month does not count. e.g. Jan 10 -> Feb 09 is 0,
    Jan 10 -> Feb 10 is 1, Jan 31 -> Feb 01 is 0."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def compound_months(amount: float, annual_rate_pct: float, months: int) -> float:
    """Value of a deposit after `months` whole months of monthly compounding."""
    if amount <= 0 or months <= 0:
        return round(amount, 2)
    return round(amount * (1 + annual_rate_pct / 100 / 12) ** months, 2)


def maturity_value(amount: float, annual_rate_pct: float,
                   start: date, maturity: date) -> float:
    """Projected value of a fixed-term deposit at its maturity date."""
    return compound_months(amount, annual_rate_pct, months_between(start, maturity))


def accrued_value(amount: float, annual_rate_pct: float,
                  start: date, asof: date | None = None) -> float:
    """Current value of a term deposit: compounded monthly up to asof
    (default today). Callers cap asof at the maturity date themselves."""
    asof = asof or date.today()
    if asof <= start:
        return round(amount, 2)
    return compound_months(amount, annual_rate_pct, months_between(start, asof))


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
        cost = float(h.get("cost_eur") or 0.0)
        if not math.isfinite(qty):
            qty = 0.0
        if not math.isfinite(price_eur):
            price_eur = 0.0
        if not math.isfinite(cost):
            cost = 0.0
        value += qty * price_eur
        invested += cost
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
