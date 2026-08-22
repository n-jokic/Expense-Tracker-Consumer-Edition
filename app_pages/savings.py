"""
Savings page: savings goals with quick deposit/withdraw/edit actions,
term-deposit accounts under goals (fixed rate, maturity date), yearly KPIs,
goal progress, projections and charts.
"""

from datetime import date
import calendar

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from db import (
    add_savings, update_savings, soft_delete_savings, restore_savings,
    add_savings_account, update_savings_account, get_savings_accounts,
    soft_delete_savings_account, restore_savings_account,
    rename_savings_goal, update_savings_goal, soft_delete_savings_goal,
)
from finance import accrued_value, maturity_value, calculate_term_payout
from services.commands import (
    CommandError, InsufficientFunds,
    deposit_to_goal, withdraw_from_goal, open_term_from_goal,
    settle_term_account, soft_delete_goal_checked, soft_delete_account_checked,
)
from insights import savings_projection
from utils import (
    SAVINGS_GOALS, SUPPORTED_CURRENCIES, MAX_AMOUNT, MAX_SAVINGS_TARGET, CHART_COLORS,
    fmt, to_display, to_eur, get_currency_symbol, progress_ratio,
    help_expander, to_excel,
)
from ui.panel import PanelSpec, page_kpi_band, panel

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
SYM     = get_currency_symbol(DC)
today   = date.today()

st.title(":material/savings: Savings")
st.caption("Save towards goals, and lock money in term deposits that mature "
           "on a date with a fixed annual interest rate.")
help_expander("How savings works",
              "Every goal has a target, an interest rate and a chain of deposits "
              "(withdrawals are negative amounts). Interest is compounded monthly "
              "on the whole months between deposits, and each goal's balance is "
              "always rolled forward to today at its latest rate — so the number "
              "you see is the current value. You can also open **term deposits** "
              "under a goal: money locked until a maturity date at a fixed annual "
              "rate — its value grows monthly and can be withdrawn into the goal "
              "when it matures.")

if (flash := st.session_state.pop("sav_flash", None)):
    if flash[0] == "success":
        st.success(flash[1], icon=":material/check_circle:")
    else:
        st.toast(flash[1], icon=":material/check_circle:")


# ── Small goal helpers ────────────────────────────────────────────────────────

def goal_rows(df, goal_name):
    """Active entries of a goal, ordered by date."""
    return df[df["goal_name"] == goal_name].sort_values("date")


def goal_attrs(rows):
    """Latest target, interest rate and currency of a goal."""
    if rows.empty:
        return 0.0, 0.0, "EUR"
    lat = rows.iloc[-1]
    tr = rows[rows["target_eur"] > 0]
    tgt = float(tr["target_eur"].iloc[-1]) if not tr.empty else 0.0
    rate = float(lat["interest_rate"]) if pd.notna(lat["interest_rate"]) else 0.0
    return (tgt,
            rate,
            str(lat["currency"] or "EUR"))


dfs_all = q.savings(user_id)
goals = sorted(set(dfs_all["goal_name"].dropna())) if not dfs_all.empty else []

# ── Dialogs (defined before use) ──────────────────────────────────────────────

@st.dialog("Add money to goal")
def deposit_dialog(uid: int, goal: str, tgt_eur: float, rate: float, gcur: str):
    """Quick deposit into an existing goal using its current target/rate."""
    gsym = get_currency_symbol(gcur)
    st.markdown(f"**{goal}** · target {fmt(tgt_eur, DC, rates) if tgt_eur > 0 else '—'} "
                f"· rate {rate:.2f}%")
    d = st.date_input("Date", value=today, key="dlg_dep_date")
    amt = st.number_input(f"Amount ({gsym})", min_value=0.0, max_value=MAX_AMOUNT,
                           step=10.0, format="%.2f", value=0.0, key="dlg_dep_amt")
    notes = st.text_input("Notes", key="dlg_dep_notes")
    if st.button("Save deposit", icon=":material/save:", type="primary",
                 width="stretch", key="dlg_dep_save"):
        if float(amt) <= 0:
            st.error("Amount must be greater than 0.")
            return
        de = to_eur(float(amt), gcur, rates)
        _fresh_dep = q.savings(uid)
        if not _fresh_dep.empty and (
            (_fresh_dep["date"].dt.date == d)
            & (_fresh_dep["goal_name"] == goal)
            & (_fresh_dep["deposited_eur"].round(2) == round(de, 2))
        ).any():
            st.toast("Already saved — duplicate prevented.", icon=":material/check:")
            st.rerun()
        try:
            deposit_to_goal(uid, goal, de, entry_date=d, target_eur=tgt_eur,
                            deposited=float(amt), currency=gcur,
                            interest_rate=rate, notes=notes)
        except InsufficientFunds as e:
            st.error(f"Couldn't save: {e}")
            return
        except CommandError as e:
            st.error(f"Couldn't save: {e}")
            return
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["sav_flash"] = (
            "success", f"**{fmt(de, DC, rates)}** added to **{goal}**")
        st.rerun()


