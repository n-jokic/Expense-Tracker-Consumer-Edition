# Notifications & Market Data — Connectivity Reference

> **Scope:** Email delivery (`notifications.py`), SMTP secrets, budget/bill/loan/weekly alert checkers, settings UI, and portfolio market-data refresh (`market_data.py`). Covers scheduling, dedupe markers, HTML safety, and external price fetchers.

---

## 1. Purpose & Scope

Notifications keep users on-budget without opening the app: budget exceeded/near-limit toasts+emails, bill/loan due reminders, and a Monday weekly summary. Market-data keeps portfolio holdings valued live (Yahoo→Stooq fallback, daily auto-refresh). Both are best-effort, never block the UI, and degrade gracefully (previous price/email retry next run).

In scope: SMTP transport, per-month dedupe markers, HTML escaping, background threads/locks, rate-limit caches. Out of scope: push notifications, SMS.

## 2. Components & Files

| Component | File | Lines | Role |
|---|---|---|---|
| Notifications | `notifications.py` | 652 | `send_email`, `send_email_async`, template builders, 4 checkers (`check_and_send_*`), marker helpers, `render_notification_settings` |
| Market data | `market_data.py` | 176 | `fetch_price_yahoo`, `fetch_price_stooq`, `fetch_price`, `_fetch_cached` (30m), `prices_are_stale`, `refresh_prices_if_due`, `maybe_refresh_in_background` |
| Secrets | `crypto.py` | — | `encrypt_str`/`decrypt_str` (Fernet) used for `smtp_password_enc` — same master secret as DB + GitHub token |
| DB settings | `db.py` | — | `get_settings`, `save_settings`, `atomic_update_setting_json`, `UserSettings` (`sent_markers`, `weekly_summary_last_sent`, `smtp_*`, `bill_reminder_days`, `weekly_summary`, `alert_email`) |
| Rates | `utils.py` / `rates.py` | — | `NEAR_LIMIT_THRESHOLD`, `effective_category_budgets`, `get_rates`, `fmt`, `SUPPORTED_CURRENCIES` |
| Loans | `finance.py` | — | `annuity_payment` (used to render loan reminder amount) |
| Settings UI | `notifications.py:render_notification_settings` + `app_pages/settings_ai.py` | — | Rendered in Settings → Notifications tab; AI paragraph section is adjacent but not part of notifications module |

## 3. Data Model & State

```python
# UserSettings columns relevant to notifications
email_alerts        bool default False
alert_email         str | None      # destination; stripped in exports
smtp_host           str | None      # stripped in exports
smtp_port           int default 587  # stripped in exports
smtp_user           str | None      # stripped in exports
smtp_password_enc   str | None      # Fernet ciphertext — never exported
bill_reminder_days  int default 2   # days before due_day to email (0-14 in UI)
weekly_summary      bool default False
weekly_summary_last_sent date | None   # persisted ONLY after confirmed delivery
sent_markers        JSON | None     # {"budget_2025_7": ["Food"], "bill_2025_7": ["<template_id>"], "loan_2025_7": [...]}

# Per-session (Streamlit) — supplements persisted markers but never replaces them
st.session_state["budget_alerted_2025_7"] = set(...)
st.session_state["reminder_sent_2025_7"]  = set(...)
st.session_state["loan_reminder_sent_2025_7"] = set(...)
st.session_state["weekly_summary_sent"] == date.today()  # one-shot guard per session
```

**Market-data state:**

```python
# Holding columns
Holding.symbol            str  e.g. "AAPL", "VWCE.DE"
Holding.last_price        float
Holding.last_price_date   datetime (UTC, written with tzinfo)
Holding.currency          str

# HoldingPrice snapshot (append-only daily)
HoldingPrice.holding_id   FK holdings.id
HoldingPrice.date         date
HoldingPrice.price        float
HoldingPrice.quantity     float   # quantity AT SNAPSHOT TIME
HoldingPrice.rate         float   # 1 EUR = X holding currency at snapshot time
HoldingPrice.value_eur    float   # quantity*price/rate (precomputed, stable if quantity/rates later change)

# In-process cache
@st.cache_data(ttl=1800) _fetch_cached(symbol) -> float | None  # 30-min failure cache including None
_refresh_lock  threading.Lock()  # global, per-process, prevents overlapping background refreshes
```

