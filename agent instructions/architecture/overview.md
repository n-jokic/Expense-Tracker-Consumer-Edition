# Architecture Overview — Expense Tracker Consumer Edition

> **Entry:** `app.py` (226) → `app_pages/*` (18 pages). **Persistence:** `db.py` (2586) + SQLCipher + `queries.py` versioned cache. **Domain:** `utils.py` (731), `finance.py`, `rates.py`. Read this first — one page to orient any agent.

---

## 1. What This App Is

Single-user-first **personal finance tracker** (Streamlit) that later grew household sharing and phone sync — but still runs as a **single-process monolith** on a home LAN or a small Docker+Caddy host. All data lives in one SQLite file (SQLCipher-encrypted by default); Streamlit renders the shell and 18 pages; a FastAPI sidecar and an MCP stdio server expose the same DB to phones and AI agents. No microservices, no message bus, no ORM migrations framework — plain SQLAlchemy declarative models with ad-hoc `init_db` migration.

**Primary jobs:** log expenses/income, budgets & fun/travel pools, savings/loans/portfolio, bank/PDF/OCR import, forecasting & insights, notifications, phone sync, MCP & GitHub backup.

---

## 2. Runtime Shape

```text
Browser ──► Streamlit :8501 (app.py + app_pages/*)
Phone PWA ─► FastAPI  :8502 (api.py /api/v2/sync) ─┐
OpenClaw  ─► MCP stdio (mcp_server.py)             ├─► same SQLite file
Streamlit ─► market_data / github_backup threads ──┘
                                            │
                              Caddy (compose.yaml) terminates 80/443 → 127.0.0.1:8501
                              Dockerfile FROM python:3.12-slim + tesseract-ocr, user appuser
```

`.streamlit/config.toml`: `address 0.0.0.0, maxUploadSize 10, theme #0F3460`. No XSRF/CORS changes.

---

## 3. Layer Map (bottom → top)

| Layer | Files | Owns |
|-------|-------|------|
| **L0 Infra** | `Dockerfile, compose.yaml, Caddyfile, .streamlit/config.toml, requirements.txt` | Build, host, TLS |
| **L1 Persistence** | `db.py, queries.py, crypto.py, app_paths.py` | 18 tables, SQLCipher+Fernet, WAL, `data_revision`, cache |
| **L2 Domain Core** | `utils.py, finance.py, rates.py, gamification.py, notifications.py` | Categories, currencies, math, rules |
| **L3 Ingestion** | `bank_import.py, pdf_import.py, ocr.py` | Normalize → review → persist |
| **L4 Intelligence** | `forecasting.py, insights.py, llm.py, market_data.py` | Forecast, anomalies, Ask |
| **L5 UI** | `app_pages/*`, `app.py` | Forms, tables, navigation, sidebar |
| **L6 Connectivity** | `api.py, sync_core.py`, household | Pairing, sync, sharing |
| **L7 Surfaces** | `mcp_server.py, github_backup.py` | Agent & backup integrations |

**Dependency direction:** UI → queries → db; ingestion → db + forecasting(categorizer); intelligence → queries; connectivity → db + crypto + sync_core; everything → utils + crypto. No domain bypasses `bump_data_revision()`.

---

## 4. Tech Inventory

| Concern | Library | Notes |
|---------|---------|-------|
| UI | `streamlit 1.61.1` + `st.navigation`, `st.cache_data`, `st.data_editor`, `st.dialog` | Single process, rerun model |
| DB | `sqlalchemy 2.0`, `sqlcipher3-wheels 0.5.7`, `sqlite3` fallback | `DATABASE_URL` overrides to Postgres (no encryption then) |
| Crypto | `cryptography Fernet`, `bcrypt 4.0` | Master secret → SQLCipher key (SHA-256) + field encryption |
| Data | `pandas 2.0`, `openpyxl, pyyaml, plotly, qrcode, pillow` | DataFrames everywhere; Excel export via `utils.to_excel` |
| ML | `scikit-learn 1.4, statsmodels 0.14, pytesseract, pdfplumber 0.11` | All optional — graceful None on missing |
| Sync | `fastapi 0.110, uvicorn 0.29, httpx, requests` | Pairing + sync, GitHub Contents API |
| Agents | `mcp 1.9` | MCPServer stdio (default) / 127.0.0.1 http |

