"""
Tests for fun-money/travel pools (utils) and milestone rewards (gamification).
"""

from datetime import date

import pandas as pd
import pytest

from utils import fun_spent, travel_spent, DEFAULT_TRAVEL_CATEGORIES


def _df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_fun_spent_sums_selected_categories_in_month():
    df = _df([
        {"date": "2025-06-05", "category": "Entertainment", "amount_eur": 20.0},
        {"date": "2025-06-10", "category": "Dining Out", "amount_eur": 30.0},
        {"date": "2025-05-01", "category": "Entertainment", "amount_eur": 999.0},
    ])
    assert fun_spent(df, ["Entertainment"], 2025, 6) == 20.0
    assert fun_spent(df, ["Entertainment", "Dining Out"], 2025, 6) == 50.0


def test_fun_spent_accepts_bare_subcategory_names():
    """Backward compatibility: a pool entry may be a subcategory name."""
    df = _df([
        {"date": "2025-06-05", "category": "Dining Out", "subcategory": "Coffee & Snacks", "amount_eur": 5.0},
        {"date": "2025-06-06", "category": "Entertainment", "subcategory": "Cinema & Theater", "amount_eur": 15.0},
    ])
    assert fun_spent(df, ["Coffee & Snacks"], 2025, 6) == 5.0


def test_travel_spent_matches_pairs():
    df = _df([
        {"date": "2025-07-01", "category": "Travel", "subcategory": "Flights & Trains", "amount_eur": 500.0},
        {"date": "2025-07-02", "category": "Travel", "subcategory": "Tours & Activities", "amount_eur": 120.0},
        {"date": "2025-07-03", "category": "Travel", "subcategory": "Hotels & Lodging", "amount_eur": 12.0},
    ])
    spent = travel_spent(df, DEFAULT_TRAVEL_CATEGORIES, 2025)
    assert spent == pytest.approx(632.0)


def test_travel_spent_whole_category_bare_name():
    df = _df([
        {"date": "2025-07-01", "category": "Travel", "subcategory": "Flights & Trains", "amount_eur": 100.0},
        {"date": "2025-07-02", "category": "Travel", "subcategory": "Hotels & Lodging", "amount_eur": 200.0},
    ])
    assert travel_spent(df, ["Travel"], 2025) == pytest.approx(300.0)


def test_travel_spent_whole_category_when_subcategory_empty():
    df = _df([
        {"date": "2025-07-01", "category": "Shopping", "subcategory": "Gifts", "amount_eur": 10.0},
        {"date": "2025-07-02", "category": "Shopping", "subcategory": "Clothing & Accessories", "amount_eur": 20.0},
    ])
    assert travel_spent(df, ["Shopping › "], 2025) == pytest.approx(30.0)


def test_travel_spent_overlapping_pairs_not_double_counted():
    """Regression: selecting both 'Travel › ' (whole category) and
    'Travel › Flights & Trains' in the pool used to count flights twice."""
    df = _df([
        {"date": "2025-07-01", "category": "Travel", "subcategory": "Flights & Trains", "amount_eur": 500.0},
        {"date": "2025-07-02", "category": "Travel", "subcategory": "Hotels & Lodging", "amount_eur": 200.0},
    ])
    spent = travel_spent(df, ["Travel › ", "Travel › Flights & Trains"], 2025)
    assert spent == pytest.approx(700.0)


# ── Milestones & rewards (DB-backed) ─────────────────────────────────────────

from db import (
    init_db, create_user, delete_user_account, username_exists,
    get_settings,
)
from auth import hash_password
from gamification import (
    get_earned_milestones, award_new_milestones, detect_raise, MILESTONE_INDEX,
)

TEST_USERNAME = "milestone_test_user"
TEST_EMAIL    = "milestone_test@example.com"


@pytest.fixture()
def ms_user():
    init_db()
    if username_exists(TEST_USERNAME):
        from db import get_user_by_username
        uid = get_user_by_username(TEST_USERNAME)["id"]
        delete_user_account(uid)
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"), "MS Tester")
    yield uid
    delete_user_account(uid)


def test_award_new_milestones_is_idempotent_and_grants_reward(ms_user):
    # raising salary earns "raise_earned" (reward 20)
    inc = pd.DataFrame({
        "income_type": ["Salary", "Salary"],
        "date": pd.to_datetime(["2025-01-01", "2025-04-01"]),
        "actual_eur": [1000.0, 1200.0],
    })
    earned = get_earned_milestones(pd.DataFrame(), inc, pd.DataFrame(), pd.DataFrame())
    ids = [m["id"] for m in earned]
    assert "raise_earned" in ids

    settings = get_settings(ms_user)
    new_ms, bonus = award_new_milestones(ms_user, earned, settings)
    assert any(m["id"] == "raise_earned" for m in new_ms)
    assert bonus == 20.0

    # reward landed as next month's fun bonus
    settings2 = get_settings(ms_user)
    assert settings2["fun_bonus_amount"] == 20.0
    today = date.today()
    nxt_m = today.month + 1 if today.month < 12 else 1
    nxt_y = today.year if today.month < 12 else today.year + 1
    assert settings2["fun_bonus_month"] == f"{nxt_y:04d}-{nxt_m:02d}"

    # awarding again is a no-op
    new_ms2, bonus2 = award_new_milestones(ms_user, earned, get_settings(ms_user))
    assert new_ms2 == []
    assert bonus2 == 0.0
    assert get_settings(ms_user)["fun_bonus_amount"] == 20.0


