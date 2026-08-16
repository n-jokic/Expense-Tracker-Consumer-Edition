"""
Rewards page: fun money, milestones/badges, streaks, and unlocks.

Moved out of Settings so gamification is front and center (Play → Rewards).
All edits here only touch settings (fun allowance / category pool) — the
milestone computation itself stays in gamification.py.
"""

import math
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import get_session, UserMilestone
from gamification import (MILESTONES, get_earned_milestones,
                          get_logging_streak, _next_milestone_hint)
from utils import (CAT_LIST, DEFAULT_FUN_CATEGORIES,
                   fmt, to_eur, to_display, fun_spent)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings

st.title(":material/workspace_premium: Rewards & badges")

dfe   = q.expenses(user_id)
dfi   = q.income(user_id)
dfs   = q.savings(user_id)
dfb   = q.budgets(user_id)
today = date.today()

earned = get_earned_milestones(dfe, dfi, dfs, dfb, settings=settings,
                               loans_df=q.loans(user_id))
earned_ids = {m["id"] for m in earned}

with get_session() as s:
    rows = s.query(UserMilestone).filter(UserMilestone.user_id == user_id).all()
earned_dates = {r.milestone_id: r.earned_at for r in rows}


# ── Fun money ─────────────────────────────────────────────────────────────────
st.subheader(":material/celebration: Fun money")
st.caption("A monthly allowance for guilt-free spending (Entertainment, eating "
           "out, hobbies…). Tracked on the Dashboard and Insights.")

fun_allowance = float(settings.get("fun_money") or 0.0)
if not math.isfinite(fun_allowance):
    fun_allowance = 0.0
fun_cats = settings.get("fun_categories") or DEFAULT_FUN_CATEGORIES
fun_month = fun_spent(dfe, fun_cats, today.year, today.month)

month_key = f"{today.year:04d}-{today.month:02d}"
bonuses_map = settings.get("fun_bonuses") or {}
bonus = float(bonuses_map.get(month_key, 0.0) or 0.0)
if bonus <= 0 and settings.get("fun_bonus_month") == month_key:
    bonus = float(settings.get("fun_bonus_amount") or 0.0)
allowance = fun_allowance + bonus
if allowance > 0:
    pct = min(fun_month / allowance, 1.0)
    st.markdown(f"**{fmt(fun_month, DC, rates)}** of {fmt(allowance, DC, rates)} "
                f"({pct * 100:.0f}%) spent this month"
                + (f" · incl. +{fmt(bonus, DC, rates)} milestone bonus" if bonus > 0 else ""))
    st.progress(pct)
else:
    st.caption("Set your monthly fun-money allowance below to start tracking it.")

with st.form("fun_form"):
    _fun_raw = float(settings.get("fun_money") or 0.0)
    f_amt = st.number_input(f"Monthly fun money ({DC})", min_value=0.0,
                            step=10.0, format="%.2f",
                            value=to_display(_fun_raw if math.isfinite(_fun_raw) else 0.0,
                                             DC, rates))
    f_cats = st.multiselect("Categories in the fun pool", CAT_LIST,
                            default=[c for c in (settings.get("fun_categories")
                                                 or DEFAULT_FUN_CATEGORIES)
                                     if c in CAT_LIST])
    if st.form_submit_button("Save fun money", type="primary", icon=":material/save:"):
        q.save_settings(user_id, {"fun_money": float(to_eur(f_amt, DC, rates)),
                                  "fun_categories": f_cats})
        st.success("✅ Fun money saved!")
        st.rerun()

if bonuses_map:
    if month_key in bonuses_map:
        st.success(f"🎁 Milestone bonus active this month: "
                   f"+{fmt(float(bonuses_map[month_key]), DC, rates)} fun money!")
    queued = sorted(k for k in bonuses_map if k != month_key)
    if queued:
        st.caption("🎁 Bonus queued for " + ", ".join(queued)
                   + f": +{fmt(sum(float(bonuses_map[k]) for k in queued), DC, rates)} fun money.")
