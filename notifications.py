"""
notifications.py — Email alerts and bill reminders for Expense Tracker v3.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

import streamlit as st
import pandas as pd

from utils import fmt, NEAR_LIMIT_THRESHOLD


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

def check_and_send_budget_alerts(user_id: int, expenses_df: pd.DataFrame,
                                  budgets_df: pd.DataFrame, settings: dict,
                                  rate: float, DC: str):
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

            # Send email if configured
            if (settings.get("email_alerts") and settings.get("alert_email") and
                    settings.get("smtp_host") and settings.get("smtp_user")):
                html = build_budget_alert_email(
                    st.session_state.get("display_name", ""),
                    cat, act_val, bud_val, rate
                )
                send_email(
                    settings["smtp_host"], int(settings.get("smtp_port", 587)),
                    settings["smtp_user"], settings.get("smtp_password_enc", ""),
                    settings["alert_email"],
                    f"Budget Alert: {cat} at {int(act_val/bud_val*100)}%", html
                )


def check_and_send_bill_reminders(user_id: int, recurring_df: pd.DataFrame,
                                   expenses_df: pd.DataFrame, settings: dict):
    """Show sidebar count of unlogged recurring bills this month."""
    if recurring_df.empty:
        return

    today   = date.today()
    active  = recurring_df[recurring_df["active"] == True]
    if active.empty:
        return

    logged = set()
    if not expenses_df.empty:
        m_exp = expenses_df[(expenses_df["date"].dt.year == today.year) &
                             (expenses_df["date"].dt.month == today.month)]
        logged = set(m_exp["description"].str.strip().tolist())

    unlogged = [row for _, row in active.iterrows()
                if row["description"].strip() not in logged]

    if not unlogged:
        return

    st.sidebar.warning(f"🔔 **{len(unlogged)} bill(s)** not yet logged this month")

    # Send email reminder if it's late in the month and not yet sent this month
    sent_key = f"reminder_sent_{today.year}_{today.month}"
    if (today.day >= 25 and sent_key not in st.session_state and
            settings.get("email_alerts") and settings.get("alert_email") and
            settings.get("smtp_host") and settings.get("smtp_user")):
        st.session_state[sent_key] = True
        for row in unlogged[:3]:  # limit to first 3
            amt_str = f"€{row['amount_eur']:,.2f}"
            html = build_bill_reminder_email(
                st.session_state.get("display_name", ""),
                row["description"], amt_str, "Due this month"
            )
            send_email(
                settings["smtp_host"], int(settings.get("smtp_port", 587)),
                settings["smtp_user"], settings.get("smtp_password_enc", ""),
                settings["alert_email"],
                f"Bill Reminder: {row['description']}", html
            )


# ── Settings UI ───────────────────────────────────────────────────────────────

def render_notification_settings(user_id: int, settings: dict):
    from db import save_settings

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
            c_save, c_test = st.columns(2)
            with c_save:
                saved = st.form_submit_button("💾 Save", type="primary", use_container_width=True)
            with c_test:
                test  = st.form_submit_button("📤 Send test email", use_container_width=True)

        if saved:
            updates = {
                "email_alerts": True,
                "alert_email": alert_email,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
            }
            if smtp_pass:
                updates["smtp_password_enc"] = smtp_pass
            save_settings(user_id, updates)
            st.success("✅ Notification settings saved!")
            st.rerun()

        if test:
            host = smtp_host or settings.get("smtp_host")
            user = smtp_user or settings.get("smtp_user")
            pwd  = smtp_pass or settings.get("smtp_password_enc")
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
        if st.button("💾 Save (disabled)", use_container_width=True):
            save_settings(user_id, {"email_alerts": False})
            st.success("Email notifications disabled.")
            st.rerun()
