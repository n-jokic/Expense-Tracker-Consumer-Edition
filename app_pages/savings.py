"""
Savings goals page: deposits with monthly compound interest, goal progress and charts.
"""

from datetime import date

import plotly.express as px
import streamlit as st

import queries as q
from db import add_savings, soft_delete_savings, restore_savings
from insights import savings_projection
from utils import (
    SAVINGS_GOALS, SUPPORTED_CURRENCIES, MAX_AMOUNT, MAX_SAVINGS_TARGET, CHART_COLORS,
    fmt, pbar, to_display, to_eur, get_currency_symbol,
    help_expander, to_excel,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
SYM     = get_currency_symbol(DC)

st.title("🎯 Savings goals")
st.caption("Log each deposit. Compound interest is calculated monthly on your running balance.")
help_expander("How compound interest works",
              "Each time you log a deposit, the app calculates: "
              "`new balance = previous_balance × (1 + monthly_rate) + deposit`. "
              "The monthly rate is your annual interest rate ÷ 12, and interest is applied "
              "for each whole month between deposits.")

oc, _ = st.columns([1, 3])
with oc:
    cur = st.selectbox("Save in", list(SUPPORTED_CURRENCIES.keys()), key="sav_cur")
sym = get_currency_symbol(cur)

with st.form("sav_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        sd  = st.date_input("Date", value=date.today())
        gn  = st.selectbox("Goal", SAVINGS_GOALS)
        tgt = st.number_input(f"Target ({sym})", min_value=0.0,
                              max_value=MAX_SAVINGS_TARGET, step=100.0, format="%.2f")
    with c2:
        dep  = st.number_input(f"Amount deposited ({sym})", min_value=0.0,
                               max_value=MAX_AMOUNT, step=10.0, format="%.2f")
        ir   = st.number_input("Annual interest rate (%)", min_value=0.0,
                               max_value=100.0, step=0.01, format="%.2f",
                               help="e.g. 4.50 for 4.5% p.a., compounded monthly")
    notes = st.text_input("Notes")
    saved = st.form_submit_button("✅ Save entry", width="stretch", type="primary")

if saved:
    de = to_eur(dep, cur, rates)
    te = to_eur(tgt, cur, rates)
    dfs_prev = q.savings(user_id)
    pb = 0.0
    if not dfs_prev.empty:
        pr = dfs_prev[dfs_prev["goal_name"] == gn]
        if not pr.empty:
            pb = float(pr.sort_values("date").iloc[-1]["balance_eur"])
    mr = (ir / 100) / 12
    nb = round(pb * (1 + mr) + de, 4)
    add_savings(user_id, {
        "date": sd, "goal_name": gn, "target_eur": te,
        "deposited": dep, "currency": cur,
        "deposited_eur": de, "interest_rate": ir, "balance_eur": nb, "notes": notes,
    })
    q.bump_db_version()
    st.success(f"✅ {gn} — new balance: {fmt(nb, DC, rates)}")

dfs = q.savings(user_id)
if not dfs.empty:
    st.divider()
    st.subheader("Goal progress")
    for g in dfs["goal_name"].unique():
        rows  = dfs[dfs["goal_name"] == g].sort_values("date")
        lat   = rows.iloc[-1]
        bal   = float(lat["balance_eur"])
        td    = float(rows["deposited_eur"].sum())
        interest = bal - td
        tr    = rows[rows["target_eur"] > 0]
        tgtv  = float(tr["target_eur"].iloc[-1]) if not tr.empty else 0
        pct   = min(bal / tgtv * 100, 100) if tgtv > 0 else 0
        col   = "#00B050" if pct >= 75 else ("#F4A261" if pct >= 40 else "#E94560")

        gc1, gc2 = st.columns([4, 1])
        with gc1:
            st.markdown(f"**{g}**")
            st.markdown(pbar(pct, col), unsafe_allow_html=True)
            proj = savings_projection(dfs, g)
            proj_str = ""
            if proj["months_to_goal"] and proj["months_to_goal"] > 0 and proj["projected_date"]:
                proj_str = f" · 🎯 Goal in ~{proj['months_to_goal']}mo ({proj['projected_date'].strftime('%b %Y')})"
            st.caption(
                f"Balance: **{fmt(bal, DC, rates)}** · "
                f"Target: {fmt(tgtv, DC, rates) if tgtv > 0 else '—'} · "
                f"Interest earned: {fmt(interest, DC, rates)} · "
                f"Rate: {lat['interest_rate']:.2f}%"
                + proj_str
            )
        with gc2:
            st.metric("", f"{pct:.1f}%" if tgtv > 0 else "—")
        st.write("")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Balance over time")
        fig = px.line(dfs, x="date",
                      y=dfs["balance_eur"].apply(lambda x: to_display(x, DC, rates)),
                      color="goal_name", markers=True,
                      labels={"y": f"Balance ({SYM})", "date": "Date", "goal_name": "Goal"},
                      color_discrete_sequence=CHART_COLORS)
        fig.update_layout(legend_title_text="",
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.subheader("Interest rate over time")
        fig2 = px.line(dfs, x="date", y="interest_rate", color="goal_name", markers=True,
                       labels={"interest_rate": "Annual Rate (%)", "date": "Date"},
                       color_discrete_sequence=CHART_COLORS)
        fig2.update_layout(legend_title_text="",
                           plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig2, width="stretch")

    with st.expander("🗑️ Delete a savings entry"):
        del_ids = dfs["id"].tolist()
        del_labels = [f"{r['date'].strftime('%d %b %Y')} — {r['goal_name']} {fmt(r['deposited_eur'], DC, rates)}"
                      for _, r in dfs.iterrows()]
        sel = st.selectbox("Select entry", del_labels, key="sav_del_sel")
        if st.button("🗑️ Move to trash", type="secondary", key="sav_del_btn", width="stretch"):
            soft_delete_savings(user_id, del_ids[del_labels.index(sel)])
            q.bump_db_version()
            st.toast("Savings entry moved to trash.", icon="🗑️")
            st.rerun()

    df_deleted = q.savings(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    if not df_deleted.empty:
        with st.expander(f"🗑️ Recently deleted savings ({len(df_deleted)})"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['goal_name']} — {row['date'].strftime('%d %b %Y')}")
                with rc2: st.write(fmt(row["deposited_eur"], DC, rates))
                with rc3:
                    if st.button("↩️ Restore", key=f"rst_sav_{row['id']}", width="stretch"):
                        restore_savings(user_id, row["id"])
                        q.bump_db_version()
                        st.toast("Savings entry restored!", icon="↩️")
                        st.rerun()

    with st.expander("📥 Export"):
        st.download_button("⬇️ Download savings.xlsx", data=to_excel(dfs),
                           file_name="savings.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("No savings yet — log your first deposit above 👆")