## 4. Flows (End-to-End)

### 4.1 Send path (all alerts funnel through this)

```
Checker (budget/bill/loan/weekly)                   send_email_async         send_email (blocking)
  | decide to alert (see §4.2-4.5)                    daemon Thread             |
  | send_email_async(host, port, user, pwd, to, subj, html, on_done=cb)  ──> |
  |                                                             send_email(*args) inside thread
  |                                                             MIME multipart/alternative
  |                                                             Subject: str(subject).replace("\r"," ").replace("\n"," ")
  |                                                             STARTTLS with ssl.create_default_context() (CERT_REQUIRED)
  |                                                             login + sendmail
  |                                                             return (True,"OK") or (False,str(e))
  |                                                             on_done(ok, err) in thread
  |                                                             if ok: _persist_marker(...)
  |                                                             else: log.warning + retry next run
  | UI never blocks — thread is daemon; app may exit and lose in-flight send
```

### 4.2 Budget alerts

```
Every page load → check_and_send_budget_alerts(user_id, expenses_df, budgets_df, settings, rates, DC)
  settings = _fresh_markers(user_id, settings)  // overlay persisted sent_markers + weekly_summary_last_sent from DB (caller's snapshot may be stale)
  Filter to current year/month (m_exp, m_bud); if either empty → no-op
  month_key = f"{year}_{month}"
  markers = {session_alerted ∪ persisted_budget_month ∪ ...}
  ca = m_exp.groupby(category).sum(amount_eur); cb = effective_category_budgets(m_bud)  // category-level budgets
  For each cat in ca:
    bud>0?  over = act>bud; level = "exceeded"|"near"; key = f"{cat}:{level}"
    already = key in alerted OR (level=="near" and cat in alerted) // "near" suppressed if already exceeded
    Threshold: act >= bud * NEAR_LIMIT_THRESHOLD  // from utils, e.g. 0.8
    If not already and threshold:
      session_mark = add key
      toast (icon 🔴 if over else 🟡)
      If email_alerts + alert_email + smtp_host + smtp_user:
        _persist_marker(user_id,"budget",month_key,cat) // pre-persist dedupe (immediate)
        build_budget_alert_email → HTML (escaped, table with spent/budget, ⚠️ header)
        send_email_async(..., on_done=_marker_on_delivery(...))  // on_done persists again after delivery
```

### 4.3 Bill reminders

```
check_and_send_bill_reminders(user_id, recurring_df, expenses_df, settings)
  _unlogged_templates(...) → active templates with no expense logged this month
    - active==True, start_month arrived (filter_started_templates)
    - matching: expense.rec_template_id == template.id OR legacy fallback (description+amount_eur on recurring=True rows)
  If unlogged: sidebar warning "🔔 N bill(s) not yet logged"
  Gate: needs email_alerts + smtp creds
  days_before = int(settings.bill_reminder_days or 2)  // UI 0-14
  month_length = calendar.monthrange(year,month)[1]
  For each unlogged row (first 5):
    due_day present?  remind_day = due_reminder_day(due_day, days_before, month_length) // clamp due_day-days_before to [1, month_length]
                      today.day == remind_day? else skip
    no due_day?       today.day <25 → skip; else due_note="Due this month"
    already sent this month? (session ∪ persisted bill_month) → skip
    persist marker + session mark
    amount_str = f"€{amount_eur:,.2f}"
    send_email_async → build_bill_reminder_email(template description, amount_str, due_note)
```

### 4.4 Loan reminders

```
check_loan_reminders(user_id, loans_df, expenses_df, settings)
  paid_loan_ids = {expense.loan_id for expenses in current month}
  unlogged = [loan for active loans if id not in paid_loan_ids]
  Same gating, due_reminder_day(payment_day, days_before, month_length), 5-cap
  Amount rendered via annuity_payment(principal_eur, annual_rate, term_months)
  Subject "Loan Payment Reminder: {loan.name}"
```

