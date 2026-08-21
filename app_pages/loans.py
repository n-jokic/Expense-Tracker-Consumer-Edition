"""
Loans page: define loans (principal, rate, term) and log monthly payments.
Payments are ordinary expenses linked to the loan, so the amortization
schedule recomputes automatically from the real payment history.
"""

from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_loan, update_loan, delete_loan
from finance import (annuity_payment, loan_schedule, _first_due, _next_due,
                     calculate_early_repayment_surcharge)
from services.commands import (CommandError, archive_loan,
                               record_loan_payment, reopen_loan)
from utils import (
    SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
    fmt, to_eur, get_currency_symbol,
    help_expander,
)
from ui.panel import PanelSpec, panel

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
today   = date.today()

st.title(":material/account_balance: Loans")
st.caption("Track loans with real amortization — payments are logged as expenses, "
           "so missed or partial payments automatically extend the payoff date.")
help_expander("How loans work",
              "Enter the principal, annual interest rate, duration and payment day. "
              "The app computes the monthly annuity payment and simulates the balance "
              "month by month against your actual logged payments. Email reminders go "
              "out a few days before the payment day (Settings → Notifications).")

if (flash := st.session_state.pop("loan_flash", None)):
    if flash[0] == "success":
        st.success(flash[1], icon=":material/check_circle:")
    else:
        st.toast(flash[1], icon=":material/check_circle:")

# ── Add loan ──────────────────────────────────────────────────────────────────
# Currency + surcharge type OUTSIDE the form so Amount label / value bounds match selection immediately.
l_cur = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="loan_cur")
l_surcharge_type = st.selectbox(
    "Early repayment surcharge",
    ["fixed", "percent"],
    format_func=lambda v: "Fixed amount" if v == "fixed" else "Percentage",
    key="loan_surcharge_type",
)
_surch_max = 100.0 if l_surcharge_type == "percent" else MAX_SAVINGS_TARGET
_surch_step = 0.1 if l_surcharge_type == "percent" else 10.0
_l_sym = get_currency_symbol(l_cur)
with st.form(f"loan_form_{l_cur}_{l_surcharge_type}", clear_on_submit=True):
    st.markdown("**:material/add: New loan**")
    c1, c2 = st.columns(2)
    with c1:
        l_name    = st.text_input("Loan name", placeholder="e.g. Car loan")
        l_principal = st.number_input(f"Principal ({_l_sym})",
                                      min_value=0.0, max_value=MAX_SAVINGS_TARGET,
                                      step=100.0, format="%.2f", value=0.0)
        l_rate    = st.number_input("Annual interest rate (%)", min_value=0.0,
                                    max_value=100.0, step=0.01, format="%.2f", value=6.0)
    with c2:
        l_start   = st.date_input("Start date", value=today)
        l_term    = st.number_input("Duration (months)", min_value=1, max_value=600,
                                    value=36, step=1)
        l_day     = st.number_input("Payment day (1-31)", min_value=1, max_value=31,
                                    value=1, step=1)
        l_notes   = st.text_input("Notes (optional)")
        l_surcharge_value = st.number_input(
            "Surcharge value (% or loan currency)", min_value=0.0,
            max_value=_surch_max, step=_surch_step,
            format="%.2f", value=0.0,
        )
    if st.form_submit_button("Save loan", type="primary", width="stretch", icon=":material/save:"):
        if not l_name.strip():
            st.error("Please give the loan a name.")
        elif float(l_principal) <= 0:
            st.error("Principal must be greater than 0.")
        else:
            _fresh_loans = q.loans(user_id)
            if not _fresh_loans.empty and (
                (_fresh_loans["name"] == l_name.strip())
                & (_fresh_loans["principal_eur"].round(2) == round(to_eur(l_principal, l_cur, rates), 2))
            ).any():
                st.toast("Already saved — duplicate loan prevented.", icon=":material/check:")
                st.rerun()
            pe = to_eur(l_principal, l_cur, rates)
            try:
                add_loan(user_id, {
                    "name": l_name.strip(), "principal": l_principal, "currency": l_cur,
                    "principal_eur": pe, "annual_rate": l_rate,
                    "start_date": l_start, "term_months": int(l_term),
                    "payment_day": int(l_day), "status": "active", "notes": l_notes,
                    "early_repayment_surcharge_type": l_surcharge_type,
                    "early_repayment_surcharge_value": float(l_surcharge_value),
                })
            except Exception as e:
                st.error(f"Couldn't save: {e}")
            else:
                q.bump_db_version()
                st.session_state["loan_flash"] = (
                    "success",
                    f"Loan **{l_name}** saved "
                    f"(monthly payment ~{fmt(annuity_payment(pe, l_rate, int(l_term)), DC, rates)})",
                )
                st.rerun()


