"""
Big purchases page: wishlist items with a 4-quadrant priority matrix
(expected usage vs work-hours needed) and a "bought → expense" handoff.
"""

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from db import (
    add_big_purchase, update_big_purchase, delete_big_purchase, add_expense,
    BIG_STATUSES,
)
from utils import (
    CAT_LIST, SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
    QUADRANT_COLORS, classify_quadrant,
    fmt, fmt_row, to_eur, get_currency_symbol,
    help_expander,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()

st.title("🛍️ Big purchases")
st.caption("Decide what's worth it: how many work-hours it costs vs how much you'll actually use it.")
help_expander("How the matrix works",
              "Each item is placed on a 4-square matrix: the x-axis is how much you expect "
              "to use it (hours/month) and the y-axis is how many hours of work it costs "
              "(price ÷ your hourly rate). High use + low work = quick win; low use + high work "
              "= reconsider. Lines are drawn at the median of your items.")

hourly_rate = float(settings.get("hourly_rate") or 0.0)

# ── Hourly rate ───────────────────────────────────────────────────────────────
hr1, hr2 = st.columns([3, 1])
with hr1:
    new_rate = st.number_input("Your hourly rate (EUR) — used for work-hour math",
                               value=hourly_rate, min_value=0.0,
                               max_value=10_000.0, step=0.5, format="%.2f",
                               key="bp_hourly_rate")
with hr2:
    st.write("")
    if st.button("💾 Save rate", width="stretch", key="bp_save_rate"):
        q.save_settings(user_id, {"hourly_rate": float(new_rate)})
        st.rerun()

# ── Add form ──────────────────────────────────────────────────────────────────
with st.form("bp_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        bp_name = st.text_input("Item name", placeholder="e.g. New laptop")
        bp_cat  = st.selectbox("Category", CAT_LIST)
        bp_price = st.number_input("Price", min_value=0.01,
                                   max_value=MAX_SAVINGS_TARGET, step=10.0, format="%.2f")
    with c2:
        bp_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()))
        bp_use  = st.number_input("Expected use (hours / month)", min_value=0.0,
                                  step=1.0, format="%.1f",
                                  help="How many hours per month will you actually use it?")
        bp_imp  = st.slider("Importance", 1, 5, 3,
                            help="1 = nice to have · 5 = life-changing")
    bp_notes = st.text_input("Notes (optional)")
    if st.form_submit_button("➕ Add to wishlist", type="primary"):
        if bp_name.strip():
            pe = to_eur(bp_price, bp_cur, rates)
            add_big_purchase(user_id, {
                "name": bp_name.strip(), "category": bp_cat,
                "price": bp_price, "currency": bp_cur, "price_eur": pe,
                "usage_hours": float(bp_use), "importance": int(bp_imp),
                "status": "wishlist", "notes": bp_notes,
            })
            q.bump_db_version()
            st.success(f"✅ **{bp_name}** added to your wishlist!")
            st.rerun()
        else:
            st.error("Please give the item a name.")

# ── Matrix & list ─────────────────────────────────────────────────────────────
dfb = q.big_purchases(user_id)
if dfb.empty:
    st.info("No big purchases yet — add one above 👆")
    st.stop()

pending = dfb[dfb["status"] != "bought"] if not dfb.empty else pd.DataFrame()

if hourly_rate > 0 and not pending.empty:
    st.divider()
    st.subheader("🧭 Priority matrix")

    work = pending["price_eur"] / hourly_rate
    med_work  = float(work.median())
    med_usage = float(pending["usage_hours"].median())
    if len(pending) < 2:
        med_work, med_usage = 20.0, 10.0

    pending = pending.copy()
    pending["work_hours"] = work
    pending["quadrant"] = pending.apply(
        lambda r: classify_quadrant(r["work_hours"], r["usage_hours"],
                                    med_work, med_usage), axis=1)

    fig = px.scatter(
        pending, x="usage_hours", y="work_hours",
        color="quadrant", size="importance", size_max=26,
        hover_name="name", hover_data={"price_eur": ":.2f", "work_hours": ":.1f"},
        color_discrete_map=QUADRANT_COLORS,
        labels={"usage_hours": "Expected use (hours/month)",
                "work_hours": "Work-hours needed", "quadrant": "Priority"},
    )
    fig.add_vline(x=med_usage, line_dash="dash", line_color="#999",
                  annotation_text="median use")
    fig.add_hline(y=med_work, line_dash="dash", line_color="#999",
                  annotation_text="median work")
    fig.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")

    st.caption(
        "**Quadrants:** 🟢 Quick wins (use a lot, cheap in work-hours) · "
        "🔵 Plan & save (use a lot, expensive) · ⚪ Maybe later (little use, cheap) · "
        "🔴 Reconsider (little use, expensive)."
    )


