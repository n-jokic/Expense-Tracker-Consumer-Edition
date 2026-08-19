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
    help_expander, sortable_grouped_ids,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

st.title(":material/autorenew: Recurring expenses")
st.caption("One-click logging for monthly fixed costs — the actual amount may differ from the expected.")
help_expander("What are recurring expenses?",
              "These are fixed monthly costs like rent, subscriptions, or utilities. "
              "Add them here once with an optional due day and start month — then tap 'Log now' "
              "each month and adjust the amount if the real bill differs from the expected one. "
              "Editing a template later never rewrites expenses already logged.")

if (msg := st.session_state.pop("rec_flash", None)):
    st.success(msg, icon=":material/check_circle:")

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
                            value=0.01 if pd.isna(row["amount"]) else max(float(row["amount"]), 0.01),
                            min_value=0.01,
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


with st.expander("Add new template", icon=":material/add:"):
    oc, _ = st.columns([1, 3])
    with oc:
        rc = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="rec_cur")
    rcat = st.selectbox("Category", CAT_LIST, key="rec_cat")
    st.caption("Currency and category apply immediately.")
    with st.form("rec_form", clear_on_submit=True):
        rsym = get_currency_symbol(rc)
        ra1, ra2 = st.columns(2)
        with ra1:
            rsub  = st.selectbox("Subcategory", ["—"] + CATEGORIES[rcat])
            rdesc = st.text_input("Description", placeholder="e.g. Monthly gym membership")
        with ra2:
            ramt   = st.number_input(f"Typical amount ({rsym})", min_value=0.0,
                                     max_value=MAX_AMOUNT, step=0.50, format="%.2f",
                                     value=0.0)
            rdue   = st.number_input("Due day (0 = none)", min_value=0, max_value=31,
                                     value=0, step=1,
                                     help="Day of the month the bill is due, e.g. 15. "
                                          "Used to sort the checklist and send email reminders.")
        rnotes = st.text_input("Notes")
        rstart = st.date_input("Starts in (month)", value=today, format="YYYY/MM/DD",
                               help="The template only appears in the monthly checklist "
                                    "and reminders from this month onward (the day is ignored).")
        if st.form_submit_button("Save template", type="primary", width="stretch", icon=":material/save:"):
            if not rdesc.strip():
                st.error("Please add a description.")
            elif ramt <= 0:
                st.error("Typical amount must be greater than 0.")
            else:
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
                st.session_state["rec_flash"] = f"**{rdesc}** saved as a template."
                st.rerun()

dfr    = q.recurring(user_id)
active = dfr[dfr["active"] == True] if not dfr.empty else pd.DataFrame()
# Only templates whose start month has arrived are due.
active = filter_started_templates(active, today.year, today.month)

if active.empty:
    st.info("No active templates yet. Add one above, or tick 'Recurring' when logging an expense.")