### 4.5 Weekly summary (Mondays only)

```
check_and_send_weekly_summary(user_id, expenses_df, settings)
  _fresh_markers; gate: weekly_summary + email_alerts + smtp creds
  date.today().weekday()!=0 → skip (Monday=0)
  st.session_state.weekly_summary_sent == today → skip // session guard: DB marker only lands AFTER delivery, so reruns before delivery would re-send
  week_start = today - 6d; last = settings.weekly_summary_last_sent
    if last >= week_start → skip // only skip if already sent THIS week (prev Monday -6d..today)
    // regression: comparing to today-7d would skip every second Monday
  window = expenses where date in [today-7d, today] (future-dated rows excluded)
  prev_window = [today-14d, today-6d) for AI paragraph delta
  top_cats = nlargest(3) categories in window
  Optional AI paragraph: llm.generate_summary({total_eur, prev_week_eur, top_categories}, settings)
    failure → log.warning, email still sent without paragraph
  Rates = get_rates(settings); DC = settings.default_currency or "EUR" // NB: bug was "display_currency" → always EUR; fixed to default_currency
  build_weekly_summary_email(display_name, window, rates, DC, ai_paragraph)
    → header "📊 Your Weekly Summary", total in user's DC (fmt), top-3 table, motivation ("Great job…" if <100 EUR else "Every euro tracked…"), AI block escaped
  on_done: save_settings {weekly_summary_last_sent: today} ONLY on ok
  session_mark: st.session_state.weekly_summary_sent = today (one-shot)
```

### 4.6 Market-data refresh

```
App login (queries / app.py) → maybe_refresh_in_background(user_id)
  holdings = get_holdings(user_id); if empty or not prices_are_stale → return
  _refresh_lock.acquire(blocking=False) else return (prevents overlap)
  daemon thread _worker:
    _updated, changed = refresh_prices_if_due(user_id, force=False, cached=False)
    if changed: bump_data_revision(user_id, include_household=False)

refresh_prices_if_due(user_id, force, cached):
  holdings = get_holdings(user_id); if empty → (0, False)
  if not (force or prices_are_stale(holdings)) → (0, False)
  get = _fetch_cached if cached else fetch_price  // _fetch_cached is 30m TTL including failures
  rates = get_rates(get_settings(user_id))
  for each holding:
    price = get(symbol)  // fetch_price = Yahoo then Stooq
    if price is None → skip (keeps previous last_price)
    update_holding(user_id, holding.id, {last_price: price, last_price_date: datetime.now(timezone.utc)})
    add_holding_price(id, price, quantity, rate)  // snapshots quantity+rate+value_eur atomically
  return (updated, updated>0)

fetch_price_yahoo: GET query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d
  meta.regularMarketPrice else last non-None close in indicators.quote
  -> float or None; >0 check
fetch_price_stooq: GET stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv → CSV parse, Close field, N/D/empty/<=0 → None
fetch_price: Yahoo first, Stooq fallback
prices_are_stale: any holding with last_price_date None/NaT or (now_utc_naive - ts).days >= PRICES_MAX_AGE_DAYS (1)
  // UTC-aware compare: now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None); ts tz-aware → tz_convert(UTC).tz_localize(None)
```

## 5. API / Interface Contract

**Notifications do not expose an HTTP API** — they are checked inline on each Streamlit page load via `check_and_send_*` calls in the app shell. Settings mutation goes through `save_settings` / `q.save_settings`.

**Market data has no REST endpoint** — symbols are `Holding.symbol` (upper-normalised). Callers use `fetch_price(symbol, timeout=4)`, `prices_are_stale(df)`, `refresh_prices_if_due(...)`, `maybe_refresh_in_background(...)`.

**Email contract:** `send_email(smtp_host, smtp_port, smtp_user, smtp_password, to_email, subject, html_body) → (bool, str)`. `send_email_async(*same_args, on_done: (ok,err)->void)` spawns daemon thread; caller must not await.