else:
    legacy_bonus = float(settings.get("fun_bonus_amount") or 0.0)
    legacy_month = settings.get("fun_bonus_month")
    if legacy_bonus > 0:
        if legacy_month == month_key:
            st.success(f"🎁 Milestone bonus active this month: "
                       f"+{fmt(legacy_bonus, DC, rates)} fun money!")
        else:
            st.caption(f"🎁 Milestone bonus queued for {legacy_month}: "
                       f"+{fmt(legacy_bonus, DC, rates)} fun money.")


# ── Streak ────────────────────────────────────────────────────────────────────
st.subheader(":material/local_fire_department: Streak")
streak = get_logging_streak(dfe)
best = 0
if not dfe.empty and "date" in dfe.columns:
    ds = sorted({d.date() for d in dfe["date"].dropna()})
    cur = 0
    prev = None
    for d in ds:
        cur = cur + 1 if (prev is not None and (d - prev).days == 1) else 1
        best = max(best, cur)
        prev = d
s1, s2 = st.columns(2)
s1.metric("Current streak", f"{streak} day{'s' if streak != 1 else ''}")
s2.metric("Best streak", f"{best} day{'s' if best != 1 else ''}")

hint = _next_milestone_hint(dfe, earned_ids)
if hint:
    st.caption(f"💡 {hint}")


# ── Badge wall ────────────────────────────────────────────────────────────────
st.subheader(f":material/military_tech: Badge wall ({len(earned_ids)}/{len(MILESTONES)})")

def _progress_hints():
    hints = {}
    n_exp = len(dfe)
    hints["first_expense"] = f"{min(n_exp, 1)}/1"
    hints["expenses_50"] = f"{min(n_exp, 50)}/50"
    hints["expenses_200"] = f"{min(n_exp, 200)}/200"
    hints["week_streak"] = f"{min(streak, 7)}/7"
    hints["month_streak"] = f"{min(streak, 30)}/30"
    if not dfe.empty and "category" in dfe.columns:
        nc = dfe["category"].nunique()
        hints["category_explorer"] = f"{min(int(nc), 10)}/10"
    n_inc = len(dfi)
    hints["first_income"] = f"{min(n_inc, 1)}/1"
    if not dfi.empty and "income_type" in dfi.columns:
        for mid, itype in (("first_salary", "Salary"),
                           ("first_bonus", "Bonus / Raise"),
                           ("first_hourly", "Hourly")):
            hints[mid] = f"{min(int((dfi['income_type'] == itype).sum()), 1)}/1"
    hints["first_budget"] = f"{min(len(dfb), 1)}/1"
    bal = 0.0
    if not dfs.empty and "balance_eur" in dfs.columns:
        vals = dfs["balance_eur"].dropna()
        bal = float(vals.max()) if not vals.empty else 0.0
    for mid, cap in (("saver_100", 100), ("saver_1000", 1000), ("saver_10000", 10000)):
        hints[mid] = f"{min(bal / cap, 1.0):.0%}"
    return hints

hints = _progress_hints()
cols = st.columns(4)
for i, m in enumerate(MILESTONES):
    with cols[i % 4]:
        with st.container(border=True):
            got = m["id"] in earned_ids
            st.markdown(f"{m['icon']} **{m['title']}**" if got
                        else f"🔒 **{m['title']}**")
            if got:
                when = earned_dates.get(m["id"])
                when_str = (f" · {when.strftime('%d %b %Y')}"
                            if when is not None else "")
                st.caption(f"Earned{when_str}"
                           + (f" · +{m['reward']:.0f} fun money" if m.get("reward") else ""))
            else:
                prog = hints.get(m["id"])
                st.caption(f"{m['desc']}" + (f" — {prog}" if prog else ""))


# ── Recent unlocks ────────────────────────────────────────────────────────────
st.subheader(":material/notifications_active: Recent unlocks")
recent = sorted(earned_dates.items(), key=lambda kv: kv[1] or pd.Timestamp.min,
                reverse=True)[:6]
if recent:
    for mid, when in recent:
        if mid in {m["id"] for m in MILESTONES}:
            m = next(m for m in MILESTONES if m["id"] == mid)
            when_str = when.strftime("%d %b %Y") if when is not None else "?"
            st.write(f"{m['icon']} **{m['title']}** — {when_str}")
else:
    st.caption("No badges unlocked yet — log your first expense to start!")


# Link back to where budgets live (keeps navigation obvious).
st.caption("Looking for budgets? They moved to **Plan → Budgets**.")
