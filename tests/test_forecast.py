"""
Tests for salary-cycle math (utils.compute_salary_cycle).
"""

from datetime import date

from utils import compute_salary_cycle


def test_mid_month_salary_cycle():
    start, end = compute_salary_cycle(date(2025, 6, 20), salary_day=10)
    assert start == date(2025, 6, 10)
    assert end == date(2025, 7, 9)


def test_before_salary_day_uses_previous_month():
    start, end = compute_salary_cycle(date(2025, 6, 5), salary_day=10)
    assert start == date(2025, 5, 10)
    assert end == date(2025, 6, 9)


def test_january_before_salary_day_wraps_year():
    start, end = compute_salary_cycle(date(2025, 1, 3), salary_day=10)
    assert start == date(2024, 12, 10)
    assert end == date(2025, 1, 9)


def test_month_end_salary_day_clamps_in_february():
    # 31 Jan salary: next cycle start would be 31 Feb -> clamp to 28/29 Feb
    start, end = compute_salary_cycle(date(2025, 2, 5), salary_day=31)
    assert start == date(2025, 1, 31)
    assert end == date(2025, 2, 27)   # day before 28 Feb (non-leap)

    start, end = compute_salary_cycle(date(2024, 2, 5), salary_day=31)
    assert start == date(2024, 1, 31)
    assert end == date(2024, 2, 28)   # day before 29 Feb (leap)


def test_day_30_salary_clamps_in_february():
    start, end = compute_salary_cycle(date(2025, 2, 10), salary_day=30)
    assert start == date(2025, 1, 30)
    assert end == date(2025, 2, 27)


def test_latest_salary_overrides_default_day():
    start, end = compute_salary_cycle(date(2025, 4, 1), salary_day=10,
                                      latest_salary=date(2025, 3, 25))
    assert start == date(2025, 3, 25)
    assert end == date(2025, 4, 24)


def test_month_end_salary_day_clamps_previous_month_start():
    """Regression: salary_day 31 with today May 1 must not raise — the
    previous month's start is clamped to April 30."""
    start, end = compute_salary_cycle(date(2025, 5, 1), salary_day=31)
    assert start == date(2025, 4, 30)
    assert end == date(2025, 5, 29)  # day before next start (May 30)

    start, end = compute_salary_cycle(date(2025, 3, 1), salary_day=30)
    assert start == date(2025, 2, 28)  # non-leap February clamp