**Settings UI:** `render_notification_settings(user_id, settings)` renders the Notifications tab (toggle, alert_email, smtp_host/port/user/password, bill_reminder_days 0-14, weekly_summary). Password field blank → keeps existing `smtp_password_enc`. Test email button calls `send_email` synchronously with spinner.

## 6. Validation & Caps

| Cap / Rule | Value | Location | Notes |
|---|---|---|---|
| SMTP port | 1–65535 | `render_notification_settings` number_input + `int(settings.smtp_port or 587)` | Fallback 587 |
| Bill/loan reminder days_before | 0–14 | Settings UI number_input; stored as int | Clamped via `due_reminder_day` |
| NEAR_LIMIT | ~0.8 (from `utils.NEAR_LIMIT_THRESHOLD`) | `check_and_send_budget_alerts` | `act >= bud*THRESHOLD` triggers near alert |
| String escape | all user text escaped | `_esc = html.escape(str(s or ""))` in every template | Prevents markup injection |
| Subject CR/LF strip | `\r\n → space` | `send_email` | Prevents header injection when subject interpolates bill/loan names |
| Email batch caps | 5 templates / 5 loans per run | `for row in unlogged[:5]` in bill/loan checkers | Prevents email flood if many bills |
| Chunk? | — | — | No chunking — one email per template/loan per month |
| PRICES_MAX_AGE_DAYS | 1 | `market_data.PRICE_MAX_AGE_DAYS` | Refresh daily |
| Price failure cache | 1800s (30 min) | `@st.cache_data(ttl=1800) _fetch_cached` | Includes None (failed) so a flapping Yahoo doesn't hammer every page load |
| fetch timeout | 4s per provider | `fetch_price*(timeout=4)`, `_open(..., timeout)` | Short — never blocks UI in background path |
| Holding amount caps | reuse `MAX_AMOUNT` (from utils, via `add_expense` family) | via holding UI validators | Prevent extreme holding values |
| Threading | daemon threads, one price refresh at a time | `_refresh_lock` + `send_email_async daemon=True` | Daemon means app exit can drop in-flight work (intentional) |

## 7. Trust Boundaries & Threat Model

| Boundary | Threat | Mitigation |
|---|---|---|
| User text → email HTML | Markup/XSS in expense description, category, display name, bill name → email client renders as HTML | `_esc` (html.escape) on every interpolation in `build_*_email`; AI paragraph also escaped before embedding |
| Bill/loan name → email Subject | `Subject: "Bill Reminder: \r\nBcc: evil@x"` → header injection | `send_email` replaces `\r`/`\n` with space in Subject |
| SMTP MITM | Downgrade or cert spoof intercepts credentials | `STARTTLS` with `ssl.create_default_context()` → `CERT_REQUIRED` + `check_hostname=True` (`tests/test_notifications.py::test_send_email_verifies_tls_certificates` asserts both) |
| Stale snapshot clobber | Two checkers in same page load race on `sent_markers` JSON → one overwrites the other's markers → duplicate email next run | `_persist_marker` + `_fresh_markers` use `atomic_update_setting_json(user_id, "sent_markers", merge_fn)` — read fresh DB, merge, write; caller snapshot may be stale but function re-reads (`tests/test_notifications.py::test_persist_marker_reads_fresh_db_state`) |
| Email-before-marker ghost | Marker persisted even though SMTP failed → user never alerted | `on_done` callback persists ONLY on `ok=True`; pre-persist exists for budget path but is best-effort — `_marker_on_delivery` logs warning and leaves marker for retry if delivery fails |
| Session vs DB race (weekly) | Rerun before async delivery completes sees no DB marker → re-sends | `weekly_summary_sent` session guard set immediately; DB marker only after delivery; week-window check uses `>= week_start (today-6d)` not `>= today-7d` (which would skip every second Monday) |
| Market price injection | Stooq/Yahoo CSV/JSON crafted to inject large negative or zero price | Parser returns None for `N/D`, empty, `<=0`; no price update → previous value kept |
| SSRF via symbol | Crafted symbol like `../../evil` hits arbitrary URL | URL templated with `{sym}` into finance domains only; no redirect follows beyond urlopen default; timeout 4s |
| Price staleness timing | Local midnight drift causes stale check off by 1 day | `prices_are_stale` compares in UTC (tz-aware conversion) — not `date.today()` |
| Secrets in exports | Alert email / SMTP creds leak via Excel export | `app_pages/settings.py` Data tab pops `smtp_password_enc, smtp_user, alert_email, smtp_host, smtp_port, gh_token_enc, ai_api_key_enc` before building settings sheet |

