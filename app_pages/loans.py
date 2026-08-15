"""
Loans page: define loans (principal, rate, term) and log monthly payments.
Payments are ordinary expenses linked to the loan, so the amortization
schedule recomputes automatically from the real payment history.
"""

from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import add_loan, update_loan, delete_loan, add_expense
from finance import annuity_payment, loan_schedule
from utils import (
    SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
    fmt, pbar, to_eur, get_currency_symbol,
    help_expander,
)

user_id = st.session_state.user_id
DC      = st.session_state.dc
rates   = st.session_state.rates
today   = date.today()

st.title("🏦 Loans")
st.caption("Track loans with real amortization — payments are logged as expenses, "
           "so missed or partial payments automatically extend the payoff date.")
help_expander("How loans work",
              "Enter the principal, annual interest rate, duration and payment day. "
              "The app computes the monthly annuity payment and simulates the balance "
              "month by month against your actual logged payments. Email reminders go "
              "out a few days before the payment day (Settings → Notifications).")

# ── Add loan ──────────────────────────────────────────────────────────────────
with st.form("loan_form", clear_on_submit=False):
    st.markdown("**➕ New loan**")
    c1, c2 = st.columns(2)
    with c1:
        l_name    = st.text_input("Loan name", placeholder="e.g. Car loan")
        l_cur     = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="loan_cur")
        l_principal = st.number_input(f"Principal ({get_currency_symbol(l_cur)})",
                                      min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                      step=100.0, format="%.2f")
        l_rate    = st.number_input("Annual interest rate (%)", min_value=0.0,
                                    max_value=100.0, step=0.01, format="%.2f", value=6.0)
    with c2:
        l_start   = st.date_input("Start date", value=today)
        l_term    = st.number_input("Duration (months)", min_value=1, max_value=600,
                                    value=36, step=1)
        l_day     = st.number_input("Payment day (1-31)", min_value=1, max_value=31,
                                    value=1, step=1)
        l_notes   = st.text_input("Notes (optional)")
    if st.form_submit_button("💾 Save loan", type="primary"):
        if l_name.strip():
            pe = to_eur(l_principal, l_cur, rates)
            add_loan(user_id, {
                "name": l_name.strip(), "principal": l_principal, "currency": l_cur,
                "principal_eur": pe, "annual_rate": l_rate,
                "start_date": l_start, "term_months": int(l_term),
                "payment_day": int(l_day), "status": "active", "notes": l_notes,
            })
            q.bump_db_version()
            st.success(f"✅ Loan **{l_name}** saved "
                       f"(monthly payment ~{fmt(annuity_payment(pe, l_rate, int(l_term)), DC, rates)})")
            st.rerun()
        else:
            st.error("Please give the loan a name.")

# ── Loan list ─────────────────────────────────────────────────────────────────
df_loans = q.loans(user_id)
if df_loans.empty:
    st.info("No loans yet — add one above 👆")
