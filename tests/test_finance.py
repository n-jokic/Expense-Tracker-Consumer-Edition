"""
Tests for loan/portfolio math (finance.py).
"""

from datetime import date

import pytest

from finance import (
    annuity_payment,
    calculate_early_repayment_surcharge,
    derive_hourly_rate,
    loan_schedule,
    portfolio_metrics,
)


def test_annuity_zero_interest():
    assert annuity_payment(1200, 0, 12) == pytest.approx(100.0)


def test_annuity_with_interest():
    # 1000 at 12% for 12 months: well-known value ≈ 88.85
    p = annuity_payment(1000, 12, 12)
    assert p == pytest.approx(88.85, abs=0.01)


def test_next_payment_breakdown_separates_interest_and_principal():
    s = loan_schedule(1000, 12, 12, date(2026, 1, 1), 25, [],
                      asof=date(2026, 1, 10))

    assert s["monthly_payment"] == pytest.approx(88.85, abs=0.01)
    assert s["next_payment_interest"] == pytest.approx(10.0, abs=0.01)
    assert s["next_payment_principal"] == pytest.approx(78.85, abs=0.01)


def test_early_repayment_surcharge_is_interest_but_not_principal():
    s = loan_schedule(
        1000, 12, 12, date(2026, 1, 1), 25,
        [{"date": date(2026, 1, 5), "amount_eur": 210.0,
          "surcharge_eur": 10.0}],
        asof=date(2026, 1, 10),
    )

    assert s["remaining_balance"] == pytest.approx(810.0, abs=0.01)
    assert s["scheduled_interest_paid"] == pytest.approx(10.0, abs=0.01)
    assert s["total_surcharge_paid"] == pytest.approx(10.0, abs=0.01)
    assert s["total_interest_paid"] == pytest.approx(20.0, abs=0.01)


def test_early_repayment_surcharge_modes():
    assert calculate_early_repayment_surcharge(250.0, "fixed", 15.0) == 15.0
    assert calculate_early_repayment_surcharge(250.0, "percent", 4.0) == 10.0
    assert calculate_early_repayment_surcharge(250.0, "fixed", 0.0) == 0.0


def test_hourly_rate_uses_weighted_income_and_salary_fallback():
    rows = [
        {"income_type": "Hourly", "hours": 20.0, "actual_eur": 1000.0, "currency": "USD"},
        {"income_type": "Hourly", "hours": 10.0, "actual_eur": 600.0, "currency": "EUR"},
        {"income_type": "Salary", "hours": None, "actual_eur": 3000.0},
        {"income_type": "Hourly", "hours": 0.0, "actual_eur": 9999.0},
    ]

    rate, source = derive_hourly_rate(rows, salary_eur=3200.0)
    assert rate == pytest.approx(1600.0 / 30.0)
    assert source == "income"

    fallback, source = derive_hourly_rate(
        [{"income_type": "Hourly", "hours": 0.0, "actual_eur": 100.0}],
        salary_eur=3200.0,
    )
    assert fallback == pytest.approx(20.0)
    assert source == "salary"

    zero_rate, source = derive_hourly_rate(
        [{"income_type": "Hourly", "hours": 2.0, "actual_eur": 0.0}],
        salary_eur=3200.0,
    )
    assert zero_rate == 0.0
    assert source == "income"


def test_early_repayment_can_pay_off_principal_without_counting_fee_as_principal():
    s = loan_schedule(
        1000, 0, 10, date(2026, 1, 1), 25,
        [{"date": date(2026, 1, 5), "amount_eur": 1010.0,
          "surcharge_eur": 10.0}],
        asof=date(2026, 1, 10),
    )
    assert s["remaining_balance"] == 0.0
    assert s["total_surcharge_paid"] == 10.0
    assert s["total_interest_paid"] == 10.0
    assert s["payoff_date"] == date(2026, 1, 25)


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


def test_schedule_burst_payments_before_first_due_accrue_interest():
    # Regression (real user data): a loan started today with several payments
    # logged on the start date — all BEFORE the first due date — used to
    # apply every payment while booking ZERO interest ("Interest paid: 0.00"
    # despite 17 payments). A month's interest must be booked at the moment
    # a payment is applied, even before its due date.
    s = loan_schedule(1000, 6, 36, date(2026, 8, 16), 1,
                      [(date(2026, 8,16), 30.42)] * 17,
                      asof=date(2026, 8, 16))
    assert s["total_interest_paid"] > 0
    # One month's interest on the pre-payment balance, then all 17 payments.
    assert s["remaining_balance"] == pytest.approx(1000 * 1.005 - 17 * 30.42,
                                                   abs=0.01)
    assert s["months_paid"] == 1


def test_schedule_early_payment_booked_exactly_once_across_snapshots():
    # Payment on Jan 20 for a Jan 25 due date: interest is booked at
    # application time (pre-due snapshot) and must NOT be booked a second
    # time once the due date passes (post-due snapshot) — both snapshots
    # agree.
    start = date(2026, 1, 10)
    payments = [(date(2026, 1, 20), 100.0)]
    before_due = loan_schedule(1200, 12, 12, start, 25, payments,
                               asof=date(2026, 1, 21))
    after_due = loan_schedule(1200, 12, 12, start, 25, payments,
                              asof=date(2026, 1, 26))
    expected = 1200 * 1.01 - 100
    assert before_due["total_interest_paid"] == pytest.approx(12.0, abs=0.01)
    assert before_due["remaining_balance"] == pytest.approx(expected, abs=0.01)
    assert after_due["total_interest_paid"] == pytest.approx(12.0, abs=0.01)
    assert after_due["remaining_balance"] == pytest.approx(expected, abs=0.01)


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


