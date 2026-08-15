"""
Tests for loan/portfolio math (finance.py).
"""

from datetime import date

import pytest

from finance import annuity_payment, loan_schedule, portfolio_metrics


def test_annuity_zero_interest():
    assert annuity_payment(1200, 0, 12) == pytest.approx(100.0)


def test_annuity_with_interest():
    # 1000 at 12% for 12 months: well-known value ≈ 88.85
    p = annuity_payment(1000, 12, 12)
    assert p == pytest.approx(88.85, abs=0.01)


def test_schedule_no_payments():
    s = loan_schedule(1200, 0, 12, date(2025, 1, 10), 10, [], asof=date(2025, 1, 15))
    assert s["remaining_balance"] == 1200.0
    assert s["remaining_months"] == 12
    # January's due date already passed; 12 payments Feb..Jan -> Jan 2026
    assert s["payoff_date"] == date(2026, 1, 10)


def test_schedule_on_time_payments():
    payments = [(date(2025, m, 10), 100.0) for m in range(1, 13)]
    s = loan_schedule(1200, 0, 12, date(2025, 1, 10), 10, payments,
                      asof=date(2025, 12, 20))
    assert s["remaining_balance"] == 0.0
    assert s["payoff_date"] == date(2025, 12, 10)


def test_schedule_missed_payment_extends_payoff():
    # one payment skipped -> balance remains and payoff moves a month out
    payments = [(date(2025, m, 10), 100.0) for m in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12)]
    s = loan_schedule(1200, 0, 12, date(2025, 1, 10), 10, payments,
                      asof=date(2025, 12, 20))
    assert s["remaining_balance"] == 100.0
    assert s["payoff_date"] == date(2026, 1, 10)


def test_schedule_partial_payment_accrues_interest():
    # 1200 at 12%: month 1 interest 12; pay only 50 -> balance 1162
    s = loan_schedule(1200, 12, 12, date(2025, 1, 1), 1,
                      [(date(2025, 1, 1), 50.0)], asof=date(2025, 1, 20))
    assert s["remaining_balance"] == pytest.approx(1162.0, abs=0.01)
    assert s["total_interest_paid"] == pytest.approx(12.0, abs=0.01)


def test_schedule_payment_day_clamped_in_february():
    # 31st payment day: February due dates clamp to 28
    s = loan_schedule(1200, 0, 12, date(2025, 1, 31), 31,
                      [(date(2025, 2, 28), 100.0)], asof=date(2025, 2, 28))
    # the February payment (due 28 Feb) is recognized
    assert s["remaining_balance"] == 1100.0
    assert s["months_paid"] == 2


def test_schedule_ignores_future_payments():
    # March payment hasn't happened yet as of Feb 1 -> not counted
    s = loan_schedule(1200, 0, 12, date(2025, 1, 10), 10,
                      [(date(2025, 3, 10), 100.0)], asof=date(2025, 2, 1))
    assert s["remaining_balance"] == 1200.0
    assert s["months_paid"] == 1  # January's due date has passed (unpaid)


def test_schedule_applies_payments_made_off_due_day():
    """Regression: payments logged on any day of the month must count
    towards that month's due date (users rarely pay on the exact day)."""
    payments = [
        (date(2025, 1, 15), 100.0),   # 5 days after the Jan 10 due
        (date(2025, 2, 3), 100.0),    # before the Feb 10 due, same month
    ]
    s = loan_schedule(1200, 0, 12, date(2025, 1, 10), 10, payments,
                      asof=date(2025, 2, 15))
    assert s["remaining_balance"] == 1000.0
    assert s["months_paid"] == 2


def test_first_due_never_precedes_loan_start():
    """Regression: start Jan 31 with payment day 1 must not accrue a phantom
    January month — the first due is Feb 1, so as of Feb 1 exactly one month
    has passed."""
    s = loan_schedule(1200, 0, 12, date(2025, 1, 31), 1, [],
                      asof=date(2025, 2, 1))
    assert s["months_paid"] == 1
    assert s["remaining_balance"] == 1200.0
    # and before Feb 1 nothing has accrued
    s0 = loan_schedule(1200, 0, 12, date(2025, 1, 31), 1, [],
                       asof=date(2025, 1, 31))
    assert s0["months_paid"] == 0


def test_first_due_in_start_month_when_day_not_passed():
    """start Jan 15, payment day 20 -> first due Jan 20, accrued by Jan 25."""
    s = loan_schedule(1200, 0, 12, date(2025, 1, 15), 20, [],
                      asof=date(2025, 1, 25))
    assert s["months_paid"] == 1


def test_zero_interest_remaining_months_uses_ceil():
    """Regression: €149 left with €100 payments needs 2 more payments
    (one full + one €49 partial); round() reported 1 and understated cost."""
    # principal 200 over 2 months at 0% -> €100/month; pay €51 in month 1
    s = loan_schedule(200, 0, 2, date(2025, 1, 10), 10,
                      [(date(2025, 1, 10), 51.0)], asof=date(2025, 2, 20))
    assert s["remaining_balance"] == pytest.approx(149.0)
    assert s["remaining_months"] == 2
    assert s["payoff_date"] == date(2025, 4, 10)


def test_february_clamp_uses_first_due_anchor():
    """31st payment day with a Dec 31 start: first due is Dec 31, February
    clamps to Feb 28 the following year."""
    s = loan_schedule(1200, 0, 12, date(2024, 12, 31), 31,
                      [(date(2025, 2, 28), 100.0)], asof=date(2025, 2, 28))
    assert s["months_paid"] == 3
    assert s["remaining_balance"] == 1100.0


def test_portfolio_metrics():
    m = portfolio_metrics([
        {"quantity": 2, "last_price_eur": 50.0, "cost_eur": 80.0},
        {"quantity": 1, "last_price_eur": 100.0, "cost_eur": 120.0},
        {"quantity": 0, "last_price_eur": 0.0, "cost_eur": 0.0},
    ])
    assert m["value"] == 200.0
    assert m["invested"] == 200.0
    assert m["gain"] == 0.0
    assert m["gain_pct"] == 0.0
    assert m["live_count"] == 2
