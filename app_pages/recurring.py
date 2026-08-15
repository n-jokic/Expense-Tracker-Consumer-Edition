"""
Recurring expenses page: monthly templates with due days and a one-tap
"Log now" that lets you record the ACTUAL amount (may differ from expected).
"""

import calendar
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_expense, add_recurring, update_recurring
from utils import (
    CATEGORIES, CAT_LIST, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    fmt, to_eur, get_currency_symbol, filter_started_templates,
    help_expander,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

st.title("🔄 Recurring expenses")
st.caption("One-click logging for monthly fixed costs — the actual amount may differ from the expected.")
help_expander("What are recurring expenses?",
              "These are fixed monthly costs like rent, subscriptions, or utilities. "
              "Add them here once with an optional due day and start month — then tap 'Log now' "
              "each month and adjust the amount if the real bill differs from the expected one. "
              "Editing a template later never rewrites expenses already logged.")

dfe   = q.expenses(user_id)
today = date.today()


@st.dialog("Edit recurring template")
def edit_template_dialog(uid: int, row):
    """Edit a template's details. Past logged expenses keep their OWN copied
    amount/category/description (they only link back via rec_template_id),
    so editing never mutates history."""
    st.markdown(f"Editing **{row['description']}**")
    n_cat = st.selectbox(
        "Category", CAT_LIST,
        index=CAT_LIST.index(row["category"]) if row["category"] in CAT_LIST else 0)
    n_sub = st.selectbox(
        "Subcategory", ["—"] + CATEGORIES[n_cat],
        index=(list(["—"] + CATEGORIES[n_cat]).index(row["subcategory"])
               if row["subcategory"] in CATEGORIES[n_cat] else 0))
    n_desc = st.text_input("Description", value=str(row["description"]))
    cur3 = str(row["currency"])
    n_cur = st.selectbox(
        "Currency", list(SUPPORTED_CURRENCIES.keys()),
        index=list(SUPPORTED_CURRENCIES.keys()).index(cur3)
        if cur3 in SUPPORTED_CURRENCIES else 0)
    n_amt = st.number_input(f"Typical amount ({get_currency_symbol(n_cur)})",
                            value=float(row["amount"]), min_value=0.01,
                            max_value=MAX_AMOUNT, step=0.50, format="%.2f")
    dd_val = int(row["due_day"]) if row["due_day"] is not None and not pd.isna(row["due_day"]) else 0
    n_due = st.number_input("Due day (0 = none)", min_value=0, max_value=31,
                            value=dd_val, step=1)
    sm_date = date(today.year, today.month, 1)
    if row.get("start_month"):
        try:
            y, m = str(row["start_month"]).split("-")[:2]
            sm_date = date(int(y), int(m), 1)
        except (ValueError, TypeError):
            pass
    n_start = st.date_input("Starts in (month)", value=sm_date, format="YYYY/MM/DD",
                            help="Only the year and month matter — the day is ignored.")
    n_notes = st.text_input("Notes", value=str(row.get("notes") or ""))
    n_active = st.checkbox("Active", value=bool(row["active"]))
    st.caption("Editing the template never changes expenses already logged — "
               "they keep the amounts and categories they were saved with.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save changes", type="primary", width="stretch"):
            n_eur = to_eur(n_amt, n_cur, rates)
            update_recurring(uid, str(row["id"]), {
                "category": n_cat,
                "subcategory": n_sub if n_sub != "—" else "",
                "description": n_desc.strip() or row["description"],
                "amount": n_amt, "currency": n_cur, "amount_eur": n_eur,
                "due_day": int(n_due) if int(n_due) > 0 else None,
                "start_month": f"{n_start.year:04d}-{n_start.month:02d}",
                "notes": n_notes, "active": bool(n_active),
            })
            q.bump_db_version()
            st.toast("Template updated — past logs are untouched.", icon="✏️")
            st.rerun()


with st.expander("➕ Add new template"):
    oc, _ = st.columns([1, 3])
    with oc:
        rc = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="rec_cur")
    rcat = st.selectbox("Category", CAT_LIST, key="rec_cat")
    with st.form("rec_form", clear_on_submit=False):
        rsym = get_currency_symbol(rc)
        ra1, ra2 = st.columns(2)
        with ra1:
            rsub  = st.selectbox("Subcategory", ["—"] + CATEGORIES[rcat])
            rdesc = st.text_input("Description", placeholder="e.g. Monthly gym membership")
        with ra2:
            ramt   = st.number_input(f"Typical amount ({rsym})", min_value=0.01,
                                     max_value=MAX_AMOUNT, step=0.50, format="%.2f")
            rdue   = st.number_input("Due day (0 = none)", min_value=0, max_value=31,
                                     value=0, step=1,
                                     help="Day of the month the bill is due, e.g. 15. "
                                          "Used to sort the checklist and send email reminders.")
        rnotes = st.text_input("Notes")
        rstart = st.date_input("Starts in (month)", value=today, format="YYYY/MM/DD",
                               help="The template only appears in the monthly checklist "
                                    "and reminders from this month onward (the day is ignored).")
        if st.form_submit_button("💾 Save template", type="primary"):
            re_eur = to_eur(ramt, rc, rates)
            add_recurring(user_id, {
                "category": rcat,
                "subcategory": rsub if rsub != "—" else "",
                "description": rdesc, "amount": ramt,
                "currency": rc, "amount_eur": re_eur,
                "due_day": int(rdue) if rdue and int(rdue) > 0 else None,
                "start_month": f"{rstart.year:04d}-{rstart.month:02d}",
                "notes": rnotes, "active": True,
            })
            q.bump_db_version()
            st.success(f"✅ {rdesc} saved as template!")
            st.rerun()

