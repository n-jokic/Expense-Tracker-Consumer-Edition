"""
Settings page: currency & rates, budgets, notifications, account, data export/backup.
"""

import calendar
import os
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from db import (
    add_budget, delete_budget, get_budgets, BACKUP_DIR,
    update_user_display_name, delete_user_account, backup_db,
)
from auth import change_password, logout
from notifications import render_notification_settings
from rates import refresh_rates_if_due
from utils import (
    CATEGORIES, CAT_LIST, SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET,
    fmt, to_eur, get_currency_symbol,
    safe_error, to_excel,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
display_name = st.session_state.display_name

st.title("⚙️ Settings")

tab_cur, tab_bud, tab_notif, tab_acct, tab_data = st.tabs(
    ["💱 Currency", "💰 Budget", "📧 Notifications", "🔐 Account", "📦 Data"]
)

# ── Currency tab ──────────────────────────────────────────────────────────────
with tab_cur:
    st.subheader("💱 Currency & exchange rates")
    st.caption("Amounts are stored in EUR; these rates convert them for display. "
               "They refresh automatically on login when older than 3 days.")
    with st.form("cur_form"):
        dc2 = st.selectbox("Default display currency",
                            list(SUPPORTED_CURRENCIES.keys()),
                            index=list(SUPPORTED_CURRENCIES.keys()).index(
                                settings.get("default_currency","EUR")))
        st.markdown("**Rates (1 EUR = ?)**")
        new_rates = {}
        for c in [c for c in SUPPORTED_CURRENCIES if c != "EUR"]:
            new_rates[c] = st.number_input(
                f"1 EUR = ? {c} ({get_currency_symbol(c)})",
                value=float(rates.get(c, 1.0)), step=0.01, format="%.4f")
        if st.form_submit_button("💾 Save", type="primary"):
            q.save_settings(user_id, {"default_currency": dc2, "currency_rates": new_rates})
            st.success("✅ Saved — rates updated for every page.")
            st.rerun()

    st.divider()
    last = settings.get("rates_updated_at")
    if last is not None:
        try:
            last_str = pd.Timestamp(last).strftime("%d %b %Y %H:%M")
        except Exception:
            last_str = str(last)
        st.caption(f"🕐 Rates last updated from the live API: **{last_str}**")
    else:
        st.caption("🕐 Rates never fetched from the live API — using built-in defaults.")

    c_ref, _ = st.columns([1, 2])
    with c_ref:
        if st.button("🔄 Refresh rates now", key="refresh_rates_btn"):
            new_settings, ok = refresh_rates_if_due(user_id, st.session_state.settings, force=True)
            if ok:
                got = new_settings.get("currency_rates") or {}
                st.success(f"✅ Rates refreshed! 1 EUR = {float(got.get('RSD', 0)):,.2f} din")
                st.rerun()
            else:
                st.error("😕 Couldn't reach the rate service — keeping your last known rates. "
                         "Check your internet connection and try again.")

# ── Budget tab ────────────────────────────────────────────────────────────────
with tab_bud:
    st.subheader("💰 Overall monthly budget")
    cur_eur = float(settings.get("monthly_budget", 0.0))
    with st.form("overall_bud_form"):
        ob_amt = st.number_input("Total monthly budget (€)", min_value=0.0,
                                 max_value=MAX_SAVINGS_TARGET,
                                 step=50.0, format="%.2f", value=cur_eur)
        if st.form_submit_button("💾 Save budget", type="primary"):
            q.save_settings(user_id, {"monthly_budget": ob_amt})
            st.success(f"✅ Budget set to {fmt(ob_amt, DC, rates)}")
            st.rerun()

    st.divider()
    st.subheader("Category budgets")
    bcat = st.selectbox("Category", CAT_LIST, key="bud_cat")
    bcur = st.selectbox("Enter in", list(SUPPORTED_CURRENCIES.keys()), key="bud_cur")
    with st.form("cat_bud_form", clear_on_submit=False):
        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            by = st.number_input("Year",  value=date.today().year,  step=1, format="%d")
            bm = st.selectbox("Month", range(1,13),
                              format_func=lambda x: calendar.month_name[x])
        with bc2:
            bsub = st.selectbox("Subcategory",
                                ["(entire category)"] + CATEGORIES[bcat])
        with bc3:
            ba = st.number_input(f"Budget ({get_currency_symbol(bcur)})", min_value=0.0,
                                 max_value=MAX_SAVINGS_TARGET, step=10.0, format="%.2f")
        if st.form_submit_button("💾 Save", type="primary"):
            be = to_eur(ba, bcur, rates)
            add_budget(user_id, {
                "year": int(by), "month": int(bm), "category": bcat,
                "subcategory": bsub if bsub != "(entire category)" else "",
                "budgeted_eur": be,
            })
            q.bump_db_version()
            st.success("✅ Budget saved")
            st.rerun()

    dfb = q.budgets(user_id)
    if not dfb.empty:
        d = dfb.copy()
        d["month"]  = d["month"].apply(lambda x: calendar.month_name[int(x)])
        d["Budget"] = d["budgeted_eur"].apply(lambda x: fmt(x, DC, rates))
        st.dataframe(d[["year","month","category","subcategory","Budget"]], hide_index=True)
        with st.expander("🗑️ Delete a budget row"):
            di = st.number_input("Row index (from table)", min_value=0,
                                 max_value=max(0, len(dfb)-1), step=1)
            if st.button("Delete", type="secondary", key="del_bud"):
                bid = int(dfb.iloc[di]["id"])
                delete_budget(user_id, bid)
                q.bump_db_version()
                st.toast("Budget row deleted.", icon="🗑️")
                st.rerun()

# ── Notifications tab ─────────────────────────────────────────────────────────
with tab_notif:
    render_notification_settings(user_id, settings)

# ── Account tab ───────────────────────────────────────────────────────────────
with tab_acct:
    st.subheader("🔐 Account")

    with st.form("display_name_form"):
        new_name = st.text_input("Display name", value=display_name)
        if st.form_submit_button("💾 Update name"):
            if new_name.strip():
                update_user_display_name(user_id, new_name.strip())
                st.session_state.display_name = new_name.strip()
                st.success("✅ Name updated!")
                st.rerun()

    st.divider()
    st.subheader("Change password")
    with st.form("pw_form"):
        old_pw  = st.text_input("Current password", type="password")
        new_pw  = st.text_input("New password", type="password",
                                placeholder="min. 8 chars, one number")
        conf_pw = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("🔒 Change password", type="primary"):
            if new_pw != conf_pw:
                safe_error("New passwords don't match.")
            else:
                ok, msg = change_password(user_id, old_pw, new_pw)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    safe_error(msg)

    st.divider()
    st.subheader("⚠️ Danger zone")
    with st.expander("🗑️ Delete my account"):
        st.error("This will permanently delete **all** your data. This cannot be undone.")
        confirm = st.text_input("Type DELETE to confirm")
        if st.button("Delete account permanently", type="secondary"):
            if confirm == "DELETE":
                delete_user_account(user_id)
                logout()
                st.rerun()
            else:
                safe_error("Please type DELETE exactly to confirm.")

# ── Data tab ──────────────────────────────────────────────────────────────────
with tab_data:
    st.subheader("📦 Export your data")
    st.caption("Download your data as Excel files. Back these up to Google Drive or OneDrive regularly.")

    data_map = {
        "expenses":  q.expenses(user_id, include_deleted=True),
        "income":    q.income(user_id, include_deleted=True),
        "savings":   q.savings(user_id, include_deleted=True),
        "budgets":   q.budgets(user_id),
        "recurring": q.recurring(user_id),
    }
    cols = st.columns(len(data_map))
    for i, (key, df_d) in enumerate(data_map.items()):
        with cols[i]:
            if not df_d.empty:
                st.download_button(
                    f"⬇️ {key}", data=to_excel(df_d),
                    file_name=f"{key}_export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_{key}", width="stretch",
                )
            else:
                st.button(f"{key} (empty)", disabled=True,
                          width="stretch", key=f"dis_{key}")

    st.divider()
    st.subheader("💾 Database backup")
    st.caption("A backup is saved automatically once per day. You can also create one now.")
    marker = os.path.join(BACKUP_DIR, ".last_backup")
    try:
        with open(marker, "r", encoding="utf-8") as f:
            st.caption(f"Last automatic backup: **{f.read().strip()}**")
    except OSError:
        pass
    if st.button("💾 Back up database now"):
        path = backup_db(force=True)
        if path:
            st.success(f"✅ Backup saved to `{path}`")
        else:
            st.info("Backups are only available with the local SQLite database.")

    st.divider()
    st.subheader("📤 Audit log export")
    df_audit_exp = q.audit(user_id, limit=10000)
    if not df_audit_exp.empty:
        st.download_button("⬇️ audit_log.xlsx", data=to_excel(df_audit_exp),
                           file_name="audit_log.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