@st.dialog("Withdraw from goal")
def withdraw_dialog(uid: int, goal: str, bal_eur: float, tgt_eur: float,
                    rate: float, gcur: str):
    """Quick withdrawal from a goal (logged as a negative deposit)."""
    gsym = get_currency_symbol(gcur)
    available = to_display(bal_eur, gcur, rates)
    st.markdown(f"**{goal}** — available: {fmt(bal_eur, gcur, rates)}")
    if bal_eur < 0.01:
        st.info("Nothing to withdraw from this goal.")
        return
    d = st.date_input("Date", value=today, key="dlg_wd_date")
    amt = st.number_input(f"Amount ({gsym})", min_value=0.0,
                           max_value=min(float(available), MAX_AMOUNT),
                           step=10.0, format="%.2f", value=0.0, key="dlg_wd_amt")
    notes = st.text_input("Notes", key="dlg_wd_notes")
    if st.button("Save withdrawal", icon=":material/save:", type="primary",
                 width="stretch", key="dlg_wd_save"):
        if float(amt) <= 0:
            st.error("Amount must be greater than 0.")
            return
        de = to_eur(float(amt), gcur, rates)
        _fresh_wd = q.savings(uid)
        if not _fresh_wd.empty and (
            (_fresh_wd["date"].dt.date == d)
            & (_fresh_wd["goal_name"] == goal)
            & (_fresh_wd["deposited_eur"].round(2) == round(-de, 2))
        ).any():
            st.toast("Already saved — duplicate prevented.", icon=":material/check:")
            st.rerun()
        try:
            withdraw_from_goal(uid, goal, de, entry_date=d, target_eur=tgt_eur,
                               currency=gcur, notes=notes or "Withdrawal")
        except InsufficientFunds as e:
            st.error(f"Couldn't save: {e}")
            return
        except CommandError as e:
            st.error(f"Couldn't save: {e}")
            return
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["sav_flash"] = (
            "success", f"**{fmt(de, DC, rates)}** withdrawn from **{goal}**")
        st.rerun()


@st.dialog("Edit goal")
def edit_goal_dialog(uid: int, goal: str):
    """Rename a goal / set its target and interest rate (applies to every
    entry; balances recompute automatically on read)."""
    rows = goal_rows(dfs_all, goal)
    tgt, rate, gcur = goal_attrs(rows)
    st.caption("Target and rate apply to all entries of this goal — balances "
               "recompute automatically. Logged deposits are never rewritten.")
    e_name = st.text_input("Goal name", value=goal, key="dlg_goal_name")
    c1, c2 = st.columns(2)
    with c1:
        # Target is entered in the DISPLAY currency: prefill the EUR value
        # converted to display, and convert back to EUR on save.
        e_tgt = st.number_input(f"Target ({SYM})", min_value=0.0,
                                 max_value=to_display(MAX_SAVINGS_TARGET, DC, rates),
                                 step=100.0, format="%.2f",
                                 value=min(to_display(float(tgt), DC, rates),
                                           to_display(MAX_SAVINGS_TARGET, DC, rates)),
                                 key="dlg_goal_tgt")
    with c2:
        e_rate = st.number_input("Annual interest rate (%)", min_value=0.0,
                                  max_value=100.0, step=0.01, format="%.2f",
                                  value=float(rate), key="dlg_goal_rate")
    if st.button("Save goal", icon=":material/save:", type="primary",
                 width="stretch", key="dlg_goal_save"):
        new_name = (e_name or "").strip()
        if not new_name:
            st.error("Please give the goal a name.")
            return
        tgt_eur = to_eur(float(e_tgt), DC, rates)
        try:
            if new_name != goal:
                if not rename_savings_goal(uid, goal, new_name):
                    st.error(f"A goal named **{new_name}** already exists — "
                             "renaming would merge the two goals.")
                    return
                # FIN-05: carry the goal's layout state across the rename —
                # the savings panel id is derived from the goal name, so the
                # collapse entry must follow or the state would be lost.
                try:
                    from ui.layout_state import update_layout_area, LayoutSaveError
                    from ui.panel import warn_layout_unsaved

                    def _carry(av, _old=goal, _new=new_name):
                        av = dict(av or {})
                        av["collapsed"] = [_new if c == _old else c
                                           for c in av.get("collapsed", [])]
                        return av
                    update_layout_area(uid, "savings", _carry)
                except LayoutSaveError as le:
                    warn_layout_unsaved(le)
                except Exception:
                    pass  # layout carry-over is best-effort; rename succeeded
                goal = new_name
            update_savings_goal(uid, goal, {"target_eur": tgt_eur,
                                            "interest_rate": float(e_rate)})
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["sav_flash"] = ("toast", f"Goal **{goal}** updated.")
        st.rerun()


@st.dialog("Delete goal?")
def delete_goal_dialog(uid: int, goal: str, n_entries: int, locked_eur: float):
    st.write(f"Delete goal **{goal}**?")
    st.caption(f"{n_entries} entries move to the trash "
               f"({fmt(locked_eur, DC, rates)} in term deposits is removed with them). "
               "You can restore individual entries from the trash.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key="dlg_goal_del_cancel", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Delete goal", key="dlg_goal_del_ok", type="primary", width="stretch"):
            try:
                soft_delete_goal_checked(uid, goal)
            except CommandError as e:
                st.error(f"Couldn't delete: {e}")
                return
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.session_state["sav_flash"] = ("toast", f"Goal **{goal}** deleted.")
            st.rerun()