@st.dialog("Delete loan?")
def delete_loan_dialog(uid, loan_id, name, remaining):
    """Confirm deleting a loan; payments already logged stay as expenses."""
    st.write(f"Delete loan **{name}**?")
    st.caption(f"Remaining balance: **{remaining}** · "
               "The loan is removed, but payments already logged remain as expenses.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"loan_cancel_{loan_id}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Delete loan", key=f"loan_confirm_{loan_id}",
                     type="primary", width="stretch"):
            try:
                delete_loan(uid, loan_id)
            except Exception as e:
                st.error(f"Couldn't save: {e}")
                return
            q.bump_db_version()
            st.session_state["loan_flash"] = ("toast", "Loan deleted (payments remain as expenses).")
            st.rerun()


@st.dialog("Edit loan")
def edit_loan_dialog(uid: int, row):
    """Edit loan terms; logged payments stay untouched."""
    st.caption("Editing loan terms does not change logged payments — the payoff math simply recomputes.")
    c1, c2 = st.columns(2)
    with c1:
        e_name = st.text_input("Loan name", value=str(row["name"]), key=f"loan_edit_name_{row['id']}")
        e_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
                              index=list(SUPPORTED_CURRENCIES.keys()).index(str(row["currency"]))
                              if str(row["currency"]) in SUPPORTED_CURRENCIES else 0,
                              key=f"loan_edit_cur_{row['id']}")
        e_principal = st.number_input(f"Principal ({get_currency_symbol(e_cur)})",
                                      min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                      step=100.0, format="%.2f",
                                      value=0.01 if pd.isna(row["principal"]) else max(float(row["principal"]), 0.01),
                                      key=f"loan_edit_principal_{row['id']}")
        e_rate = st.number_input("Annual interest rate (%)", min_value=0.0,
                                 max_value=100.0, step=0.01, format="%.2f",
                                 value=float(row["annual_rate"]) if pd.notna(row["annual_rate"]) else 0.0,
                                 key=f"loan_edit_rate_{row['id']}")
    with c2:
        e_start = st.date_input("Start date",
                                value=row["start_date"].date() if pd.notna(row["start_date"]) else today,
                                key=f"loan_edit_start_{row['id']}")
        e_term = st.number_input("Duration (months)", min_value=1, max_value=600,
                                 value=int(row["term_months"]) if pd.notna(row["term_months"]) else 12,
                                 step=1, key=f"loan_edit_term_{row['id']}")
        e_day = st.number_input("Payment day (1-31)", min_value=1, max_value=31,
                                value=int(row["payment_day"]) if pd.notna(row["payment_day"]) else 1,
                                step=1, key=f"loan_edit_day_{row['id']}")
        # Status is NOT editable here: paid_off/archive transitions go through
        # the gated archive/reopen commands so the payoff invariant cannot be
        # bypassed by a free status selector.
    e_notes = st.text_input("Notes (optional)",
                            value=str(row["notes"]) if pd.notna(row["notes"]) else "",
                            key=f"loan_edit_notes_{row['id']}")
    e_surcharge_type = str(row.get("early_repayment_surcharge_type") or "fixed")
    if e_surcharge_type not in {"fixed", "percent"}:
        e_surcharge_type = "fixed"
    e_surcharge_type = st.selectbox(
        "Early repayment surcharge", ["fixed", "percent"],
        index=["fixed", "percent"].index(e_surcharge_type),
        format_func=lambda v: "Fixed amount" if v == "fixed" else "Percentage",
        key=f"loan_edit_surcharge_type_{row['id']}",
    )
    e_surcharge_raw = (float(row.get("early_repayment_surcharge_value") or 0.0)
                       if pd.notna(row.get("early_repayment_surcharge_value")) else 0.0)
    e_surcharge_max = 100.0 if e_surcharge_type == "percent" else MAX_SAVINGS_TARGET
    e_surcharge_value = st.number_input(
        "Surcharge value (% or loan currency)", min_value=0.0,
        max_value=e_surcharge_max,
        step=0.1 if e_surcharge_type == "percent" else 10.0,
        format="%.2f",
        value=min(e_surcharge_raw, e_surcharge_max),
        key=f"loan_edit_surcharge_value_{row['id']}",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"loan_edit_cancel_{row['id']}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Save", type="primary", key=f"loan_edit_save_{row['id']}", width="stretch"):
            if not e_name.strip():
                st.error("Please give the loan a name.")
            else:
                pe = to_eur(float(e_principal), e_cur, rates)
                try:
                    update_loan(uid, str(row["id"]), {
                        "name": e_name.strip(), "principal": float(e_principal),
                        "currency": e_cur, "principal_eur": pe,
                        "annual_rate": float(e_rate), "start_date": e_start,
                        "term_months": int(e_term), "payment_day": int(e_day),
                        "notes": e_notes,
                        "early_repayment_surcharge_type": e_surcharge_type,
                        "early_repayment_surcharge_value": float(e_surcharge_value),
                    })
                except Exception as e:
                    st.error(f"Couldn't save: {e}")
                    return
                q.bump_db_version()
                st.session_state["loan_flash"] = ("toast", f"Loan **{e_name.strip()}** updated.")
                st.rerun()


