"""
Big purchases page: wishlist items with a 4-quadrant priority matrix
(expected usage vs work-hours needed), optional savings-goal funding
links (FIN-06) and an atomic buy/refund flow (FIN-07).

Money rules: buying and refunding go through services/purchase_commands.py
(one transaction = one audit group = one revision bump). The free status
selector cannot reach "bought" — the buy command is the only path.
"""

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from db import (
    add_big_purchase, update_big_purchase, delete_big_purchase,
)
from finance import derive_hourly_rate
from services.commands import CommandError
from services.purchase_commands import (
    FUNDING_SAVINGS_GOAL, FUNDING_UNALLOCATED,
    SELECTABLE_STATUSES, buy_wishlist_item, create_wishlist_target,
    funding_summary, is_selectable_status, refund_wishlist_item,
    resolve_linked_goal_name, set_purchase_funding,
)
from utils import (
    CAT_LIST, SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
    QUADRANT_COLORS, classify_quadrant,
    fmt, fmt_row, to_eur, get_currency_symbol,
    help_expander, draggable_card_board,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()

_SELECTABLE = list(SELECTABLE_STATUSES)

def _bp_update_status(item_id: str):
    value = st.session_state[f"bp_status_{item_id}"]
    # FIN-07: arbitrary status changes cannot bypass the buy command.
    if not is_selectable_status(value):
        st.error("'bought' can only be set through the buy flow.")
        return
    try:
        update_big_purchase(user_id, item_id, {"status": value})
    except Exception as e:
        st.error(f"Couldn't save: {e}")
        return
    q.bump_db_version()


def _bump_to_revision(res):
    """Adopt the command's revision so queries refresh immediately."""
    if res.revision is not None:
        try:
            st.session_state.db_version = int(res.revision)
            st.session_state["_snap_version"] = int(res.revision)
        except Exception:
            pass


def _goal_choices() -> dict[str, str]:
    """Existing savings goals -> stable anchor reference (earliest row id)."""
    try:
        dfg = q.savings(user_id)
    except Exception:
        return {}
    if dfg.empty:
        return {}
    choices: dict[str, str] = {}
    for _, r in dfg.iterrows():
        name = str(r.get("goal_name") or "").strip()
        if not name:
            continue
        rid = str(r.get("id"))
        cur = choices.get(name)
        if cur is None or rid < cur:
            choices[name] = rid
    return choices


st.title(":material/shopping_cart: Big purchases")
st.caption("Decide what's worth it: how many work-hours it costs vs how much you'll actually use it.")
help_expander("How the matrix works",
              "Each item is placed on a 4-square matrix: the x-axis is how much you expect "
              "to use it (hours/month) and the y-axis is how many hours of work it costs "
              "(price ÷ your hourly rate). High use + low work = quick win; low use + high work "
              "= reconsider. Lines are drawn at the median of your items.")

if (flash := st.session_state.pop("bp_flash", None)):
    if flash[0] == "success":
        st.success(flash[1], icon=":material/check_circle:")
    else:
        st.toast(flash[1], icon=":material/check_circle:")

dfi = q.income(user_id)
salary_eur = 0.0
try:
    salary_eur = to_eur(float(settings.get("salary_amount") or 0.0),
                        settings.get("salary_currency") or "EUR", rates)
except (TypeError, ValueError):
    pass
hourly_rate, rate_source = derive_hourly_rate(dfi, salary_eur)

# ── Hourly rate ───────────────────────────────────────────────────────────────
if rate_source == "income":
    st.caption(f"Hourly rate: **{hourly_rate:,.2f} EUR** — calculated from Hourly income "
               "(actual EUR ÷ recorded hours).")
elif rate_source == "salary":
    st.caption(f"Hourly rate: **{hourly_rate:,.2f} EUR** — calculated from salary "
               "(salary ÷ 160 hours/month). Add Hourly income entries to replace this fallback.")
else:
    st.info("Add Hourly income or salary data to calculate work-hours automatically.")

# ── Add form ──────────────────────────────────────────────────────────────────
with st.form("bp_form", clear_on_submit=True):
    c1, c2 = st.columns(2)
    with c1:
        bp_name = st.text_input("Item name", placeholder="e.g. New laptop")
        bp_cat  = st.selectbox("Category", CAT_LIST)
        bp_price = st.number_input("Price", min_value=0.0,
                                   max_value=MAX_SAVINGS_TARGET, step=10.0,
                                   format="%.2f", value=0.0)
    with c2:
        bp_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()))
        bp_use  = st.number_input("Expected use (hours / month)", min_value=0.0,
                                   step=1.0, format="%.1f",
                                   help="How many hours per month will you actually use it?")
        bp_imp  = st.slider("Importance", 1, 5, 3,
                            help="1 = nice to have · 5 = life-changing")
    bp_notes = st.text_input("Notes (optional)")

    # FIN-06: every wishlist item may declare where the money will come from.
    bp_fund_mode = st.radio(
        "Savings target",
        ["No target — pay from unallocated funds",
         "Create a new savings target",
         "Link an existing goal"],
        index=0, horizontal=True,
        help="Optional: tie this wish to a savings goal so you can fund "
             "the purchase from it later.")
    bp_new_goal   = ""
    bp_new_target = 0.0
    bp_link_goal  = ""
    _goal_opts = sorted(_goal_choices())
    if bp_fund_mode == "Create a new savings target":
        b1, b2 = st.columns([2, 1])
        with b1:
            bp_new_goal = st.text_input("New target name", placeholder="e.g. Laptop fund")
        with b2:
            bp_new_target = st.number_input("Target amount (EUR)", min_value=0.0,
                                            max_value=MAX_SAVINGS_TARGET, step=50.0,
                                            format="%.2f", value=0.0)
    elif bp_fund_mode == "Link an existing goal":
        if _goal_opts:
            bp_link_goal = st.selectbox("Goal", _goal_opts)
        else:
            st.caption("No savings goals yet — pick “Create a new savings target” "
                       "or leave the item without a target.")

    if st.form_submit_button("Add to wishlist", type="primary", width="stretch", icon=":material/add:"):
        if not bp_name.strip():
            st.error("Please give the item a name.")
        elif float(bp_price) <= 0:
            st.error("Price must be greater than 0.")
        elif bp_fund_mode == "Create a new savings target" and not bp_new_goal.strip():
            st.error("Please name the new savings target.")
        elif bp_fund_mode == "Link an existing goal" and not bp_link_goal:
            st.error("Pick a goal to link, or switch the savings target off.")
        else:
            _fresh_bp = q.big_purchases(user_id)
            if not _fresh_bp.empty and (
                (_fresh_bp["name"] == bp_name.strip()) & (_fresh_bp["category"] == bp_cat)
            ).any():
                st.toast("Already saved — duplicate prevented.", icon=":material/check:")
                st.rerun()
            pe = to_eur(bp_price, bp_cur, rates)
            # FIN-06: resolve the funding reference BEFORE the insert so the
            # item is created with its stable link in place.
            fund_src, fund_ref = None, None
            try:
                if bp_fund_mode == "No target — pay from unallocated funds":
                    fund_src = FUNDING_UNALLOCATED
                elif bp_fund_mode == "Create a new savings target":
                    tres = create_wishlist_target(user_id, bp_new_goal.strip(),
                                                  target_eur=float(bp_new_target))
                    fund_src, fund_ref = FUNDING_SAVINGS_GOAL, tres.affected_ids[0]
                    _bump_to_revision(tres)
                else:
                    fund_src = FUNDING_SAVINGS_GOAL
                    fund_ref = _goal_choices().get(bp_link_goal)
                    if not fund_ref:
                        raise CommandError("The selected goal could not be linked.")
                add_big_purchase(user_id, {
                    "name": bp_name.strip(), "category": bp_cat,
                    "price": bp_price, "currency": bp_cur, "price_eur": pe,
                    "usage_hours": float(bp_use), "importance": int(bp_imp),
                    "status": "wishlist", "notes": bp_notes,
                    "funding_source": fund_src, "funding_goal_ref": fund_ref,
                })
            except CommandError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.session_state["bp_flash"] = ("success", f"**{bp_name}** added to your wishlist.")
                st.rerun()