@st.dialog("Confirm purchase")
def confirm_purchase_dialog(uid, purchase_id, name, category, amount, currency,
                            amount_eur, notes):
    """Confirm a wishlist item was bought: marks it bought and logs the expense."""
    st.write(f"Mark **{name}** as bought and log it as an expense?")
    st.caption(
        f"This will mark the item as **bought** and log a new expense of "
        f"**{amount:,.2f} {currency}** (≈ {fmt(amount_eur, DC, rates)}) on today's date."
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"bp_cancel_{purchase_id}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Confirm & log expense", key=f"bp_confirm_{purchase_id}",
                     type="primary", width="stretch"):
            update_big_purchase(uid, purchase_id, {"status": "bought"})
            add_expense(uid, {
                "date": today, "category": category, "subcategory": "",
                "description": f"{name} (big purchase)",
                "amount": float(amount), "currency": str(currency),
                "amount_eur": float(amount_eur),
                "recurring": False, "notes": str(notes) or "Big purchase",
            })
            q.bump_db_version()
            st.toast(f"✅ Logged **{name}** as an expense!", icon="🛍️")
            st.rerun()


@st.dialog("Edit wishlist item")
def edit_purchase_dialog(uid: int, row):
    """Edit wishlist item details; the status flow is unchanged."""
    st.caption("Editing the wishlist item does not change the expense already logged "
               "when it was bought (if any).")
    c1, c2 = st.columns(2)
    with c1:
        e_name = st.text_input("Item name", value=str(row["name"]), key="bp_edit_name")
        e_cat  = st.selectbox("Category", CAT_LIST,
                              index=CAT_LIST.index(str(row["category"]))
                              if str(row["category"]) in CAT_LIST else 0,
                              key="bp_edit_cat")
        e_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                              if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                              key="bp_edit_cur")
    with c2:
        e_price = st.number_input(f"Price ({get_currency_symbol(e_cur)})",
                                  min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                  step=10.0, format="%.2f",
                                  value=max(float(row["price"]), 0.01), key="bp_edit_price")
        e_use = st.number_input("Expected use (hours / month)", min_value=0.0,
                                step=1.0, format="%.1f",
                                value=float(row["usage_hours"]), key="bp_edit_use")
        e_imp = st.slider("Importance", 1, 5, int(row["importance"]),
                          help="1 = nice to have · 5 = life-changing", key="bp_edit_imp")
    e_notes = st.text_input("Notes (optional)",
                            value=str(row["notes"]) if pd.notna(row["notes"]) else "",
                            key="bp_edit_notes")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"bp_edit_cancel_{row['id']}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save", type="primary", key=f"bp_edit_save_{row['id']}", width="stretch"):
            if not e_name.strip():
                st.error("Please give the item a name.")
            else:
                pe = to_eur(float(e_price), e_cur, rates)
                update_big_purchase(uid, str(row["id"]), {
                    "name": e_name.strip(), "category": e_cat,
                    "price": float(e_price), "currency": e_cur, "price_eur": pe,
                    "usage_hours": float(e_use), "importance": int(e_imp),
                    "notes": e_notes,
                })
                q.bump_db_version()
                st.toast(f"**{e_name.strip()}** updated.", icon="✏️")
                st.rerun()


# ── Item list ─────────────────────────────────────────────────────────────────
st.divider()
st.subheader("📝 Wishlist items")
for _, row in dfb.iterrows():
    if hourly_rate > 0 and row["price_eur"] > 0:
        wh = float(row["price_eur"]) / hourly_rate
        work_str = f" · ≈ {wh:,.0f} h of work"
    else:
        work_str = ""

    status_icon = {"wishlist": "⭐", "saving": "🐷", "bought": "✅"}.get(row["status"], "⭐")

    l1, l2, l3, l4 = st.columns([3.2, 1.6, 1.6, 1.4])
    with l1:
        st.markdown(
            f"{status_icon} **{row['name']}**  \n"
            f"<span style='color:#888;font-size:12px;'>{row['category']} · "
            f"importance {int(row['importance'])}/5 · "
            f"use {float(row['usage_hours']):,.1f} h/mo{work_str}</span>",
            unsafe_allow_html=True)
    with l2:
        st.write(fmt_row(row["price_eur"], row["price"], row["currency"], DC, rates))

    with l3:
        new_status = st.selectbox(
            "Status", BIG_STATUSES,
            index=BIG_STATUSES.index(row["status"]) if row["status"] in BIG_STATUSES else 0,
            key=f"bp_status_{row['id']}", label_visibility="collapsed",
            on_change=lambda i=row["id"]: (
                update_big_purchase(user_id, i, {"status": st.session_state[f"bp_status_{i}"]}),
                q.bump_db_version(),
            ),
        )
    with l4:
        if row["status"] != "bought":
            if st.button("✅ Bought → log expense", key=f"bp_buy_{row['id']}", width="stretch"):
                confirm_purchase_dialog(
                    user_id, str(row["id"]), str(row["name"]), str(row["category"]),
                    float(row["price"]), str(row["currency"]), float(row["price_eur"]),
                    str(row.get("notes", "")),
                )
        if st.button(":material/edit: Edit", key=f"bp_edit_{row['id']}", width="stretch",
                     help="Edit this item"):
            edit_purchase_dialog(user_id, row)
        if st.button(":material/delete: Delete", key=f"bp_del_{row['id']}", width="stretch",
                     help="Delete this item"):
            delete_big_purchase(user_id, row["id"])
            q.bump_db_version()
            st.rerun()
    st.divider()
