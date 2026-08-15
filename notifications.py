"""
notifications.py — Email alerts and bill reminders for Expense Tracker v3.

SMTP passwords are stored encrypted (Fernet) and emails are sent from a
background thread so the UI never blocks on a slow SMTP server.
"""

import os
import base64
import hashlib
import smtplib
import threading
import calendar
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, timedelta

import streamlit as st
import pandas as pd

import queries as q
from db import BASE_DIR
from utils import NEAR_LIMIT_THRESHOLD


# ── SMTP password encryption (Fernet) ─────────────────────────────────────────

def _fernet_key() -> bytes:
    try:
        secret = st.secrets.get("encryption_key")
    except Exception:
        secret = None
    if secret:
        digest = hashlib.sha256(str(secret).encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    key_path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read()
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key)
    return key


def _encrypt(plain: str) -> str:
    if not plain:
        return ""
    from cryptography.fernet import Fernet
    return Fernet(_fernet_key()).encrypt(plain.encode("utf-8")).decode("utf-8")


def _decrypt(enc: str) -> str:
    if not enc:
        return ""
    from cryptography.fernet import Fernet
    try:
        return Fernet(_fernet_key()).decrypt(enc.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


# ── Core email sender ─────────────────────────────────────────────────────────

def send_email(smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
               to_email: str, subject: str, html_body: str) -> tuple[bool, str]:
    try:
        msg                    = MIMEMultipart("alternative")
        msg["Subject"]         = subject
        msg["From"]            = smtp_user
        msg["To"]              = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())
        return True, "OK"
    except Exception as e:
        return False, str(e)


def send_email_async(*args):
    """Fire-and-forget email from a daemon thread; never blocks the UI."""
    threading.Thread(target=send_email, args=args, daemon=True).start()


# ── Email template helpers ────────────────────────────────────────────────────