# ── Matrix & list ─────────────────────────────────────────────────────────────
dfb = q.big_purchases(user_id)
if dfb.empty:
    st.info("No big purchases yet — add one above")
    st.stop()

try:
    _sav_rows = q.savings(user_id).to_dict("records")
except Exception:
    _sav_rows = []

pending = dfb[dfb["status"] != "bought"] if not dfb.empty else pd.DataFrame()

if hourly_rate > 0 and not pending.empty:
    st.divider()
    st.subheader("Priority matrix")

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
                            amount_eur, notes, fund_line=""):
    """Confirm a wishlist purchase: ONE atomic command writes the expense,
    the funding debit and the bought stamp together."""
    st.write(f"Mark **{name}** as bought and log it as an expense?")
    st.caption(
        f"This will mark the item as **bought** and log a new expense of "
        f"**{amount:,.2f} {currency}** (≈ {fmt(amount_eur, DC, rates)}) on today's date."
    )
    if fund_line:
        st.caption(fund_line)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"bp_cancel_{purchase_id}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Confirm & log expense", key=f"bp_confirm_{purchase_id}",
                     type="primary", width="stretch"):
            # Recompute the EUR value at confirm time with the CURRENT rates —
            # the snapshotted price_eur may be stale if rates changed since
            # the item was added/edited.
            try:
                ae = to_eur(float(amount), str(currency), rates)
                res = buy_wishlist_item(uid, str(purchase_id), amount_eur=ae)
            except CommandError as e:
                st.error(str(e))
                return
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            if not res.changed:
                st.toast("Already bought — duplicate prevented.", icon=":material/check:")
            else:
                _bump_to_revision(res)
                st.session_state["bp_flash"] = ("toast", f"Logged **{name}** as an expense.")
            st.rerun()


