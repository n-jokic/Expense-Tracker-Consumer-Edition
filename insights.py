"""
insights.py — Auto-generated spending insights for Expense Tracker v3.
Receives DataFrames; returns human-readable insight strings rendered in Streamlit.
"""

from datetime import date
import calendar
import math

import pandas as pd
import streamlit as st

from utils import (fmt, NEAR_LIMIT_THRESHOLD, DEFAULT_TRAVEL_CATEGORIES,
                   DEFAULT_FUN_CATEGORIES, get_currency_symbol, to_display)
from gamification import detect_raise
from forecasting import detect_anomalies, detect_subscriptions
import queries as q


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
        (expenses_df["date"].dt.date >= period_start)
        & (expenses_df["date"].dt.date <= today)   # future-dated rows don't count yet
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
    balance      = float(latest["balance_eur"]) if pd.notna(latest["balance_eur"]) else 0.0
    target       = float(rows["target_eur"].max()) if pd.notna(rows["target_eur"].max()) else 0.0
    interest_rate = float(latest["interest_rate"]) if pd.notna(latest["interest_rate"]) else 0.0

    if target <= 0 or balance >= target:
        return {"current_balance": balance, "target": target,
                "months_to_goal": 0, "projected_date": date.today()}

    # Average monthly deposit over the last 3 calendar months with deposits
    if len(rows) >= 2:
        monthly = (rows.set_index("date")["deposited_eur"]
                   .resample("MS").sum().dropna())
        if monthly.empty:
            monthly_dep = float(rows["deposited_eur"].mean())
        else:
            monthly_dep = float(monthly.tail(3).mean())
    else:
        monthly_dep = float(rows["deposited_eur"].mean())

    # Withdrawals (or zero/NaN deposits) can't reach the goal — no projection.
    if pd.isna(monthly_dep) or monthly_dep <= 0:
        return {"current_balance": balance, "target": target,
                "months_to_goal": None, "projected_date": None}

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
                    savings_df: pd.DataFrame, settings: dict, DC: str, rates: dict,
                    recurring_df: pd.DataFrame | None = None,
                    loans_df: pd.DataFrame | None = None,
                    user_id: int | None = None):
    """Render the full insights page."""
    st.title(":material/lightbulb: Spending Insights")
    st.caption("Auto-generated observations about your finances — updated every time you open this page.")

    SYM     = get_currency_symbol(DC)
    AMT_FMT = f"%.0f {SYM}" if DC in ("RSD", "HUF", "HRK") else f"{SYM}%.2f"

    today   = date.today()
    year    = today.year
    month   = today.month
    cards   = []

    # ── Insight 1: Month-over-month spending ──────────────────────────────────
    if not expenses_df.empty:
        mom = month_over_month(expenses_df, "amount_eur", year, month)
        cur_str  = fmt(mom["current"],  DC, rates)
        prev_str = fmt(mom["previous"], DC, rates)
        pct      = abs(mom["change_pct"])
        if mom["trend"] == "up":
            cards.append(("warning", f"You've spent **{cur_str}** this month — "
                          f"**{pct:.0f}% more** than last month ({prev_str}). "
                          f"Consider reviewing your recent expenses."))
        elif mom["trend"] == "down":
            cards.append(("success", f"Great news! You've spent **{cur_str}** this month — "
                          f"**{pct:.0f}% less** than last month ({prev_str}). Keep it up!"))
        else:
            cards.append(("info", f"Your spending this month (**{cur_str}**) "
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
        cards.append(("info", f"Your biggest spend this month is **{cat}** "
                     f"at **{fmt(amt, DC, rates)}** — {direction} vs last month."))

    # ── Insight 3: Unusual expenses ───────────────────────────────────────────
    unusual = unusual_expenses(expenses_df, multiplier=2.5)
    this_month_unusual = unusual[
        (unusual["date"].dt.year == year) & (unusual["date"].dt.month == month)
    ] if not unusual.empty else pd.DataFrame()
    if not this_month_unusual.empty:
        row = this_month_unusual.nlargest(1, "amount_eur").iloc[0]
        cat_avg = float(expenses_df[expenses_df["category"] == row["category"]]["amount_eur"].mean())
        mult    = round(row["amount_eur"] / cat_avg, 1) if cat_avg > 0 else 0
        cards.append(("warning", f"Unusual expense spotted: **{row['description']}** "
                     f"({row['category']}) for **{fmt(row['amount_eur'], DC, rates)}** — "
                     f"{mult}× your typical {row['category']} spend."))

    # ── Insight 4: Savings projections ────────────────────────────────────────
    if not savings_df.empty:
        for goal in savings_df["goal_name"].unique():
            proj = savings_projection(savings_df, goal)
            if proj["months_to_goal"] is not None and proj["months_to_goal"] > 0:
                proj_str = proj["projected_date"].strftime("%b %Y") if proj["projected_date"] else "?"
                bal_str  = fmt(proj["current_balance"], DC, rates)
                tgt_str  = fmt(proj["target"], DC, rates)
                cards.append(("success",
                    f"**{goal}**: Balance is **{bal_str}** of {tgt_str} target. "
                    f"At your current deposit rate you'll reach it in ~**{proj['months_to_goal']} months** "
                    f"({proj_str})."))
            elif proj["months_to_goal"] == 0:
                cards.append(("success", f"**{goal}** goal reached! "
                             f"Balance: {fmt(proj['current_balance'], DC, rates)} — "
                             f"congratulations!"))

    # ── Insight 5: Budget burn rate ───────────────────────────────────────────
    _bud_raw = float(settings.get("monthly_budget", 0.0))
    total_budget = _bud_raw if math.isfinite(_bud_raw) else 0.0
    if total_budget > 0 and not expenses_df.empty:
        from datetime import date as _date
        period_start = _date(today.year, today.month, 1)
        days_left = days_until_budget_depleted(expenses_df, total_budget, period_start)
        if days_left is not None:
            if days_left == 0:
                cards.append(("error",
                    f"You've already exceeded your monthly budget of "
                    f"**{fmt(total_budget, DC, rates)}**! Time to slow down spending."))
            elif days_left < 7:
                cards.append(("warning",
                    f"At your current pace your budget of **{fmt(total_budget, DC, rates)}** "
                    f"will run out in ~**{days_left} days**. Consider cutting back."))
            else:
                days_in_month = calendar.monthrange(today.year, today.month)[1]
                days_remaining_month = days_in_month - today.day
                if days_left >= days_remaining_month:
                    cards.append(("success",
                        f"You're on track — your budget should last the rest of the month "
                        f"({fmt(total_budget, DC, rates)} total)."))

    # ── Insight 6: Income vs expense ratio ────────────────────────────────────
    if not income_df.empty and not expenses_df.empty:
        inc_mom = month_over_month(income_df, "actual_eur", year, month)
        exp_mom = month_over_month(expenses_df, "amount_eur", year, month)
        if inc_mom["current"] > 0 and exp_mom["current"] > 0:
            ratio = exp_mom["current"] / inc_mom["current"] * 100
            saved_pct = 100 - ratio
            if saved_pct >= 20:
                cards.append(("success",
                    f"You're saving **{saved_pct:.0f}%** of your income this month — excellent!"))
            elif saved_pct >= 10:
                cards.append(("info",
                    f"You're saving **{saved_pct:.0f}%** of your income this month. "
                    f"Aim for 20% for a healthy savings rate."))
            elif saved_pct < 0:
                cards.append(("error",
                    f"Your expenses (**{fmt(exp_mom['current'], DC, rates)}**) exceed your income "
                    f"(**{fmt(inc_mom['current'], DC, rates)}**) this month. Review your spending."))

    # ── Insight 7: Top merchants this month ───────────────────────────────────
    if not expenses_df.empty:
        m_exp = expenses_df[(expenses_df["date"].dt.year == year) &
                            (expenses_df["date"].dt.month == month)]
        if not m_exp.empty:
            merch = m_exp.groupby("description")["amount_eur"].sum().nlargest(3)
            top_list = ", ".join(
                f"**{d}** ({fmt(a, DC, rates)})" for d, a in merch.items())
            cards.append(("info", f"Top spending this month: {top_list}."))

    # ── Insight 8: No-spend days ──────────────────────────────────────────────
    if not expenses_df.empty:
        m_exp = expenses_df[(expenses_df["date"].dt.year == year) &
                            (expenses_df["date"].dt.month == month)]
        if not m_exp.empty:
            m_exp = m_exp[m_exp["date"].dt.date <= today]  # ignore future-dated rows
            spent_days = set(m_exp["date"].dt.day.dropna())
            no_spend = today.day - len(spent_days)
            if no_spend >= 3:
                cards.append(("success",
                    f"You've had **{no_spend} no-spend days** so far this month. Nice discipline!"))

    # ── Insight 9: Fixed costs (recurring) ────────────────────────────────────
    if recurring_df is not None and not recurring_df.empty:
        rec_active = recurring_df[recurring_df["active"] == True]
        if not rec_active.empty:
            monthly = float(rec_active["amount_eur"].sum())
            cards.append(("info",
                f"Your fixed costs are **{fmt(monthly, DC, rates)}/month** "
                f"({fmt(monthly * 12, DC, rates)}/year) across {len(rec_active)} recurring bills."))

    # ── Insight 10: Income highlights (raise / bonus / types) ─────────────────
    if not income_df.empty:
        if detect_raise(income_df):
            sal = income_df[income_df["income_type"].fillna("Other") == "Salary"].sort_values("date")
            if len(sal) >= 2:
                latest, prev = float(sal.iloc[-1]["actual_eur"]), float(sal.iloc[-2]["actual_eur"])
                if latest > prev:
                    cards.append(("success",
                        f"Raise detected! Your latest salary is "
                        f"**{fmt(latest, DC, rates)}** "
                        f"(up from {fmt(prev, DC, rates)})."))

        bonus = income_df[income_df["income_type"].fillna("Other").isin(["Bonus / Raise", "Bonus"])]
        bonus = bonus[bonus["date"].dt.year == year]
        if not bonus.empty:
            cards.append(("success",
                f"Bonuses this year: **{fmt(float(bonus['actual_eur'].sum()), DC, rates)}**. "
                f"Nice extra!"))

        m_inc = income_df[(income_df["date"].dt.year == year) &
                          (income_df["date"].dt.month == month)]
        if not m_inc.empty:
            by_type = m_inc.groupby("income_type")["actual_eur"].sum()
            top_type, top_val = by_type.idxmax(), float(by_type.max())
            cards.append(("info",
                f"Income this month is led by **{top_type}** "
                f"({fmt(top_val, DC, rates)})."))

    # ── Insight 11: Last 30 days vs previous 30 days ──────────────────────────
    if not expenses_df.empty:
        recent = expenses_df[(expenses_df["date"] >= pd.Timestamp(today) - pd.Timedelta(days=30))
                             & (expenses_df["date"] <= pd.Timestamp(today))]
        prev   = expenses_df[(expenses_df["date"] < pd.Timestamp(today) - pd.Timedelta(days=30)) &
                             (expenses_df["date"] >= pd.Timestamp(today) - pd.Timedelta(days=60))]
        r_sum, p_sum = float(recent["amount_eur"].sum()), float(prev["amount_eur"].sum())
        if p_sum > 0:
            pct = (r_sum - p_sum) / p_sum * 100
            if abs(pct) >= 10:
                kind = "error" if pct > 0 else "success"
                direction = "more" if pct > 0 else "less"
                cards.append((kind,
                    f"You spent **{abs(pct):.0f}% {direction}** in the last 30 days "
                    f"({fmt(r_sum, DC, rates)}) than the 30 days before ({fmt(p_sum, DC, rates)})."))

    # ── Insight 12: Loans ─────────────────────────────────────────────────────
    if loans_df is not None and not loans_df.empty:
        from finance import annuity_payment
        active = loans_df[loans_df["status"] == "active"]
        if not active.empty:
            monthly_total = sum(
                annuity_payment(
                    float(r["principal_eur"]) if pd.notna(r["principal_eur"]) else 0.0,
                    float(r["annual_rate"]) if pd.notna(r["annual_rate"]) else 0.0,
                    int(r["term_months"]) if pd.notna(r["term_months"]) else 12)
                for _, r in active.iterrows())
            cards.append(("info",
                f"You have **{len(active)} active loan(s)** — "
                f"**{fmt(monthly_total, DC, rates)}/month** in scheduled payments."))

    # ── Insight 13: Fun money ─────────────────────────────────────────────────
    fun_money = float(settings.get("fun_money") or 0.0)
    if fun_money > 0 and not expenses_df.empty:
        from utils import fun_spent
        cats = settings.get("fun_categories") or DEFAULT_FUN_CATEGORIES
        spent = fun_spent(expenses_df, cats, year, month)
        if spent > fun_money:
            cards.append(("warning",
                f"Fun money exhausted: **{fmt(spent, DC, rates)}** of "
                f"{fmt(fun_money, DC, rates)} — {fmt(spent - fun_money, DC, rates)} over."))
        elif spent > fun_money * NEAR_LIMIT_THRESHOLD:
            cards.append(("warning",
                f"Fun money almost gone: {fmt(fun_money - spent, DC, rates)} left this month."))
        else:
            cards.append(("success",
                f"Fun money on track: **{fmt(fun_money - spent, DC, rates)}** left this month."))

    # ── Insight 14: Travel budget ─────────────────────────────────────────────
    travel_budget = float(settings.get("travel_budget") or 0.0)
    if travel_budget > 0 and not expenses_df.empty:
        from utils import travel_spent
        pairs = settings.get("travel_categories") or DEFAULT_TRAVEL_CATEGORIES
        spent_t = travel_spent(expenses_df, pairs, year)
        days_in_year = 366 if calendar.isleap(year) else 365
        year_pct = today.timetuple().tm_yday / days_in_year * 100
        budget_pct = spent_t / travel_budget * 100
        lvl = "error" if spent_t > travel_budget else ("warning" if budget_pct > year_pct else "info")
        cards.append((lvl,
            f"Travel budget: **{fmt(spent_t, DC, rates)}** of "
            f"{fmt(travel_budget, DC, rates)} this year ({budget_pct:.0f}% used, "
            f"{year_pct:.0f}% of the year gone)."))

    # ── Render cards ──────────────────────────────────────────────────────────
    if not cards:
        st.info("Log some expenses and income to start seeing personalised insights here.",
                icon=":material/lightbulb:")
        return

    for card_type, text in cards:
        if card_type == "success":
            st.success(text, icon=":material/check_circle:")
        elif card_type == "warning":
            st.warning(text, icon=":material/warning:")
        elif card_type == "error":
            st.error(text, icon=":material/error:")
        else:
            st.info(text, icon=":material/info:")

    # ── ML anomaly scan (IsolationForest; runs on the server) ─────────────────
    anomalies = detect_anomalies(expenses_df)
    if not anomalies.empty:
        st.subheader(":material/search: ML anomaly scan")
        st.caption("A lightweight isolation-forest model flags transactions that "
                   "don't fit your usual spending pattern.")
        show = anomalies.sort_values("date", ascending=False).head(10).copy()
        show["date"]   = show["date"].dt.strftime("%d %b %Y").fillna("")
        show["Amount"] = show["amount_eur"].apply(lambda x: to_display(x, DC, rates))
        show["Vs median"] = show["multiplier"].apply(lambda x: f"{x}×" if x is not None and x > 1 else "—")
        st.dataframe(
            show[["date","description","category","Amount","Vs median"]],
            hide_index=True,
            column_config={
                "Amount": st.column_config.NumberColumn("Amount", format=AMT_FMT),
            },
        )

    # ── Subscription / recurring detection ────────────────────────────────────
    subs = detect_subscriptions(expenses_df)
    if not subs.empty:
        from db import add_recurring
        st.subheader(":material/repeat: These look like subscriptions")
        st.caption("Regular monthly charges detected — add them as recurring "
                   "templates to get due-day reminders.")
        for idx, row in enumerate(subs.head(6).itertuples()):
            # itertuples gives named access to the subscription rows
            with st.container(horizontal=True):
                st.markdown(f"**{row.description}** · {row.months_seen} months "
                            f"· every ~{row.avg_gap_days:.0f} days")
                st.write(fmt(float(row.amount_eur), DC, rates))
                if user_id is None:
                    continue
                if st.button("Add", icon=":material/add:",
                             key=f"sub_{idx}_{row.last_date}", width="stretch"):
                    add_recurring(user_id, {
                        "category": row.category, "subcategory": "",
                        "description": str(row.description),
                        "amount": float(row.amount_eur),
                        "currency": "EUR", "amount_eur": float(row.amount_eur),
                        "due_day": None, "notes": "Detected from your spending",
                        "active": True,
                    })
                    q.bump_db_version()
                    st.toast(f"Added '{row.description}' to Recurring",
                             icon=":material/repeat:")
                    st.rerun()

    # ── Month-over-month table ────────────────────────────────────────────────
    st.subheader(":material/calendar_month: Month-over-month by category")
    if not expenses_df.empty:
        rows = []
        for cat in expenses_df["category"].unique():
            cat_df = expenses_df[expenses_df["category"] == cat]
            m = month_over_month(cat_df, "amount_eur", year, month)
            rows.append({
                "Category": cat,
                "This month": to_display(m["current"], DC, rates),
                "Last month": to_display(m["previous"], DC, rates),
                "Change": f"{'▲' if m['trend']=='up' else '▼' if m['trend']=='down' else '—'} "
                          f"{abs(m['change_pct']):.0f}%",
            })
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                column_config={
                    "This month": st.column_config.NumberColumn("This month", format=AMT_FMT),
                    "Last month": st.column_config.NumberColumn("Last month", format=AMT_FMT),
                },
            )
    else:
        st.info("No expense data yet.", icon=":material/table_chart:")