@st.dialog("Withdraw term deposit")
def withdraw_account_dialog(uid: int, row):
    """Log the deposit's value into the goal and close the account."""
    has_dates = pd.notna(row["start_date"]) and pd.notna(row["maturity_date"])
    matured = (has_dates and row["maturity_date"].date() <= today)
    payout_date = (row["maturity_date"].date() if matured else today)
    early_rate = row.get("early_annual_rate")
    early_rate = None if pd.isna(early_rate) else float(early_rate)
    # FIN-05: the payout policy is explicit — matured pays the full term
    # rate; early pays the agreed early rate, or principal-only when none.
    payout = calculate_term_payout(
        float(row["amount_eur"] or 0.0), float(row["annual_rate"] or 0.0),
        row["start_date"].date() if has_dates else None,
        payout_date, maturity_date=row["maturity_date"].date() if has_dates else None,
        early_annual_rate_pct=early_rate,
        withdrawal_kind="matured" if matured else ("early" if has_dates else "no_dates"))
    val = payout["payout_eur"]
    gcur = str(row["currency"] or "EUR")
    if payout["kind"] == "matured":
        payout_label = (f"Matured — full term rate {payout['rate_applied']:.2f}% "
                        f"(interest {fmt(payout['interest_eur'], DC, rates)})")
    elif payout["interest_eur"] > 0:
        payout_label = (f"Early withdrawal — agreed early rate "
                        f"{payout['rate_applied']:.2f}% "
                        f"(interest {fmt(payout['interest_eur'], DC, rates)})")
    elif payout["kind"] == "early":
        payout_label = "Early withdrawal — no early rate agreed: principal only, no interest"
    else:
        payout_label = "No dates on this deposit — principal only, no interest"
    st.markdown(f"**{row['name']}** ({row['goal_name']})")
    st.write(f"Logging **{fmt(val, DC, rates)}** into the goal on "
             f"{payout_date.strftime('%d %b %Y')}. {payout_label}.")
    if st.button("Withdraw and close", icon=":material/check:", type="primary",
                 width="stretch", key="dlg_acc_wd"):
        # Re-read the account: guard against double-clicking Withdraw before
        # the rerun (which would log the payout twice).
        fresh_accs = get_savings_accounts(uid)
        fresh_accs = fresh_accs[fresh_accs["id"] == str(row["id"])]
        if fresh_accs.empty or fresh_accs.iloc[0]["status"] != "active":
            st.session_state["sav_flash"] = ("toast", "This deposit was already withdrawn.")
            st.rerun()
        tgt, rate, _ = goal_attrs(goal_rows(dfs_all, str(row["goal_name"])))
        try:
            # FIN-04/05: one atomic settlement — principal + realized interest
            # (per the locked early-withdrawal policy) credited to the goal
            # exactly once, interest booked as income.
            settle_term_account(uid, str(row["id"]),
                                realized_interest_eur=payout["interest_eur"],
                                payout_date=payout_date)
        except CommandError as e:
            st.error(f"Couldn't save: {e}")
            return
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["sav_flash"] = (
            "success", f"**{fmt(val, DC, rates)}** moved into **{row['goal_name']}**")
        st.rerun()


@st.dialog("Edit term deposit")
def edit_account_dialog(uid: int, row):
    c1, c2 = st.columns(2)
    with c1:
        e_name = st.text_input("Account name", value=str(row["name"]), key=f"dlg_acc_name_{row['id']}")
        e_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                              if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                              key=f"dlg_acc_cur_{row['id']}")
        e_amt = st.number_input(f"Amount ({get_currency_symbol(e_cur)})",
                                min_value=0.01, max_value=MAX_AMOUNT,
                                step=10.0, format="%.2f",
                                value=0.01 if pd.isna(row["amount"]) else max(float(row["amount"]), 0.01),
                                key=f"dlg_acc_amt_{row['id']}")
    with c2:
        e_rate = st.number_input("Annual interest rate (%)", min_value=0.0,
                                 max_value=100.0, step=0.01, format="%.2f",
                                 value=float(row["annual_rate"]), key=f"dlg_acc_rate_{row['id']}")
        e_start = st.date_input("Start date",
                                value=row["start_date"].date() if pd.notna(row["start_date"]) else today,
                                key=f"dlg_acc_start_{row['id']}")
        e_mat = st.date_input("Maturity date",
                              value=row["maturity_date"].date() if pd.notna(row["maturity_date"]) else today,
                              key=f"dlg_acc_mat_{row['id']}")
    row_goal = str(row["goal_name"])
    goal_missing = row_goal not in goals
    if goal_missing:
        st.warning(f"This account's goal **{row_goal}** has no savings entries. "
                   "Choose a goal below or it stays under its current name.")
        goal_opts = goals + [row_goal]
        goal_idx = len(goal_opts) - 1
    else:
        goal_opts = goals
        goal_idx = goals.index(row_goal)
    e_goal = st.selectbox("Goal", goal_opts, index=goal_idx, key=f"dlg_acc_goal_{row['id']}")
    if st.button("Save", icon=":material/save:", type="primary", width="stretch",
                 key="dlg_acc_save"):
        if e_mat <= e_start:
            st.error("Maturity date must be after the start date.")
            return
        ae = to_eur(float(e_amt), e_cur, rates)
        try:
            update_savings_account(uid, str(row["id"]), {
                "name": e_name.strip(), "amount": float(e_amt), "currency": e_cur,
                "amount_eur": ae, "annual_rate": float(e_rate),
                "start_date": e_start, "maturity_date": e_mat, "goal_name": e_goal,
            })
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["sav_flash"] = ("toast", "Term deposit updated.")
        st.rerun()