## 8. Authentication, Authorization & Secrets

- **SMTP creds:** `smtp_password_enc = encrypt_str(plaintext)` (Fernet, key from `crypto.py` master secret — same key as DB SQLCipher + GitHub token). Decrypted only inside checker right before send: `decrypt_str(settings.smtp_password_enc or "")`. Never logged, never in HTML, never in exports. Password field in settings: blank means "keep existing" — no zeroing.
- **No separate notification auth:** eligibility is "user has saved settings with alert_email + smtp creds and enabled flags". No token.
- **Market data:** key-less, no auth; scraping via User-Agent `ExpenseTracker/1.0 (+local personal app)` — no API key in repo or settings.
- **AI paragraph:** weekly summary fetches `llm.generate_summary` using `ai_*` settings (provider none|local|api; api key is `ai_api_key_enc` Fernet). Untrusted model output escaped before email insertion. Failure is non-fatal (`except: log.warning`).

## 9. Concurrency, Atomicity & Ordering

- **Markers:** `atomic_update_setting_json` → `SELECT user_settings WHERE user_id=?` + JSON merge in Python → `UPDATE` — short transaction; concurrent callbacks both succeed and merge via sets (see `_persist_marker: set(cur[kind_month]) ∪ {item}`).
- **Weekly summary ordering:** checks Monday first, then session guard, then DB marker, then computes windows, then spawns thread, then sets session guard — order prevents double-send even if two page loads interleave.
- **Price refresh:** `_refresh_lock = threading.Lock()`; `maybe_refresh_in_background` tries `acquire(blocking=False)` — if held, no-op. Worker always releases in `finally`. `refresh_prices_if_due` called with `cached=False` in background (bypass 30m cache) so actual prices fetched; interactive path uses cached.
- **Holding snapshot:** `update_holding` + `add_holding_price` per holding in loop, not atomic across holdings — partial success is fine; caller returns `updated` count.

## 10. Error Handling, Observability & Audit

| Failure | Handling | Observability |
|---|---|---|
| SMTP login/connect/STARTTLS error | `send_email` catches all → `(False, str(e))`; `on_done` logs warning and does NOT persist marker → retry next page load | `log.warning("email not delivered (%s/%s/%s): %s — will retry", kind, month, item, err)` |
| `_persist_marker` failure | `try: _persist_marker except: pass` (budget path) or warning log | Budget dedupe best-effort; next run will retry |
| AI paragraph failure | `except Exception as e: log.warning("weekly summary AI paragraph unavailable: %s", e)` | Email still sent without AI block |
| Rates lookup failure | `get_rates` returns defaults; `fmt` fallback | Weekly email still renders in EUR fallback |
| Market fetch failure | `fetch_price_yahoo/stooq` catch + `logger.warning`, return None; `_fetch_cached` caches None 30m | holdings keep previous `last_price`; `refresh_prices_if_due` returns `(0, False)` |
| Missing/Unlogged fallback | If `recurring_df` empty or all `start_month` in future → no sidebar warning | Silent no-op |
| Exports with no data | Sheet omitted if DataFrame empty | Zip still valid |

**Sidebar observability:** bill/loan checkers always render `st.sidebar.warning("🔔 N bill(s) …" / "💳 N loan …")` when unlogged items exist — visible even when email not configured.

## 11. Configuration & Deployment Surfaces

