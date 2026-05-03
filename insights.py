"""
insights.py — Auto-generated spending insights for Expense Tracker v3.
Receives DataFrames; returns human-readable insight strings rendered in Streamlit.
"""

from datetime import date, datetime
import calendar

import pandas as pd
import streamlit as st

from utils import fmt, NEAR_LIMIT_THRESHOLD


# ── Analysis functions ────────────────────────────────────────────────────────

def month_over_month(df: pd.DataFrame, col: str, current_year: int, current_month: int) -> dict:
    """Compare current month total vs previous month for the given column."""
    if df.empty or col not in df.columns:
        return {"current": 0.0, "previous": 0.0, "change_pct": 0.0, "trend": "same"}

    prev_month = current_month - 1 if current_month > 1 else 12
    prev_year  = current_year if current_month > 1 else current_year - 1

    cur  = df[(df["date"].dt.year == current_year) & (df["date"].dt.month == current_month)][col].sum()
    prev = df[(df["date"].dt.year == prev_year)    & (df["date"].dt.month == prev_month)][col].sum()

    cur, prev = float(cur), float(prev)
    if prev == 0:
        change_pct = 100.0 if cur > 0 else 0.0
        trend = "up" if cur > 0 else "same"
    else:
        change_pct = ((cur - prev) / prev) * 100
        trend = "up" if cur > prev else ("down" if cur < prev else "same")

    return {"current": cur, "previous": prev, "change_pct": round(change_pct, 1), "trend": trend}


def top_category_this_month(expenses_df: pd.DataFrame, year: int, month: int):
    """Return (category, amount_eur) for the biggest spending category this month."""
    if expenses_df.empty:
        return None
    m = expenses_df[(expenses_df["date"].dt.year == year) & (expenses_df["date"].dt.month == month)]
    if m.empty:
        return None
    grp = m.groupby("category")["amount_eur"].sum()
    cat = grp.idxmax()
    return cat, float(grp.max())


def unusual_expenses(expenses_df: pd.DataFrame, multiplier: float = 2.0) -> pd.DataFrame:
    """Return expenses where amount > multiplier × that category's average."""
    if expenses_df.empty:
        return pd.DataFrame()
    avgs = expenses_df.groupby("category")["amount_eur"].mean()
    def is_unusual(row):
        avg = avgs.get(row["category"], 0)
        return avg > 0 and row["amount_eur"] > avg * multiplier
    return expenses_df[expenses_df.apply(is_unusual, axis=1)].copy()


def days_until_budget_depleted(expenses_df: pd.DataFrame, total_budget_eur: float,
                                period_start: date) -> int | None:
    """Estimate how many days remain before budget runs out at current burn rate."""
    if total_budget_eur <= 0 or expenses_df.empty:
        return None
    today = date.today()
    days_elapsed = max((today - period_start).days + 1, 1)
    period_exp = expenses_df[
        expenses_df["date"].dt.date >= period_start
    ]
    if period_exp.empty:
        return None
    spent = float(period_exp["amount_eur"].sum())
    daily_avg = spent / days_elapsed
    if daily_avg <= 0:
        return None
    remaining_budget = total_budget_eur - spent
    if remaining_budget <= 0:
        return 0
    return int(remaining_budget / daily_avg)


