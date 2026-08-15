"""
Log income page: entry form, history, trash & restore.
"""

from datetime import date

import streamlit as st

import queries as q
from db import add_income, soft_delete_income, restore_income
from utils import (
    INCOME_SOURCES, SUPPORTED_CURRENCIES, MAX_AMOUNT,
    fmt, fmt_dual, to_eur, get_currency_symbol,
    help_expander, to_excel,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates

st.title("💵 Log income")
help_expander("Budgeted vs actual", "Enter what you *expected* to earn (budgeted) "
              "and what you *actually* received. This powers your savings rate calculations.")

oc, _ = st.columns([1, 3])
with oc:
    cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="inc_cur")
sym = get_currency_symbol(cur)

with st.form("inc_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        inc_date = st.date_input("Date", value=date.today())
        source   = st.selectbox("Source", INCOME_SOURCES)
    with c2:
        budgeted = st.number_input(f"Budgeted ({sym})", min_value=0.0,
                                   max_value=MAX_AMOUNT, step=10.0, format="%.2f")
        actual   = st.number_input(f"Actual ({sym})", min_value=0.0,
                                   max_value=MAX_AMOUNT, step=10.0, format="%.2f")
    notes = st.text_input("Notes")
    saved = st.form_submit_button("✅ Save income", width="stretch", type="primary")

if saved:
    be = to_eur(budgeted, cur, rates)
    ae = to_eur(actual,   cur, rates)
    add_income(user_id, {
        "date": inc_date, "source": source,
        "budgeted": budgeted, "actual": actual,
        "currency": cur, "budgeted_eur": be, "actual_eur": ae, "notes": notes,
    })
    q.bump_db_version()
    st.success(f"✅ {source} — {fmt_dual(actual, cur, ae)}")

dfi = q.income(user_id)
if not dfi.empty:
    st.divider()
    st.subheader("Income history")
    d = dfi.sort_values("date", ascending=False).head(30).copy()
    d["Date"]     = d["date"].dt.strftime("%d %b %Y").fillna("")
    d["Budgeted"] = d["budgeted_eur"].apply(lambda x: fmt(x, DC, rates))
    d["Actual"]   = d["actual_eur"].apply(lambda x: fmt(x, DC, rates))
    d["Original"] = d.apply(lambda r: fmt_dual(r["actual"], r["currency"], r["actual_eur"]), axis=1)
    st.dataframe(d[["Date","source","Budgeted","Actual","Original","notes"]], hide_index=True)

    with st.expander("🗑️ Delete an income entry"):
        del_ids = dfi["id"].tolist()
        del_labels = [f"{r['date'].strftime('%d %b %Y')} — {r['source']} {fmt(r['actual_eur'], DC, rates)}"
                      for _, r in dfi.iterrows()]
        sel = st.selectbox("Select entry", del_labels, key="inc_del_sel")
        if st.button("🗑️ Move to trash", type="secondary", key="inc_del_btn", width="stretch"):
            sel_idx = del_labels.index(sel)
            soft_delete_income(user_id, del_ids[sel_idx])
            q.bump_db_version()
            st.toast("Income entry moved to trash.", icon="🗑️")
            st.rerun()

    # Restore deleted income entries
    df_deleted = q.income(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    if not df_deleted.empty:
        with st.expander(f"🗑️ Recently deleted income ({len(df_deleted)})"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['source']} — {row['date'].strftime('%d %b %Y')}")
                with rc2: st.write(fmt(row["actual_eur"], DC, rates))
                with rc3:
                    if st.button("↩️ Restore", key=f"rst_inc_{row['id']}", width="stretch"):
                        restore_income(user_id, row["id"])
                        q.bump_db_version()
                        st.toast("Income entry restored!", icon="↩️")
                        st.rerun()

    with st.expander("📥 Export"):
        st.download_button("⬇️ Download income.xlsx", data=to_excel(dfi),
                           file_name="income.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("No income entries yet — add your first one above 👆")
