"""
finance.py — Pure financial math: loan amortization and portfolio metrics.
No I/O or Streamlit dependencies; fully unit-tested.
"""

import calendar
import math
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


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
                # Payment doesn't even cover interest: the balance GROWS every
                # month, so the loan never amortizes under this payment.
                # remaining_months stays 0 -> payoff_date stays None and the
                # schedule reports an unbounded future instead of fabricating
                # a one-month payoff.
                remaining_months = 0
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

    # None marks "never pays off at this payment": future interest is unbounded.
    interest_remaining = ((monthly * remaining_months - bal)
                          if remaining_months else None)
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
        "total_interest_remaining": (round(max(interest_remaining, 0.0), 2)
                                     if interest_remaining is not None else None),
        "next_payment_interest": round(next_interest, 2),
        "next_payment_principal": round(next_principal, 2),
        "months_paid": months_paid,
        "total_cost": round(principal + total_interest
                            + (max(interest_remaining, 0.0)
                               if interest_remaining is not None else 0.0), 2),
    }


def loan_payment_split(principal: float, annual_rate_pct: float, term_months: int,
                       start_date: date, payment_day: int, payments: list,
                       payment_date: date, amount_eur: float,
                       surcharge_eur: float = 0.0) -> dict:
    """Schedule-derived principal/interest split for ONE new loan payment.

    ``loan_schedule`` is the single source of truth: ``balance_before`` is the
    remaining balance just before this payment is applied (booked interest
    capitalized), computed against the ACTUAL payment history. The interest
    component is the month's accrual estimate ``balance_before * r`` capped at
    the money available for principal (payment minus early-repayment
    surcharge), so a partial payment can never report more interest than was
    paid. The surcharge is INCLUSIVE metadata inside ``amount_eur`` — it is
    reported separately but never added on top and never reduces the balance.

    By construction ``principal_eur + interest_eur + surcharge_eur ==
    amount_eur`` cent-exact (locked representation check).

    Returns: balance_before, available_eur, principal_eur, interest_eur,
    surcharge_eur, pays_off (the payment reaches the remaining balance within
    the locked €0.01 tolerance).
    """
    total = round(float(amount_eur or 0.0), 2)
    surcharge = round(min(max(float(surcharge_eur or 0.0), 0.0), total), 2)
    available = round(total - surcharge, 2)
    sched = loan_schedule(principal, annual_rate_pct, term_months,
                          start_date, payment_day, payments, asof=payment_date)
    bal_before = float(sched["remaining_balance"])
    r = (annual_rate_pct / 100.0) / 12.0
    interest = min(max(bal_before, 0.0) * r, max(available, 0.0))
    interest = min(max(round(interest, 2), 0.0), max(available, 0.0))
    return {
        "balance_before": bal_before,
        "available_eur": available,
        "principal_eur": round(available - interest, 2),
        "interest_eur": interest,
        "surcharge_eur": surcharge,
        # Locked payoff invariant: |remaining| ≤ €0.01 counts as paid off.
        "pays_off": available >= bal_before - 0.01,
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


# ── Savings goal balance timeline (FIN-01) ────────────────────────────────────

_CENT = Decimal("0.01")


def _finite_float(v) -> float:
    """Coerce to a finite float; None/NaN/inf/garbage all count as 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def goal_balance_timeline(rows, asof: date | None = None) -> dict:
    """Posted-balance walk + cumulative accrual for ONE savings goal.

    Locked financial-model semantics (FIN-04):
      * The posted balance starts at the first deposit; every event changes
        the posted balance starting on its own event date. Unrealized
        interest is NEVER compounded into the posted balance.
      * ``accrued_interest_eur`` is the CUMULATIVE daily ACT/365 accrual
        from inception to ``asof`` over every segment at the balance and
        rate in effect there. Day-count: an event's new balance applies
        FROM its own date; the final segment INCLUDES ``asof`` — a deposit
        held for all of January posts 31 days (€1,000 @ 3.65% → €3.10).
      * The earning rate is established by DEPOSIT rows; withdrawals and
        system credit rows (postings/settlements) preserve the in-flight
        rate so accrual continues uninterrupted.
      * Postings already made are separate credit rows: callers subtract
        their sum (accrual_key rows dated ≤ asof) to get the still-unposted
        remainder. No clamps anywhere; NaN deposits count as 0; rows
        without a usable date contribute their deposit but open no window.

    ``rows``: iterable of mappings (or pandas Series) with ``date``
    (date/datetime/None — callers normalize pandas NaT to None),
    ``deposited_eur`` and ``interest_rate``. Rows may arrive in any order;
    sorting is stable (input order kept for equal dates, unusable dates
    first).

    Returns ``{"posted_balance_eur", "accrued_interest_eur",
    "total_value_eur"}`` as floats quantized to €0.01 (ROUND_HALF_UP).
    Accrued interest is display/posting valuation, never spendable until
    posted by the monthly command.
    """
    bal = Decimal("0")
    rate_pct = Decimal("0")
    accrued = Decimal("0")
    last_event_date: date | None = None
    events = []
    for row in rows:
        get = row.get if hasattr(row, "get") else (lambda k, _r=row: _r[k])
        raw_date = get("date")
        d: date | None = None
        if isinstance(raw_date, datetime):
            try:
                d = raw_date.date()
            except ValueError:      # pandas NaT subclasses datetime
                d = None
        elif isinstance(raw_date, date):
            d = raw_date
        events.append({"d": d,
                       "dep": Decimal(str(_finite_float(get("deposited_eur")))),
                       "rate": Decimal(str(_finite_float(get("interest_rate"))))})
    events.sort(key=lambda ev: (ev["d"] is None, ev["d"] or date.min))

    def _accrue(bal_: Decimal, rate_: Decimal, start: date, end: date) -> Decimal:
        days = (end - start).days
        if days <= 0 or bal_ == 0:
            return Decimal("0")
        return bal_ * rate_ / Decimal("36500") * days

    for ev in events:
        if ev["d"] is not None and last_event_date is not None \
                and ev["d"] > last_event_date:
            # segment between events: balance/rate in effect since the
            # previous event, up to (excluding) this event's date
            accrued += _accrue(bal, rate_pct, last_event_date, ev["d"])
        bal += ev["dep"]
        if ev["d"] is not None:
            last_event_date = ev["d"]
            # The earning rate is established by DEPOSITS; withdrawals and
            # system credit rows (postings/settlements) preserve the
            # in-flight rate so accrual continues uninterrupted.
            if ev["dep"] > 0:
                rate_pct = ev["rate"]

    posted = bal.quantize(_CENT, rounding=ROUND_HALF_UP)
    if asof is not None and last_event_date is not None and asof >= last_event_date:
        # final segment INCLUDES `asof` (locked day-count convention)
        accrued += _accrue(bal, rate_pct, last_event_date, asof) + (
            bal * rate_pct / Decimal("36500") if asof >= last_event_date else Decimal("0"))
    return {"posted_balance_eur": float(posted),
            "accrued_interest_eur": float(accrued.quantize(_CENT, rounding=ROUND_HALF_UP)),
            "total_value_eur": float((posted + accrued).quantize(_CENT, rounding=ROUND_HALF_UP))}


# ── Portfolio math ────────────────────────────────────────────────────────────

def calculate_term_payout(amount_eur: float, annual_rate_pct: float,
                          start_date, end_date, *,
                          maturity_date=None,
                          early_annual_rate_pct: float | None = None,
                          withdrawal_kind: str = "early") -> dict:
    """Term-deposit payout under the locked early-withdrawal policy (FIN-05).

    Locked model: term interest pays out ONCE at the end of term. Early
    closure is an explicit workflow governed by an optional agreed
    ``early_annual_rate``:

    * ``withdrawal_kind="matured"`` (end_date >= maturity_date): interest is
      the full term value minus principal (monthly compounding via
      ``maturity_value``); rate_applied = annual_rate_pct.
    * ``withdrawal_kind="early"``: interest accrues at the agreed early rate
      (simple ACT/365, one day per day held) when one was agreed; with no
      agreed early rate the payout is PRINCIPAL ONLY — no interest.
    * ``withdrawal_kind="no_dates"``: payout = principal, interest 0.

    Returns ``{"payout_eur", "principal_eur", "interest_eur", "rate_applied",
    "kind"}`` quantized to €0.01 (ROUND_HALF_UP). No clamps.
    """
    principal = _finite_float(amount_eur)
    p = Decimal(str(principal)).quantize(_CENT, rounding=ROUND_HALF_UP)

    def _d(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.date()
        return v if isinstance(v, date) else None

    kind = withdrawal_kind
    if kind not in ("matured", "early", "no_dates"):
        kind = "early"
    if kind == "no_dates" or _d(start_date) is None or _d(end_date) is None:
        return {"payout_eur": float(p), "principal_eur": float(p),
                "interest_eur": 0.0, "rate_applied": 0.0, "kind": "no_dates"}

    start = _d(start_date)
    end = _d(end_date)
    mat = _d(maturity_date)
    if kind == "matured" and mat is not None and end >= mat:
        interest = Decimal(str(maturity_value(
            principal, float(annual_rate_pct), start, mat))) - p
        rate = float(annual_rate_pct)
        interest = max(interest, Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)
        return {"payout_eur": float(p + interest), "principal_eur": float(p),
                "interest_eur": float(interest), "rate_applied": rate,
                "kind": "matured"}

    # early withdrawal
    if early_annual_rate_pct is None:
        return {"payout_eur": float(p), "principal_eur": float(p),
                "interest_eur": 0.0, "rate_applied": 0.0, "kind": "early"}
    days = (end - start).days
    if days < 0:
        days = 0
    rate = float(early_annual_rate_pct)
    interest = (p * Decimal(str(rate)) / Decimal("36500") * days).quantize(
        _CENT, rounding=ROUND_HALF_UP)
    return {"payout_eur": float(p + interest), "principal_eur": float(p),
            "interest_eur": float(interest), "rate_applied": rate,
            "kind": "early"}



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