---

## 5. Where State Lives

| State | Location | Access |
|-------|----------|--------|
| **Source of truth** | `state_dir()/expense_tracker.db` (or `DB_PATH`, or Postgres via `DATABASE_URL`) | `db.get_session()` scoped by `user_id` |
| **Revision** | `users.data_revision` + `user_settings.*` | `db.bump_data_revision(user_id)` invalidates all `queries.py` caches (shared across sessions/household) |
| **Session** | `st.session_state{user_id, display_name, settings, rates, dc, onboarding_complete, db_version}` | Written once at boot; `settings` refreshed via `q.save_settings` |
| **Cache** | `@st.cache_data ttl 120-300 key=(user_id, version)` | 14 readers in `queries.py`; settings uncached |
| **In-memory** | `auth._attempts`, `api._pair_attempts`, `rates._fetch_cached(ttl1800)`, `llm._local_cache`, categorizer model dict | Process-local, lost on restart — never source of truth |
| **Files** | `data/.secret_key` (Fernet), `data/backups/*.db` (30d), `models/*.gguf` | Never committed; key never uploaded |

---

## 6. Boot Sequence (app.py)

```text
st.set_page_config(layout wide, wallet icon)
inject_mobile_css()            # utils — responsive tweaks
init_db()                      # db.py — create_all + _migrate + _wait_for_migration_lock
backup_db()                    # db.py — daily copy to data/backups/ (retention 30)
require_auth() or st.stop()    # auth.py — bcrypt, throttle 5/60s, ALLOW_REGISTRATION gate
onboarding_complete? or render_onboarding(); st.stop()  # onboarding.py 2-step
settings = get_settings(user_id)                       # db.py — UserSettings row
settings, _ = refresh_rates_if_due(settings, 3d)       # rates.py — Frankfurter→open.er-api, 30m failure cache
maybe_auto_backup(settings)    # github_backup — background thread, never blocks
_build snapshots: q.expenses/income/savings/budgets/loans → milestones → toasts/balloons
sidebar: display_name, currency select (persist via q.save_settings), rate form (re-read fresh_rates), gamification, QR (get_lan_urls/TLS_ENABLED), logout
check_and_send_* (bill/budget/loan/weekly) + maybe_refresh_in_background() # notifications + market_data (threads)
st.navigation({Overview, Track, Plan, Understand, Play, Household & Data}).run()
```

---

## 7. Page Registry

| Group | Page | File |
|-------|------|------|
| Overview | Dashboard | `dashboard.py` (default) |
| Track | Log expense, Log income, Savings goals, Bank import | `log_expense, log_income, savings, bank_import_view` |
| Plan | Budgets, Recurring, Loans, Big purchases, Travel, Portfolio | `budgets, recurring, loans, big_purchases, travel, portfolio` |
| Understand | Forecast, Insights, Ask | `forecast, insights_view, ask` |
| Play | Rewards & badges | `rewards` |
| Household & Data | Household, Audit log, Settings | `household, audit_log, settings (+ settings_ai)` |

All pages read `st.session_state.user_id/DC/rates/settings`; all writes go through `db.*` then `q.bump_db_version()`.

---

## 8. Cross-Cutting Concerns

- **User scoping:** every `db.*(user_id, ...)` filters `user_id`; admin flag exists but not used for cross-read.
- **Category taxonomy:** `utils.CATEGORIES` (12 cats) + `TAXONOMY_MIGRATION` applied at `db._migrate` and `sync_core.validate_fields`. See `domain/currency-and-taxonomy.md`.
- **Currency:** amounts stored dual `amount + amount_eur` (`to_eur` at write, `fmt` at read). Sync stores canonical Eur; display converts via `rates[DC]`.
- **Phone access:** experimental — LAN URL:port from `get_lan_urls`, QR via `qrcode`, Caddy only in compose. No auth beyond session cookie.
- **Background work:** GitHub backup, market refresh, email sending all daemon threads — never block UI; markers only after confirmed delivery.

---

## 9. Where to Go Next

- Need the dependency graph → `dependency-map.md`
- Need flows spanning multiple docs → `execution-flows.md`
- Need rules that bite across domains → `invariants.md`
- Task-specific routing → `../README.md` context router
