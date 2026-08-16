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
5. [How the ML models work](#-how-the-ml-models-work)
6. [Currency model](#currency-model)
7. [Security notes](#security-notes)
8. [Configuration](#configuration)
9. [Project structure](#project-structure)
10. [Running tests](#running-tests)
11. [Hosting (VPS / server)](#hosting-later-vps--server)
12. [Roadmap](#roadmap)

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
dependencies, and starts the server on `0.0.0.0:8501` over plain **HTTP**.

Open **http://localhost:8501** on the PC.

> Want HTTPS instead? Set `EXPENSE_TRACKER_TLS=1` before running the
> launcher — it generates a self-signed certificate in `data/certs/` (once)
> and serves the app and the sync API over HTTPS. Your browser/phone will
> ask you to accept the self-signed certificate the first time.

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
- One-tap monthly salary logging, salary-cycle projection, and automatic
  **raise detection** (a salary entry higher than every earlier one).
- Every entry can be **edited after the fact** — date, source, income type,
  actual/budgeted amount, currency, and notes, via an "Edit an income entry"
  dialog — and editing touches only that row.
- Soft-delete + restore, like expenses.

**Savings goals** (`app_pages/savings.py`)

- Named goals with a target; log **deposits and withdrawals** (balance is
  clamped at zero, never negative).
- **Monthly compound interest**: the balance chain is recomputed on every read
  from the deposit history, compounding at each entry's interest rate over the
  elapsed months. This means the chain is always consistent — and editing or
  deleting an entry intentionally updates the chain *from that entry forward*
  (nothing else is rewritten).
- Entries are editable (date, amount, target in EUR, interest rate, notes)
  via an "Edit a savings entry" dialog, and soft-delete/restore is supported.

### Editing & history safety (applies everywhere)

Almost every entry in the app can be edited or corrected after it was
created, with one consistent rule: **editing never rewrites the history that
was already recorded.**

| Entry | Editing | History guarantee |
|---|---|---|
| Expenses | Inline in the history editor (all fields, paginated) | Each expense stores its own original amount/currency/EUR value; edits rewrite only that row |
| Income | "Edit an income entry" dialog (date, source, type, amount, currency, budgeted, notes) | Only that row changes |
| Savings entries | "Edit a savings entry" dialog (date, amount, target in EUR, interest, notes) | The balance **chain** recomputes from that entry forward — that's the intended math, no other rows are rewritten |
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
- Locale-aware number parsing (e.g. `1.234,56`), date-first parsing (so dates
  are never mistaken for amounts), and debit/credit detection.
- **Auto-categorisation**: your learned classifier first (see ML section),
  then a keyword map as fallback.
- A review editor lets you correct categories and untick rows before import;
  the EUR value is recalculated from what you edited, and duplicates (both
  against the database and within the same upload) are skipped.

### Plan — budgets & commitments

**Budgets** (Settings → Budget)

- **Overall monthly budget** entered in your display currency with a live EUR
  preview (stored as the EUR base value).
- **Category budgets** with optional subcategory granularity. Each scope
  (year, month, category, subcategory) is unique — saving the same scope again
  updates it. When subcategory budgets exist they are authoritative for that
  category; otherwise the whole-category budget applies. Overlapping rows are
  never summed together.
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

**Loans** (`app_pages/loans.py`)

- Principal (any currency), annual interest rate, term in months, start date,
  and payment day.
- **Real amortization against your actual payment history**: the schedule
  attributes each logged payment to its due month (payments made off the due
  day still count), accrues interest monthly, and reports remaining balance,
  remaining months, payoff date, interest paid/remaining, and total cost.
  Missed or partial payments extend the payoff date.
- The first due date is the first payment day **on or after** the start date
  (no phantom first month), and the remaining-payment count rounds up so a
  €149 balance at €100/month correctly needs 2 payments.
- **Editable terms** (name, principal, rate, term, payment day, start date,
  status): editing recomputes the schedule but never touches logged payments.
- Email reminders N days before the due day; deleting a loan keeps its payment
  expenses.

**Big purchases** (`app_pages/big_purchases.py`)

- Wishlist items with price, expected usage (hours/month), and importance
  (1–5), plotted on a **4-quadrant priority matrix** (Quick wins / Plan & save /
  Maybe later / Reconsider).
- Status flow: wishlist → saving → **bought**, with a confirmation dialog that
  logs the purchase as an expense in one step.
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

### Household & Data

**Household** (`app_pages/household.py`)

- Create a household with a **shareable invite code** (always visible with a
  copy block after creation), join via a code, and leave.
- The dashboard's household view combines expenses across members while
  keeping personal income/savings/budgets/loans separate — no misleading
  mixed totals.

**Audit log** — every create/update/delete across the app is recorded with a
timestamp, table, record id, and details; exportable to Excel.

**Settings** also contains: currency & rates, budgets, fun money, travel,
notifications, account (display name, password change, account deletion with
typed confirmation), data export/backup, and phone sync.

### Gamification & fun money

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
  persisted, and add a bonus to next month's fun-money allowance.
- A **budget-adherence streak** counts consecutive months under budget.

### Notifications & email alerts

- **Budget alerts**: toast (and optional email) when a category budget is
  ≥ 90 % used or exceeded.
- **Bill reminders**: email N days before a recurring bill's due day (N is a
  setting, default 2); templates without a due day use the old "on/after the
  25th" fallback.
- **Loan reminders**: same logic for loan payments.
- **Weekly summary**: every Monday (never skipped), a spending summary in your
  display currency.
- Alerts are sent from a background thread (the UI never blocks on SMTP), and
  a "sent" marker is persisted **only after the mail server confirms
  delivery** — failed sends are retried on a later run instead of being
  silently marked sent.
- Your own SMTP account is used; the password is encrypted (Fernet) and the
  STARTTLS connection verifies the mail server's certificate.

### Data, backups & export

- **Export everything**: a zip containing Excel files for expenses, income,
  savings, budgets, recurring, big purchases, loans, holdings, holding-price
  history, audit log, settings, household metadata, devices, milestones, and
  sync conflicts — plus individual per-table downloads.
- **Spreadsheet safety**: cells starting with `=`, `+`, `-`, or `@` are
  exported as inert text, so user-entered descriptions can't execute as
  formulas when the file is opened.
- **Backups**: a WAL-safe SQLite snapshot is taken automatically once per day;
  the manual "Back up now" button always takes a fresh, timestamped copy
  (even twice on the same day), writes are atomic, and old backups are pruned
  after 30 days.

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

The offline PWA client itself is the next milestone — the server contract is
ready.

### Receipt OCR setup (optional)

```bat
winget install UB-Mannheim.TesseractOCR
```

The Docker image installs it automatically. Without Tesseract, the rest of the
app works normally and the scan control shows a friendly hint instead.

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

- Passwords are bcrypt-hashed; SMTP passwords are encrypted (Fernet) with a key
  in `data/.secret_key` or the `encryption_key` Streamlit secret.
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
make_cert.py            # one-shot self-signed certificate generator
run_server.bat/.ps1     # HTTPS launchers (cert + app + API)
compose.yaml/Caddyfile  # secure Docker deployment
app_pages/*.py          # the 15 UI pages
tests/                  # 200+ pytest regression/AppTest suites
```

## Running tests

```bat
pip install -r requirements-dev.txt
python -m pytest
```

The suite (224 tests) covers the currency engine, loan amortization edge
cases, backups, notifications, bank import, forecast/anomaly/categorizer
behaviour, OCR, PDF parsing, portfolio snapshots, budget scoping, entry
editing (including the "edits never rewrite history" guarantees), the sync
protocol and API (pairing, throttling, cursors, conflicts), formula-injection
safety, cache invalidation, gamification achievements, plus Streamlit AppTest
smoke tests that run every page. Tests use a throwaway database and never
touch `data/expense_tracker.db`.

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