@st.dialog("Edit wishlist item")
def edit_purchase_dialog(uid: int, row):
    """Edit wishlist item details and its funding target; the expense already
    logged for a bought item is never touched here."""
    st.caption("Editing the wishlist item does not change the expense already logged "
               "when it was bought (if any).")
    c1, c2 = st.columns(2)
    with c1:
        e_name = st.text_input("Item name", value=str(row["name"]), key=f"bp_edit_name_{row['id']}")
        e_cat  = st.selectbox("Category", CAT_LIST,
                              index=CAT_LIST.index(str(row["category"]))
                              if str(row["category"]) in CAT_LIST else 0,
                              key=f"bp_edit_cat_{row['id']}")
        e_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                              if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                              key=f"bp_edit_cur_{row['id']}")
    with c2:
        e_price = st.number_input(f"Price ({get_currency_symbol(e_cur)})",
                                  min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                  step=10.0, format="%.2f",
                                  value=0.01 if pd.isna(row["price"]) else max(float(row["price"]), 0.01),
                                  key=f"bp_edit_price_{row['id']}")
        e_use = st.number_input("Expected use (hours / month)", min_value=0.0,
                                step=1.0, format="%.1f",
                                value=float(row["usage_hours"]), key=f"bp_edit_use_{row['id']}")
        e_imp = st.slider("Importance", 1, 5, int(row["importance"]),
                          help="1 = nice to have · 5 = life-changing", key=f"bp_edit_imp_{row['id']}")
    e_notes = st.text_input("Notes (optional)",
                            value=str(row["notes"]) if pd.notna(row["notes"]) else "",
                            key=f"bp_edit_notes_{row['id']}")

    # FIN-06: the funding target can be changed or cleared; the stable
    # reference survives every other edit untouched.
    _choices = _goal_choices()
    _opts = ["(no target)", "(unallocated funds)"] + sorted(_choices)
    cur_src = str(row.get("funding_source") or "")
    cur_resolved = resolve_linked_goal_name(uid, row.get("funding_goal_ref")) \
        if cur_src == FUNDING_SAVINGS_GOAL else None
    if cur_src == FUNDING_SAVINGS_GOAL:
        e_fund_default = (cur_resolved if cur_resolved in _choices
                          else "(no target)")
    elif cur_src == FUNDING_UNALLOCATED:
        e_fund_default = "(unallocated funds)"
    else:
        e_fund_default = "(no target)"
    e_fund = st.selectbox(
        "Savings target", _opts,
        index=_opts.index(e_fund_default) if e_fund_default in _opts else 0,
        key=f"bp_edit_fund_{row['id']}",
        help="Where should the money come from when you buy this?")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"bp_edit_cancel_{row['id']}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save", type="primary", key=f"bp_edit_save_{row['id']}", width="stretch"):
            if not e_name.strip():
                st.error("Please give the item a name.")
                return
            pe = to_eur(float(e_price), e_cur, rates)
            try:
                update_big_purchase(uid, str(row["id"]), {
                    "name": e_name.strip(), "category": e_cat,
                    "price": float(e_price), "currency": e_cur, "price_eur": pe,
                    "usage_hours": float(e_use), "importance": int(e_imp),
                    "notes": e_notes,
                })
                # Funding is metadata, but still routed through the command
                # layer so validation + audit stay consistent.
                if e_fund == "(no target)":
                    if cur_src:
                        set_purchase_funding(uid, str(row["id"]), source=None)
                elif e_fund == "(unallocated funds)":
                    if cur_src != FUNDING_UNALLOCATED:
                        set_purchase_funding(uid, str(row["id"]),
                                             source=FUNDING_UNALLOCATED)
                else:
                    ref = _choices.get(e_fund)
                    if not ref:
                        raise CommandError("The selected goal could not be linked.")
                    if cur_src != FUNDING_SAVINGS_GOAL or \
                            str(row.get("funding_goal_ref")) != str(ref):
                        set_purchase_funding(uid, str(row["id"]),
                                             source=FUNDING_SAVINGS_GOAL, goal_ref=ref)
            except CommandError as e:
                st.error(str(e))
                return
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.toast(f"**{e_name.strip()}** updated.", icon="✏️")
            st.rerun()