def _html_wrap(title: str, body: str) -> str:
    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#333;">
      <div style="background:#0F3460;border-radius:12px;padding:20px;text-align:center;margin-bottom:24px;">
        <h1 style="color:#fff;margin:0;font-size:22px;">💰 Expense Tracker</h1>
      </div>
      <h2 style="color:#0F3460;">{title}</h2>
      {body}
      <hr style="margin-top:32px;border:none;border-top:1px solid #eee;">
      <p style="color:#aaa;font-size:11px;">You received this because email alerts are enabled in your settings.</p>
    </body></html>
    """


def build_budget_alert_email(display_name: str, category: str,
                              spent_eur: float, budget_eur: float, rate: float) -> str:
    pct  = int(spent_eur / budget_eur * 100) if budget_eur > 0 else 0
    over = spent_eur > budget_eur
    color = "#E94560" if over else "#F4A261"
    status = "exceeded" if over else f"at {pct}%"
    body = f"""
    <p>Hi {display_name},</p>
    <p>Your <strong>{category}</strong> budget is <span style="color:{color};font-weight:bold;">{status}</span>.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr style="background:#f5f5f5;">
        <td style="padding:10px;border-radius:6px 0 0 6px;"><strong>Spent</strong></td>
        <td style="padding:10px;color:#E94560;font-weight:bold;">€{spent_eur:,.2f}</td>
      </tr>
      <tr>
        <td style="padding:10px;"><strong>Budget</strong></td>
        <td style="padding:10px;color:#0F3460;font-weight:bold;">€{budget_eur:,.2f}</td>
      </tr>
    </table>
    <p>{'Consider cutting back on ' + category + ' spending for the rest of the month.' if over else 'You are close to your limit — keep an eye on ' + category + ' spending.'}</p>
    """
    return _html_wrap(f"⚠️ Budget Alert: {category}", body)


def build_bill_reminder_email(display_name: str, bill_name: str,
                               amount_str: str, due_note: str) -> str:
    body = f"""
    <p>Hi {display_name},</p>
    <p>A reminder that your recurring bill <strong>{bill_name}</strong> hasn't been logged yet this month.</p>
    <div style="background:#f5f7fa;border-left:4px solid #0F3460;padding:14px;border-radius:6px;margin:16px 0;">
      <strong>{bill_name}</strong> — {amount_str}<br>
      <span style="color:#888;font-size:13px;">{due_note}</span>
    </div>
    <p>Log it in the app when you're ready.</p>
    """
    return _html_wrap("🔔 Bill Reminder", body)


def build_weekly_summary_email(display_name: str, week_expenses_df: pd.DataFrame,
                                rate: float, DC: str) -> str:
    total = float(week_expenses_df["amount_eur"].sum()) if not week_expenses_df.empty else 0.0
    top3  = ""
    if not week_expenses_df.empty:
        cats = week_expenses_df.groupby("category")["amount_eur"].sum().nlargest(3)
        rows_html = "".join(
            f"<tr><td style='padding:8px;'>{cat}</td>"
            f"<td style='padding:8px;text-align:right;'>€{amt:,.2f}</td></tr>"
            for cat, amt in cats.items()
        )
        top3 = f"""
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr style="background:#f5f5f5;"><th style="padding:8px;text-align:left;">Category</th>
          <th style="padding:8px;text-align:right;">Amount</th></tr>
          {rows_html}
        </table>"""
    motivation = ("Great job keeping spending low this week! 🌟"
                  if total < 100 else
                  "Every euro tracked is a step toward your financial goals. 💪")
    body = f"""
    <p>Hi {display_name},</p>
    <p>Here's your weekly spending summary:</p>
    <div style="background:#0F3460;color:#fff;border-radius:10px;padding:16px;text-align:center;margin:16px 0;">
      <div style="font-size:13px;opacity:0.8;">Total spent this week</div>
      <div style="font-size:28px;font-weight:bold;">€{total:,.2f}</div>
    </div>
    {'<h3>Top categories:</h3>' + top3 if top3 else ''}
    <p style="color:#555;">{motivation}</p>
    """
    return _html_wrap("📊 Your Weekly Summary", body)


# ── In-app alert checkers ─────────────────────────────────────────────────────

def due_reminder_day(due_day: int, days_before: int, month_length: int) -> int:
    """Day of the month to send a bill reminder, clamped to the month length.

    due_day 29-31 in a short month remind on the last day; reminders never
    wrap before day 1.
    """
    day = int(due_day) - int(days_before)
    if day < 1:
        day = 1
    return min(day, int(month_length))


def _unlogged_templates(recurring_df: pd.DataFrame, expenses_df: pd.DataFrame,
                        today: date) -> list:
    """Active templates with no expense logged this month.

    Matching: expense.rec_template_id == template id (new rows), with a
    description+amount fallback for rows logged before template links existed.
    """
    active = recurring_df[recurring_df["active"] == True]
    if active.empty:
        return []

    template_ids = set()
    desc_amounts = set()
    if not expenses_df.empty:
        m_exp = expenses_df[(expenses_df["date"].dt.year == today.year) &
                             (expenses_df["date"].dt.month == today.month)]
        if "rec_template_id" in m_exp.columns:
            template_ids = set(m_exp["rec_template_id"].dropna().astype(str))
        desc_amounts = set(zip(m_exp["description"].str.strip().str.lower(),
                               m_exp["amount_eur"].round(2)))

    unlogged = []
    for _, row in active.iterrows():
        tid = str(row.get("id"))
        key = (str(row["description"]).strip().lower(),
               round(float(row["amount_eur"] or 0.0), 2))
        if tid in template_ids or key in desc_amounts:
            continue
        unlogged.append(row)
    return unlogged


def check_and_send_budget_alerts(user_id: int, expenses_df: pd.DataFrame,
                                  budgets_df: pd.DataFrame, settings: dict,
                                  rates: dict, DC: str):
    """Check budget limits and show toasts + optionally send emails."""
    if expenses_df.empty or budgets_df.empty:
        return

    today   = date.today()
    m_exp   = expenses_df[(expenses_df["date"].dt.year == today.year) &
                           (expenses_df["date"].dt.month == today.month)]
    m_bud   = budgets_df[(budgets_df["year"] == today.year) &
                          (budgets_df["month"] == today.month)]
    if m_exp.empty or m_bud.empty:
        return

    alerted_key = f"budget_alerted_{today.year}_{today.month}"
    if alerted_key not in st.session_state:
        st.session_state[alerted_key] = set()

    ca = m_exp.groupby("category")["amount_eur"].sum()
    cb = m_bud.groupby("category")["budgeted_eur"].sum()

    for cat in ca.index:
        bud_val = float(cb.get(cat, 0))
        act_val = float(ca.get(cat, 0))
        if bud_val <= 0:
            continue
        if act_val >= bud_val * NEAR_LIMIT_THRESHOLD and cat not in st.session_state[alerted_key]:
            st.session_state[alerted_key].add(cat)
            over = act_val > bud_val
            icon = "🔴" if over else "🟡"
            msg  = (f"{icon} **{cat}** budget {'exceeded' if over else 'nearly full'}: "
                    f"€{act_val:,.2f} of €{bud_val:,.2f}")
            st.toast(msg, icon="⚠️")

            # Send email if configured (background thread — never blocks the UI)
            if (settings.get("email_alerts") and settings.get("alert_email") and
                    settings.get("smtp_host") and settings.get("smtp_user")):
                html = build_budget_alert_email(
                    st.session_state.get("display_name", ""),
                    cat, act_val, bud_val, rates.get("RSD", 117.0)
                )
                send_email_async(
                    settings["smtp_host"], int(settings.get("smtp_port", 587)),
                    settings["smtp_user"], _decrypt(settings.get("smtp_password_enc") or ""),
                    settings["alert_email"],
                    f"Budget Alert: {cat} at {int(act_val/bud_val*100)}%", html
                )


def check_and_send_bill_reminders(user_id: int, recurring_df: pd.DataFrame,
                                   expenses_df: pd.DataFrame, settings: dict):
    """Show sidebar count of unlogged recurring bills and email reminders.

    Templates with a due_day are emailed N days before the due date (setting
    bill_reminder_days); templates without one keep the old "on/after the 25th"
    fallback. One email per template per month.
    """
    if recurring_df.empty:
        return

    today   = date.today()
    unlogged = _unlogged_templates(recurring_df, expenses_df, today)
    if not unlogged:
        return

    st.sidebar.warning(f"🔔 **{len(unlogged)} bill(s)** not yet logged this month")

    if not (settings.get("email_alerts") and settings.get("alert_email") and
            settings.get("smtp_host") and settings.get("smtp_user")):
        return

    days_before  = int(settings.get("bill_reminder_days", 2) or 2)
    month_length = calendar.monthrange(today.year, today.month)[1]
    sent_key = f"reminder_sent_{today.year}_{today.month}"
    sent = set(st.session_state.get(sent_key, set()))

    for row in unlogged[:5]:
        key = str(row.get("id"))
        if key in sent:
            continue

        due_day = row.get("due_day")
        if due_day is not None and not pd.isna(due_day):
            if today.day != due_reminder_day(int(due_day), days_before, month_length):
                continue
            due_note = f"Due {calendar.month_name[today.month]} {int(due_day)}"
        else:
            if today.day < 25:
                continue
            due_note = "Due this month"

        sent.add(key)
        amt_str = f"€{float(row['amount_eur']):,.2f}"
        html = build_bill_reminder_email(
            st.session_state.get("display_name", ""),
            row["description"], amt_str, due_note
        )
        send_email_async(
            settings["smtp_host"], int(settings.get("smtp_port", 587)),
            settings["smtp_user"], _decrypt(settings.get("smtp_password_enc") or ""),
            settings["alert_email"],
            f"Bill Reminder: {row['description']}", html
        )
    st.session_state[sent_key] = sent


def check_loan_reminders(user_id: int, loans_df: pd.DataFrame,
                         expenses_df: pd.DataFrame, settings: dict):
    """Sidebar count + email reminders for loan payments not yet logged.

    Loans with a payment_day are emailed N days before the due day (clamped
    to the month length); one email per loan per month.
    """
    if loans_df is None or loans_df.empty:
        return
    today = date.today()

    paid_loan_ids = set()
    if not expenses_df.empty and "loan_id" in expenses_df.columns:
        m_exp = expenses_df[(expenses_df["date"].dt.year == today.year) &
                            (expenses_df["date"].dt.month == today.month)]
        paid_loan_ids = set(m_exp["loan_id"].dropna().astype(str))

    active = loans_df[loans_df["status"] == "active"]
    unlogged = [row for _, row in active.iterrows()
                if str(row["id"]) not in paid_loan_ids]
    if not unlogged:
        return

    st.sidebar.warning(f"💳 **{len(unlogged)} loan payment(s)** not logged this month")

    if not (settings.get("email_alerts") and settings.get("alert_email") and
            settings.get("smtp_host") and settings.get("smtp_user")):
        return

    days_before  = int(settings.get("bill_reminder_days", 2) or 2)
    month_length = calendar.monthrange(today.year, today.month)[1]
    sent_key = f"loan_reminder_sent_{today.year}_{today.month}"
    sent = set(st.session_state.get(sent_key, set()))

    from finance import annuity_payment
    for row in unlogged[:5]:
        key = str(row["id"])
        if key in sent:
            continue
        payment_day = int(row.get("payment_day") or 1)
        if today.day != due_reminder_day(payment_day, days_before, month_length):
            continue
        sent.add(key)
        monthly = annuity_payment(float(row["principal_eur"] or 0),
                                  float(row["annual_rate"] or 0),
                                  int(row["term_months"] or 12))
        html = build_bill_reminder_email(
            st.session_state.get("display_name", ""),
            f"{row['name']} (loan)", f"€{monthly:,.2f}",
            f"Due {calendar.month_name[today.month]} {payment_day}"
        )
        send_email_async(
            settings["smtp_host"], int(settings.get("smtp_port", 587)),
            settings["smtp_user"], _decrypt(settings.get("smtp_password_enc") or ""),
            settings["alert_email"],
            f"Loan Payment Reminder: {row['name']}", html
        )
    st.session_state[sent_key] = sent


def check_and_send_weekly_summary(user_id: int, expenses_df: pd.DataFrame,
                                  settings: dict):
    """Send the weekly spending summary email on Mondays (once per week)."""
    if not settings.get("weekly_summary"):
        return
    if not (settings.get("email_alerts") and settings.get("alert_email") and
            settings.get("smtp_host") and settings.get("smtp_user")):
        return

    today  = date.today()
    if today.weekday() != 0:  # Monday only
        return

    this_monday = today - timedelta(days=7)
    last = settings.get("weekly_summary_last_sent")
    if last is not None:
        try:
            last = pd.to_datetime(last).date()
        except Exception:
            last = None
        if last is not None and last >= this_monday:
            return

    week = (expenses_df[expenses_df["date"] >= pd.Timestamp(this_monday)]
            if not expenses_df.empty else pd.DataFrame())
    html = build_weekly_summary_email(
        st.session_state.get("display_name", ""), week, 117.0, "EUR")

    send_email_async(
        settings["smtp_host"], int(settings.get("smtp_port", 587)),
        settings["smtp_user"], _decrypt(settings.get("smtp_password_enc") or ""),
        settings["alert_email"],
        "Your Weekly Spending Summary", html
    )
    q.save_settings(user_id, {"weekly_summary_last_sent": today})


# ── Settings UI ───────────────────────────────────────────────────────────────

def render_notification_settings(user_id: int, settings: dict):
    st.subheader("📧 Email Notifications")
    st.caption("Get budget alerts and bill reminders by email.")

    email_on = st.toggle("Enable email notifications",
                          value=bool(settings.get("email_alerts", False)))

    if email_on:
        with st.form("notif_form"):
            alert_email = st.text_input("Send alerts to",
                                         value=settings.get("alert_email") or "",
                                         placeholder="you@example.com")
            c1, c2 = st.columns([3, 1])
            with c1:
                smtp_host = st.text_input("SMTP host",
                                           value=settings.get("smtp_host") or "",
                                           placeholder="smtp.gmail.com")
            with c2:
                smtp_port = st.number_input("Port",
                                             value=int(settings.get("smtp_port") or 587),
                                             min_value=1, max_value=65535, step=1)
            smtp_user = st.text_input("SMTP username / email",
                                       value=settings.get("smtp_user") or "",
                                       placeholder="your@gmail.com")
            smtp_pass = st.text_input("SMTP password / app password",
                                       type="password",
                                       placeholder="Leave blank to keep existing password")
            c3, c4 = st.columns(2)
            with c3:
                days_before = st.number_input(
                    "Bill / loan reminder: days before due",
                    value=int(settings.get("bill_reminder_days") or 2),
                    min_value=0, max_value=14, step=1,
                    help="Bills and loan payments with a due day get an email this many days before they are due.")
            with c4:
                weekly = st.toggle("Weekly summary email (Mondays)",
                                   value=bool(settings.get("weekly_summary", False)))
            c_save, c_test = st.columns(2)
            with c_save:
                saved = st.form_submit_button("💾 Save", type="primary", width="stretch")
            with c_test:
                test  = st.form_submit_button("📤 Send test email", width="stretch")

        if saved:
            updates = {
                "email_alerts": True,
                "alert_email": alert_email,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "bill_reminder_days": int(days_before),
                "weekly_summary": bool(weekly),
            }
            if smtp_pass:
                updates["smtp_password_enc"] = _encrypt(smtp_pass)
            q.save_settings(user_id, updates)
            st.success("✅ Notification settings saved!")
            st.rerun()

        if test:
            host = smtp_host or settings.get("smtp_host")
            user = smtp_user or settings.get("smtp_user")
            pwd  = smtp_pass or _decrypt(settings.get("smtp_password_enc") or "")
            to   = alert_email or settings.get("alert_email")
            if not all([host, user, pwd, to]):
                st.error("Please fill in all SMTP fields before testing.")
            else:
                with st.spinner("Sending test email..."):
                    ok, msg = send_email(host, int(smtp_port), user, pwd, to,
                                          "Test — Expense Tracker Notifications",
                                          _html_wrap("✅ Test Successful",
                                                     "<p>Your email notifications are set up correctly!</p>"))
                if ok:
                    st.success(f"✅ Test email sent to {to}!")
                else:
                    st.error(f"Failed to send: {msg}")
    else:
        if st.button("💾 Save (disabled)", width="stretch"):
            q.save_settings(user_id, {"email_alerts": False})
            st.success("Email notifications disabled.")
            st.rerun()
