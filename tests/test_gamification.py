"""
Tests for the fun gamification achievements: month-based habits, timing
badges, category/currency diversity, savings streaks, subscriptions, and the
meta "Achievement Hunter" badge.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from gamification import get_earned_milestones, get_logging_streak, _saver_streak
from utils import CATEGORIES

EMPTY = pd.DataFrame()


def _ids(expenses, income=None, savings=None, budgets=None):
    return {m["id"] for m in get_earned_milestones(
        expenses, income if income is not None else EMPTY,
        savings if savings is not None else EMPTY,
        budgets if budgets is not None else EMPTY,
    )}


def _row(d, category="Other", sub="", amount=10.0, currency="EUR", hour=None):
    r = {"date": pd.Timestamp(d), "category": category, "subcategory": sub,
         "description": "x", "amount": amount, "currency": currency,
         "amount_eur": amount}
    if hour is not None:
        r["created_at"] = pd.Timestamp(d).replace(hour=hour)
    return r


def test_monthly_habit_badges():
    today = date.today()
    rows = [_row(today, "Food & Dining", "Coffee & Snacks", 3.0) for _ in range(20)]
    rows += [_row(today, "Health", "Gym & Fitness", 15.0) for _ in range(8)]
    rows += [_row(today, "Transport", "Public Transit", 1.5) for _ in range(15)]
    rows += [_row(today, "Food & Dining", "Groceries", 30.0) for _ in range(8)]
    rows += [_row(today, "Food & Dining", "Work Lunch", 7.0) for _ in range(10)]
    ids = _ids(pd.DataFrame(rows))
    assert {"coffee_connoisseur", "gym_rat", "transit_pro",
            "grocery_guru", "lunch_legend", "micro_spender"} <= ids


def test_timing_and_weekend_badges():
    today = date.today()
    rows = [_row(today, hour=7) for _ in range(5)]       # early bird
    rows += [_row(today, hour=23) for _ in range(5)]     # night owl
    # 10 weekend rows in the current month (find Saturdays/Sundays)
    d = date(today.year, today.month, 1)
    while d.weekday() not in (5, 6):
        d += timedelta(days=1)
    wd = d
    rows += [_row(wd + timedelta(days=i)) for i in range(10) if (wd + timedelta(days=i)).month == today.month]
    ids = _ids(pd.DataFrame(rows))
    assert "early_bird" in ids
    assert "night_owl" in ids


def test_big_ticket_and_diversity_badges():
    today = date.today()
    rows = [_row(today, amount=600.0)]
    rows.append(_row(today, currency="USD", amount=10.0))
    rows.append(_row(today, currency="RSD", amount=500.0))
    rows.append(_row(today, "Other", "Charity & Donations", 20.0))
    rows.append(_row(today, "Personal", "Gifts", 15.0))
    rows.append(_row(today, "Personal", "Gifts", 15.0))
    rows.append(_row(today, "Personal", "Gifts", 15.0))
    rows.append(_row(today, "Transport", "Flights & Trains", 120.0))
    ids = _ids(pd.DataFrame(rows))
    assert {"big_ticket", "currency_hopper", "charity_champion",
            "gift_giver", "travel_bug"} <= ids


def test_category_explorer_requires_every_category():
    today = date.today()
    rows = [_row(today, category=c, amount=1.0) for c in CATEGORIES.keys()]
    ids = _ids(pd.DataFrame(rows))
    assert "category_explorer" in ids
    # missing one category -> not earned
    ids2 = _ids(pd.DataFrame(rows[:-1]))
    assert "category_explorer" not in ids2


def test_home_steady_needs_12_housing_months():
    rows = [_row(date(2024, m, 5), "Housing", "Rent / Mortgage", 500.0)
            for m in range(1, 13)]
    assert "home_steady" in _ids(pd.DataFrame(rows))
    assert "home_steady" not in _ids(pd.DataFrame(rows[:11]))


def test_hustler_three_sources_in_one_month():
    today = date.today()
    inc = pd.DataFrame([
        {"date": pd.Timestamp(today), "source": "Primary Salary",
         "income_type": "Salary", "actual_eur": 1000.0},
        {"date": pd.Timestamp(today), "source": "Freelance",
         "income_type": "Freelance", "actual_eur": 100.0},
        {"date": pd.Timestamp(today), "source": "Rental",
         "income_type": "Rental", "actual_eur": 300.0},
    ])
    ids = _ids(EMPTY, income=inc)
    assert "hustler" in ids


def test_squirrel_mode_three_positive_months():
    today = date.today()
    rows = []
    for i in range(3):
        d = date(today.year, today.month, 1) - pd.DateOffset(months=i)
        rows.append({"date": pd.Timestamp(d), "goal_name": "Fund",
                     "deposited_eur": 50.0, "balance_eur": 50.0,
                     "target_eur": 1000.0, "interest_rate": 0.0,
                     "deposited": 50.0, "currency": "EUR", "notes": ""})
    assert _saver_streak(pd.DataFrame(rows)) is True
    assert "squirrel_mode" in _ids(EMPTY, savings=pd.DataFrame(rows))
    # one negative month breaks the streak
    rows[-1]["deposited_eur"] = -10.0
    assert _saver_streak(pd.DataFrame(rows)) is False


def test_sub_detective_finds_three_subscriptions():
    rows = []
    for m in range(1, 5):
        for desc, amt in (("NETFLIX", 12.99), ("SPOTIFY", 9.99), ("GYM", 25.0)):
            rows.append({"date": pd.Timestamp(2025, m, 3), "category": "Other",
                         "description": desc, "amount_eur": amt})
    ids = _ids(pd.DataFrame(rows))
    assert "sub_detective" in ids


def test_penny_pincher_compares_to_recent_average():
    today = date.today()
    py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    rows = []
    # 6 months of history ending last month: five fat months, one lean month
    for i in range(1, 7):
        d = date(today.year, today.month, 1) - pd.DateOffset(months=i)
        n = 5
        amt = 50.0 if (d.year, d.month) == (py, pm) else 100.0
        rows += [_row(d, amount=amt) for _ in range(n)]
    ids = _ids(pd.DataFrame(rows))
    assert "penny_pincher" in ids


def test_achievement_hunter_meta_badge():
    today = date.today()
    rows = [_row(today, "Food & Dining", "Coffee & Snacks", 3.0, hour=7)
            for _ in range(20)]                       # coffee, early bird, micro
    rows.append(_row(today, amount=600.0))            # big ticket
    rows.append(_row(today, currency="USD", amount=5.0))
    rows.append(_row(today, currency="RSD", amount=300.0))  # currency hopper
    rows.append(_row(today, "Other", "Charity & Donations", 10.0))  # charity
    rows += [_row(today, "Personal", "Gifts", 10.0) for _ in range(3)]  # gifts
    rows.append(_row(today, "Transport", "Flights & Trains", 100.0))   # travel
    for c in CATEGORIES.keys():                       # category explorer
        rows.append(_row(today, c, amount=1.0))
    ids = _ids(pd.DataFrame(rows))
    assert len(ids) >= 10
    assert "achievement_hunter" in ids


def test_logging_streak_unchanged():
    today = date.today()
    rows = [_row(today - timedelta(days=i)) for i in range(3)]
    assert get_logging_streak(pd.DataFrame(rows)) == 3