def test_debt_free_milestone(ms_user):
    loans = pd.DataFrame([{"status": "paid_off"}])
    earned = get_earned_milestones(pd.DataFrame(), pd.DataFrame(),
                                   pd.DataFrame(), pd.DataFrame(),
                                   loans_df=loans)
    assert any(m["id"] == "debt_free" for m in earned)


def test_fun_keeper_milestone(ms_user):
    today = date.today()
    prev_m = today.month - 1 if today.month > 1 else 12
    prev_y = today.year if today.month > 1 else today.year - 1
    expenses = _df([
        {"date": f"{prev_y}-{prev_m:02d}-05",
         "category": "Entertainment", "amount_eur": 10.0},
    ])
    settings = {"fun_money": 100.0, "fun_categories": ["Entertainment"]}
    earned = get_earned_milestones(expenses, pd.DataFrame(), pd.DataFrame(),
                                   pd.DataFrame(), settings=settings)
    assert any(m["id"] == "fun_keeper" for m in earned)


def test_fun_bonus_set_vs_add_across_months(ms_user):
    """Regression: a bonus queued for an EARLIER month was added to the new
    bonus, double-counting it in later months. A fresh award must SET the
    amount when the queued month differs."""
    from db import save_settings as _raw_save
    today = date.today()
    nxt_m = today.month + 1 if today.month < 12 else 1
    nxt_y = today.year if today.month < 12 else today.year + 1
    nxt_key = f"{nxt_y:04d}-{nxt_m:02d}"

    # Simulate a bonus queued for a month that has since passed.
    _raw_save(ms_user, {"fun_bonus_amount": 20.0,
                        "fun_bonus_month": f"{today.year:04d}-{today.month:02d}"})

    inc = pd.DataFrame({
        "income_type": ["Salary", "Salary"],
        "date": pd.to_datetime(["2025-01-01", "2025-04-01"]),
        "actual_eur": [1000.0, 1200.0],
    })
    earned = get_earned_milestones(pd.DataFrame(), inc, pd.DataFrame(), pd.DataFrame())
    award_new_milestones(ms_user, earned, get_settings(ms_user))
    s = get_settings(ms_user)
    assert s["fun_bonus_amount"] == 20.0          # set, not 40
    assert s["fun_bonus_month"] == nxt_key

    # A second award queued for the SAME month accumulates.
    _raw_save(ms_user, {"fun_bonus_amount": 20.0, "fun_bonus_month": nxt_key})
    # earn a different reward in the same run
    big_inc = pd.DataFrame({
        "income_type": ["Salary", "Salary"],
        "date": pd.to_datetime(["2025-01-01", "2025-04-01"]),
        "actual_eur": [1000.0, 1400.0],
    })
    # clear the earned marker so the same milestone can be re-awarded
    from db import get_session, UserMilestone
    with get_session() as sess:
        sess.query(UserMilestone).filter(UserMilestone.user_id == ms_user).delete()
    earned2 = get_earned_milestones(pd.DataFrame(), big_inc, pd.DataFrame(), pd.DataFrame())
    award_new_milestones(ms_user, earned2, get_settings(ms_user))
    s2 = get_settings(ms_user)
    assert s2["fun_bonus_amount"] == 40.0          # same queue -> accumulate
    assert s2["fun_bonus_month"] == nxt_key


def test_fun_bonus_map_preserves_active_month_bonus(ms_user):
    """Regression: a bonus still being DISPLAYED this month used to be
    overwritten when a new milestone was earned (mid-month loss). With the
    per-month map both months keep their bonuses."""
    today = date.today()
    nxt_m = today.month + 1 if today.month < 12 else 1
    nxt_y = today.year if today.month < 12 else today.year + 1
    nxt_key = f"{nxt_y:04d}-{nxt_m:02d}"
    cur_key = f"{today.year:04d}-{today.month:02d}"

    from db import save_settings as _raw_save
    _raw_save(ms_user, {"fun_bonuses": {cur_key: 20.0},
                        "fun_bonus_amount": 20.0, "fun_bonus_month": cur_key})

    inc = pd.DataFrame({
        "income_type": ["Salary", "Salary"],
        "date": pd.to_datetime(["2025-01-01", "2025-04-01"]),
        "actual_eur": [1000.0, 1200.0],
    })
    earned = get_earned_milestones(pd.DataFrame(), inc, pd.DataFrame(), pd.DataFrame())
    award_new_milestones(ms_user, earned, get_settings(ms_user))
    s = get_settings(ms_user)
    bonuses = s.get("fun_bonuses") or {}
    assert bonuses.get(cur_key) == 20.0   # this month's bonus survives
    assert bonuses.get(nxt_key) == 20.0   # next month queued separately