def _loan_payment_records(pay_df):
    """Return schedule inputs while retaining early-payment fee metadata."""
    records = []
    for _, payment in pay_df.iterrows():
        if pd.isna(payment.get("date")):
            continue
        records.append({
            "date": payment["date"].date(),
            "amount_eur": float(payment.get("amount_eur") or 0.0),
            "surcharge_eur": float(payment.get("loan_surcharge_eur") or 0.0),
            "type": str(payment.get("loan_payment_type") or "regular"),
        })
    return records


@st.dialog("Early repayment")
def early_repayment_dialog(uid: int, row, sched: dict, payments: list):
    """Log principal plus a configured fee without reducing balance by the fee."""
    lcur = str(row["currency"])
    lsym = get_currency_symbol(lcur)
    fx = float(rates.get(lcur, 1.0) or 1.0) if lcur != "EUR" else 1.0
    max_principal = sched["remaining_balance"] * fx
    if max_principal <= 0.005:
        st.info("This loan has no remaining principal.")
        return

    mode = str(row.get("early_repayment_surcharge_type") or "fixed")
    if mode not in {"fixed", "percent"}:
        mode = "fixed"
    configured = float(row.get("early_repayment_surcharge_value") or 0.0)
    p_date = st.date_input("Date", value=today, key=f"loan_early_date_{row['id']}")
    principal = st.number_input(
        f"Principal ({lsym})", min_value=0.01, max_value=max(max_principal, 0.01),
        value=min(max_principal, max(max_principal, 0.01)), step=10.0,
        format="%.2f", key=f"loan_early_amount_{row['id']}",
    )
    surcharge = calculate_early_repayment_surcharge(principal, mode, configured)
    principal_eur = to_eur(principal, lcur, rates)
    surcharge_eur = to_eur(surcharge, lcur, rates)
    st.caption(f"Principal: **{fmt(principal_eur, DC, rates)}** · "
               f"Surcharge: **{fmt(surcharge_eur, DC, rates)}** · "
               f"Total expense: **{fmt(principal_eur + surcharge_eur, DC, rates)}**")
    if p_date > today:
        st.warning("Early repayments must be dated today or earlier.")
    if st.button("Log early repayment", type="primary",
                 key=f"loan_early_save_{row['id']}", width="stretch"):
        if p_date > today:
            st.error("Choose today or an earlier date.")
            return
        total_eur = principal_eur + surcharge_eur
        _pf_early = q.loan_payments(uid, str(row["id"]))
        if not _pf_early.empty and (
            (_pf_early["date"].dt.date == p_date) & (_pf_early["amount_eur"].round(2) == round(total_eur, 2))
        ).any():
            st.toast("Already saved — duplicate payment prevented.", icon=":material/check:")
            st.rerun()
        # ONE atomic command: expense + audited split + payoff flip.
        try:
            record_loan_payment(
                uid, str(row["id"]), total_eur, p_date,
                surcharge_eur=surcharge_eur, payment_type="early",
                currency=lcur, amount=float(principal + surcharge),
                notes="Early repayment",
            )
        except CommandError as e:
            st.error(f"Couldn't save: {e}")
            return
        except Exception as e:
            st.error(f"Couldn't save: {e}")
            return
        q.bump_db_version()
        st.session_state["loan_flash"] = ("success", f"Early repayment logged for {row['name']}")
        st.rerun()


# ── Loan list (active first, paid-off loans under Archived) ──────────────────
df_loans = q.loans(user_id)
if df_loans.empty:
    st.info("No loans yet — add one above")