@st.dialog("Delete term deposit?")
def delete_account_dialog(uid: int, acc_id: str, name: str):
    st.write(f"Delete term deposit **{name}**?")
    st.caption("It moves to the trash — no money is logged anywhere.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"dlg_acc_del_cancel_{acc_id}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Delete", key=f"dlg_acc_del_ok_{acc_id}", type="primary",
                     width="stretch"):
            try:
                soft_delete_account_checked(uid, acc_id)
            except CommandError as e:
                st.error(f"Couldn't delete: {e}")
                return
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.session_state["sav_flash"] = ("toast", "Term deposit deleted.")
            st.rerun()


@st.dialog("Edit savings entry")
def edit_savings_dialog(uid: int, row):
    """Edit one savings entry; balances recompute from this entry forward."""
    st.caption("Balances are a chain computed from all entries — editing an entry updates the "
               "balance from that entry forward; other rows are untouched.")
    c1, c2 = st.columns(2)
    with c1:
        e_date = st.date_input("Date", value=row["date"].date() if pd.notna(row["date"]) else today,
                               key=f"sav_edit_date_{row['id']}")
        e_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                              if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                              key=f"sav_edit_cur_{row['id']}")
    with c2:
        e_dep = st.number_input(f"Amount deposited ({get_currency_symbol(e_cur)}) — negative = withdrawal",
                                min_value=-MAX_AMOUNT, max_value=MAX_AMOUNT,
                                step=10.0, format="%.2f",
                                value=float(row["deposited"]) if pd.notna(row["deposited"]) else 0.0,
                                key=f"sav_edit_dep_{row['id']}")
        # Target is entered in the DISPLAY currency: prefill the stored EUR
        # value converted to display, and convert back to EUR on save.
        _tgt_eur = float(row["target_eur"]) if pd.notna(row["target_eur"]) else 0.0
        e_tgt = st.number_input(f"Target ({SYM})", min_value=0.0,
                                max_value=to_display(MAX_SAVINGS_TARGET, DC, rates),
                                step=100.0, format="%.2f",
                                value=min(to_display(_tgt_eur, DC, rates),
                                          to_display(MAX_SAVINGS_TARGET, DC, rates)),
                                key=f"sav_edit_tgt_{row['id']}")
    e_ir = st.number_input("Annual interest rate (%)", min_value=0.0,
                           max_value=100.0, step=0.01, format="%.2f",
                           value=float(row["interest_rate"]) if pd.notna(row["interest_rate"]) else 0.0,
                           key=f"sav_edit_ir_{row['id']}")
    e_notes = st.text_input("Notes", value=str(row["notes"]) if pd.notna(row["notes"]) else "",
                            key=f"sav_edit_notes_{row['id']}")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"sav_edit_cancel_{row['id']}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save", type="primary", key=f"sav_edit_save_{row['id']}", width="stretch"):
            de = to_eur(float(e_dep), e_cur, rates)
            try:
                update_savings(uid, str(row["id"]), {
                    "date": e_date, "deposited": float(e_dep),
                    "currency": e_cur, "deposited_eur": de,
                    "target_eur": to_eur(float(e_tgt), DC, rates),
                    "interest_rate": float(e_ir),
                    "notes": e_notes,
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.toast("Savings entry updated.", icon="✏️")
            st.rerun()


# ── Entry form (first deposit creates the goal) ───────────────────────────────
goal_options = ["➕ New goal..."] + [g for g in SAVINGS_GOALS if g not in goals] + goals

# Goal selector + currency are OUTSIDE the form so changing them rebuilds the
# form immediately (reactive invariant). Form key includes both so switching
# either reconstructs the amount label / new-goal fields.
gn_sel = st.selectbox("Goal", goal_options, key="sav_goal_select")
cur = st.selectbox("Save in", list(SUPPORTED_CURRENCIES.keys()), key="sav_cur")
sym = get_currency_symbol(cur)

# Precompute the "existing goal" caption outside the form too.
if gn_sel != "➕ New goal...":
    _grows = goal_rows(dfs_all, gn_sel)
    _gt, _gr, _gc = goal_attrs(_grows)
    _existing_caption = (
        f"Target: {fmt(_gt, DC, rates) if _gt > 0 else '—'} · "
        f"Interest rate: {_gr:.2f}% · Currency: {_gc} "
        "(edit via the goal's ✏️ Edit button)"
    )
else:
    _existing_caption = None  # new-goal branch shows fields instead

with st.form(f"sav_form_{gn_sel}_{cur}", clear_on_submit=True):
    st.markdown("**:material/add: Log deposit / withdrawal**")
    c1, c2 = st.columns(2)
    with c1:
        sd = st.date_input("Date", value=today)
        if gn_sel == "➕ New goal...":
            new_goal = st.text_input("New goal name", placeholder="e.g. New laptop")
            tgt = st.number_input(f"Target ({SYM})", min_value=0.0,
                                   max_value=MAX_SAVINGS_TARGET, step=100.0,
                                   format="%.2f", value=0.0)
            ir = st.number_input("Annual interest rate (%)", min_value=0.0,
                                  max_value=100.0, step=0.01, format="%.2f",
                                  value=0.0,
                                  help="e.g. 4.50 for 4.5% p.a., compounded monthly")
        else:
            st.caption(_existing_caption)
    with c2:
        dep = st.number_input(f"Amount ({sym}) — negative = withdrawal",
                               min_value=-MAX_AMOUNT, max_value=MAX_AMOUNT,
                               step=10.0, format="%.2f", value=0.0)
        notes = st.text_input("Notes")
    saved = st.form_submit_button("Save entry", icon=":material/save:",
                                   width="stretch", type="primary")

if saved:
    goal_name = (new_goal.strip() if gn_sel == "➕ New goal..." else gn_sel)
    if not goal_name:
        st.error("Please name your new goal.")
    elif dep == 0:
        st.error("Amount is 0 — nothing to save.")
    else:
        if gn_sel == "➕ New goal...":
            # Target label is in the DISPLAY currency (SYM above).
            te = to_eur(float(tgt), DC, rates)
            use_rate = float(ir)
            current_bal = 0.0
        else:
            _rows = goal_rows(dfs_all, goal_name)
            te, use_rate, _ = goal_attrs(_rows)
            current_bal = float(_rows.iloc[-1]["balance_eur"]) if not _rows.empty else 0.0
        de = to_eur(float(dep), cur, rates)
        _fresh_sav = q.savings(user_id)
        if not _fresh_sav.empty and (
            (_fresh_sav["date"].dt.date == sd)
            & (_fresh_sav["goal_name"] == goal_name)
            & (_fresh_sav["deposited_eur"].round(2) == round(de, 2))
        ).any():
            st.toast("Already saved — duplicate prevented.", icon=":material/check:")
            st.rerun()
        if de < 0 and abs(de) > current_bal + 1e-9:
            st.error(f"Withdrawal exceeds the goal balance "
                     f"({fmt(current_bal, DC, rates)}) — the balance cannot go negative.")
        else:
            try:
                if de >= 0:
                    deposit_to_goal(user_id, goal_name, de, entry_date=sd,
                                    target_eur=te, deposited=float(dep),
                                    currency=cur, interest_rate=use_rate,
                                    notes=notes)
                else:
                    withdraw_from_goal(user_id, goal_name, abs(de), entry_date=sd,
                                       target_eur=te, currency=cur, notes=notes)
            except InsufficientFunds as e:
                st.error(f"Couldn't save: {e}")
            except CommandError as e:
                st.error(f"Couldn't save: {e}")
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                fresh = q.savings(user_id)
                _nrows = goal_rows(fresh, goal_name)
                nb = float(_nrows.iloc[-1]["balance_eur"]) if not _nrows.empty else de
                action = "withdrawn from" if dep < 0 else "saved to"
                # #13: flash + rerun so the goal list/balances re-render from
                # the bumped cache instead of patched locals (was stale).
                st.session_state["sav_flash"] = (
                    "success",
                    f"**{fmt(abs(de), DC, rates)}** {action} **{goal_name}** — "
                    f"balance: {fmt(nb, DC, rates)}")
                st.rerun()

dfs = q.savings(user_id)
accs = q.savings_accounts(user_id)

if not dfs.empty:
    # ── Yearly KPIs ──────────────────────────────────────────────────────────
    ydf = dfs[dfs["date"].dt.year == today.year]
    interest_total = 0.0
    total_balance  = 0.0
    for g in dfs["goal_name"].unique():
        rows = dfs[dfs["goal_name"] == g].sort_values("date")
        bal = float(rows.iloc[-1]["balance_eur"])
        dep_sum = float(rows["deposited_eur"].sum())
        # FIN-04: balances are POSTED principal — interest shown is the
        # posted interest credits plus still-unposted pending accrual.
        posted_mask = rows["accrual_key"].notna() if "accrual_key" in rows.columns \
            else pd.Series(False, index=rows.index)
        posted_int = float(rows.loc[posted_mask, "deposited_eur"].fillna(0).sum())
        pending_int = float(pd.to_numeric(rows.get("pending_interest_eur"),
                                          errors="coerce").fillna(0.0).sum()) \
            if "pending_interest_eur" in rows.columns else 0.0
        interest_total += posted_int + pending_int
        total_balance  += bal
    saved_year = float(ydf["deposited_eur"].sum()) if not ydf.empty else 0.0

    locked_eur = 0.0
    for _, a in accs.iterrows():
        if a["status"] == "closed":
            continue
        if pd.isna(a["start_date"]) or pd.isna(a["maturity_date"]):
            locked_eur += float(a["amount_eur"] or 0.0)   # no dates -> no accrual
            continue
        end = (a["maturity_date"].date()
               if a["maturity_date"].date() < today else today)
        locked_eur += accrued_value(float(a["amount_eur"]), float(a["annual_rate"]),
                                    a["start_date"].date(), end)

    # Portfolio value (investments count as savings)
    portfolio_value = 0.0
    df_hold = q.holdings(user_id)
    if not df_hold.empty:
        for _, h in df_hold.iterrows():
            hcur = str(h["currency"] or "EUR")  # NB: not `cur` — that's the save widget
            price_eur = float(h["last_price"] or 0.0)
            if hcur != "EUR" and price_eur > 0:
                price_eur = price_eur / (rates.get(hcur, 1.0) or 1.0)
            portfolio_value += float(h["quantity"] or 0.0) * price_eur

    st.divider()
    page_kpi_band([
        {"label": "Total balance", "value": fmt(total_balance + locked_eur, DC, rates)},
        {"label": "Saved this year", "value": fmt(saved_year, DC, rates)},
        {"label": "Interest earned", "value": fmt(interest_total, DC, rates)},
        {"label": "Locked (term)", "value": fmt(locked_eur, DC, rates)},
        {"label": "Portfolio", "value": fmt(portfolio_value, DC, rates)},
        {"label": "Active goals", "value": str(dfs["goal_name"].nunique())},
    ])

    # ── Goal progress (cards with quick actions) ─────────────────────────────
    st.divider()
    st.subheader("Goals")

    def _render_account_card(row):
        """One term-deposit card (FIN-05: rendered exactly once — inside its
        goal panel, or in the orphan section when the goal is gone)."""
        acc_id = str(row["id"])
        has_dates = pd.notna(row["start_date"]) and pd.notna(row["maturity_date"])
        matured = (row["status"] == "active" and has_dates
                   and row["maturity_date"].date() <= today)
        if has_dates:
            end = (row["maturity_date"].date()
                   if row["maturity_date"].date() < today else today)
            cur_val = accrued_value(float(row["amount_eur"]), float(row["annual_rate"]),
                                    row["start_date"].date(), end)
            mat_val = maturity_value(float(row["amount_eur"]), float(row["annual_rate"]),
                                     row["start_date"].date(), row["maturity_date"].date())
        else:
            cur_val = float(row["amount_eur"] or 0.0)
            mat_val = cur_val
        days_left = ((row["maturity_date"].date() - today).days
                     if has_dates else None)
        status_txt = ("**Matured — ready to withdraw**"
                      if matured else
                      ("Closed" if row["status"] == "closed" else "Active"))
        with st.container(border=True):
            m1, m2, m3, m4 = st.columns([2.4, 1, 1, 1])
            with m1:
                st.markdown(f"🔒 **{row['name']}** · {status_txt}")
                maturity_txt = (row["maturity_date"].strftime("%d %b %Y")
                                if has_dates else "—")
                days_txt = (f" · {days_left} days left" if days_left is not None
                            and days_left >= 0 and row["status"] == "active" else "")
                rate_txt = f"{float(row['annual_rate']):.2f}% p.a."
                early = row.get("early_annual_rate")
                if not pd.isna(early):
                    rate_txt += f" · early {float(early):.2f}%"
                st.caption(f"Rate: {rate_txt} · "
                           f"Matures: {maturity_txt}{days_txt}")
            with m2:
                st.metric("Deposited", fmt(row["amount_eur"], DC, rates),
                          label_visibility="collapsed")
            with m3:
                st.metric("Now", fmt(cur_val, DC, rates),
                          label_visibility="collapsed")
            with m4:
                st.metric("At maturity", fmt(mat_val, DC, rates),
                          label_visibility="collapsed")
            with st.container(horizontal=True):
                if row["status"] == "active":
                    if st.button("Withdraw", icon=":material/savings:",
                                 key=f"savacc_wd_{acc_id}", type="primary"):
                        withdraw_account_dialog(user_id, row)
                    if st.button("Edit", icon=":material/edit:",
                                 key=f"savacc_ed_{acc_id}"):
                        edit_account_dialog(user_id, row)
                if st.button("Delete", icon=":material/delete:",
                             key=f"savacc_del_{acc_id}"):
                    delete_account_dialog(user_id, acc_id, str(row["name"]))

    for idx, g in enumerate(goals):
        rows  = dfs[dfs["goal_name"] == g].sort_values("date")
        lat   = rows.iloc[-1]
        bal   = float(lat["balance_eur"])
        td    = float(rows["deposited_eur"].sum())
        # FIN-04: posted credits + pending accrual, not balance-minus-deposits
        _pmask = rows["accrual_key"].notna() if "accrual_key" in rows.columns \
            else pd.Series(False, index=rows.index)
        interest = (float(rows.loc[_pmask, "deposited_eur"].fillna(0).sum())
                    + float(pd.to_numeric(rows.get("pending_interest_eur"),
                                          errors="coerce").fillna(0.0).sum())
                    if "pending_interest_eur" in rows.columns
                    else float(rows.loc[_pmask, "deposited_eur"].fillna(0).sum()))
        tgtv, grat, gcur = goal_attrs(rows)
        g_accs = accs[(accs["goal_name"] == g) & (accs["status"] != "closed")]
        g_locked = 0.0
        for _, a in g_accs.iterrows():
            if pd.isna(a["start_date"]) or pd.isna(a["maturity_date"]):
                g_locked += float(a["amount_eur"] or 0.0)
                continue
            end = (a["maturity_date"].date()
                   if a["maturity_date"].date() < today else today)
            g_locked += accrued_value(float(a["amount_eur"]), float(a["annual_rate"]),
                                      a["start_date"].date(), end)
        _net_goal = bal + g_locked
        ratio = progress_ratio(_net_goal, tgtv)
        pct = ratio * 100
        avg_dep = float(rows["deposited_eur"].tail(3).mean()) if not rows.empty else 0.0

        spec = PanelSpec(id=f"sav_goal_{g}", title=g,
                         collapsible=True, default_expanded=True)
        expanded, container = panel(spec, user_id=user_id, area="savings")
        if expanded:
            with container:
                h1, h2 = st.columns([4, 1])
                with h1:
                    st.markdown(f"**{g}**")
                    prog_text = f"{pct:.0f}% of target" if tgtv > 0 else "No target set"
                    if _net_goal < 0:
                        prog_text += " · overdrawn — balance below zero"
                    st.progress(ratio, text=prog_text)
                    proj = savings_projection(dfs, g)
                    proj_str = ""
                    if proj["months_to_goal"] and proj["months_to_goal"] > 0 and proj["projected_date"]:
                        proj_str = f" · 🎯 Goal in ~{proj['months_to_goal']}mo ({proj['projected_date'].strftime('%b %Y')})"
                    st.caption(
                        f"Balance: **{fmt(bal, DC, rates)}**"
                        + (f" + {fmt(g_locked, DC, rates)} locked" if g_locked > 0 else "")
                        + f" · Target: {fmt(tgtv, DC, rates) if tgtv > 0 else '—'} · "
                        f"Interest earned: {fmt(interest, DC, rates)} · "
                        f"Rate: {grat:.2f}% · ~{fmt(avg_dep, DC, rates)}/mo"
                        + proj_str
                    )
                with h2:
                    st.metric("Progress", f"{pct:.1f}%" if tgtv > 0 else "—",
                              label_visibility="collapsed")
                with st.container(horizontal=True):
                    if st.button("Deposit", icon=":material/add:",
                                 key=f"goal_dep_{idx}", type="primary"):
                        deposit_dialog(user_id, g, tgtv, grat, gcur)
                    if st.button("Withdraw", icon=":material/remove:",
                                 key=f"goal_wd_{idx}"):
                        withdraw_dialog(user_id, g, bal, tgtv, grat, gcur)
                    if st.button("Edit goal", icon=":material/edit:",
                                 key=f"goal_ed_{idx}"):
                        edit_goal_dialog(user_id, g)
                    if st.button("Delete goal", icon=":material/delete:",
                                 key=f"goal_del_{idx}"):
                        delete_goal_dialog(user_id, g, len(rows), g_locked)
                # FIN-05: this goal's term accounts (active AND closed) render
                # exactly once — here, inside their goal panel.
                if not accs.empty:
                    g_all = accs[accs["goal_name"] == g]
                    for _, acc_row in g_all.iterrows():
                        _render_account_card(acc_row)

    # ── Term-deposit accounts ────────────────────────────────────────────────
    st.divider()
    st.subheader(":material/lock: Term-deposit accounts")
    st.caption("Lock money under a goal until a maturity date at a fixed annual rate. "
               "The value compounds monthly; withdraw it into the goal at (or before) maturity.")

    if not goals:
        st.info("Create a goal above first — term deposits live under a goal.")
    if goals:
        # Currency outside the form so Amount label matches selection immediately.
        # The goal selector itself does NOT drive any other widget (safe inside).
        a_cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                             key="savacc_cur")
        a_sym = get_currency_symbol(a_cur)
        with st.form(f"savacc_form_{a_cur}", clear_on_submit=True):
            st.markdown("**:material/add: New term deposit**")
            c1, c2 = st.columns(2)
            with c1:
                a_goal = st.selectbox("Goal", goals, key="savacc_goal")
                a_name = st.text_input("Account name", placeholder="e.g. 12-month CD")
                a_amt  = st.number_input(f"Amount ({a_sym})",
                                         min_value=0.0, max_value=MAX_AMOUNT,
                                         step=10.0, format="%.2f", value=0.0)
            with c2:
                a_rate = st.number_input("Annual interest rate (%)", min_value=0.0,
                                         max_value=100.0, step=0.01, format="%.2f",
                                         value=3.0,
                                         help="Fixed rate for the whole term, compounded monthly")
                a_start = st.date_input("Start date", value=today, key="savacc_start")
                _mat_default = date(today.year + 1, today.month,
                                    min(today.day, calendar.monthrange(today.year + 1, today.month)[1]))
                a_mat   = st.date_input("Maturity date", value=_mat_default,
                                        min_value=today, key="savacc_mat")
            a_notes = st.text_input("Notes (optional)")
            if st.form_submit_button("Open term deposit", icon=":material/lock:",
                                     type="primary", width="stretch"):
                if not a_name.strip():
                    st.error("Please give the account a name.")
                elif float(a_amt) <= 0:
                    st.error("Amount must be greater than 0.")
                elif a_mat <= a_start:
                    st.error("Maturity date must be after the start date.")
                else:
                    ae = to_eur(float(a_amt), a_cur, rates)
                    _fresh_savacc = q.savings_accounts(user_id)
                    if not _fresh_savacc.empty and (
                        (_fresh_savacc["name"] == a_name.strip())
                        & (_fresh_savacc["goal_name"] == a_goal)
                        & (_fresh_savacc["amount_eur"].round(2) == round(ae, 2))
                    ).any():
                        st.toast("Already saved — duplicate prevented.", icon=":material/check:")
                        st.rerun()
                    try:
                        # FIN-04: zero-sum — the account AND its goal debit
                        # are written in one transaction.
                        open_term_from_goal(user_id, a_goal, a_name.strip(), ae,
                                            float(a_rate), a_start, a_mat,
                                            currency=a_cur, amount=float(a_amt),
                                            notes=a_notes)
                    except InsufficientFunds as e:
                        st.error(f"Couldn't save: {e}")
                    except CommandError as e:
                        st.error(f"Couldn't save: {e}")
                    except Exception as e:
                        st.error(f"Couldn't save: {e}")
                    else:
                        q.bump_db_version()
                        mat_val = maturity_value(ae, float(a_rate), a_start, a_mat)
                        st.session_state["sav_flash"] = (
                            "success",
                            f"Term deposit **{a_name.strip()}** opened — "
                            f"{fmt(ae, DC, rates)} → {fmt(mat_val, DC, rates)} at maturity",
                        )
                        st.rerun()

    # FIN-05: only TRUE orphans (goal no longer exists) render outside their
    # goal panel — linked accounts already rendered above, exactly once.
    orphan_accs = accs[~accs["goal_name"].isin(set(goals))] if not accs.empty else accs
    if not orphan_accs.empty:
        st.caption("Unlinked term deposits (their goal no longer exists) — "
                   "withdraw or delete them to clean up:")
        for _, row in orphan_accs.iterrows():
            _render_account_card(row)
    elif goals:
        st.caption("No term deposits yet — open one above.")

    # ── Charts ────────────────────────────────────────────────────────────────
    st.divider()
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

    # ── Goal projections ─────────────────────────────────────────────────────
    st.subheader("Goal projections")
    proj_rows = []
    for g in goals:
        proj = savings_projection(dfs, g)
        if proj["months_to_goal"] and proj["months_to_goal"] > 0 and proj["projected_date"]:
            proj_rows.append({"goal": g, "date": today,
                              "balance": to_display(proj["current_balance"], DC, rates)})
            proj_rows.append({"goal": g, "date": proj["projected_date"],
                              "balance": to_display(proj["target"], DC, rates)})
    if proj_rows:
        pdf = pd.DataFrame(proj_rows)
        figp = px.line(pdf, x="date", y="balance", color="goal", markers=True,
                       labels={"balance": f"Balance ({SYM})", "date": "Date", "goal": "Goal"},
                       color_discrete_sequence=CHART_COLORS)
        figp.update_layout(legend_title_text="",
                           plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(figp, width="stretch")
    else:
        st.info("Set a target for a goal to see its projection (today → target date).")

    # ── Manage entries ───────────────────────────────────────────────────────
    with st.expander("Manage savings entries", icon=":material/manage_accounts:"):
        st.markdown("**Edit an entry** — balances recompute from that entry forward.")
        edit_ids = dfs["id"].tolist()
        edit_labels = [f"{r['date'].strftime('%d %b %Y') if pd.notna(r['date']) else '—'} — {r['goal_name']} {fmt(r['deposited_eur'], DC, rates)}"
                       for _, r in dfs.iterrows()]
        edit_idx = st.selectbox("Select entry", range(len(edit_labels)),
                                format_func=lambda i: edit_labels[i], key="sav_edit_sel")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Edit entry", icon=":material/edit:", key="sav_edit_btn",
                         width="stretch"):
                edit_savings_dialog(user_id, dfs.iloc[edit_idx])
        with c2:
            if st.button("Move to trash", icon=":material/delete:",
                         key="sav_del_btn", width="stretch"):
                try:
                    soft_delete_savings(user_id, edit_ids[edit_idx])
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                else:
                    q.bump_db_version()
                    st.toast("Savings entry moved to trash.", icon="🗑️")
                    st.rerun()

    df_deleted = q.savings(user_id, include_deleted=True)
    df_deleted = df_deleted[df_deleted["is_deleted"] == True]
    accs_deleted = q.savings_accounts(user_id, include_deleted=True)
    accs_deleted = accs_deleted[accs_deleted["is_deleted"] == True]
    if not df_deleted.empty or not accs_deleted.empty:
        with st.expander(f"Trash (savings: {len(df_deleted)} · term deposits: {len(accs_deleted)})",
                         icon=":material/delete:"):
            for _, row in df_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"{row['goal_name']} — {row['date'].strftime('%d %b %Y') if pd.notna(row['date']) else '—'}")
                with rc2: st.write(fmt(row["deposited_eur"], DC, rates))
                with rc3:
                    if st.button("Restore", icon=":material/undo:", key=f"rst_sav_{row['id']}", width="stretch"):
                        try:
                            restore_savings(user_id, row["id"])
                        except Exception as e:
                            st.error(f"Couldn't save: {e}")
                        else:
                            q.bump_db_version()
                            st.toast("Savings entry restored!", icon="↩️")
                            st.rerun()
            for _, row in accs_deleted.iterrows():
                rc1, rc2, rc3 = st.columns([3, 2, 1])
                with rc1: st.write(f"🔒 {row['name']} — {row['goal_name']}")
                with rc2: st.write(fmt(row["amount_eur"], DC, rates))
                with rc3:
                    if st.button("Restore", icon=":material/undo:", key=f"rst_acc_{row['id']}", width="stretch"):
                        try:
                            restore_savings_account(user_id, row["id"])
                        except Exception as e:
                            st.error(f"Couldn't save: {e}")
                        else:
                            q.bump_db_version()
                            st.toast("Term deposit restored!", icon="↩️")
                            st.rerun()

    with st.expander("Export", icon=":material/download:"):
        st.download_button("Download savings.xlsx", icon=":material/download:", data=to_excel(dfs),
                           file_name="savings.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if not accs.empty:
            st.download_button("Download term deposits.xlsx", icon=":material/download:",
                               data=to_excel(accs),
                               file_name="term_deposits.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("No savings yet — log your first deposit above")
