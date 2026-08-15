# 💰 Expense Tracker — Consumer Edition (v4)

A personal finance app with expense/income/savings logging, budgets, forecasting,
auto-insights, gamification, bank-statement import, households, and email alerts.

The app runs as a **server on one computer** (your PC). Any phone, tablet, or
laptop **on the same Wi-Fi network** opens it in a browser — all devices share
the same database, so there is nothing to sync and no conflicts.

---

## Quick start (Windows)

```bat
:: one-time setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: every time you want to use the app
run_server.bat
```

`run_server.bat` (or `run_server.ps1`) activates the venv, installs missing
dependencies, and starts the server on `0.0.0.0:8501`.

Open **http://localhost:8501** on the PC.

## 📱 Using it from your phone

1. Make sure the phone is on the **same Wi-Fi** as the PC.
2. Start the server with `run_server.bat`.
3. In the app sidebar you'll see a **QR code** under "Phone access" — scan it
   with the phone camera (Android: Google Lens; iOS: camera app) and the app
   opens in the phone's browser.
4. You can also type the address shown when the server starts, e.g.
   `http://192.168.1.23:8501` or `http://DESKTOP-NAME:8501`.

**First run only:** if Windows Firewall asks, allow access on **Private
networks**. If no prompt appears and the phone can't connect, allow the port
manually (run as Administrator):

```bat
netsh advfirewall firewall add rule name="Expense Tracker 8501" dir=in action=allow protocol=TCP localport=8501 profile=private
```

**Tip:** give the PC a fixed IP (DHCP reservation in your router) so the URL
stays the same forever.

> The server must be running on the PC for the phone to connect. Data lives in
> `data/expense_tracker.db` on the PC — back it up in **Settings → Data**
> (automatic daily backups are saved to `data/backups/`).

## Features

- **Log expense / income / savings** — multi-currency, subcategories, notes
- **Savings goals** — custom goals, deposits *and* withdrawals (balance never
  goes negative), monthly compound interest, yearly KPIs and goal projections
- **Income types** — salaried (fixed salary + one-tap monthly logging, raise
  detection), hourly (hours × rate), bonuses, freelance, investment, rental
- **Recurring** — monthly bill checklist with due days, one-tap "Log now"
  that lets you record the actual amount (may differ from the expected)
- **Big purchases** — 4-quadrant priority matrix (expected use vs work-hours
  needed), status tracking, and "bought → log as expense" handoff
- **Dashboard** — KPIs with period-over-period deltas, budget progress bars,
  cumulative net cash flow, fixed costs per year, monthly trends, savings rate
- **Forecast** — projects current-cycle spending (period-average or 7-day
  burn rate) against your budget
- **Insights** — month-over-month, top merchants, no-spend days, unusual
  expenses, salary/bonus highlights, savings projections
- **Bank import** — Revolut / N26 / Wise / generic CSV with auto-categorisation
  and duplicate detection
- **Household** — share a combined dashboard with family via invite code
- **Gamification** — streaks, badges, salary/raise/bonus milestones
- **Audit log** — every change is recorded
- **Email alerts** — budget warnings, due-date bill reminders (N days before
  due), and an optional weekly summary on Mondays (your own SMTP account)

## Currency model

All amounts are stored in EUR plus the **original** amount and currency, and a
per-currency rate table (Settings → Currency, or the quick RSD control in the
sidebar). Because the original amount is preserved, changing rates later never
rewrites your history.

**Live rates:** on login, exchange rates refresh automatically from free public
APIs (ECB via frankfurter.app, with open.er-api.com covering RSD/BAM) whenever
the stored rates are older than 3 days. If the network is unavailable, the last
known rates are kept untouched — you can also refresh manually or edit rates by
hand in Settings → Currency.

## Security notes

- Passwords are bcrypt-hashed; SMTP passwords are encrypted (Fernet) with a key
  in `data/.secret_key` or the `encryption_key` Streamlit secret.
- Anyone on your network can create an account while registration is open.
  When hosting publicly, set `ALLOW_REGISTRATION=false` (env var or
  `st.secrets`) and put the app behind HTTPS.
- Login attempts are throttled (5 per minute per client).

## Running tests

```bat
pip install -r requirements-dev.txt
python -m pytest
```

## Hosting later (VPS / server)

The data layer is SQLAlchemy, so SQLite → PostgreSQL is configuration, not code:

```bash
docker compose up -d --build        # SQLite in a named volume, port 8501
```

```bash
# or with PostgreSQL:
export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/expenses
export ALLOW_REGISTRATION=false
pip install psycopg2-binary
streamlit run app.py --server.address 0.0.0.0
```

For a public deployment, terminate HTTPS with a reverse proxy (Caddy, nginx,
Cloudflare Tunnel) and disable open registration as above.
