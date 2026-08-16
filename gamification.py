"""
gamification.py — Streaks, milestones, and badges for Expense Tracker v3.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st


# ── Milestone definitions ─────────────────────────────────────────────────────

MILESTONES = [
    {"id": "first_expense",   "icon": "🎯", "title": "First Step",      "desc": "Logged your first expense"},
    {"id": "first_income",    "icon": "💵", "title": "Income Aware",    "desc": "Logged your first income entry"},
    {"id": "week_streak",     "icon": "🔥", "title": "Week Warrior",    "desc": "7-day logging streak"},
    {"id": "month_streak",    "icon": "🏆", "title": "Month Master",    "desc": "30-day logging streak"},
    {"id": "budget_keeper",   "icon": "💚", "title": "Budget Keeper",   "desc": "Stayed under budget for a full month"},
    {"id": "saver_100",       "icon": "💰", "title": "First Hundred",   "desc": "Saved your first €100"},
    {"id": "saver_1000",      "icon": "🏦", "title": "Four Figures",    "desc": "Saved €1,000 total"},
    {"id": "saver_10000",     "icon": "🚀", "title": "Ten Grand",       "desc": "Saved €10,000 total"},
    {"id": "expenses_50",     "icon": "📊", "title": "Data Driven",     "desc": "Logged 50 expenses"},
    {"id": "expenses_200",    "icon": "📈", "title": "Power Tracker",   "desc": "Logged 200 expenses"},
    {"id": "goal_reached",    "icon": "🎉", "title": "Goal Crusher",    "desc": "Reached a savings goal"},
    {"id": "no_luxury_month", "icon": "🧘", "title": "Mindful Month",   "desc": "A full month with zero Entertainment spend"},
    {"id": "first_budget",    "icon": "📋", "title": "Budget Setter",   "desc": "Set your first category budget"},
    {"id": "first_salary",    "icon": "💼", "title": "Salary Sorted",   "desc": "Logged your first salary"},
    {"id": "raise_earned",    "icon": "📈", "title": "Level Up",        "desc": "Got a raise", "reward": 20.0},
    {"id": "first_bonus",     "icon": "🎁", "title": "Bonus Time",      "desc": "Logged a bonus"},
    {"id": "first_hourly",    "icon": "⏱️", "title": "Side Hustle",     "desc": "Logged hourly income"},
    {"id": "fun_keeper",      "icon": "🎈", "title": "Fun Master",      "desc": "Stayed within your fun money for a full month", "reward": 10.0},
    {"id": "budget_keeper_3", "icon": "🏅", "title": "Budget Champion", "desc": "Stayed under budget 3 months in a row", "reward": 25.0},
    {"id": "debt_free",       "icon": "🕊️", "title": "Debt Free",       "desc": "Paid off all your loans", "reward": 50.0},

    # ── Fun achievements ─────────────────────────────────────────────────────
    {"id": "coffee_connoisseur", "icon": "☕",  "title": "Caffeine Addict", "desc": "10+ coffee & snack runs in one month"},
    {"id": "gym_rat",            "icon": "🏋️", "title": "Gym Rat",         "desc": "8+ gym visits in one month"},
    {"id": "transit_pro",        "icon": "🚌", "title": "City Slicker",    "desc": "15+ public-transit rides in one month"},
    {"id": "grocery_guru",       "icon": "🥕", "title": "Grocery Guru",    "desc": "8+ grocery trips in one month"},
    {"id": "lunch_legend",       "icon": "🥪", "title": "Lunch Legend",    "desc": "10+ work lunches in one month"},
    {"id": "early_bird",         "icon": "🌅", "title": "Early Bird",      "desc": "Logged 5 expenses before 9:00"},
    {"id": "night_owl",          "icon": "🦉", "title": "Night Owl",       "desc": "Logged 5 expenses after 23:00"},
    {"id": "weekend_spender",    "icon": "🛍️", "title": "Weekend Warrior","desc": "10+ weekend expenses in one month"},
    {"id": "micro_spender",      "icon": "🪙", "title": "Micro Spender",   "desc": "20+ expenses under €5 in one month"},
    {"id": "big_ticket",         "icon": "💎", "title": "Big Spender",     "desc": "Logged a single expense over €500"},
    {"id": "penny_pincher",      "icon": "📉", "title": "Penny Pincher",   "desc": "A full month at least 30% below your average", "reward": 5.0},
    {"id": "category_explorer",  "icon": "🌈", "title": "Category Explorer", "desc": "Spent in every top-level category", "reward": 5.0},
    {"id": "charity_champion",   "icon": "❤️", "title": "Kind Heart",      "desc": "Made a charity donation"},
    {"id": "gift_giver",         "icon": "🎁", "title": "Santa's Helper",  "desc": "3+ gifts in one month"},
    {"id": "travel_bug",         "icon": "✈️", "title": "Jet Setter",      "desc": "Booked flights or a hotel"},
    {"id": "currency_hopper",    "icon": "🌍", "title": "Globe Trotter",   "desc": "Spent in 3+ different currencies"},
    {"id": "home_steady",        "icon": "🏠", "title": "Home Steady",     "desc": "Housing costs in 12 different months"},
    {"id": "hustler",            "icon": "🎭", "title": "Hustler",         "desc": "Income from 3+ sources in one month"},
    {"id": "squirrel_mode",      "icon": "🐿️", "title": "Squirrel Mode",  "desc": "3 months in a row of net-positive savings", "reward": 10.0},
    {"id": "sub_detective",      "icon": "🔁", "title": "Sub Detective",   "desc": "Spotted 3+ recurring subscriptions", "reward": 5.0},
    {"id": "achievement_hunter", "icon": "🧭", "title": "Achievement Hunter", "desc": "Earned 10 different badges", "reward": 15.0},
]

MILESTONE_INDEX = {m["id"]: m for m in MILESTONES}


# ── Streak functions ──────────────────────────────────────────────────────────

def get_logging_streak(expenses_df: pd.DataFrame) -> int:
    """Return consecutive days with at least one expense logged, ending today or yesterday."""
    if expenses_df.empty:
        return 0
    unique_days = set(expenses_df["date"].dt.date.dropna().tolist())
    today       = date.today()
    streak      = 0
    check       = today if today in unique_days else today - timedelta(days=1)
    while check in unique_days:
        streak += 1
        check  -= timedelta(days=1)
    return streak


def get_budget_adherence_streak(expenses_df: pd.DataFrame, budgets_df: pd.DataFrame,
                                 year: int, month: int) -> int:
    """Return consecutive months (going back from current) where spending stayed under budget."""
    if expenses_df.empty or budgets_df.empty:
        return 0
    streak    = 0
    check_y   = year
    check_m   = month
    for _ in range(24):  # check up to 24 months back
        m_exp = expenses_df[
            (expenses_df["date"].dt.year == check_y) &
            (expenses_df["date"].dt.month == check_m)
        ]
        m_bud = budgets_df[
            (budgets_df["year"] == check_y) &
            (budgets_df["month"] == check_m)
        ]
        if m_bud.empty:
            break
        from utils import effective_category_budgets
        total_budget = float(sum(effective_category_budgets(m_bud).values()))
        total_spent  = float(m_exp["amount_eur"].sum()) if not m_exp.empty else 0.0
        if total_budget > 0 and total_spent <= total_budget:
            streak += 1
        else:
            break
        check_m -= 1
        if check_m == 0:
            check_m = 12
            check_y -= 1
    return streak


# ── Milestone checker ─────────────────────────────────────────────────────────

def detect_raise(income_df: pd.DataFrame) -> bool:
    """True when some salary entry is higher than every earlier salary entry."""
    if income_df.empty or "income_type" not in income_df.columns:
        return False
    sal = income_df[income_df["income_type"].fillna("Other") == "Salary"].sort_values("date")
    if len(sal) < 2:
        return False
    prev_max = float(sal.iloc[0]["actual_eur"] or 0.0)
    for _, r in sal.iloc[1:].iterrows():
        a = float(r["actual_eur"] or 0.0)
        if a > prev_max:
            return True
        prev_max = max(prev_max, a)
    return False


# ── Fun-achievement helpers ───────────────────────────────────────────────────

def _month_frame(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[(df["date"].dt.year == year) & (df["date"].dt.month == month)]


def _sub_in_month(df: pd.DataFrame, year: int, month: int, sub: str) -> int:
    if df.empty or "subcategory" not in df.columns:
        return 0
    m = _month_frame(df, year, month)
    return int((m["subcategory"].fillna("") == sub).sum())


def _weekend_in_month(df: pd.DataFrame, year: int, month: int) -> int:
    m = _month_frame(df, year, month)
    if m.empty:
        return 0
    return int(m["date"].dt.dayofweek.isin([5, 6]).sum())


def _under_in_month(df: pd.DataFrame, year: int, month: int, limit: float) -> int:
    m = _month_frame(df, year, month)
    if m.empty:
        return 0
    return int((m["amount_eur"] < limit).sum())


def _prev_month(today: date) -> tuple[int, int]:
    if today.month > 1:
        return today.year, today.month - 1
    return today.year - 1, 12


def _penny_pincher(df: pd.DataFrame, today: date) -> bool:
    """Previous full month with ≥5 expenses totalling ≤70% of the recent
    (6-month) average monthly spend."""
    if df.empty:
        return False
    py, pm = _prev_month(today)
    m = _month_frame(df, py, pm)
    if len(m) < 5:
        return False
    first = date(today.year, today.month, 1) - pd.DateOffset(months=6)
    hist = df[df["date"] >= first]
    if len(hist) < 6:
        return False
    totals = hist.groupby(hist["date"].dt.to_period("M"))["amount_eur"].sum()
    avg = float(totals.mean())
    if avg <= 0:
        return False
    return float(m["amount_eur"].sum()) <= 0.7 * avg


def _saver_streak(df: pd.DataFrame, n: int = 3) -> bool:
    """n consecutive recorded months (ending with the latest) where net
    deposits were positive."""
    if df.empty:
        return False
    net = df.groupby(df["date"].dt.to_period("M"))["deposited_eur"].sum().sort_index()
    streak = 0
    for p in reversed(net.index):
        if float(net[p]) > 0:
            streak += 1
        else:
            break
    return streak >= n


def get_earned_milestones(expenses_df: pd.DataFrame, income_df: pd.DataFrame,
                           savings_df: pd.DataFrame, budgets_df: pd.DataFrame,
                           settings: dict | None = None,
                           loans_df: pd.DataFrame | None = None) -> list[dict]:
    """Return list of milestone dicts that have been earned."""
    earned = []
    today  = date.today()

    # first_expense
    if not expenses_df.empty:
        earned.append(MILESTONE_INDEX["first_expense"])

    # first_income
    if not income_df.empty:
        earned.append(MILESTONE_INDEX["first_income"])

    # income types
    if not income_df.empty and "income_type" in income_df.columns:
        types = income_df["income_type"].fillna("Other")
        if (types == "Salary").any():
            earned.append(MILESTONE_INDEX["first_salary"])
        if (types == "Hourly").any():
            earned.append(MILESTONE_INDEX["first_hourly"])
        if types.isin(["Bonus / Raise", "Bonus"]).any():
            earned.append(MILESTONE_INDEX["first_bonus"])
        if detect_raise(income_df):
            earned.append(MILESTONE_INDEX["raise_earned"])

    # expense counts
    exp_count = len(expenses_df) if not expenses_df.empty else 0
    if exp_count >= 50:
        earned.append(MILESTONE_INDEX["expenses_50"])
    if exp_count >= 200:
        earned.append(MILESTONE_INDEX["expenses_200"])

    # streaks
    streak = get_logging_streak(expenses_df)
    if streak >= 7:
        earned.append(MILESTONE_INDEX["week_streak"])
    if streak >= 30:
        earned.append(MILESTONE_INDEX["month_streak"])

    # budget adherence
    if not budgets_df.empty:
        earned.append(MILESTONE_INDEX["first_budget"])
        adh = get_budget_adherence_streak(expenses_df, budgets_df, today.year, today.month)
        if adh >= 1:
            earned.append(MILESTONE_INDEX["budget_keeper"])
        if adh >= 3:
            earned.append(MILESTONE_INDEX["budget_keeper_3"])

    # fun money keeper (previous full month within the allowance)
    if settings and float(settings.get("fun_money") or 0.0) > 0:
        from utils import fun_spent
        cats = settings.get("fun_categories") or ["Entertainment"]
        prev_m = today.month - 1 if today.month > 1 else 12
        prev_y = today.year if today.month > 1 else today.year - 1
        spent = fun_spent(expenses_df, cats, prev_y, prev_m)
        if spent <= float(settings["fun_money"]) and not expenses_df.empty:
            any_prev = expenses_df[(expenses_df["date"].dt.year == prev_y) &
                                   (expenses_df["date"].dt.month == prev_m)]
            if not any_prev.empty:
                earned.append(MILESTONE_INDEX["fun_keeper"])

    # debt free
    if loans_df is not None and not loans_df.empty:
        if (loans_df["status"] == "paid_off").all():
            earned.append(MILESTONE_INDEX["debt_free"])

    # savings totals
    if not savings_df.empty:
        total_saved = float(savings_df["deposited_eur"].sum())
        if total_saved >= 100:
            earned.append(MILESTONE_INDEX["saver_100"])
        if total_saved >= 1000:
            earned.append(MILESTONE_INDEX["saver_1000"])
        if total_saved >= 10000:
            earned.append(MILESTONE_INDEX["saver_10000"])

        # goal reached
        for goal in savings_df["goal_name"].unique():
            rows   = savings_df[savings_df["goal_name"] == goal]
            target = float(rows["target_eur"].max())
            bal    = float(rows["balance_eur"].max())
            if target > 0 and bal >= target:
                earned.append(MILESTONE_INDEX["goal_reached"])
                break

    # no luxury month
    if not expenses_df.empty:
        prev_m = today.month - 1 if today.month > 1 else 12
        prev_y = today.year if today.month > 1 else today.year - 1
        ent = expenses_df[
            (expenses_df["date"].dt.year == prev_y) &
            (expenses_df["date"].dt.month == prev_m) &
            (expenses_df["category"] == "Entertainment")
        ]
        if ent.empty:
            # also check we actually had expenses that month (not just no data)
            any_exp = expenses_df[
                (expenses_df["date"].dt.year == prev_y) &
                (expenses_df["date"].dt.month == prev_m)
            ]
            if not any_exp.empty:
                earned.append(MILESTONE_INDEX["no_luxury_month"])

    # ── Fun achievements ─────────────────────────────────────────────────────
    if not expenses_df.empty:
        ty, tm = today.year, today.month
        for mid, sub, need in (
            ("coffee_connoisseur", "Coffee & Snacks", 10),
            ("gym_rat", "Gym & Fitness", 8),
            ("transit_pro", "Public Transit", 15),
            ("grocery_guru", "Groceries", 8),
            ("lunch_legend", "Work Lunch", 10),
        ):
            if _sub_in_month(expenses_df, ty, tm, sub) >= need:
                earned.append(MILESTONE_INDEX[mid])

        if _weekend_in_month(expenses_df, ty, tm) >= 10:
            earned.append(MILESTONE_INDEX["weekend_spender"])
        if _under_in_month(expenses_df, ty, tm, 5.0) >= 20:
            earned.append(MILESTONE_INDEX["micro_spender"])
        if (expenses_df["amount_eur"] > 500).any():
            earned.append(MILESTONE_INDEX["big_ticket"])

        if "created_at" in expenses_df.columns:
            hours = pd.to_datetime(expenses_df["created_at"],
                                   errors="coerce").dt.hour.dropna()
            if int((hours < 9).sum()) >= 5:
                earned.append(MILESTONE_INDEX["early_bird"])
            if int((hours >= 23).sum()) >= 5:
                earned.append(MILESTONE_INDEX["night_owl"])

        if "subcategory" in expenses_df.columns:
            subs = expenses_df["subcategory"].fillna("")
            if (subs == "Charity & Donations").any():
                earned.append(MILESTONE_INDEX["charity_champion"])
            if _sub_in_month(expenses_df, ty, tm, "Gifts") >= 3:
                earned.append(MILESTONE_INDEX["gift_giver"])
            if subs.isin(["Flights & Trains", "Hotels & Lodging"]).any():
                earned.append(MILESTONE_INDEX["travel_bug"])

        if "currency" in expenses_df.columns and \
                expenses_df["currency"].nunique() >= 3:
            earned.append(MILESTONE_INDEX["currency_hopper"])

        from utils import CATEGORIES
        if set(expenses_df["category"].dropna()) >= set(CATEGORIES.keys()):
            earned.append(MILESTONE_INDEX["category_explorer"])

        housing = expenses_df[expenses_df["category"] == "Housing"]
        if not housing.empty and housing["date"].dt.to_period("M").nunique() >= 12:
            earned.append(MILESTONE_INDEX["home_steady"])

        if _penny_pincher(expenses_df, today):
            earned.append(MILESTONE_INDEX["penny_pincher"])

        if len(expenses_df) >= 12:
            try:
                from forecasting import detect_subscriptions
                if len(detect_subscriptions(expenses_df)) >= 3:
                    earned.append(MILESTONE_INDEX["sub_detective"])
            except Exception:
                pass

    if not income_df.empty:
        m_inc = _month_frame(income_df, today.year, today.month)
        if not m_inc.empty and "source" in m_inc.columns and \
                m_inc["source"].fillna("Other").nunique() >= 3:
            earned.append(MILESTONE_INDEX["hustler"])

    if not savings_df.empty and _saver_streak(savings_df):
        earned.append(MILESTONE_INDEX["squirrel_mode"])

    # Deduplicate
    seen = set()
    unique_earned = []
    for m in earned:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique_earned.append(m)

    # Meta badge: earning 10 different badges unlocks the hunter badge itself.
    if len(unique_earned) >= 10 and "achievement_hunter" not in seen:
        unique_earned.append(MILESTONE_INDEX["achievement_hunter"])
    return unique_earned


# ── Persistent unlocks + rewards ─────────────────────────────────────────────

def award_new_milestones(user_id: int, earned: list[dict], settings: dict):
    """Persist newly earned milestones once and grant their fun-money rewards.

    Returns (new_milestone_dicts, total_bonus). Rewards land in
    fun_bonus_amount for NEXT month (fun_bonus_month).
    """
    from db import record_milestones
    import queries as q

    new_ids = record_milestones(user_id, [m["id"] for m in earned])
    if not new_ids:
        return [], 0.0

    new_ms = [MILESTONE_INDEX[i] for i in new_ids if i in MILESTONE_INDEX]
    bonus = sum(float(m.get("reward") or 0.0) for m in new_ms)
    if bonus > 0:
        today = date.today()
        nxt_m = today.month + 1 if today.month < 12 else 1
        nxt_y = today.year if today.month < 12 else today.year + 1
        q.save_settings(user_id, {
            "fun_bonus_amount": float(settings.get("fun_bonus_amount") or 0.0) + bonus,
            "fun_bonus_month": f"{nxt_y:04d}-{nxt_m:02d}",
        })
    return new_ms, bonus


def _next_milestone_hint(expenses_df: pd.DataFrame, earned_ids: set) -> str | None:
    """Return a hint string for the closest unearned milestone."""
    exp_count = len(expenses_df) if not expenses_df.empty else 0
    streak    = get_logging_streak(expenses_df)

    if "first_expense" not in earned_ids:
        return "Log your first expense to earn your first badge! 🎯"
    if "coffee_connoisseur" not in earned_ids:
        return "10+ coffee & snack runs in a month earn the Caffeine Addict badge ☕"
    if "category_explorer" not in earned_ids:
        return "Spend in every category to earn the Category Explorer badge 🌈"
    if "week_streak" not in earned_ids and streak > 0:
        return f"Log for {7 - streak} more day(s) to earn the Week Warrior badge 🔥"
    if "month_streak" not in earned_ids and streak >= 7:
        return f"Log for {30 - streak} more day(s) to earn the Month Master badge 🏆"
    if "expenses_50" not in earned_ids:
        return f"Log {50 - exp_count} more expenses to earn Data Driven 📊"
    if "expenses_200" not in earned_ids:
        return f"Log {200 - exp_count} more expenses to earn Power Tracker 📈"
    return None


# ── Streamlit sidebar renderer ────────────────────────────────────────────────

def render_gamification_sidebar(expenses_df: pd.DataFrame, income_df: pd.DataFrame,
                                  savings_df: pd.DataFrame, budgets_df: pd.DataFrame,
                                  settings: dict | None = None,
                                  loans_df: pd.DataFrame | None = None):
    """Render streak and badges in the sidebar."""
    streak  = get_logging_streak(expenses_df)
    earned  = get_earned_milestones(expenses_df, income_df, savings_df, budgets_df,
                                    settings=settings, loans_df=loans_df)
    ids     = {m["id"] for m in earned}

    # Streak display
    if streak > 0:
        fire = "🔥" * min(streak // 7 + 1, 5)
        st.markdown(f"**{fire} {streak}-day streak!**")
        st.caption("Keep logging daily to grow your streak.")
    else:
        st.markdown("**Start your streak today!** Log an expense.")

    # Badge grid
    if earned:
        st.markdown("**🏅 Badges earned:**")
        badge_html = " ".join(
            f'<span class="badge" title="{m["desc"]}">{m["icon"]} {m["title"]}</span>'
            for m in earned
        )
        st.markdown(badge_html, unsafe_allow_html=True)
    else:
        st.caption("No badges yet — start logging to earn them!")

    # Next milestone hint
    hint = _next_milestone_hint(expenses_df, ids)
    if hint:
        st.caption(f"💡 {hint}")