dfr    = q.recurring(user_id)
active = dfr[dfr["active"] == True] if not dfr.empty else pd.DataFrame()
# Only templates whose start month has arrived are due.
active = filter_started_templates(active, today.year, today.month)

if active.empty:
    st.info("No active templates yet. Add one above, or tick '🔄 Recurring' when logging an expense.")
else:
    st.subheader(f"Monthly checklist — {calendar.month_name[today.month]} {today.year}")

    # Sort by due day (undated last); match logged bills via template link
    active = active.sort_values(
        by="due_day",
        key=lambda s: s.fillna(32).astype(int),
    )

    logged_ids = set()
    if not dfe.empty:
        tm = dfe[(dfe["date"].dt.year == today.year) & (dfe["date"].dt.month == today.month)]
        if "rec_template_id" in tm.columns:
            logged_ids = set(tm["rec_template_id"].dropna().astype(str))

    month_len = calendar.monthrange(today.year, today.month)[1]

    for idx, row in active.iterrows():
        done = str(row["id"]) in logged_ids
        rc1, rc2, rc3, rc4 = st.columns([3, 1.4, 1.6, 1.6])

        with rc1:
            ic = "✅" if done else "⏳"
            st.markdown(
                f"{ic} **{row['description']}**  \n"
                f"<span style='color:#888;font-size:12px;'>"
                f"{row['category']}{' › '+row['subcategory'] if row['subcategory'] else ''}"
                f"</span>", unsafe_allow_html=True
            )

        with rc2:
            st.write(fmt(float(row["amount_eur"]), DC, rates))

        with rc3:
            dd = row.get("due_day")
            if dd is not None and not pd.isna(dd) and int(dd) > 0:
                dd = int(dd)
                due_date = date(today.year, today.month, min(dd, month_len))
                days_left = (due_date - today).days
                if days_left < 0:
                    st.caption("⚠️ overdue")
                elif days_left == 0:
                    st.caption("⏰ due today")
                else:
                    st.caption(f"due {calendar.month_name[today.month]} {dd} · in {days_left}d")
            elif done:
                st.caption("")
            else:
                st.caption("no due day")

        with rc4:
            if done:
                st.success("Logged ✓")
            else:
                with st.popover("Log now", key=f"lr_{idx}"):
                    cur2  = str(row["currency"])
                    sym2  = get_currency_symbol(cur2)
                    st.markdown(f"**{row['description']}** — expected {fmt(float(row['amount_eur']), DC, rates)}")
                    p_date = st.date_input("Date", value=today, key=f"lr_d_{idx}")
                    p_amt  = st.number_input(f"Actual amount ({sym2})",
                                             value=float(row["amount"]),
                                             min_value=0.01, max_value=MAX_AMOUNT,
                                             step=0.50, format="%.2f", key=f"lr_a_{idx}")
                    if st.button("✅ Log it", key=f"lr_c_{idx}", type="primary", width="stretch"):
                        ae = to_eur(p_amt, cur2, rates)
                        add_expense(user_id, {
                            "date": p_date, "category": row["category"],
                            "subcategory": row["subcategory"],
                            "description": row["description"],
                            "amount": p_amt,
                            "currency": cur2,
                            "amount_eur": ae,
                            "recurring": True,
                            "rec_template_id": str(row["id"]),
                            "notes": str(row.get("notes","")),
                        })
                        q.bump_db_version()
                        diff = float(p_amt) - float(row["amount"])
                        extra = ""
                        if abs(diff) > 0.005:
                            extra = f" ({'+' if diff > 0 else ''}{diff:,.2f} {cur2} vs expected)"
                        st.toast(f"✅ Logged {row['description']}: {p_amt:,.2f} {cur2}{extra}")
                        st.rerun()
            if st.button(":material/edit: Edit", key=f"ed_{idx}", width="stretch"):
                edit_template_dialog(user_id, row)
            if st.button("Remove", key=f"dr_{idx}", type="secondary", width="stretch"):
                update_recurring(user_id, row["id"], {"active": False})
                q.bump_db_version()
                st.rerun()
        st.divider()