else:
    st.subheader(f"Monthly checklist — {calendar.month_name[today.month]} {today.year}")

    def _persist_grouped_order(groups, rows):
        by_id = {str(row["id"]): row for _, row in rows.iterrows()}
        changed = False
        for category, item_ids in groups.items():
            valid_subcategories = set(CATEGORIES.get(category, []))
            for position, item_id in enumerate(item_ids):
                row = by_id.get(str(item_id))
                if row is None:
                    continue
                updates = {"sort_order": position}
                if str(row["category"]) != str(category):
                    updates["category"] = str(category)
                    current_subcategory = str(row.get("subcategory") or "")
                    if current_subcategory and current_subcategory not in valid_subcategories:
                        updates["subcategory"] = ""
                current_order = row.get("sort_order")
                order_changed = (pd.isna(current_order)
                                 or int(current_order) != position)
                category_changed = "category" in updates
                subcategory_changed = "subcategory" in updates
                if order_changed or category_changed or subcategory_changed:
                    update_recurring(user_id, str(item_id), updates)
                    changed = True
        if changed:
            q.bump_db_version()
            st.rerun()

    categories = {str(category) for category in active["category"].dropna()}
    category_order = [category for category in CAT_LIST if category in categories]
    category_order += sorted(categories - set(category_order))
    groups = {}
    for category in category_order:
        rows = active[active["category"] == category].copy()
        rows["_sort"] = pd.to_numeric(rows["sort_order"], errors="coerce").fillna(0)
        rows = rows.sort_values(["_sort", "due_day", "description"],
                                key=lambda s: s.fillna(32).astype(int)
                                if s.name == "due_day" else s,
                                na_position="last")
        groups[category] = [
            (str(row["id"]), str(row["description"])) for _, row in rows.iterrows()
        ]

    st.caption("Drag templates to reorder them or move them between categories.")
    ordered = sortable_grouped_ids(groups, f"recurring_order_{user_id}")
    _persist_grouped_order(ordered, active)
    rows_by_id = {str(row["id"]): row for _, row in active.iterrows()}
    ordered_ids = [item_id for category in ordered for item_id in ordered[category]]

    # One source of truth for "logged this month": the same helper the email/
    # sidebar reminders use, including the legacy description+amount fallback
    # for rows logged before rec_template_id links existed.
    from notifications import _unlogged_templates
    unlogged_ids = {str(r["id"]) for r in _unlogged_templates(active, dfe, today)}

    month_len = calendar.monthrange(today.year, today.month)[1]

    previous_category = None
    for item_id in ordered_ids:
        row = rows_by_id[item_id]
        if row["category"] != previous_category:
            st.markdown(f"**{row['category']}**")
            previous_category = row["category"]
        done = str(row["id"]) not in unlogged_ids
        rc1, rc2, rc3, rc4 = st.columns([3, 1.4, 1.6, 1.6])

        with rc1:
            ic = "✅" if done else "⏳"
            st.markdown(f"{ic} **{row['description']}**")
            st.caption(f"{row['category']}{' › ' + row['subcategory'] if row['subcategory'] else ''}")

        with rc2:
            st.metric("Expected", fmt(float(row["amount_eur"]), DC, rates),
                      label_visibility="collapsed")

        with rc3:
            # NB: the due/overdue state is only meaningful while the bill is
            # NOT logged this month — a logged bill must never show a stuck
            # "overdue" warning (regression).
            if done:
                st.caption("paid this month")
            else:
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
                else:
                    st.caption("no due day")

        with rc4:
            rid = str(row["id"])
            if done:
                st.success("Logged ✓")
            else:
                with st.popover("Log now", key=f"lr_{rid}"):
                    cur2  = str(row["currency"])
                    sym2  = get_currency_symbol(cur2)
                    st.markdown(f"**{row['description']}** — expected {fmt(float(row['amount_eur']), DC, rates)}")
                    p_date = st.date_input("Date", value=today, key=f"lr_d_{rid}")
                    p_amt  = st.number_input(f"Actual amount ({sym2})",
                                             value=float(row["amount"]),
                                             min_value=0.01, max_value=MAX_AMOUNT,
                                             step=0.50, format="%.2f", key=f"lr_a_{rid}")
                    if st.button("Log it", icon=":material/check:", key=f"lr_c_{rid}", type="primary", width="stretch"):
                        ae = to_eur(p_amt, cur2, rates)
                        add_expense(user_id, {
                            "date": p_date, "category": row["category"],
                            "subcategory": row["subcategory"],
                            "description": row["description"],
                            "amount": p_amt,
                            "currency": cur2,
                            "amount_eur": ae,
                            "recurring": True,
                            "rec_template_id": rid,
                            "notes": str(row.get("notes","")),
                        })
                        q.bump_db_version()
                        diff = float(p_amt) - float(row["amount"])
                        extra = ""
                        if abs(diff) > 0.005:
                            extra = f" ({'+' if diff > 0 else ''}{diff:,.2f} {cur2} vs expected)"
                        st.toast(f"✅ Logged {row['description']}: {p_amt:,.2f} {cur2}{extra}")
                        st.rerun()
            if st.button("Edit", icon=":material/edit:", key=f"ed_{rid}", width="stretch"):
                edit_template_dialog(user_id, row)
            if st.button("Remove", icon=":material/delete:", key=f"dr_{rid}", type="secondary", width="stretch"):
                update_recurring(user_id, row["id"], {"active": False})
                q.bump_db_version()
                st.rerun()