else:
    st.divider()
    total_debt = 0.0
    debt_free_dates = []
    for _, row in df_loans.iterrows():
        loan_id = str(row["id"])
        pay_df = q.loan_payments(user_id, loan_id)
        payments = [(r["date"].date(), float(r["amount_eur"]))
                    for _, r in pay_df.iterrows() if pd.notna(r["date"])]
        start_date = (row["start_date"].date() if pd.notna(row["start_date"])
                      else date.today())
        sched = loan_schedule(
            float(row["principal_eur"]), float(row["annual_rate"]),
            int(row["term_months"]), start_date,
            int(row["payment_day"]), payments, today)

        if row["status"] == "active":
            total_debt += sched["remaining_balance"]
            if sched["payoff_date"]:
                debt_free_dates.append(sched["payoff_date"])

        repaid_pct = (1 - sched["remaining_balance"] / float(row["principal_eur"])) * 100 \
            if row["principal_eur"] > 0 else 0.0
        repaid_pct = min(max(repaid_pct, 0.0), 100.0)

        with st.container(border=True):
            h1, h2, h3 = st.columns([3, 1.4, 1])
            with h1:
                status_icon = "✅" if row["status"] == "paid_off" else "🏦"
                st.markdown(f"{status_icon} **{row['name']}** — "
                            f"{float(row['annual_rate']):.2f}% · "
                            f"{int(row['term_months'])} mo")
                st.markdown(pbar(repaid_pct, "#0F3460"), unsafe_allow_html=True)
                payoff_str = (sched["payoff_date"].strftime("%b %Y")
                              if sched["payoff_date"] else "—")
                overdue = False
                if row["status"] == "active":
                    month_paid = any((p[0].year == today.year and p[0].month == today.month)
                                     for p in payments)
                    overdue = (not month_paid and today.day > int(row["payment_day"]))
                st.caption(
                    f"Monthly: **{fmt(sched['monthly_payment'], DC, rates)}** · "
                    f"Remaining: **{fmt(sched['remaining_balance'], DC, rates)}** "
                    f"({sched['remaining_months']} mo) · "
                    f"Payoff: **{payoff_str}** · "
                    f"Interest paid: {fmt(sched['total_interest_paid'], DC, rates)} · "
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
                        p_date = st.date_input("Date", value=today, key=f"loan_pd_{loan_id}")
                        p_amt  = st.number_input(f"Amount ({lsym})",
                                                 value=monthly_in_cur,
                                                 min_value=0.01, max_value=MAX_SAVINGS_TARGET,
                                                 step=10.0, format="%.2f", key=f"loan_pa_{loan_id}")
                        if st.button("✅ Log", key=f"loan_pc_{loan_id}", type="primary", width="stretch"):
                            # the user-typed amount is already in the loan's currency
                            amount_in_cur = float(p_amt)
                            ae = to_eur(amount_in_cur, lcur, rates)
                            add_expense(user_id, {
                                "date": p_date,
                                "category": "Loans & Debt",
                                "subcategory": "Loan Repayment",
                                "description": f"{row['name']} payment",
                                "amount": amount_in_cur,
                                "currency": lcur,
                                "amount_eur": ae,
                                "recurring": False,
                                "loan_id": loan_id,
                                "notes": "Loan payment",
                            })
                            q.bump_db_version()
                            st.toast(f"✅ Payment logged for {row['name']}", icon="🏦")
                            st.rerun()
                else:
                    st.success("Paid off ✓")

            c1, c2 = st.columns(2)
            with c1:
                with st.expander(f"🧾 Payments ({len(payments)})"):
                    if payments:
                        for p_date, amt in sorted(payments, reverse=True):
                            st.write(f"{p_date.strftime('%d %b %Y')} — {fmt(amt, DC, rates)}")
                    else:
                        st.caption("No payments logged yet.")
            with c2:
                bc1, bc2 = st.columns(2)
                with bc1:
                    new_status = "paid_off" if row["status"] == "active" else "active"
                    lbl = "✅ Mark paid off" if row["status"] == "active" else "↩️ Reopen"
                    if st.button(lbl, key=f"loan_st_{loan_id}", width="stretch"):
                        update_loan(user_id, loan_id, {"status": new_status})
                        q.bump_db_version()
                        st.rerun()
                with bc2:
                    if st.button("🗑️ Delete", key=f"loan_del_{loan_id}", width="stretch"):
                        delete_loan(user_id, loan_id)
                        q.bump_db_version()
                        st.toast("Loan deleted (payments remain as expenses).", icon="🗑️")
                        st.rerun()

    st.divider()
    if total_debt > 0:
        dk1, dk2 = st.columns(2)
        with dk1:
            st.markdown(f'<div class="kpi"><div class="kpi-lbl">Total debt</div>'
                        f'<div class="kpi-val neg">{fmt(total_debt, DC, rates)}</div></div>',
                        unsafe_allow_html=True)
        with dk2:
            free_date = max(debt_free_dates).strftime("%b %Y") if debt_free_dates else "—"
            st.markdown(f'<div class="kpi"><div class="kpi-lbl">Debt-free by</div>'
                        f'<div class="kpi-val pos">{free_date}</div></div>',
                        unsafe_allow_html=True)
    else:
        st.success("🎉 You're debt-free!")
