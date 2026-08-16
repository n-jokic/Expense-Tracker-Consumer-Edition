"""
Custom-milestone tests: creation/validation, metric computation, one-time
awarding with fun-money queueing (idempotent), and account-deletion cleanup.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from db import (init_db, create_user, delete_user_account, username_exists,
                get_user_by_username, add_expense, add_income, add_savings,
                add_custom_milestone, get_custom_milestones,
                delete_custom_milestone, mark_custom_milestone_achieved,
                get_settings)
from gamification import custom_metric_value, award_custom_milestones
from auth import hash_password

TEST_USERNAME = "cm_test_user"
TEST_EMAIL    = "cm_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "CM Tester")
    yield uid
    delete_user_account(uid)


def _exp(uid, n=1, amount=10.0, when=None):
    for i in range(n):
        add_expense(uid, {"date": when or date(2026, 8, 1),
                          "category": "Groceries", "description": f"e{i}",
                          "amount": amount, "currency": "EUR",
                          "amount_eur": amount})


def test_create_validate_and_delete(test_user):
    mid = add_custom_milestone(test_user, {"title": "Save 500",
                                           "metric": "savings_balance",
                                           "target": 500.0, "reward": 20.0})
    rows = get_custom_milestones(test_user)
    assert len(rows) == 1
    assert rows.iloc[0]["title"] == "Save 500"
    assert rows.iloc[0]["reward"] == 20.0
    assert pd.isna(rows.iloc[0]["achieved_at"])

    for bad, msg in [
        ({"title": "x", "metric": "nope", "target": 5.0}, "metric"),
        ({"title": "x", "metric": "expenses_count", "target": 0.0}, "target"),
        ({"title": "x", "metric": "expenses_count", "target": -5.0}, "target"),
        ({"title": "x", "metric": "expenses_count", "target": 5.0,
          "reward": -1.0}, "reward"),
        ({"title": "  ", "metric": "expenses_count", "target": 5.0}, "title"),
    ]:
        with pytest.raises(ValueError, match=msg):
            add_custom_milestone(test_user, bad)

    assert delete_custom_milestone(test_user, mid) is True
    assert get_custom_milestones(test_user).empty
    assert delete_custom_milestone(test_user, mid) is False  # already gone


def test_metric_values_nan_safe(test_user):
    empty = custom_metric_value("expenses_eur", get_empty(), get_empty(),
                                get_empty())
    assert empty == 0.0

    _exp(test_user, 3, amount=10.0)
    add_income(test_user, {"date": date(2026, 8, 1), "source": "Salary",
                           "income_type": "Salary", "budgeted": 100.0,
                           "actual": 100.0, "currency": "EUR",
                           "budgeted_eur": 100.0, "actual_eur": 100.0})
    from db import get_expenses, get_income, get_savings
    exp, inc, sav = get_expenses(test_user), get_income(test_user), get_savings(test_user)
    assert custom_metric_value("expenses_count", exp, inc, sav) == 3.0
    assert custom_metric_value("expenses_eur", exp, inc, sav) == 30.0
    assert custom_metric_value("income_eur", exp, inc, sav) == 100.0
    assert custom_metric_value("savings_balance", exp, inc, sav) == 0.0
    assert custom_metric_value("categories_count", exp, inc, sav) == 1.0
    assert custom_metric_value("streak_days", exp, inc, sav) >= 0.0


def get_empty():
    import pandas as pd
    return pd.DataFrame()


def test_award_is_once_and_queues_fun_money(test_user):
    _exp(test_user, 3, amount=10.0)
    add_custom_milestone(test_user, {"title": "3 expenses",
                                     "metric": "expenses_count",
                                     "target": 3.0, "reward": 15.0})
    from db import get_expenses, get_income, get_savings
    settings = get_settings(test_user)

    newly, total = award_custom_milestones(
        test_user, get_expenses(test_user), get_income(test_user),
        get_savings(test_user), settings)
    assert total == 15.0 and len(newly) == 1 and newly[0]["title"] == "3 expenses"

    rows = get_custom_milestones(test_user)
    assert rows.iloc[0]["achieved_at"] is not None

    # Second run: nothing new, no double reward.
    newly2, total2 = award_custom_milestones(
        test_user, get_expenses(test_user), get_income(test_user),
        get_savings(test_user), get_settings(test_user))
    assert newly2 == [] and total2 == 0.0

    # The reward landed in NEXT month's per-month fun-bonus map.
    today = date.today()
    nxt_m = today.month + 1 if today.month < 12 else 1
    nxt_y = today.year if today.month < 12 else today.year + 1
    nxt_key = f"{nxt_y:04d}-{nxt_m:02d}"
    bonuses = get_settings(test_user).get("fun_bonuses") or {}
    assert bonuses.get(nxt_key) == 15.0


def test_unreached_milestone_never_awards(test_user):
    _exp(test_user, 1, amount=5.0)
    add_custom_milestone(test_user, {"title": "10 expenses",
                                     "metric": "expenses_count",
                                     "target": 10.0, "reward": 50.0})
    from db import get_expenses, get_income, get_savings
    newly, total = award_custom_milestones(
        test_user, get_expenses(test_user), get_income(test_user),
        get_savings(test_user), get_settings(test_user))
    assert newly == [] and total == 0.0
    assert pd.isna(get_custom_milestones(test_user).iloc[0]["achieved_at"])


def test_account_deletion_removes_custom_milestones(test_user):
    add_custom_milestone(test_user, {"title": "x", "metric": "expenses_count",
                                     "target": 1.0, "reward": 0.0})
    assert not get_custom_milestones(test_user).empty
    delete_user_account(test_user)
    # A new user with the same... no — after deletion the rows must be gone.
    rows = get_custom_milestones(test_user)
    assert rows.empty


def test_catalog_and_custom_awards_both_queue(monkeypatch, test_user):
    # Regression: a catalog award and a custom-milestone award in the SAME
    # rerun both queue onto next month's key — the second must ADD to the
    # first (the merge reads fresh DB settings), never overwrite it.
    from gamification import award_new_milestones
    from db import get_expenses, get_income, get_savings
    _exp(test_user, 3, amount=10.0)
    add_custom_milestone(test_user, {"title": "3 expenses",
                                     "metric": "expenses_count",
                                     "target": 3.0, "reward": 15.0})

    settings = get_settings(test_user)  # the SAME stale dict both calls get
    earned = [{"id": "raise_earned"}]   # catalog badge with reward 20
    _, cat_bonus = award_new_milestones(test_user, earned, settings)
    assert cat_bonus == 20.0

    _, cm_bonus = award_custom_milestones(
        test_user, get_expenses(test_user), get_income(test_user),
        get_savings(test_user), settings)
    assert cm_bonus == 15.0

    today = date.today()
    nxt_m = today.month + 1 if today.month < 12 else 1
    nxt_y = today.year if today.month < 12 else today.year + 1
    nxt_key = f"{nxt_y:04d}-{nxt_m:02d}"
    bonuses = get_settings(test_user).get("fun_bonuses") or {}
    assert bonuses.get(nxt_key) == 35.0  # 20 + 15, not 15


def test_manual_mark_achieved(test_user):
    mid = add_custom_milestone(test_user, {"title": "manual",
                                           "metric": "streak_days",
                                           "target": 1.0, "reward": 0.0})
    assert mark_custom_milestone_achieved(test_user, mid) is True
    assert get_custom_milestones(test_user).iloc[0]["achieved_at"] is not None
    # The conditional UPDATE wins exactly once — a second mark loses.
    assert mark_custom_milestone_achieved(test_user, mid) is False
    assert mark_custom_milestone_achieved(test_user, "missing-id") is False