# ── Item list ─────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Wishlist items")


def _persist_grouped_order(groups, rows):
    by_id = {str(row["id"]): row for _, row in rows.iterrows()}
    try:
        from services.commands import ItemMove as _ItemMove, reorder_big_purchases
        moves: list[_ItemMove] = []
        for category, item_ids in groups.items():
            for position, item_id in enumerate(item_ids):
                if str(item_id) in by_id:
                    row = by_id[str(item_id)]
                    changed = (pd.isna(row.get("sort_order")) or int(row.get("sort_order")) != position) or (str(row.get("category")) != str(category))
                    if changed:
                        moves.append(_ItemMove(id=str(item_id), group=str(category), position=position))
        if not moves:
            return
        res = reorder_big_purchases(user_id, moves)
        if res.changed and res.revision is not None:
            _bump_to_revision(res)
            st.rerun()
    except Exception as e:
        st.error(f"Couldn't save: {e}")
        return


def _fund_caption(row) -> str:
    try:
        return funding_summary(row, _sav_rows)
    except Exception:
        return ""


def _render_purchase_card(row, archived=False):
    if hourly_rate > 0 and row["price_eur"] > 0:
        wh = float(row["price_eur"]) / hourly_rate
        work_str = f" · ≈ {wh:,.0f} h of work"
    else:
        work_str = ""

    status_icon = {"wishlist": "⭐", "saving": "🐷", "bought": "✅"}.get(row["status"], "⭐")

    with st.container(border=True):
        l1, l2, l3 = st.columns([3.5, 1.6, 1.3])
        with l1:
            st.markdown(f"{status_icon} **{row['name']}**")
            st.caption(f"{row['category']} · importance {int(row['importance'])}/5 · "
                       f"use {float(row['usage_hours']):,.1f} h/mo{work_str}")
            fund_line = _fund_caption(row)
            if fund_line:
                st.caption(f"🎯 {fund_line}")
        with l2:
            st.write(fmt_row(row["price_eur"], row["price"], row["currency"], DC, rates))

        with l3:
            if archived:
                st.caption("Bought · archived")
                if st.button("Refund & restore", icon=":material/undo:",
                             key=f"bp_restore_{row['id']}", width="stretch",
                             help="Reverses the purchase: soft-deletes the linked "
                                  "expense and the funding debit, restores the "
                                  "previous status."):

                    try:
                        res = refund_wishlist_item(user_id, str(row["id"]))
                    except CommandError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        _bump_to_revision(res)
                        st.rerun()
            else:
                st.selectbox(
                    "Status", _SELECTABLE,
                    index=_SELECTABLE.index(row["status"])
                    if row["status"] in _SELECTABLE else 0,
                    key=f"bp_status_{row['id']}", label_visibility="collapsed",
                    on_change=lambda i=row["id"]: _bp_update_status(i),
                )
                # on_change already handles DB save via _bp_update_status
                if row["status"] != "bought" and st.button(
                        "Bought → log expense", icon=":material/check_circle:",
                        key=f"bp_buy_{row['id']}", width="stretch"):
                    confirm_purchase_dialog(
                        user_id, str(row["id"]), str(row["name"]), str(row["category"]),
                        float(row["price"]), str(row["currency"]), float(row["price_eur"]),
                        str(row.get("notes", "")), _fund_caption(row),
                    )

        with st.popover("More", icon=":material/more_vert:"):
            if st.button("Edit", icon=":material/edit:", key=f"bp_edit_{row['id']}",
                         width="stretch"):
                edit_purchase_dialog(user_id, row)
            if st.button("Delete", icon=":material/delete:", key=f"bp_del_{row['id']}",
                         width="stretch"):
                try:
                    delete_big_purchase(user_id, row["id"])
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    q.bump_db_version()
                    st.rerun()


