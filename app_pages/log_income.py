"""
Log income page: salaried / hourly / bonus income with a fixed-salary setup.
Logging a salary above the stored fixed salary offers to record the raise.
"""

import calendar
import math
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_income, update_income, soft_delete_income, restore_income
from utils import (
    INCOME_SOURCES, INCOME_TYPES, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    fmt, fmt_dual, to_eur, get_currency_symbol,
    help_expander, to_excel,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()

help_expander("Salary, hourly & bonus income",
              "Set up your fixed salary once, then log it each month with one tap. "
              "Hourly work is logged as hours × rate. When a salary entry is higher "
              "than your stored salary, we offer to record it as a raise.")

# ── Fixed salary setup ────────────────────────────────────────────────────────
with st.expander("My fixed salary", icon=":material/work:"):
    with st.form("salary_setup"):
        s1, s2, s3, s4 = st.columns([2, 1.5, 1, 1])
        with s1:
            _sal_raw = float(settings.get("salary_amount") or 0.0)
            s_amt = st.number_input("Monthly salary amount",
                                    value=_sal_raw if math.isfinite(_sal_raw) else 0.0,
                                    min_value=0.0, max_value=MAX_AMOUNT,
                                    step=10.0, format="%.2f")
        with s2:
            s_cur_default = settings.get("salary_currency", "EUR")
            s_cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                                 index=list(SUPPORTED_CURRENCIES.keys()).index(s_cur_default)
                                 if s_cur_default in SUPPORTED_CURRENCIES else 0)
        with s3:
            s_day = st.number_input("Payday (day of month)",
                                    value=int(settings.get("salary_day") or 1),
                                    min_value=1, max_value=31, step=1)
        with s4:
            s_active = st.toggle("Active", value=bool(settings.get("salary_active", False)))
        if st.form_submit_button("Save salary", type="primary", width="stretch",
                                 icon=":material/save:"):
            try:
                q.save_settings(user_id, {
                    "salary_amount": float(s_amt), "salary_currency": s_cur,
                    "salary_day": int(s_day), "salary_active": bool(s_active),
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                st.success("Fixed salary saved!", icon=":material/check:")
                st.rerun()

salary_amount   = float(settings.get("salary_amount") or 0.0)
salary_currency = settings.get("salary_currency", "EUR")
salary_day      = int(settings.get("salary_day") or 1)
salary_active   = bool(settings.get("salary_active", False))

# ── One-tap "log my salary" ───────────────────────────────────────────────────
dfi = q.income(user_id)
salary_logged_this_month = False
if not dfi.empty:
    salary_logged_this_month = bool(
        ((dfi["income_type"].fillna("Other") == "Salary") &
         (dfi["date"].dt.year == today.year) &
         (dfi["date"].dt.month == today.month)).any()
    )

if salary_active and salary_amount > 0:
    _, qc = st.columns([3, 1.8])
    with qc:
        if salary_logged_this_month:
            st.success(f"Salary for {calendar.month_name[today.month]} logged",
                       icon=":material/check:")
        elif st.button("Log my salary for this month", width="stretch", key="log_salary_btn",
                       icon=":material/add:"):
            month_len = calendar.monthrange(today.year, today.month)[1]
            pay_date  = date(today.year, today.month, min(salary_day, month_len))
            ae = to_eur(salary_amount, salary_currency, rates)
            _fresh_sal = q.income(user_id)
            if not _fresh_sal.empty and (
                (_fresh_sal["date"].dt.date == pay_date) & (_fresh_sal["income_type"] == "Salary")
            ).any():
                st.toast("Salary already logged this month.", icon=":material/check:")
                st.rerun()
            try:
                add_income(user_id, {
                    "date": pay_date, "source": "Salary", "income_type": "Salary",
                    "hours": None, "rate": None,
                    "budgeted": salary_amount, "actual": salary_amount,
                    "currency": salary_currency, "budgeted_eur": ae, "actual_eur": ae,
                    "notes": "Fixed salary",
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.toast(f"Salary logged for {calendar.month_name[pay_date.month]}", icon=":material/work:")
                st.rerun()

# ── Entry form ────────────────────────────────────────────────────────────────
oc1, oc2 = st.columns([1, 1.5])
with oc1:
    inc_type = st.selectbox("Income type", INCOME_TYPES, key="inc_type")
with oc2:
    cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="inc_cur")
sym = get_currency_symbol(cur)

with st.form("inc_form", clear_on_submit=True):
    c1, c2 = st.columns(2)
    with c1:
        inc_date = st.date_input("Date", value=today)
        if inc_type == "Hourly":
            hours   = st.number_input("Hours worked", min_value=0.0, max_value=744.0,
                                      step=0.5, format="%.1f")
            hr_rate = st.number_input(f"Hourly rate ({sym})", min_value=0.0,
                                      max_value=MAX_AMOUNT, step=1.0, format="%.2f")
            computed = round(float(hours) * float(hr_rate), 2)
        elif inc_type in ("Freelance", "Investment", "Rental", "Other"):
            budgeted = st.number_input(f"Budgeted ({sym})", min_value=0.0,
                                       max_value=MAX_AMOUNT, step=10.0, format="%.2f")
    with c2:
        if inc_type == "Hourly":
            st.caption(f"Actual = {hours:,.1f} h × {hr_rate:,.2f} = **{computed:,.2f} {sym}**")
            actual = 0.0  # computed on save
        else:
            actual = st.number_input(f"Actual ({sym})", min_value=0.0,
                                     max_value=MAX_AMOUNT, step=10.0, format="%.2f")

    # inc_type/cur/salary vars are outside the form so these branches are safe.
    # The only same-form dependency was raise_cb gated on use_fixed/actual — we
    # render raise_cb whenever use_fixed is unticked (even if actual is still 0;
    # the save handler validates the threshold).
    use_fixed = False
    raise_cb  = False
    if inc_type == "Salary" and salary_active and salary_amount > 0:
        use_fixed = st.checkbox(
            f"Use my fixed salary ({fmt_dual(salary_amount, salary_currency, to_eur(salary_amount, salary_currency, rates))})",
            value=True)
        if not use_fixed:
            # Always render so unticking + typing actual > salary in one submit still shows the checkbox.
            raise_cb = st.checkbox("Update my fixed salary — this is a raise", value=True)

    notes = st.text_input("Notes")
    saved = st.form_submit_button("Save income", width="stretch", type="primary",
                                  icon=":material/save:")

if saved:
    month_len = calendar.monthrange(today.year, today.month)[1]

    if inc_type == "Hourly":
        actual_val   = computed
        budgeted_val = computed
        hours_val    = float(hours)
        rate_val     = float(hr_rate)
    elif inc_type == "Salary":
        hours_val = rate_val = None
        if use_fixed and salary_amount > 0:
            actual_val   = salary_amount
            budgeted_val = salary_amount
            cur          = salary_currency
            inc_date     = date(today.year, today.month, min(salary_day, month_len))
        else:
            actual_val   = float(actual)
            budgeted_val = actual_val
    else:
        hours_val    = rate_val = None
        actual_val   = float(actual)
        budgeted_val = float(budgeted) if inc_type in ("Freelance","Investment","Rental","Other") else actual_val

    be = to_eur(budgeted_val, cur, rates)
    ae = to_eur(actual_val,   cur, rates)
    _fresh_inc = q.income(user_id)
    if not _fresh_inc.empty and (
        (_fresh_inc["date"].dt.date == inc_date)
        & (_fresh_inc["income_type"] == inc_type)
        & (_fresh_inc["actual_eur"].round(2) == round(ae, 2))
    ).any():
        st.toast("Already saved — duplicate prevented.", icon=":material/check:")
        st.rerun()
    try:
        add_income(user_id, {
            "date": inc_date, "source": inc_type, "income_type": inc_type,
            "hours": hours_val, "rate": rate_val,
            "budgeted": budgeted_val, "actual": actual_val,
            "currency": cur, "budgeted_eur": be, "actual_eur": ae, "notes": notes,
        })
        if inc_type == "Salary" and raise_cb and not use_fixed and actual_val > salary_amount + 0.005:
            q.save_settings(user_id, {
                "salary_amount": actual_val, "salary_currency": cur,
                "salary_active": True,
            })
            st.toast("Raise recorded — fixed salary updated!", icon=":material/trending_up:")
    except Exception as e:
        st.error(f"Couldn't save: {e}")
    else:
        q.bump_db_version()
        st.success(f"{inc_type} — {fmt_dual(actual_val, cur, ae)}", icon=":material/check:")

# ── Edit income entry ─────────────────────────────────────────────────────────
@st.dialog("Edit income entry")
def edit_income_dialog(uid: int, row):
    """Edit one income entry in place — only this row changes."""
    st.caption("Editing an income entry updates only this entry — no other history changes.")

    e_date   = st.date_input("Date", value=row["date"].date() if pd.notna(row["date"]) else today,
                             key=f"inc_edit_date_{row['id']}")
    e_source = st.selectbox("Source", INCOME_SOURCES,
                            index=INCOME_SOURCES.index(str(row["source"]))
                            if str(row["source"]) in INCOME_SOURCES else 0,
                            key=f"inc_edit_source_{row['id']}")
    e_type   = st.selectbox("Income type", INCOME_TYPES,
                            index=INCOME_TYPES.index(str(row["income_type"]))
                            if str(row["income_type"]) in INCOME_TYPES else 0,
                            key=f"inc_edit_type_{row['id']}")
    e_cur    = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                            index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                            if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                            key=f"inc_edit_cur_{row['id']}")
    esym     = get_currency_symbol(e_cur)
    e_actual = st.number_input(f"Actual amount ({esym})", min_value=0.01,
                               max_value=MAX_AMOUNT, step=10.0, format="%.2f",
                               value=0.01 if pd.isna(row["actual"]) else max(float(row["actual"]), 0.01),
                               key=f"inc_edit_actual_{row['id']}")
    e_budgeted = st.number_input(f"Budgeted amount ({esym}) — optional", min_value=0.0,
                                 max_value=MAX_AMOUNT, step=10.0, format="%.2f",
                                 value=float(row["budgeted"]) if pd.notna(row["budgeted"]) else 0.0,
                                 key=f"inc_edit_budgeted_{row['id']}")
    e_notes  = st.text_input("Notes", value=str(row["notes"]) if pd.notna(row["notes"]) else "",
                             key=f"inc_edit_notes_{row['id']}")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"inc_edit_cancel_{row['id']}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save", type="primary", key=f"inc_edit_save_{row['id']}", width="stretch"):
            ae = to_eur(float(e_actual), e_cur, rates)
            be = to_eur(float(e_budgeted), e_cur, rates)
            try:
                update_income(uid, str(row["id"]), {
                    "date": e_date, "source": e_source, "income_type": e_type,
                    "actual": float(e_actual), "budgeted": float(e_budgeted),
                    "currency": e_cur, "actual_eur": ae, "budgeted_eur": be,
                    "notes": e_notes,
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.toast("Income entry updated.", icon=":material/edit:")
            st.rerun()

# ── History ───────────────────────────────────────────────────────────────────
dfi = q.income(user_id)
if not dfi.empty:
    st.subheader("Income history")

    tfilt = st.multiselect("Type filter", INCOME_TYPES, key="inc_tfilt")

    d = dfi.sort_values("date", ascending=False).head(50).copy()
    if tfilt:
        d = d[d["income_type"].isin(tfilt)]

    d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
    d["Type"]     = d["income_type"].fillna("Other")
    d["Budgeted"] = d["budgeted_eur"].apply(lambda x: fmt(x, DC, rates))
    d["Actual"]   = d["actual_eur"].apply(lambda x: fmt(x, DC, rates))
    d["Amount (original)"] = d.apply(lambda r: fmt_dual(r["actual"], r["currency"], r["actual_eur"]), axis=1)
    d = d.rename(columns={"notes": "Notes"})
    st.dataframe(d[["Date","Type","Budgeted","Actual","Amount (original)","Notes"]], hide_index=True)

    with st.expander("Delete an income entry", icon=":material/delete:"):
        del_ids = dfi["id"].tolist()
        del_labels = [f"{r['date'].strftime('%d %b %Y') if pd.notna(r['date']) else '—'} — {r['income_type']} {fmt(r['actual_eur'], DC, rates)}"
                      for _, r in dfi.iterrows()]
        sel_idx = st.selectbox("Select entry", range(len(del_labels)),
                               format_func=lambda i: del_labels[i], key="inc_del_sel")
        if st.button("Move to trash", type="secondary", key="inc_del_btn", width="stretch",
                     icon=":material/delete:"):
            try:
                soft_delete_income(user_id, del_ids[sel_idx])
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.toast("Income entry moved to trash.", icon=":material/delete:")
                st.rerun()

    with st.expander("Edit an income entry", icon=":material/edit:"):
        edit_ids = dfi["id"].tolist()
        edit_labels = [f"{r['date'].strftime('%d %b %Y') if pd.notna(r['date']) else '—'} — {r['income_type']} {fmt(r['actual_eur'], DC, rates)}"
                       for _, r in dfi.iterrows()]
        edit_idx = st.selectbox("Select entry", range(len(edit_labels)),
                                format_func=lambda i: edit_labels[i], key="inc_edit_sel")
        if st.button("Edit", key="inc_edit_btn", width="stretch", icon=":material/edit:"):
            edit_income_dialog(user_id, dfi.iloc[edit_idx])

    df_deleted = q.income(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    if not df_deleted.empty:
        with st.expander(f"Recently deleted income ({len(df_deleted)})", icon=":material/delete:"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['income_type']} — {row['date'].strftime('%d %b %Y') if pd.notna(row['date']) else '—'}")
                with rc2: st.write(fmt(row["actual_eur"], DC, rates))
                with rc3:
                    if st.button("Restore", key=f"rst_inc_{row['id']}", width="stretch",
                                 icon=":material/undo:"):
                        try:
                            restore_income(user_id, row["id"])
                        except Exception as e:
                            st.error(f"Couldn't save: {e}")
                        else:
                            q.bump_db_version()
                            st.toast("Income entry restored!", icon=":material/undo:")
                            st.rerun()

    with st.expander("Export", icon=":material/download:"):
        st.download_button("Download income.xlsx", data=to_excel(dfi),
                           file_name="income.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:")
else:
    st.caption("No income entries yet — add your first one above.")