- **Settings keys (UserSettings):** `email_alerts`, `alert_email`, `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password_enc`, `bill_reminder_days`, `weekly_summary`, `weekly_summary_last_sent`, `sent_markers`.
- **Secrets via crypto:** `encrypt_str`/`decrypt_str` depend on master secret `state_dir()/.secret_key` or `EXPENSE_TRACKER_DB_KEY`. Losing secret → unreadable smtp_password_enc (requires re-entry).
- **Scheduling:** no cron — checkers run on every Streamlit page load (i.e. every rerun). Cadence enforced by markers + calendar day checks (25th fallback, due_day match, Monday). Market prices refreshed on login / every page load gated by `prices_are_stale`.
- **Retries:** marker not set on failure → next page load retries. No exponential backoff.
- **Env vars:** none for notifications; market data uses no env.
- **Deployment:** SMTP must allow STARTTLS on chosen port (default 587). Common provider: Gmail App Password (STARTTLS). No relay — direct `smtplib.SMTP`.

## 12. Tests & Verification

| Suite | Command | What it proves |
|---|---|---|
| `tests/test_notifications.py` (10) | `pytest tests/test_notifications.py -v` | `_persist_marker` fresh DB merge, `_marker_on_delivery` only-on-success, weekly every Monday (last week doesn't suppress), `default_currency` not hardcoded EUR, failed send not marked, session guard prevents resend, HTML escape in all builders, subject CR/LF strip, TLS `CERT_REQUIRED`+hostname |
| `tests/test_market_data.py` (6) | `pytest tests/test_market_data.py -v` | Yahoo regularMarketPrice, fall-back to close, garbage→None, Stooq CSV parse, Stooq N/D→None, `prices_are_stale` empty/None/old logic (UTC) |
| Manual market | `python -c "from market_data import fetch_price; print(fetch_price('AAPL'))"` | Live Yahoo+Stooq path |
| Manual email | Settings → Notifications → Send test email (spinner) | Sync path via `send_email` (not async) |
| Manual weekly | Set `weekly_summary` on Monday, trigger page load, check `weekly_summary_last_sent` in DB | Monday guard + session guard + AI paragraph escaped |

Run gate: `pytest tests/test_notifications.py tests/test_market_data.py -q`.

## 13. Pitfalls, TODOs & Guidance for Agents

**Do:**

- Wrap every email interpolation with `_esc` — including AI paragraph.
- Strip CR/LF from any new Subject interpolation.
- Use `_fresh_markers` at top of any new checker — never trust passed `settings` for marker decisions.
- Persist markers through `atomic_update_setting_json` merge, never by overwriting `settings.sent_markers`.
- Keep market refresh in background daemon + lock; never block UI thread on `urlopen`.
- Use UTC for any price age comparison; never `date.today()` against a UTC timestamp.
- After fixing marker or window logic, add a Monday-edge-case test (today vs today-6d vs today-7d).
- Respect `DEFAULT_CURRENCY` (settings key `default_currency`), not any assumed display name.

**Don't:**

- Don't cache or log decrypted SMTP passwords.
- Don't persist weekly `weekly_summary_last_sent` before `on_done(ok=True)` — next run would think it was already sent this week.
- Don't swallow AI or fee failures by suppressing email — log and send without that section.
- Don't increase email batch caps (`[:5]`) without considering Gmail rate limits.
- Don't bypass `_clean` / JSON safety when adding new market data fields to snapshots.

**TODO / Known gaps:**

- Email rate limiting is implicit (one email per item per month via markers) — burst of 5+ bills on 25th still sends 5 emails at once.
- No unsubscribe header on emails → spam classification risk.
- Weekly summary runs on every page load on Mondays until first send — session guard handles reruns but a multi-tab open Monday morning still sends once (intended) not zero.
- Market-data 30m cache is persisted across Streamlit's `st.cache_data` — clear with "Clear cache" in Streamlit menu if stale price debugging needed.
- SMTP errors are not surfaced in UI except via logs — checker silently retries; test email path is the only visible error.