def savings_projection(savings_df: pd.DataFrame, goal_name: str) -> dict:
    """Project when a savings goal will be reached."""
    empty = {"current_balance": 0.0, "target": 0.0, "months_to_goal": None, "projected_date": None}
    if savings_df.empty:
        return empty
    rows = savings_df[savings_df["goal_name"] == goal_name].sort_values("date")
    if rows.empty:
        return empty

    latest       = rows.iloc[-1]
    balance      = float(latest["balance_eur"])
    target       = float(rows["target_eur"].max())
    interest_rate = float(latest["interest_rate"])

    if target <= 0 or balance >= target:
        return {"current_balance": balance, "target": target,
                "months_to_goal": 0, "projected_date": date.today()}

    # Average monthly deposit over last 3 months
    if len(rows) >= 2:
        monthly_dep = float(rows["deposited_eur"].tail(3).mean())
    else:
        monthly_dep = float(rows["deposited_eur"].mean())

    monthly_rate = (interest_rate / 100) / 12
    cur_bal = balance
    months  = 0
    while cur_bal < target and months < 600:
        cur_bal = cur_bal * (1 + monthly_rate) + monthly_dep
        months += 1

    if months >= 600:
        return {"current_balance": balance, "target": target,
                "months_to_goal": None, "projected_date": None}

    today = date.today()
    proj_year  = today.year + (today.month - 1 + months) // 12
    proj_month = (today.month - 1 + months) % 12 + 1
    proj_date  = date(proj_year, proj_month, 1)
    return {"current_balance": balance, "target": target,
            "months_to_goal": months, "projected_date": proj_date}


# ── Main render function ───────────────────────────────────────────────────────