def test_payment_in_first_due_month_counts():
    """Regression: start Jan 31 with payment day 1 -> first due is Feb 1. A
    payment logged in February must credit the FEBRUARY due, not land one
    bucket late (which made logged payments invisible to the balance)."""
    s = loan_schedule(1200, 0, 12, date(2025, 1, 31), 1,
                      [(date(2025, 2, 15), 100.0)], asof=date(2025, 2, 28))
    assert s["remaining_balance"] == 1100.0
    assert s["months_paid"] == 1


def test_payment_before_due_day_reduces_balance_immediately():
    """Regression: a payment logged this month, BEFORE the payment day has
    passed, must reduce the balance right away. The payment month is counted
    (its interest — here 0 — is booked at application time)."""
    s = loan_schedule(10000, 0, 12, date(2026, 1, 1), 25,
                      [(date(2026, 1, 5), 1000.0)], asof=date(2026, 1, 10))
    assert s["remaining_balance"] == 9000.0
    assert s["months_paid"] == 1
    assert s["total_interest_paid"] == 0.0


def test_payment_before_first_due_reduces_balance():
    """Regression: a payment made before the very first due date must count."""
    s = loan_schedule(10000, 0, 12, date(2026, 1, 20), 1,
                      [(date(2026, 1, 25), 1000.0)], asof=date(2026, 1, 26))
    assert s["remaining_balance"] == 9000.0
    assert s["months_paid"] == 1


def test_early_payoff_in_current_month_uses_due_date():
    """Paying the whole balance before the due day clears the loan with the
    current month's due date as the payoff date."""
    s = loan_schedule(10000, 0, 12, date(2026, 1, 1), 25,
                      [(date(2026, 1, 5), 10000.0)], asof=date(2026, 1, 10))
    assert s["remaining_balance"] == 0.0
    assert s["payoff_date"] == date(2026, 1, 25)


def test_current_month_payment_not_counted_twice_after_due_passes():
    """Once the due day passes, the same payment is applied as that month's
    payment — the balance must not change again."""
    before = loan_schedule(10000, 0, 12, date(2026, 1, 1), 5,
                           [(date(2026, 1, 3), 1000.0)], asof=date(2026, 1, 4))
    after = loan_schedule(10000, 0, 12, date(2026, 1, 1), 5,
                          [(date(2026, 1, 3), 1000.0)], asof=date(2026, 1, 6))
    assert before["remaining_balance"] == 9000.0
    assert after["remaining_balance"] == 9000.0
    assert after["months_paid"] == 1


def test_payoff_date_after_current_month_payment():
    """Regression: a payment applied to the not-yet-due current month must
    not shift the payoff date one month early (the paid slot is consumed)."""
    # 1000 at 0% over 10 months -> 100/month; pay 100 before the Jan 25 due:
    # 9 remaining payments Feb..Oct -> payoff Oct 25.
    s = loan_schedule(1000, 0, 10, date(2026, 1, 1), 25,
                      [(date(2026, 1, 5), 100.0)], asof=date(2026, 1, 10))
    assert s["remaining_balance"] == 900.0
    assert s["remaining_months"] == 9
    assert s["payoff_date"] == date(2026, 10, 25)
    # no payment: same payoff (10 events Jan..Oct)
    s0 = loan_schedule(1000, 0, 10, date(2026, 1, 1), 25, [], asof=date(2026, 1, 10))
    assert s0["payoff_date"] == date(2026, 10, 25)


def test_payoff_date_after_current_month_prepayment():
    """A prepayment covering several monthly payments shortens the loan:
    300 paid up front -> 7 remaining events Feb..Aug."""
    s = loan_schedule(1000, 0, 10, date(2026, 1, 1), 25,
                      [(date(2026, 1, 5), 300.0)], asof=date(2026, 1, 10))
    assert s["remaining_balance"] == 700.0
    assert s["remaining_months"] == 7
    assert s["payoff_date"] == date(2026, 8, 25)


def test_term_deposit_math():
    from finance import months_between, maturity_value, accrued_value
    assert months_between(date(2026, 1, 10), date(2026, 2, 9)) == 0
    assert months_between(date(2026, 1, 10), date(2026, 2, 10)) == 1
    assert months_between(date(2026, 1, 31), date(2026, 2, 1)) == 0
    assert months_between(date(2026, 1, 10), date(2026, 1, 11)) == 0
    # 1000 at 12% p.a. for 12 months -> 1000 * 1.01^12
    assert maturity_value(1000.0, 12.0, date(2026, 1, 1), date(2027, 1, 1)) \
        == pytest.approx(1126.83, abs=0.01)
    assert accrued_value(1000.0, 12.0, date(2026, 1, 1), date(2026, 1, 15)) == 1000.0
    assert accrued_value(1000.0, 12.0, date(2026, 1, 1), date(2026, 4, 15)) \
        == pytest.approx(1030.30, abs=0.01)


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