else:
    if "status" in df_loans.columns:
        _active_rows = df_loans[df_loans["status"].astype(str) != "paid_off"]
        _archived_rows = df_loans[df_loans["status"].astype(str) == "paid_off"]
        df_loans = pd.concat([_active_rows, _archived_rows], ignore_index=True)
    total_debt = 0.0
    debt_free_dates = []
    _archive_shown = False
    for _, row in df_loans.iterrows():
        if row["status"] == "paid_off" and not _archive_shown:
            _archive_shown = True
            st.divider()
            st.subheader(":material/archive: Archived")
            st.caption("Paid-off loans keep their full payment and audit "
                       "history. Reopen one to resume active calculations.")
        loan_id = str(row["id"])
        pay_df = q.loan_payments(user_id, loan_id)
        payments = _loan_payment_records(pay_df)
        start_date = (row["start_date"].date() if pd.notna(row["start_date"])
                      else date.today())
        _principal = float(row["principal_eur"]) if pd.notna(row["principal_eur"]) else 0.0
        _rate = float(row["annual_rate"]) if pd.notna(row["annual_rate"]) else 0.0
        sched = loan_schedule(
            _principal, _rate,
            int(row["term_months"]), start_date,
            int(row["payment_day"]), payments, today)

        if row["status"] == "active":
            total_debt += sched["remaining_balance"]
            if sched["payoff_date"]:
                debt_free_dates.append(sched["payoff_date"])

        repaid_pct = (1 - sched["remaining_balance"] / float(row["principal_eur"])) * 100 \
            if row["principal_eur"] > 0 else 0.0
        repaid_pct = min(max(repaid_pct, 0.0), 100.0)

        status_icon = "✅" if row["status"] == "paid_off" else "🏦"
        spec = PanelSpec(
            id=f"loan_{loan_id}",
            title=str(row["name"]),
            icon=status_icon,
            collapsible=True,
            default_expanded=True,
            reorderable=True,
            summary=f"{float(row['annual_rate']):.2f}% · {int(row['term_months'])} mo",
            badge=f"{repaid_pct:.0f}% repaid",
        )
        expanded, content = panel(spec, user_id=user_id, area="loans")
        if not expanded:
            continue
        with content:
            h1, h2, h3 = st.columns([3, 1.4, 1])
            with h1:
                st.progress(repaid_pct / 100, text=f"{repaid_pct:.0f}% repaid")
                payoff_str = (sched["payoff_date"].strftime("%b %Y")
                              if sched["payoff_date"] else "—")
                overdue = False
                if row["status"] == "active":
                    month_paid = any((p["date"].year == today.year and p["date"].month == today.month)
                                     for p in payments)
                    # Overdue only once the CURRENT month's due date (clamped
                    # to the month's length, e.g. the 31st -> Feb 28) has
                    # actually passed and no payment was logged this month.
                    # Loans whose first due date hasn't arrived yet are never
                    # overdue.
                    first_due = _first_due(start_date, int(row["payment_day"]))
                    if first_due <= today:
                        k = ((today.year - first_due.year) * 12
                             + (today.month - first_due.month))
                        due_this_month = _next_due(start_date, int(row["payment_day"]), k)
                        overdue = (not month_paid and today > due_this_month)
                mo_str = ("never at this payment"
                          if sched["remaining_months"] == 0
                          and sched["remaining_balance"] > 0.005
                          else f"{sched['remaining_months']} mo")
                st.caption(
                    f"Monthly: **{fmt(sched['monthly_payment'], DC, rates)}** · "
                    f"Remaining: **{fmt(sched['remaining_balance'], DC, rates)}** "
                    f"({mo_str}) · "
                    f"Payoff: **{payoff_str}** · "
                    f"Interest paid (incl. fees): {fmt(sched['total_interest_paid'], DC, rates)} · "
                    f"Next split: {fmt(sched['next_payment_interest'], DC, rates)} interest + "
                    f"{fmt(sched['next_payment_principal'], DC, rates)} principal · "
                    f"Total cost: {fmt(sched['total_cost'], DC, rates)}"
                    + (" · ⚠️ payment overdue" if overdue else "")
                )
            with h2:
                st.metric("Remaining", fmt(sched["remaining_balance"], DC, rates))
            with h3:
                if row["status"] == "active":
                    with st.popover("Log payment", key=f"loan_pay_{loan_id}"):
                        lcur = str(row["currency"])
                        lsym = get_currency_symbol(lcur)
                        # prefill the expected payment converted into the loan's currency
                        monthly_in_cur = float(sched["monthly_payment"])
                        if lcur != "EUR":
                            monthly_in_cur = monthly_in_cur * (rates.get(lcur, 1.0) or 1.0)
                        st.markdown(f"Expected payment: {fmt(sched['monthly_payment'], DC, rates)}")
                        st.caption(f"Next installment: {fmt(sched['next_payment_interest'], DC, rates)} interest · "
                                   f"{fmt(sched['next_payment_principal'], DC, rates)} principal")
                        p_date = st.date_input("Date", value=today, key=f"loan_pd_{loan_id}")
                        p_amt  = st.number_input(f"Amount ({lsym})",
                                                 value=max(monthly_in_cur, 0.01),
                                                 min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                                 step=10.0, format="%.2f", key=f"loan_pa_{loan_id}")
                        if st.button("Log", icon=":material/check:", key=f"loan_pc_{loan_id}", type="primary", width="stretch"):
                            # the user-typed amount is already in the loan's currency
                            amount_in_cur = float(p_amt)
                            ae = to_eur(amount_in_cur, lcur, rates)
                            _pf_log = q.loan_payments(user_id, loan_id)
                            if not _pf_log.empty and (
                                (_pf_log["date"].dt.date == p_date) & (_pf_log["amount_eur"].round(2) == round(ae, 2))
                            ).any():
                                st.toast("Already saved — duplicate payment prevented.", icon=":material/check:")
                                st.rerun()
                            try:
                                # ONE atomic command: expense + audited
                                # principal/interest split + payoff flip.
                                record_loan_payment(
                                    user_id, loan_id, ae, p_date,
                                    currency=lcur, amount=amount_in_cur,
                                    notes="Loan payment",
                                )
                            except CommandError as e:
                                st.error(str(e))
                            except Exception as e:
                                st.error(f"Couldn't save: {e}")
                            else:
                                q.bump_db_version()
                                st.session_state["loan_flash"] = ("toast", f"Payment logged for {row['name']}")
                                st.rerun()
                    if st.button("Early repayment", key=f"loan_early_{loan_id}",
                                 icon=":material/payments:", width="stretch"):
                        early_repayment_dialog(user_id, row, sched, payments)
                else:
                    st.success("Paid off ✓")

            c1, c2 = st.columns(2)
            with c1:
                with st.expander(f"Payments ({len(payments)})", icon=":material/receipt:"):
                    if payments:
                        # NB: pay_date/pay_amt, not p_date/p_amt — those are
                        # the "Log payment" popover widgets in this same scope.
                        for payment in sorted(payments, key=lambda p: p["date"], reverse=True):
                            label = ("Early repayment" if payment["type"] == "early"
                                     else "Payment")
                            fee = (f" · fee {fmt(payment['surcharge_eur'], DC, rates)}"
                                   if payment["surcharge_eur"] > 0 else "")
                            st.write(f"{payment['date'].strftime('%d %b %Y')} — {label}: "
                                     f"{fmt(payment['amount_eur'], DC, rates)}{fee}")
                    else:
                        st.caption("No payments logged yet.")
            with c2:
                with st.container(horizontal=True):
                    st_lbl, st_icon = ("Mark paid off", ":material/check_circle:") \
                        if row["status"] == "active" else ("Reopen", ":material/undo:")
                    if st.button(st_lbl, icon=st_icon, key=f"loan_st_{loan_id}"):
                        # Gated transitions: archive requires the payoff
                        # invariant (remaining ≤ €0.01); reopen restores the
                        # active calculations. Rejections surface as errors.
                        try:
                            if row["status"] == "active":
                                archive_loan(user_id, loan_id)
                            else:
                                reopen_loan(user_id, loan_id)
                        except CommandError as e:
                            st.error(str(e))
                        except Exception as e:
                            st.error(f"Couldn't save: {e}")
                        else:
                            q.bump_db_version()
                            st.rerun()
                    if st.button("Delete", icon=":material/delete:", key=f"loan_del_{loan_id}"):
                        delete_loan_dialog(user_id, loan_id, str(row["name"]),
                                           fmt(sched["remaining_balance"], DC, rates))
                    if st.button("Edit", icon=":material/edit:", key=f"loan_edit_{loan_id}"):
                        edit_loan_dialog(user_id, row)

    if total_debt > 0:
        free_date = max(debt_free_dates).strftime("%b %Y") if debt_free_dates else "—"
        with st.container(border=True):
            with st.container(horizontal=True):
                st.metric("Total debt", fmt(total_debt, DC, rates))
                st.metric("Debt-free by", free_date)
    else:
        st.success("🎉 You're debt-free!")