def render_insights(expenses_df: pd.DataFrame, income_df: pd.DataFrame,
                    savings_df: pd.DataFrame, settings: dict, DC: str, rate: float):
    """Render the full insights page."""
    st.title("💡 Spending Insights")
    st.caption("Auto-generated observations about your finances — updated every time you open this page.")

    today   = date.today()
    year    = today.year
    month   = today.month
    cards   = []

    # ── Insight 1: Month-over-month spending ──────────────────────────────────
    if not expenses_df.empty:
        mom = month_over_month(expenses_df, "amount_eur", year, month)
        cur_str  = fmt(mom["current"],  DC, rate)
        prev_str = fmt(mom["previous"], DC, rate)
        pct      = abs(mom["change_pct"])
        if mom["trend"] == "up":
            cards.append(("warning", f"📈 You've spent **{cur_str}** this month — "
                          f"**{pct:.0f}% more** than last month ({prev_str}). "
                          f"Consider reviewing your recent expenses."))
        elif mom["trend"] == "down":
            cards.append(("success", f"📉 Great news! You've spent **{cur_str}** this month — "
                          f"**{pct:.0f}% less** than last month ({prev_str}). Keep it up!"))
        else:
            cards.append(("info", f"📊 Your spending this month (**{cur_str}**) "
                          f"is about the same as last month ({prev_str})."))

    # ── Insight 2: Top category ───────────────────────────────────────────────
    top = top_category_this_month(expenses_df, year, month)
    if top:
        cat, amt = top
        cat_mom = month_over_month(
            expenses_df[expenses_df["category"] == cat], "amount_eur", year, month
        )
        direction = f"up {cat_mom['change_pct']:.0f}%" if cat_mom["trend"] == "up" \
                    else (f"down {abs(cat_mom['change_pct']):.0f}%" if cat_mom["trend"] == "down" else "steady")
        cards.append(("info", f"🏆 Your biggest spend this month is **{cat}** "
                     f"at **{fmt(amt, DC, rate)}** — {direction} vs last month."))

    # ── Insight 3: Unusual expenses ───────────────────────────────────────────
    unusual = unusual_expenses(expenses_df, multiplier=2.5)
    this_month_unusual = unusual[
        (unusual["date"].dt.year == year) & (unusual["date"].dt.month == month)
    ] if not unusual.empty else pd.DataFrame()
    if not this_month_unusual.empty:
        row = this_month_unusual.nlargest(1, "amount_eur").iloc[0]
        cat_avg = float(expenses_df[expenses_df["category"] == row["category"]]["amount_eur"].mean())
        mult    = round(row["amount_eur"] / cat_avg, 1) if cat_avg > 0 else 0
        cards.append(("warning", f"⚠️ Unusual expense spotted: **{row['description']}** "
                     f"({row['category']}) for **{fmt(row['amount_eur'], DC, rate)}** — "
                     f"{mult}× your typical {row['category']} spend."))

    # ── Insight 4: Savings projections ────────────────────────────────────────
    if not savings_df.empty:
        for goal in savings_df["goal_name"].unique():
            proj = savings_projection(savings_df, goal)
            if proj["months_to_goal"] is not None and proj["months_to_goal"] > 0:
                proj_str = proj["projected_date"].strftime("%b %Y") if proj["projected_date"] else "?"
                bal_str  = fmt(proj["current_balance"], DC, rate)
                tgt_str  = fmt(proj["target"], DC, rate)
                cards.append(("success",
                    f"🏦 **{goal}**: Balance is **{bal_str}** of {tgt_str} target. "
                    f"At your current deposit rate you'll reach it in ~**{proj['months_to_goal']} months** "
                    f"({proj_str})."))
            elif proj["months_to_goal"] == 0:
                cards.append(("success", f"🎉 **{goal}** goal reached! "
                             f"Balance: {fmt(proj['current_balance'], DC, rate)} — "
                             f"congratulations!"))

    # ── Insight 5: Budget burn rate ───────────────────────────────────────────
    total_budget = float(settings.get("monthly_budget", 0.0))
    if total_budget > 0 and not expenses_df.empty:
        from datetime import date as _date
        period_start = _date(today.year, today.month, 1)
        days_left = days_until_budget_depleted(expenses_df, total_budget, period_start)
        if days_left is not None:
            if days_left == 0:
                cards.append(("error",
                    f"🚨 You've already exceeded your monthly budget of "
                    f"**{fmt(total_budget, DC, rate)}**! Time to slow down spending."))
            elif days_left < 7:
                cards.append(("warning",
                    f"⏰ At your current pace your budget of **{fmt(total_budget, DC, rate)}** "
                    f"will run out in ~**{days_left} days**. Consider cutting back."))
            else:
                days_in_month = calendar.monthrange(today.year, today.month)[1]
                days_remaining_month = days_in_month - today.day
                if days_left >= days_remaining_month:
                    cards.append(("success",
                        f"✅ You're on track — your budget should last the rest of the month "
                        f"({fmt(total_budget, DC, rate)} total)."))

    # ── Insight 6: Income vs expense ratio ────────────────────────────────────
    if not income_df.empty and not expenses_df.empty:
        inc_mom = month_over_month(income_df, "actual_eur", year, month)
        exp_mom = month_over_month(expenses_df, "amount_eur", year, month)
        if inc_mom["current"] > 0 and exp_mom["current"] > 0:
            ratio = exp_mom["current"] / inc_mom["current"] * 100
            saved_pct = 100 - ratio
            if saved_pct >= 20:
                cards.append(("success",
                    f"💪 You're saving **{saved_pct:.0f}%** of your income this month — excellent!"))
            elif saved_pct >= 10:
                cards.append(("info",
                    f"📊 You're saving **{saved_pct:.0f}%** of your income this month. "
                    f"Aim for 20% for a healthy savings rate."))
            elif saved_pct < 0:
                cards.append(("error",
                    f"💸 Your expenses (**{fmt(exp_mom['current'], DC, rate)}**) exceed your income "
                    f"(**{fmt(inc_mom['current'], DC, rate)}**) this month. Review your spending."))

    # ── Render cards ──────────────────────────────────────────────────────────
    if not cards:
        st.info("💡 Log some expenses and income to start seeing personalised insights here.")
        return

    for card_type, text in cards:
        if card_type == "success":
            st.success(text)
        elif card_type == "warning":
            st.warning(text)
        elif card_type == "error":
            st.error(text)
        else:
            st.info(text)

    # ── Month-over-month table ────────────────────────────────────────────────
    st.divider()
    st.subheader("📅 Month-over-month by category")
    if not expenses_df.empty:
        rows = []
        for cat in expenses_df["category"].unique():
            cat_df = expenses_df[expenses_df["category"] == cat]
            m = month_over_month(cat_df, "amount_eur", year, month)
            rows.append({
                "Category": cat,
                "This month": fmt(m["current"], DC, rate),
                "Last month": fmt(m["previous"], DC, rate),
                "Change": f"{'▲' if m['trend']=='up' else '▼' if m['trend']=='down' else '—'} "
                          f"{abs(m['change_pct']):.0f}%",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No expense data yet.")
