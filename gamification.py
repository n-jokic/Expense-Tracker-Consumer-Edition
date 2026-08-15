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
        total_budget = float(m_bud["budgeted_eur"].sum())
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
        max_balance = float(savings_df.groupby("goal_name")["balance_eur"].max().max())
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

    # Deduplicate
    seen = set()
    unique_earned = []
    for m in earned:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique_earned.append(m)
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
