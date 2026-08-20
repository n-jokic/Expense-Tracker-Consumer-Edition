# 💰 Expense Tracker — Consumer Edition (v4)

A personal finance app with expense/income/savings logging, budgets, forecasting,
auto-insights, gamification, bank-statement import, households, email alerts, a
stock portfolio tracker, and an (experimental) phone sync API — all in one
self-hosted Python app.

The app runs as a **server on one computer** (your PC). Any phone, tablet, or
laptop **on the same Wi-Fi network** opens it in a browser — all devices share
the same database, so there is nothing to sync and no conflicts.

---

## Table of contents

1. [Quick start](#quick-start-windows)
2. [Using it from your phone](#-using-it-from-your-phone)
3. [Outside your home network](#-using-the-app-outside-your-home-network)
4. [Feature guide](#feature-guide)
5. [Backing up to GitHub (free)](#-backing-up-to-github-free)
6. [AI assistant (local Gemma / API key)](#-ai-assistant-local-gemma--api-key)
7. [AI assistant access (OpenClaw / MCP)](#-ai-assistant-access-openclaw--mcp)
8. [How the ML models work](#-how-the-ml-models-work)
9. [Currency model](#currency-model)
10. [Security notes](#security-notes)
11. [Configuration](#configuration)
12. [Project structure](#project-structure)
13. [Running tests](#running-tests)
14. [Hosting (VPS / server)](#hosting-later-vps--server)
15. [Roadmap](#roadmap)

---

## Quick start (Windows)

```bat
:: one-time setup
python -m venv .venv-clean
.venv-clean\Scripts\activate
pip install -r requirements.txt

:: every time you want to use the app
run_server.bat
```

`run_server.bat` (or `run_server.ps1`) activates `.venv-clean` automatically
(falling back to `.venv` when present), installs missing
dependencies, and starts the server on `0.0.0.0:8501` over plain **HTTP**.

Open **http://localhost:8501** on the PC.

> **First run on an existing installation:** the database is encrypted once,
> automatically (SQLCipher, AES-256). The key lives in `data/.secret_key` —
> keep it safe (see [Security notes](#security-notes)): it is deliberately
> never included in backups, and losing it means losing your data.

### 🖥️ Windows installer (optional — no terminal needed)

Build `dist\installer\ExpenseTracker-Setup.exe` once (Python 3.12 and Inno Setup 6 required):

```bat
build_exe.bat
```

Run the installer — it bundles Python, Streamlit, SQLCipher, and the Vulkan
llama.cpp runtime, installs under Program Files, and starts the app plus the
phone-sync API without a system Python. User data is kept in
`%LOCALAPPDATA%\ExpenseTracker` and survives uninstall.

> The desktop launcher always serves **plain HTTP** on your home network.
> Want HTTPS instead? Use `run_server.bat` / `run_server.ps1` with
> `EXPENSE_TRACKER_TLS=1` set — the script generates a self-signed
> certificate in `data/certs/` (once) and serves the app and the sync API
> over HTTPS. Your browser/phone will ask you to accept the self-signed
> certificate the first time.

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
> `%LOCALAPPDATA%\ExpenseTracker\expense_tracker.db` for the installed app
> (automatic daily backups are saved to `backups/`). Source installs continue
> to use `data/`.

## 🌍 Using the app outside your home network

Three options, in order of simplicity:

1. **Tailscale (recommended, free).** Install Tailscale on the PC and on the
   phone; both join your private encrypted network. From anywhere, open the
   app at the PC's tailnet address (e.g. `http://pc-name:8501`) — exactly like
   being on the home Wi-Fi. Nothing is exposed to the public internet.
2. **Cloudflare Tunnel (browser only, free).** Run `run_tunnel.bat` after
   installing `cloudflared` — you get a public `https://…trycloudflare.com`
   URL that works from any network. ⚠️ The URL is public: set
   `ALLOW_REGISTRATION=false` first (env var or `.streamlit/secrets.toml`).
3. **Self-host on a VPS.** `docker compose up -d`, put Caddy (see `Caddyfile`)
   or nginx in front for HTTPS, set a domain, and optionally point
   `DATABASE_URL` at a PostgreSQL instance. Disable open registration.

---

## Feature guide

The app is organised into five navigation groups: **Overview**, **Track**,
**Plan**, **Understand**, and **Household & Data**.

### Overview — Dashboard

- **KPI cards** for the selected period (month / 3 months / year): income,
  expenses, saved, net balance, and savings rate — with period-over-period
  deltas.
- **Task hub**: one-click links to log an expense, log income, and set budgets,
  an **Upcoming bills** list (recurring bills due within 7 days), and
  **Recent activity** (your 5 latest expenses) — all visible even when you
  have no data yet.
- **One-tap logging**: ☕ Coffee, 🍔 Lunch, and 🚌 Transit buttons log a preset
  everyday expense in a single click (amounts fixed in EUR, logged under the
  matching category/subcategory, editable later like any expense).
- **Last 7 days**: a mini sparkline of daily spending right under the KPIs.
- **Net worth strip**: today's savings balances + portfolio value − total
  debt, so the whole financial picture is one glance away.
- **Budget alerts & progress bars** for the current month, including the
  "near limit" (≥ 90 %) and "exceeded" states.
- **🎈 Fun money**: a guilt-free monthly allowance across categories you pick;
  milestone bonuses are added automatically in the month they're earned.
- **Charts**: spending by category (pie), budget vs actual, top-10 largest
  expenses, monthly trends, and cumulative net cash flow.
- **Debt KPIs**: total debt across loans and the projected debt-free date.
- **Personal vs Household view**: switch to the household view to see combined
  household spending (see *Household* below). In household mode, personal
  income, savings, budgets, loans, and fun money are hidden so nothing is
  mixed into misleading totals.

### Track — logging

**Log expense** (`app_pages/log_expense.py`)

- Multi-currency amounts with subcategories and notes; the EUR base value is
  computed from the current rate table and stored **together with the original
  amount and currency**, so later rate changes never rewrite history.
- Optionally save the entry as a **recurring template** in one tick.
- **Receipt scanning (OCR)**: photograph or upload a receipt; Tesseract reads
  it **on the server** (the phone only sends the photo), the app guesses the
  total amount, merchant, and category, and you accept, edit, or reject the
  result before anything is saved. Images are kept in memory only.
- **History editor**: search and filter all expenses, edit any field inline
  (paginated, "Showing X–Y of N"), and trash/restore rows. Deleted rows are
  **soft-deleted** and can be restored; the data is never silently destroyed.
- Excel export of the expense list (formula-safe, see Security).

**Log income** (`app_pages/log_income.py`)

- Income types: **Salary, Hourly, Bonus/Raise, Freelance, Investment, Rental,
  Other**. Hourly entries store hours × rate and compute the total.
- Big-purchase work-hours use a weighted rate from valid Hourly entries
  (`sum(actual EUR) / sum(hours)`); if none exist, the configured salary is
  converted to EUR and divided by 160. The old manual hourly setting remains
  only for compatibility.
- One-tap monthly salary logging, salary-cycle projection, and automatic
  **raise detection** (a salary entry higher than every earlier one).
- Every entry can be **edited after the fact** — date, source, income type,
  actual/budgeted amount, currency, and notes, via an "Edit an income entry"
  dialog — and editing touches only that row.
- Soft-delete + restore, like expenses.

**Savings** (`app_pages/savings.py`)

- Named goals with a target; log **deposits and withdrawals** (balance is
  clamped at zero, never negative). Creating a goal is as simple as logging the
  first entry — the target and interest rate you give it become the goal's.
- **Goal cards** make a goal easy to manage after creation: every goal has
  **Deposit**, **Withdraw**, **Edit goal** and **Delete goal** actions right on
  the card.
  - *Edit goal* renames the goal (across all its entries and term deposits),
    and sets its **target and interest rate** — applied to all of the goal's
    entries; the balance chain recomputes automatically.
  - *Delete goal* moves every entry to the trash (restorable) and removes its
    term-deposit accounts.
- **Monthly compound interest**: the balance chain is recomputed on every read
  from the deposit history, compounding at each entry's interest rate over the
  elapsed months, and the latest entry is rolled forward to **today** at the
  goal's latest rate — the displayed balance is always the current value.
  Editing or deleting an entry intentionally updates the chain *from that entry
  forward* (nothing else is rewritten).
- **Term-deposit accounts**: open one or several accounts *under a goal* —
  each has its own amount, currency, **fixed annual interest rate**, start date
  and **maturity date**. The value compounds monthly; the card shows the
  current and maturity values, the days remaining, and when the deposit
  matures you can **withdraw it into the goal** (or early, at the accrued
  value) with one click — it is logged as a goal deposit and the account is
  closed. Accounts, goals, and locked value also count towards the goal's
  progress bar and the "Locked (term)" KPI.
- Individual entries remain editable (date, amount, target, interest rate,
  notes) via "Manage savings entries", and everything is soft-delete/restore
  supported (including trashed term deposits).

### Editing & history safety (applies everywhere)

Almost every entry in the app can be edited or corrected after it was
created, with one consistent rule: **editing never rewrites the history that
was already recorded.**

| Entry | Editing | History guarantee |
|---|---|---|
| Expenses | Inline in the history editor (all fields, paginated) | Each expense stores its own original amount/currency/EUR value; edits rewrite only that row |
| Income | "Edit an income entry" dialog (date, source, type, amount, currency, budgeted, notes) | Only that row changes |
| Savings entries | "Manage savings entries" dialog (date, amount, target, interest, notes); goals via "Edit goal" | The balance **chain** recomputes from that entry forward — that's the intended math, no other rows are rewritten |
| Term deposits | "Edit" dialog (name, amount, currency, rate, dates, goal) | Only that account changes; logged goal entries are untouched |
| Budgets | Re-save the same year/month/category/subcategory scope — it upserts | One row per scope, never duplicates |
| Recurring templates | "Edit" dialog (description, expected amount, currency, due day, start month, notes, active) | **Past logged expenses are untouched** — they keep the amounts/categories they were saved with and only link back to the template |
| Loans | "Edit" dialog (name, principal, currency, rate, term, payment day, start date, status, notes) | Logged payments are untouched; the amortization math simply recomputes |
| Big purchases | "Edit" dialog (name, category, price, currency, usage, importance, notes) | The expense logged at purchase time (if any) is untouched |
| Holdings | Quantity (Manage holdings) | Cost basis and price-snapshot history stay as recorded |

Deletions are equally careful: expenses/income/savings are **soft-deleted**
(trash + restore), and destructive actions (holding removal, loan deletion,
purchase confirmation, budget rows, device revocation, account deletion)
require confirmation dialogs. Every change is written to the **audit log**.

**Bank import** (`app_pages/bank_import_view.py`)

- CSV import for **Revolut, N26, Wise, and generic** formats, plus **PDF bank
  statements** (pdfplumber extracts both tables and free text).
- Locale-aware number parsing (e.g. `1.234,56` and Serbian dot-thousands
  `1.234` = 1234), day-first date parsing with an ambiguity heuristic (so
  `05/02/2025` is 5 February, never May 2), and debit/credit detection —
  statements whose bank exports debits as POSITIVE amounts get an "inverted
  sign convention" checkbox.
- **Auto-categorisation**: your learned classifier first (see ML section),
  then a keyword map as fallback.
- A review editor lets you correct categories and untick rows before import;
  the EUR value is recalculated from what you edited, and duplicates (both
  against the database and within the same upload) are skipped.

### Plan — budgets & commitments

**Budgets** (Plan → **Budgets** — its own page, no longer buried in Settings)

- **Overall monthly budget** entered in your display currency with a live EUR
  preview (stored as the EUR base value), plus a live progress bar of this
  month's actual spending against it.
- **Category budgets** with optional subcategory granularity. Each scope
  (year, month, category, subcategory) is unique — saving the same scope again
  updates it. When subcategory budgets exist they are authoritative for that
  category; otherwise the whole-category budget applies. Overlapping rows are
  never summed together.
- The page shows **per-category progress bars for the current month** next to
  the add form and the full row table.
- Budgets feed the dashboard progress bars, in-app toasts, and optional email
  alerts.

**Recurring expenses** (`app_pages/recurring.py`)

- Templates with category, subcategory, description, **typical amount and
  currency**, optional **due day**, optional **start month**, notes, and
  active flag.
- A monthly checklist shows every due template; **"Log now"** opens a popover
  prefilled with the expected amount so you can record the **actual** amount
  (which may differ).
- **Fully editable**: description, expected amount, currency, due day, start
  month, and notes can all be changed later — **editing a template never
  rewrites expenses already logged**. Past entries keep the amounts and
  categories they were saved with; they only link back to the template so the
  checklist knows the bill was logged this month.
- Templates only appear in checklists, reminders, and "upcoming bills" from
  their start month onward; "Remove" deactivates (never deletes).
- Active templates are grouped by category and can be dragged within or
  between categories; the order is persisted. Moving a template clears a
  subcategory that is not valid for its new category.

**Loans** (`app_pages/loans.py`)

- Principal (any currency), annual interest rate, term in months, start date,
  and payment day.
- **Real amortization against your actual payment history**: the schedule
  attributes each logged payment to its due month (payments made off the due
  day still count), accrues interest monthly, and reports remaining balance,
  remaining months, payoff date, interest paid/remaining, and total cost.
  Missed or partial payments extend the payoff date. A logged payment reduces
  the principal **immediately** — even before that month's payment day arrives.
  A month's interest is booked when its due date passes **or when a payment is
  applied to it** (whichever comes first), so paying early — or logging several
  payments before the first due — still accrues interest instead of treating
  the loan as interest-free.
- The first due date is the first payment day **on or after** the start date
  (no phantom first month), and the remaining-payment count rounds up so a
  €149 balance at €100/month correctly needs 2 payments.
- **Editable terms** (name, principal, rate, term, payment day, start date,
  status, early-repayment surcharge): editing recomputes the schedule but
  never touches logged payments.
- Each loan has an optional early-repayment surcharge: fixed in the loan
  currency or a percentage of the entered principal (default 0). The separate
  Early repayment action logs one expense for principal plus surcharge; only
  principal reduces the balance, while the surcharge is included in interest
  paid. The next installment shows its interest/principal split.
- Email reminders N days before the due day; deleting a loan keeps its payment
  expenses.

**Big purchases** (`app_pages/big_purchases.py`)

- Wishlist items with price, expected usage (hours/month), and importance
  (1–5), plotted on a **4-quadrant priority matrix** (Quick wins / Plan & save /
  Maybe later / Reconsider).
- Status flow: wishlist → saving → **bought**, with a confirmation dialog that
  logs the purchase as an expense in one step.
- Bought rows remain recoverable in a collapsed **Archived** section. Active
  rows are compact cards grouped by category and can be dragged to reorder or
  move between categories; the order is persisted.
- Name, category, price, usage, importance, and notes are editable; deleting a
  wishlist row never touches the expense logged at purchase time.

**Travel budget** (`app_pages/travel.py`)

- A yearly allowance (custom amount per year) with a category pool for flights,
  hotels, and vacation spending; tracks on-pace status against the year and
  links to your vacation savings goal.

**Portfolio** (`app_pages/portfolio.py`)

- Track stocks/ETFs by symbol with quantity, currency, and cost basis.
- **Free daily prices** from Yahoo Finance with a Stooq CSV fallback;
  refreshes automatically in the background once per day (never blocks the UI)
  or on demand.
- KPIs: current value, invested, gain and gain %; allocation pie; and a
  **value-over-time** chart. Every price snapshot stores the **quantity and
  currency rate at snapshot time**, so historical values stay exact even if
  you later edit the quantity or the rates change (rows from before this
  feature are labelled "≈ estimated").
- Holdings can be edited (quantity) or removed with a confirmation dialog
  (removal also deletes the holding's price history).

### Understand — analysis

**Forecast** (`app_pages/forecast.py`)

- Projects this month's total spending with three methods: period average,
  last-7-days burn rate, or the **ETS machine-learning forecast** (see ML
  section), compared against your monthly budget.
- Salary-cycle awareness: spending between paychecks is scaled to the salary
  period.

**Insights** (`app_pages/insights_view.py`)

- Month-over-month comparisons, top merchants, no-spend days, savings
  projection, raise/bonus highlights, subscription detection, anomaly scan,
  spending-pattern clustering, and budget suggestions (all explained in the ML
  section below).
- With an AI provider configured, an optional **"In short"** narrative
  summarizes the month in plain language on top of the cards.

**Ask your data** (`app_pages/ask.py`)

- A chat with your own finances, powered by the same AI assistant: ask
  "how much did I spend this month?", "what was my biggest category?", or
  "what did I spend at the grocery store?" and the model answers from a
  **sanitized numeric snapshot** of your data (aggregates plus sanitized
  names/descriptions of goals, loans, bills, and your recent transactions —
  newlines stripped, values capped). With the local Gemma provider nothing
  leaves the machine; with an API provider only that sanitized snapshot is
  sent.
- **Follow-ups keep context**: the last few turns are re-sent (sanitized)
  with each question, so "and what about groceries?" builds on the previous
  answer. A caption shows which provider/model is answering.
- The model may do simple arithmetic on the provided numbers but is prompted
  to never invent figures — and to say so when the data can't answer. Answers
  are conversational help, not an audit trail: double-check against the pages
  themselves. Without a provider the page explains how to set one up.

### Household & Data

**Household** (`app_pages/household.py`)

- Create a household with a **shareable invite code** (always visible with a
  copy block after creation, plus a **Regenerate** button to revoke a leaked
  code), join via a code, and leave.
- The dashboard's household view combines expenses across members while
  keeping personal income/savings/budgets/loans separate — no misleading
  mixed totals. The combined view shows expenses of **current** members:
  expenses logged while someone was a member stay on their own account when
  they leave, and joining never reveals another member's pre-membership
  history.

**Audit log** — every create/update/delete across the app is recorded with a
timestamp, table, record id, and details; exportable to Excel.

**Settings** also contains: currency & rates, travel, notifications (SMTP +
optional AI assistant), account (display name, password change, account
deletion with typed confirmation), data export/backup (incl. GitHub), and
phone sync. Budgets and fun money live on their own pages (see above).

### Gamification & fun money

**Rewards & badges** (Play → **Rewards** — its own page)

- **Fun money**: allowance and the fun-category pool are edited right on the
  page (no more digging through Settings), with this month's spend progress and
  active/queued milestone bonuses.
- **Badge wall**: the full 40-badge catalog as a grid — earned badges show
  their unlock date and reward, locked ones show the requirement and a cheap
  progress hint where computable (e.g. "23/50 expenses", "5/7 streak days").
- **Streaks**: current and best logging streak, plus the next-badge hint.
- **Recent unlocks**: the newest badges with their dates.
- **My milestones**: create your own goals with a fun-money reward — pick a
  metric (expenses count/total, income, savings balance, logging streak, or
  categories used), a target, and a reward in €. Each milestone is evaluated
  from your data on every app start, awarded **once**, and its reward lands in
  next month's fun money exactly like badge rewards. Progress bars show how
  close each open milestone is; delete any time.
- The page is organized as **Milestones** first and **Badges** second. The
  Badges tab groups earned and locked cards, shows progress hints, streaks,
  and recent unlocks.

- **Streaks and badges**: logging streaks (7/30 days), first expense/income,
  first budget, first salary, budget keeper (full month under budget), saving
  €100/€1,000/€10,000, logging 50/200 expenses, reaching a savings goal, a
  zero-entertainment month, raise earned, first bonus, first hourly income.
- **Fun achievements** — 21 playful badges mined from your habits:
  ☕ Caffeine Addict (10 coffee runs/month), 🏋️ Gym Rat, 🚌 City Slicker
  (transit rides), 🥕 Grocery Guru, 🥪 Lunch Legend, 🌅 Early Bird (logging
  before 9:00), 🦉 Night Owl (after 23:00), 🛍️ Weekend Warrior, 🪙 Micro
  Spender, 💎 Big Spender, 📉 Penny Pincher (a month ≥30 % below your
  average), 🌈 Category Explorer (spent in every category), ❤️ Kind Heart,
  🎁 Santa's Helper, ✈️ Jet Setter, 🌍 Globe Trotter (3+ currencies), 🏠
  Home Steady (12 months of housing), 🎭 Hustler (3+ income sources/month),
  🐿️ Squirrel Mode (3 saving months in a row), 🔁 Sub Detective (spotted 3+
  subscriptions), and the meta-badge 🧭 Achievement Hunter (earn any 10).
- **Milestones unlock fun-money rewards**: rewards are granted once, are
  persisted, and add a bonus to next month's fun-money allowance. Bonuses are
  tracked **per month** — a bonus queued for one month is never lost when a
  new milestone is earned for the following one.
- A **budget-adherence streak** counts consecutive months under budget
  (the in-progress month never counts).
- Badge semantics worth knowing: **Squirrel Mode** requires consecutive
  net-positive saving months with no gaps (a skipped month breaks the
  streak); **Penny Pincher** compares last month against the 6 complete
  months before it; **Goal reached** is judged on each goal's current
  balance vs target (withdrawing below target after reaching it retires the
  badge); refunds never count as "micro" spending; **Hustler** credits any
  month with 3+ income sources.

### Notifications & email alerts

- **Budget alerts**: toast (and optional email) when a category budget is
  ≥ 90 % used or exceeded.
- **Bill reminders**: email N days before a recurring bill's due day (N is a
  setting, default 2); templates without a due day use the old "on/after the
  25th" fallback.
- **Loan reminders**: same logic for loan payments.
- **Weekly summary**: every Monday (never skipped), a spending summary in your
  display currency — with an optional AI-written paragraph when an AI provider
  is configured (see the next section).
- Alerts are sent from a background thread (the UI never blocks on SMTP), and
  a "sent" marker is persisted **only after the mail server confirms
  delivery** — failed sends are retried on a later run instead of being
  silently marked sent.
- Your own SMTP account is used; the password is encrypted (Fernet) and the
  STARTTLS connection verifies the mail server's certificate.

### Data, backups & export

- **Export everything**: a zip containing Excel files for expenses, income,
  savings, term deposits, budgets, recurring, big purchases, loans, holdings,
  holding-price history, audit log, settings, household metadata, devices,
  milestones, and sync conflicts — plus individual per-table downloads.
  Credentials are never exported: the settings sheet excludes your SMTP
  password/user and alert email address.
- **Spreadsheet safety**: cells starting with `=`, `+`, `-`, or `@` are
  exported as inert text, so user-entered descriptions can't execute as
  formulas when the file is opened.
- **Backups**: a WAL-safe SQLite snapshot is taken automatically once per day;
  the manual "Back up now" button always takes a fresh, timestamped copy
  (even twice on the same day), writes are atomic, and old backups are pruned
  after 30 days. Backups are **ciphertext** — the database is SQLCipher-
  encrypted at rest, so a backup file is useless without the key.
- **Off-site backups**: Settings → Data can push the encrypted database to a
  **private GitHub repository** automatically (once a day) or on demand — see
  [Backing up to GitHub](#-backing-up-to-github-free).

### ☁️ Backing up to GitHub (free)

The app can upload its **encrypted** database backups to a **private GitHub
repository** — free, off-site, and readable only with your key (which is never
uploaded). Setup takes ~5 minutes:

1. **Create a private repository** (one per household is fine) at
   github.com → *New repository* → name it e.g. `expense-tracker-backup` →
   **Private** → Create. Do not add a README.
2. **Create a fine-grained personal access token**:
   GitHub → *Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token*.
   - *Repository access*: **Only select repositories** → your backup repo.
   - *Permissions → Repository permissions → Contents*: **Read and write**.
   - Expiration: your choice (a year is fine; you'll just re-enter a new one).
3. In the app: **Settings → Data → GitHub backup** — paste
   `your-username/expense-tracker-backup` and the token, set the retention
   (days to keep backups, default 14), enable the daily automatic backup,
   and press **Back up to GitHub now** for the first upload.

What happens under the hood:

- the local backup (already SQLCipher ciphertext) is uploaded to
  `backups/YYYY-MM-DD/…` in the repo — **nothing readable ever leaves the PC**;
- files over 50 MB are split into parts (GitHub hard-caps files at 100 MB)
  and a `.manifest.json` with SHA-256 checksums of every part is written
  **last** — a backup folder only becomes restorable once its manifest exists;
- backups older than the retention window are deleted automatically;
- the token is stored Fernet-encrypted in the local database, is **never
  included in exports**, and only the GitHub API ever sees it;
- the daily automatic backup runs in the background on app start (once per
  24 h) and never blocks the UI; the last result is shown in Settings.

**Restoring from GitHub** (after a disk loss / new PC):

```bat
:: list what's in the repo
python github_backup.py list --user your_username

:: download + verify (SHA-256) without touching the live database
python github_backup.py restore 2026-08-16_120000 --user your_username --out C:\restore

:: or restore directly over the live database (stop the app/API first!)
python github_backup.py restore 2026-08-16_120000 --user your_username --replace
```

`restore --replace` refuses to run while WAL files exist (i.e. while the app
is running), keeps the previous database as a `pre_restore_*` copy, and asks
you to restart the app afterwards. If the database itself is unreadable (so
Settings can't be read), point the CLI at the repo with `GH_REPO` and
`GH_TOKEN` environment variables instead of `--user`.

> ⚠️ The **key is not on GitHub**. Also back up `data/.secret_key` separately
> (password manager, USB stick, or paper) — without it, neither the GitHub
> backups nor the local database can ever be opened.

### 🤖 AI assistant (local Gemma / API key)

An **optional** lightweight LLM writes the weekly-summary email paragraph and
the Insights "In short" narrative. Without it, the app uses its built-in
templates — nothing else changes. Configure it in
**Settings → Notifications → AI assistant**.

**Local Gemma (recommended — private, free, runs in < 4 GB VRAM)**

1. The Windows installer already includes the pinned Vulkan runtime. For a
   source install, the local-AI runtime is **optional** and installed
   separately:

   ```bat
   .venv-clean\Scripts\python.exe -m pip install -r requirements-ai.txt
   ```

   (`requirements-ai.txt` pins llama-cpp-python 0.3.34 with the Vulkan wheel
   index.) The rest of the app never needs it — without it, the UI shows an
   actionable "runtime missing" notice instead of failing.
2. Download a GGUF model from HuggingFace (`bartowski`) — or run the helper
   `python tasks\download_model.py`, which downloads the recommended Gemma 3
   1B Q4_K_M GGUF into `data\models\` with resume support:
   - **Gemma 3 1B Q4_K_M** (~0.9 GB — the default recommendation):
     `bartowski/google_gemma-3-1b-it-GGUF` → `google_gemma-3-1b-it-Q4_K_M.gguf`
   - alternative **Gemma 2 2B Q4_K_M** (~1.6 GB):
     `bartowski/google_gemma-2-2b-it-GGUF`
   A source install auto-detects the recommended file at
   `data\models\google_gemma-3-1b-it-Q4_K_M.gguf` (in the repo folder); the
   installed app auto-detects it at
   `%LOCALAPPDATA%\ExpenseTracker\models\google_gemma-3-1b-it-Q4_K_M.gguf`.
   For another model, paste its full `.gguf` path instead.
3. In the app: Settings → Notifications → AI assistant → provider **Local
   Gemma model**. **GPU layers**: `-1` puts everything on the GPU (2 GB VRAM
   is plenty for the 1B model); `0` runs on CPU (a few seconds per summary).
4. Press **Test summary** to verify.

GGUF model weights are never included in the installer.

**External API key**

Any **OpenAI-compatible** endpoint works (OpenRouter, Groq, Together, …):
pick provider **External API**, set the base URL (default
`https://openrouter.ai/api/v1`), the model name (default
`google/gemma-3-12b-it`), and the API key. The key is stored Fernet-encrypted,
never exported, and never logged.

Notes:

- Generation uses a strict prompt over **numeric aggregates only** (no raw
  user text), and model output is HTML-escaped before it goes into the email.
  The **Ask your data** chat gets the same treatment: the prompt embeds a
  sanitized snapshot (newlines stripped, values capped) so stored data can
  never steer the model.
- Every failure — missing model, API error, timeout — silently falls back to
  the built-in template, so email sending is never blocked or broken by the
  LLM.

### Phone sync API (experimental — offline PWA groundwork)

🧪 **Experimental.** The sync API (`python api.py`, port 8502) pairs a phone
app with a one-time code (Settings → Sync) and accepts device changes with
conflict detection: records edited on both sides since the last sync are
parked in Settings → Sync for manual resolution (keep device / keep server).

The v2 protocol is security-hardened:

- every change is validated against **per-table field schemas** (unknown
  fields, protected fields, wrong types, and oversized strings are rejected);
- the sync cursor is the device's **server-recorded last-sync time** — a
  client cannot send null/future timestamps to bypass conflict detection;
- compare-and-update runs in **one database transaction** (no race window);
- record ids owned by another account are silently remapped (no cross-account
  existence oracle);
- payloads are capped (500 changes per call, 5,000 snapshot rows);
- pairing codes are cryptographically random, single-use, expire in 10
  minutes, and are rate-limited (5 tries / 10 min / IP); device tokens are
  SHA-256-hashed, expire after 90 days, and are refreshed by use.

The syncable tables are expenses, income, savings, and term-deposit accounts
(`savings_accounts`). Every sync-originated create/update is written to the
**audit log** (marked "via sync"). The offline PWA client itself is the next
milestone — the server contract is ready.

### Receipt OCR setup (optional)

```bat
winget install UB-Mannheim.TesseractOCR
```

The Docker image installs it automatically. The app **auto-detects**
Tesseract wherever winget installed it (PATH, `Program Files`, or the
registry) — no PATH setup and no server restart needed; install once and the
scan control starts working. Without Tesseract, the rest of the app works
normally and the scan control explains exactly what's missing.

### 🤖 AI assistant access (OpenClaw / MCP)

The app ships an **MCP server** (`mcp_server.py`) so a local AI assistant —
[OpenClaw](https://docs.openclaw.ai/tools/mcp) or any MCP client — can read
your finances and log entries for you. It talks to the same encrypted
database the app uses, so nothing is duplicated.

Exposed tools:

| Tool | What it does |
|---|---|
| `expense_summary` | Month's spending, income, net, budget usage, top category, fun money |
| `list_expenses` / `search_expenses` | Expenses by month / free-text search |
| `list_income`, `list_budgets` | Income entries and category budgets |
| `list_savings_goals` | Goal balances, targets, term deposits |
| `list_recurring_bills`, `list_loans` | Bills and loans |
| `get_milestones` | Earned gamification badges |
| `get_insights` | Month-over-month trends, unusual expenses, budget burn-down, and an optional AI narrative |
| `ask_data` | Free-form question answered over your data by the AI assistant (read-only) |
| `add_expense`, `add_income` | **Writes** — validated, audit-logged ("via mcp"), instantly visible in the app |

When an AI provider is configured, `get_insights` adds a plain-text `narrative`.
If the provider is off or generation fails, the existing structured metrics are
still returned unchanged.

Connect it to OpenClaw (run once):

```bat
openclaw mcp add expense-tracker ^
  --command C:\path\to\Expense-Tracker-Consumer-Edition\.venv-clean\Scripts\python.exe ^
  --arg C:\path\to\Expense-Tracker-Consumer-Edition\mcp_server.py ^
  --cwd C:\path\to\Expense-Tracker-Consumer-Edition
openclaw mcp doctor expense-tracker --probe
```

(Equivalently: Settings → MCP → *Add server* in the OpenClaw Control UI, or
a `mcp.servers.expense-tracker` entry in the OpenClaw config with
`command`/`args`/`cwd` and `transport: "stdio"`.)

Notes:

- The server targets one account: `EXPENSE_TRACKER_MCP_USERNAME`, or the
  first-created account when unset.
- Writes bump the shared cache revision, so open browser sessions pick them
  up on their next refresh; every write lands in the audit log with its
  MCP origin.
- Stdio mode (the default, and the OpenClaw setup above) trusts the local
  machine only. An optional HTTP mode (`python mcp_server.py --http`, port
  8510) is **bound to 127.0.0.1 with no authentication** — enable it only for
  a remote OpenClaw Gateway when you trust every local process, and restrict
  the write tools with OpenClaw's tool policies.

---

## 🧠 How the ML models work

Every model runs **on the server** (the phone only renders results), so they
work identically on any device, including budget Android phones. All models
are local to your data, degrade gracefully, and never block the UI.

### 1. Next-month spending forecast (ETS / Holt-Winters)

- **What it does:** predicts next month's total spending (and per-category
  totals) from your own expense history.
- **Algorithm:** `statsmodels` Exponential Smoothing with an additive trend
  (`ExponentialSmoothing(..., trend="add")`), fitted to your monthly EUR
  totals.
- **Data rules:** it requires **6 elapsed calendar months** of history, and
  the months must be **contiguous** — a missing month means the model refuses
  to guess instead of interpolating spending that never happened (sparse
  multi-year purchases do *not* become artificial continuous spending).
- **Intervals:** the forecast is reported with a ±2 standard-deviation band
  from the model's residuals.
- **Fallback:** with too little or gappy history, the page uses the period
  average or the 7-day burn rate instead, and labels the result accordingly.

### 2. Anomaly detection (Isolation Forest)

- **What it does:** flags unusual transactions for review on the Insights
  page.
- **Algorithm:** scikit-learn `IsolationForest` (contamination 5 %, fixed
  random seed for reproducibility) over features derived from each expense:
  EUR amount, day of week, month, and category.
- **Explanation:** each flagged row is annotated with how many times larger it
  is than the **median amount of its own category** (e.g. "6.2× your usual
  groceries"), so the flag is explainable rather than a black box.
- **Data rules:** needs at least 20 expenses; smaller histories return nothing.

### 3. Learned expense categorizer (TF-IDF + Logistic Regression)

- **What it does:** suggests a category for a merchant description in bank
  import and receipt OCR, learned **from your own labelled expenses only**.
- **Algorithm:** TF-IDF character features (word and 2-word n-grams) fed to a
  multinomial Logistic Regression.
- **Training:** on demand, from your expense descriptions and the categories
  you (or the app) assigned. It needs at least 10 rows across at least 2
  categories; below that it stays silent and the keyword map is used.
- **Per-user isolation:** one model per account — training data never leaks
  between users.
- **Freshness:** the model is cached per
  `(user, model version, dataset fingerprint)`. **Any correction, addition, or
  deletion of an expense changes the fingerprint and retrains the model
  immediately**, so it never serves stale suggestions after you fix a wrong
  category. Account deletion clears the cache.
- **Precedence:** classifier → keyword map → manual review. You always see and
  can change the suggestion before anything is saved.
- **Telemetry (measurement-first):** every suggestion records its source
  (classifier/keywords), confidence, model version, normalized merchant, and
  whether you accepted or corrected it — the basis for measuring correction
  rate and deciding when the model is good enough to extend (subcategory
  prediction, character n-grams, higher training floor).

### 4. Monthly spending-pattern clustering (KMeans)

- Groups your past months by their category-mix similarity (KMeans on the
  category composition of each month) and describes the current month's
  cluster with its dominant categories, e.g. "this month looks like your
  travel-heavy months". Requires enough monthly history; otherwise it reports
  "not enough data".

### 5. Subscription detection (rule-based)

- Finds `(description, amount)` pairs that repeat with an **average gap of
  25–35 days across at least 3 months** — a simple, explainable monthly-bill
  detector — and offers one-click "add to Recurring".

### 6. Budget suggestions

- Suggests per-category budgets from your history: the recent 6-month average
  per category plus its linear trend (one step ahead), for categories with at
  least 3 months of data — a starting point you can edit.

### ML principles

- **No cloud processing, no cross-user training, no LLM "financial advice".**
  All models train on the server from your data alone.
- Everything is **server-side and lazy**: models train only when there is
  enough data, run quickly on one machine, and fall back to transparent
  rule-based behaviour otherwise.
- Receipt images are processed in memory and never stored.

---

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

**Rate validation:** zero, negative, and non-finite rates are rejected at
entry and ignored if found in stored settings — a zero rate can never be
silently interpreted as a 1:1 conversion.

## Security notes

- **The whole database is encrypted at rest** (SQLCipher 4, AES-256). The key
  is derived from the master secret in `data/.secret_key` (or
  `EXPENSE_TRACKER_DB_KEY` / the `encryption_key` Streamlit secret). An
  existing plaintext database is migrated automatically on first start, with
  verification and crash-safe rollback. **Back up `data/.secret_key`
  separately** — without it the database, the backups, the SMTP password, and
  the GitHub token are all unreadable. (`DATABASE_URL`/PostgreSQL hosts are
  out of scope: encrypt the volume instead.)
- Passwords are never stored in plaintext: logins are bcrypt-hashed, the SMTP
  password, the GitHub backup token, and the AI API key are Fernet-encrypted
  with the same master key (and none of them are ever included in exports).
- **Backups are ciphertext** — local `data/backups/` files and GitHub uploads
  contain no readable data, and the key is never included.
- LAN traffic is plain HTTP by default (suitable for a trusted home network).
  For encrypted traffic, set `EXPENSE_TRACKER_TLS=1` — the launchers generate
  a self-signed certificate and serve the app and the sync API over HTTPS.
  (The certificate is self-signed, so trust it once on each device; when you
  later host publicly, terminate TLS with a real certificate at the reverse
  proxy instead.)
- SMTP STARTTLS verifies the mail server's certificate and hostname by default.
- Device tokens are stored hashed (SHA-256), expire after 90 days without use,
  and pairing codes are single-use, 10-minute, rate-limited (5 tries / 10 min).
- Spreadsheet exports escape formula-like cells (=, +, -, @) so user-entered
  text can't execute when the file is opened.
- Anyone on your network can create an account while registration is open.
  When hosting publicly, set `ALLOW_REGISTRATION=false` (env var or
  `st.secrets`) — the Docker Compose default is already `false`, and the
  Streamlit port is bound to loopback with Caddy as the only public endpoint.
- Login attempts are throttled (5 per minute per client).
- The sync API is schema-validated and conflict-protected (see the sync
  section above).

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `ALLOW_REGISTRATION` | `false` hides the create-account tab | `true` (app) / `false` (Docker) |
| `DATABASE_URL` | SQLAlchemy URL (e.g. PostgreSQL) | SQLite `data/expense_tracker.db` |
| `DB_PATH` / `BACKUP_DIR` | SQLite file / backup location overrides (used by tests) | `data/…` |
| `EXPENSE_TRACKER_DB_KEY` | Master secret override (64 hex chars, a base64 key, or a passphrase — hashed) | `data/.secret_key` |
| `EXPENSE_TRACKER_NO_ENCRYPT` | `1` disables SQLCipher (escape hatch; encryption is the default) | unset (encrypted) |
| `EXPENSE_TRACKER_MCP_USERNAME` | Account the MCP server reads/writes | first-created account |
| `EXPENSE_TRACKER_TLS` | `1` serves the app/API over HTTPS (self-signed cert) and advertises `https://` LAN URLs | unset (plain HTTP) |
| `STREAMLIT_SERVER_PORT` | Streamlit port | 8501 |

## Project structure

```
app.py                  # entry: auth/onboarding gates, sidebar, alerts, nav
auth.py                 # login/registration (throttled), password hashing
onboarding.py           # 2-step first-run wizard
db.py                   # SQLAlchemy models, migrations, CRUD, backups, devices
queries.py              # cached readers keyed by a shared DB revision
utils.py                # currency engine, formatting, categories, CSS, helpers
finance.py              # loan amortization + portfolio math (pure, tested)
market_data.py          # Yahoo/Stooq price fetching + background refresh
rates.py                # live exchange-rate refresh (frankfurter / er-api)
forecasting.py          # ML: ETS forecast, anomalies, categorizer, KMeans, ...
insights.py             # Insights page renderer
gamification.py         # milestones, streaks, badges, fun-money rewards
notifications.py        # email alerts/reminders/weekly summary
ocr.py                  # Tesseract receipt pipeline (amount/merchant/category)
pdf_import.py           # PDF bank-statement extraction
bank_import.py          # CSV import + review + dedupe
sync_core.py            # sync protocol: schemas, cursor, atomic apply, snapshot
api.py                  # FastAPI sync API (port 8502), pairing, rate limits
crypto.py               # master key: SQLCipher DB key + Fernet field encryption
llm.py                  # optional LLM: local Gemma (llama.cpp) or API key
models/                 # optional local Gemma GGUF files (ignored)
mcp_server.py           # MCP server for OpenClaw / AI assistants (stdio or HTTP)
github_backup.py        # encrypted backups to GitHub + restore CLI
make_cert.py            # one-shot self-signed certificate generator
run_server.bat/.ps1     # HTTPS launchers (cert + app + API)
compose.yaml/Caddyfile  # secure Docker deployment
app_pages/*.py          # UI pages (Budgets, Rewards & badges, Ask your data, …)
tests/                  # 397 pytest regression/AppTest suites (updated for reliability hardening)
```

## Running tests

```bat
pip install -r requirements-dev.txt
python -m pytest
```

The suite (397 tests) covers the currency engine, loan amortization edge
cases (including interest booked when payments are applied before their due
date), backups, notifications, bank import, forecast/anomaly/categorizer
behaviour, OCR, PDF parsing, portfolio snapshots, budget scoping, entry
editing (including the "edits never rewrite history" guarantees), the sync
protocol and API (pairing, throttling, cursors, conflicts), formula-injection
safety, cache invalidation, gamification achievements, database encryption
(creation, plaintext→ciphertext migration, wrong keys, encrypted backups),
GitHub backups (chunking, checksums, retention, error paths — with mocked
HTTP), the MCP tools, the optional LLM layer (mocked providers, escaping,
fallbacks), plus Streamlit AppTest smoke tests that run every page. Tests use
a throwaway database and never touch `data/expense_tracker.db`.

## Hosting later (VPS / server)

The data layer is SQLAlchemy, so SQLite → PostgreSQL is configuration, not code:

```bash
docker compose up -d --build        # SQLite in a named volume; app on loopback,
                                    # Caddy is the only public endpoint
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

## Roadmap

- **Offline phone PWA** — the sync server contract is ready; the client app is
  the next milestone.
- **ML upgrades, measurement-first** — the suggestion telemetry collected
  today will drive: subcategory prediction, character n-grams, a higher
  training floor, ETS backtesting against seasonal-naive baselines, and
  explainable median/MAD anomaly rules before IsolationForest.
- **OCR upgrade (optional)** — benchmark Tesseract against the small
  `latin_PP-OCRv5_mobile_rec` model (supports Serbian and other Latin-script
  languages); PP-Structure for table-heavy PDFs only where parsing fails.
- **LLM ideas (the engine is in place)** — anomaly explanations in words and
  OCR/bank-import merchant & category normalization when the trained
  classifier is unsure. Insights/MCP narratives and the ask-your-data chat
  are shipped — see the Feature guide.

Shipped recently: SQLCipher database encryption with automatic migration,
encrypted GitHub backups with a restore CLI, the OpenClaw/MCP assistant
integration, the optional local-Gemma/API assistant (weekly emails, Insights
narrative, MCP Insights narrative, and the ask-your-data chat), dedicated Budgets and
Rewards & badges pages, user-created custom milestones with fun-money rewards,
category-grouped ordered commitments/wishlists, automatic income-based hourly
rates, loan early-repayment surcharge tracking, and reliability hardening
(recurring dialogs, error boundaries on all DB sinks, multi-write atomicity,
double-submit guards, atomic JSON settings merges, budget-scope correction,
and NaN/FK guards).
## Agent Knowledge System

Future AI agents (and subagents) start at [`agent instructions/README.md`](./agent%20instructions/README.md) — the coordinator router for 19 domain docs (8 subsystems + 4 architecture docs) that map the codebase, dependency graph, execution flows, and invariants (G1–G13). See [`agent instructions/`](./agent%20instructions/) for shell/auth, persistence/crypto/caching, currency/taxonomy, ledger/recurring/audit, planning/wealth, ingestion, intelligence, and connectivity surfaces.

## QA Bug Map

The latest validated QA dossier lives at [`qa/reports/final-qa-bug-map.md`](./qa/reports/final-qa-bug-map.md) (registries `qa/registry-findings.json` + `qa/registry-patterns.json`) — produced by a coordinator-controlled swarm (8 domain teams → 36 adversarial validators → 6 narrow pattern hunters → 3 boundary teams, 53 subagents, no production-code mutations). Summary: **36 candidates → 19 CONFIRMED + 1 HIGH-CONFIDENCE + 6 SUSPECTED + 10 NOT-A-BUG**; hunters add **18 analogous manifestations** (SYSTEMIC patterns P1–P6: unbounded whitelist, stale derived state, sentinel/NaN asymmetry, lock/memo divergence, time/EU heuristic drift, N+1 partial commit). See the report for causal chains, dynamic taxonomy, and remediation order.