active = dfb[dfb["status"] != "bought"].copy()
if not active.empty:
    categories = {str(category) for category in active["category"].dropna()}
    category_order = [category for category in CAT_LIST if category in categories]
    category_order += sorted(categories - set(category_order))
    groups = {}
    rows_by_id = {str(row["id"]): row for _, row in active.iterrows()}
    for category in category_order:
        rows = active[active["category"] == category].copy()
        rows["_sort"] = pd.to_numeric(rows["sort_order"], errors="coerce").fillna(0)
        rows = rows.sort_values(["_sort", "created_at", "name"], na_position="last")
        cards = []
        for _, row in rows.iterrows():
            work = (f" · ≈ {float(row['price_eur']) / hourly_rate:,.0f} h of work"
                    if hourly_rate > 0 and row["price_eur"] > 0 else "")
            fund_line = _fund_caption(row)
            details = (f"importance {int(row['importance'])}/5 · "
                       f"use {float(row['usage_hours']):,.1f} h/mo{work}")
            if fund_line:
                details += f"\n🎯 {fund_line}"
            cards.append({"id": str(row["id"]), "title": str(row["name"]),
                "details": details,
                "amount": fmt_row(row["price_eur"], row["price"], row["currency"], DC, rates),
                "actions": [
                    {"type": "select", "label": "Status", "action": "status",
                     "value": str(row["status"]), "options": _SELECTABLE},
                    {"label": "Bought → log expense", "action": "buy"},
                    {"label": "Edit", "action": "edit"}, {"label": "Delete", "action": "delete"}]})
        groups[category] = cards
    st.caption("Drag complete cards between categories. Use Alt+Up / Alt+Down to move by keyboard.")
    try:
        from ui.board import grouped_board
        _br = grouped_board(
            f"big_purchase_order_{user_id}", groups,
            allow_group_reorder=True, allow_item_reorder=True,
            allow_cross_group_move=True, collapsible=True,
        )
        ordered, action = _br.item_order, _br.action
    except Exception:
        ordered, action = draggable_card_board(groups, f"big_purchase_order_{user_id}")
    _persist_grouped_order(ordered, active)
    if action:
        row = rows_by_id[action["id"]]
        if action["action"] == "status" and is_selectable_status(action.get("value", "")):
            try:
                update_big_purchase(user_id, str(row["id"]), {"status": action["value"]})
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.rerun()
        elif action["action"] == "buy":
            confirm_purchase_dialog(user_id, str(row["id"]), str(row["name"]),
                str(row["category"]), float(row["price"]), str(row["currency"]),
                float(row["price_eur"]), str(row.get("notes", "")),
                _fund_caption(row))
        elif action["action"] == "edit":
            edit_purchase_dialog(user_id, row)
        elif action["action"] == "delete":
            try:
                delete_big_purchase(user_id, row["id"])
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.rerun()
else:
    st.info("All wishlist items are archived. Add a new item above or refund one below.")

bought = dfb[dfb["status"] == "bought"]
if not bought.empty:
    with st.expander(f"Archived ({len(bought)})", expanded=False):
        for _, row in bought.sort_values("created_at", ascending=False).iterrows():
            _render_purchase_card(row, archived=True)
