# Shell and Navigation — App Boot, Shared Sidebar & st.navigation Router

## 1. Purpose
Single-process Streamlit shell that owns boot order, global CSS, per-user session hydration, shared sidebar widgets, and grouped st.navigation routing. Every rerun enters app.py top-to-bottom; no app_pages/*.py renders without passing require_auth and the onboarding gate. Bundles LAN phone-access UI (get_lan_urls / qr_png / TLS_ENABLED) with deploy surface (.streamlit/config.toml, Dockerfile, compose.yaml).

## 2. Source Scope
| File | Lines | Symbols |
|------|-------|----------|
| app.py | 226 | boot sequence, with st.sidebar 104-179, pg = st.navigation({...}) 192-225, pg.run() 226 |
| utils.py | 731 | inject_mobile_css 602, get_server_port 672, get_lan_urls 685, qr_png 719, TLS_ENABLED 210, SUPPORTED_CURRENCIES 180, DEFAULT_RATES 188, APP_PORT 207, get_rates 243 |
| app_paths.py | 22 | resource_dir 8, state_dir 12, model_dir 21 |
| db.py | 2586 | init_db 649, backup_db 2195, get_settings 1858, save_settings 1902, get_engine 269, BACKUP_DIR 40, _SETTINGS_DEFAULTS 1838 |
| queries.py | 186 | db_version 21, bump_db_version 35, get_settings 176, save_settings 181 |
| rates.py | 117 | refresh_rates_if_due 93, rates_are_stale 76, RATES_MAX_AGE_DAYS 23, _fetch_cached 70 |
| .streamlit/config.toml | 15 | [theme] primaryColor #0F3460, [server] address=0.0.0.0 maxUploadSize 10, [browser] gatherUsageStats false |
| Dockerfile | 26 | FROM python:3.12-slim, EXPOSE 8501, HEALTHCHECK .../_stcore/health, USER appuser |
| compose.yaml | 30 | services.app (127.0.0.1:8501), services.caddy, ALLOW_REGISTRATION env, app-data volume |
| gamification.py | — | render_gamification_sidebar, get_earned_milestones, award_new_milestones, award_custom_milestones (app.py 80-102) |
| notifications.py | — | check_and_send_budget_alerts, check_and_send_bill_reminders, check_and_send_weekly_summary, check_loan_reminders (app.py 184-187) |
| market_data.py | — | maybe_refresh_in_background 189 |
| github_backup.py | — | maybe_auto_backup 57 |

Imperative module-level code; no classes. Re-executed on every Streamlit rerun.

## 3. Internal Architecture
```
Streamlit rerun -> app.py top-to-bottom
  32 st.set_page_config(page_title, page_icon, layout='wide', initial_sidebar_state='auto')
  38 inject_mobile_css()                          utils.py:602  <style> KPI/progress/badge + @media 768px
  39 init_db()                                    db.py:649  Base.metadata.create_all + _migrate (guard _MIGRATED)
  40 backup_db()                                  db.py:2195  WAL-safe src.backup(dst) once/day via BACKUP_DIR/.last_backup
  43 if not require_auth(): st.stop()             auth.py:257  re-calls init_db; renders login if unauthenticated
  47 user_id/display_name <- st.session_state     seeded by auth.py:220-222 on login
  49 db_version init if absent                    app.py:49-50  fallback 0 pre-login
  51 st.session_state.settings = get_settings()   db.py:1858  fresh dict(_SETTINGS_DEFAULTS) if no row
  54 settings,_ = refresh_rates_if_due()          rates.py:93  stale>=3d -> _fetch_cached (30m) Frankfurter->open.er-api
  57 maybe_auto_backup()  (try/warn)              github_backup.py  daily GH backup, never blocks UI
  64 if not onboarding_complete (default False): render_onboarding(); st.stop()
  68 settings/rates alias + rates -> st.session_state.rates
  75 _expenses/_income/_savings/_budgets/_loans snapshots (single fetch, reused)
  80 get_earned_milestones -> award_new_milestones / award_custom_milestones -> toast/balloons
 104 with st.sidebar:  display_name header, DC select, rate_form, gamification, phone QR, logout
 182 check_and_send_bill_reminders / budget_alerts / loan_reminders / weekly_summary
 189 maybe_refresh_in_background()                 market_data.py  daily portfolio refresh (background)
 192 pg = st.navigation({group: [st.Page(...)]})  grouped dict
 226 pg.run()                                     dispatch to app_pages/*.py
```
Steps 43 and 64 are hard gates (st.stop()); nothing below executes unauthenticated or pre-onboarding. Task description lists onboarding gate before get_settings; ground truth is gate at 64 after hydration 47-61 — see invariants.

## 4. Important Symbols
| Symbol | File:Line | Role |
|--------|-----------|------|
| st.set_page_config | app.py:32 | First Streamlit call; wide layout, auto sidebar |
| inject_mobile_css | utils.py:602 | Emits KPI/progress/badge CSS + mobile breakpoint 768px; sidebar min-width 240px |
| init_db | db.py:649 | create_all + _migrate once per process (_MIGRATED flag); force_migrate for tests |
| backup_db | db.py:2195 | Daily WAL-safe backup; force=True for manual; prunes BACKUP_RETENTION_DAYS 30 |
| require_auth | auth.py:257 | Calls init_db; returns bool; renders login page on False |
| get_settings (db) | db.py:1858 | Fresh read; returns dict(_SETTINGS_DEFAULTS) when no UserSettings row |
| get_settings (queries) | queries.py:176 | Thin fresh wrapper (comment 177 always read fresh) |
| save_settings (queries) | queries.py:181 | _db_save_settings -> refresh st.session_state.settings -> bump_db_version |
| refresh_rates_if_due | rates.py:93 | Merges fetch_live_rates into currency_rates + rates_updated_at |
| maybe_auto_backup | github_backup.py | Lazy import in try 57-61; warning only on ImportError |
| SUPPORTED_CURRENCIES | utils.py:180 | 12 codes {EUR:Euro,RSD:din,...} drives sidebar select 108 |
| get_rates | utils.py:243 | DEFAULT_RATES merged with validated settings.currency_rates via _valid_rate |
| TLS_ENABLED | utils.py:210 | EXPENSE_TRACKER_TLS=='1' selects https scheme 711 |
| get_lan_urls | utils.py:685 | @st.cache_data(ttl=60) UDP 8.8.8.8 probe + hostname fallback; filters 127./169.254. |
| get_server_port | utils.py:672 | st.get_option('server.port') -> STREAMLIT_SERVER_PORT -> APP_PORT 8501 |
| qr_png | utils.py:719 | qrcode.make(url) PNG bytes; SVG rejected (ns prefix invisible) |
| state_dir / resource_dir | app_paths.py:12/8 | Writable data dir vs bundled read-only; respects EXPENSE_TRACKER_DATA_DIR, frozen LOCALAPPDATA |
| st.navigation | app.py:192 | Grouped dict -> nav object; pg.run() 226 dispatches |

## 5. Inputs / Outputs
Inputs: st.session_state auth keys, user_settings row, env vars (ALLOW_REGISTRATION, EXPENSE_TRACKER_TLS, STREAMLIT_SERVER_PORT, EXPENSE_TRACKER_DATA_DIR), LAN interfaces (socket), live FX APIs (Frankfurter/open.er-api), BACKUP_DIR/.last_backup marker.

Outputs: CSS injection, DB file creation/migration, timestamped backup file, sidebar HTML (currency select, rate form, QR, logout button), st.navigation dispatch, st.toast/st.balloons, sent_markers dedup, background threads (GH backup, market refresh).

Contract: app.py owns per-rerun hydration; app_pages/*.py only read st.session_state.{settings,rates,dc,user_id,db_version} and queries.* (cached on db_version). settings itself is never cached.

## 6. State & Ownership
| Key | Owner | Mutation | Notes |
|-----|-------|----------|-------|
| authenticated | auth.py:219 | logout 173 clears | Gate in require_auth 260 |
| user_id | auth.py:220 | — | FK for all DB queries |
| username / display_name | auth.py:221-222 | update_user_display_name | Header 106 |
| household_id | auth.py:223 | join/create household | Shared revision scope |
| onboarding_complete | auth.py:224 / db.py:2112 | onboarding.py:126/133 sets True | Default False gate 64 |
| onboarding_step | auth.py:225 (0) | onboarding.py:41,81 | 0 welcome ->1 currency/budget ->2 first expense |
| db_version | app.py:49 | queries.bump_db_version 35 | Mirrors User.data_revision via get_data_revision 893 |
| settings | app.py:51 | queries.save_settings 184 | Dict copy; keys from _SETTINGS_DEFAULTS 1838 |
| rates | app.py:70 | refresh_rates_if_due | Derived via get_rates |
| dc | app.py:118 | sidebar selectbox 111 | Session-local; persisted on change 114 |
| weekly_summary_sent / pair_code | notifications/sync | — | Cleared in logout 177 |

queries.* cache keys (user_id, db_version) ttl 300 (holdings 120, get_lan_urls 60, _fetch_cached 1800). logout 179 does st.cache_data.clear() + clear_categorizers() (cache_resource ML model must not leak across users).

## 7. Execution Flows

### Flow A — Fresh boot to dashboard
1. st.set_page_config 32 -> inject_mobile_css 38 -> init_db 39 -> backup_db 40 (check .last_backup today).
2. require_auth 43 renders render_login_page (auth.py:191) tabs; login submit -> login_user throttled (5/60s, shared local bucket) -> on ok sets 6 keys + st.rerun().
3. Hydration 47-54: get_settings fresh -> refresh_rates_if_due checks rates_are_stale (>=3d or never); miss -> _fetch_cached (30m, includes None) merges currency_rates.
4. maybe_auto_backup 57 try/except (broken import warns, does not disable feature permanently).
5. Onboarding gate 64: if False -> render_onboarding step 0 hero -> step 1 currency/budget -> step 2 first expense/skip -> set_onboarding_complete persists -> rerun falls through.
6. Alias 68-70, snapshots 75-79, awards 80-102, sidebar 104, alerts 184-189, pg.run() 226 -> default dashboard.py.

### Flow B — Sidebar currency and rate race
1. Authenticated rerun passes gates; settings/rates hydrated.
2. Sidebar 108-118: DC = st.selectbox from SUPPORTED_CURRENCIES; if DC != dc_default: q.save_settings({default_currency: DC}) -> session refresh (queries 184). No rerun; value used immediately; st.session_state.dc = DC 118.
3. Rate form 120-139: form rate_form with rsd_val (min 0.0001); submit validates >0 and ==self (NaN guard 128); race-safe re-read _db_get_settings(user_id) 132 before merge, so concurrent currency_rates edits from Settings page are not clobbered; q.save_settings({currency_rates: fresh_rates}) -> st.rerun(); else st.error.

### Flow C — Phone access panel (experimental) + alerts
1. Sidebar 151: port = get_server_port() -> urls, hostname = get_lan_urls(port) (cached 60s). Scheme from TLS_ENABLED 711.
2. If urls: st.code(urls[0]), qr_png(urls[0]) 156 (try 163: warning + caption fallback, never crashes shell), st.image + st.download_button (key dl_qr), hostname caption https://hostname:port if present, experimental notice 172. Else caption run_server.bat hint 175.
3. Alerts 182-189: check_and_send_bill_reminders (recurring+expenses), check_and_send_budget_alerts (expenses+budgets+rates+DC), check_loan_reminders, check_and_send_weekly_summary; maybe_refresh_in_background for holdings.

## 8. Dependencies
- Streamlit >=1.57: st.set_page_config, st.navigation/st.Page, st.cache_data, st.session_state, st.get_option('server.port').
- SQLAlchemy + SQLite/SQLCipher: get_engine WAL/FK/busy_timeout pragmas; BASE_DIR from state_dir().
- qrcode + Pillow for qr_png; absence handled in sidebar try.
- Deploy: .streamlit/config.toml theme #0F3460 on #FFFFFF, secondaryBackgroundColor #F0F2F6, server.address 0.0.0.0 (LAN), maxUploadSize 10, gatherUsageStats false; Dockerfile 3.12-slim + tesseract, non-root appuser, health /_stcore/health; compose.yaml clamps to 127.0.0.1:8501 behind Caddy, ALLOW_REGISTRATION default false.
- Internal: every app_pages/*.py depends on shell hydration; none call init_db/require_auth themselves.

## 9. Cross-Subsystem Interfaces
| Consumer | Shell provides | Link |
|----------|---------------|------|
| Auth / Onboarding | Boot gates, session seeding, onboarding_complete default | -> auth-and-onboarding.md |
| Queries / DB | settings/rates/db_version hydration, bump_data_revision | db.py:893/902 |
| Currency engine | get_rates, SUPPORTED_CURRENCIES, rate-form race pattern | utils.py:180-288 |
| Notifications | settings, DC, rates, DataFrames; dedup via sent_markers | notifications.py |
| Gamification | Snapshots+settings to render_gamification_sidebar 143 | Milestones 84-102 |
| Deploy / Sync | get_lan_urls/qr_png/TLS_ENABLED + state_dir/resource_dir | app_paths.py |
| Styling | inject_mobile_css stylesheet | Responsive 768px |

Navigation mapping (app.py:192-225):
- Overview — dashboard.py (default True) dashboard
- Track — log_expense.py receipt_long, log_income.py payments, savings.py savings, bank_import_view.py account_balance_wallet
- Plan — budgets.py, recurring.py event_repeat, loans.py account_balance, big_purchases.py shopping_bag, travel.py flight, portfolio.py trending_up
- Understand — forecast.py query_stats, insights_view.py lightbulb, ask.py forum
- Play — rewards.py workspace_premium
- Household & Data — household.py groups, audit_log.py history, settings.py settings

Dict-key order is render order. Add pages by inserting st.Page entries; no separate router registry.

## 10. Architectural Invariants
1. st.set_page_config is first Streamlit call (32); any earlier call raises.
2. Gates are terminal: if not require_auth(): st.stop() 43 and if not onboarding_complete: render_onboarding(); st.stop() 64. No fall-through.
3. Migrations once per process: _MIGRATED flag in init_db 659; force_migrate=True only for tests re-seeding legacy data.
4. Backup once per day unless force=True; marker BACKUP_DIR/.last_backup; atomic os.replace(tmp,dest); WAL-safe src.backup(dst).
5. Settings never cached; get_settings 176 comment 177 always read fresh.
6. Milestone snapshots computed once per rerun 75-79 and reused for award + sidebar; doubling ML work is a regression.
7. Rate race guard: re-read _db_get_settings 132 before merging currency_rates; never merge onto stale rates dict.
8. QR failure is non-fatal 163-167; warning + caption, never crashes shell.
9. Server binds 0.0.0.0 in config.toml:12 for LAN; compose.yaml re-clamps to loopback + Caddy for public hosting.
10. Sidebar dc and settings refresh is synchronous via queries.save_settings 184; logout must clear st.cache_data and ML cache_resource.

## 11. Change Impact
| Change | Surfaces | Risk |
|--------|----------|------|
| Add page | app.py:192-225 nav dict + app_pages/<name>.py | Low |
| Rename nav group | dict keys 192 | Medium — hash URL/bookmark breaks |
| Reorder boot steps | app.py:32-70 | High — gate ordering correctness-critical |
| Edit CSS | utils.py:602 | Medium — every page; test 768px breakpoint |
| Change backup retention | utils:BACKUP_RETENTION_DAYS / db.py:2249 | Low |
| Expose new env var | compose.yaml + Dockerfile + app.py | Low |
| Alter SUPPORTED_CURRENCIES | sidebar 108 + DEFAULT_RATES + get_rates | High — FX pipeline |
| Change APP_PORT | utils.py:207 + config.toml + Dockerfile + compose.yaml | Medium — must stay consistent |

## 12. Agent Modification Rules
- Never move st.set_page_config after any Streamlit call.
- Do not remove st.stop() after auth/onboarding gates.
- Keep _MIGRATED guard; only bypass with explicit test force_migrate.
- Preserve rate-form re-read (_db_get_settings 132); removing it reintroduces lost-update on currency_rates.
- Wrap new sidebar network/codec calls in try/except like QR 163.
- New pages: st.Page('app_pages/<name>.py', title=..., icon=':material/...:', default?) inside correct group; verify material icon exists.
- Theme goes in .streamlit/config.toml [theme], not inline CSS overrides.
- Docker/compose port must stay consistent with utils.APP_PORT 207 and get_server_port fallback chain.

## 13. Common Tasks Router
| Task | Entry point | Notes |
|------|-------------|-------|
| Add page | app.py:192-225 | Insert st.Page in correct group; create app_pages/<page>.py |
| Change currencies | utils.py:180 + DEFAULT_RATES 188 | Also update onboarding.py:55 select |
| Tweak mobile layout | utils.py:602 | Edit @media(max-width:768px); do not globally stack all columns |
| Enable phone TLS | utils.py:210 env EXPENSE_TRACKER_TLS | Run make_cert.py first (comment 209) |
| Adjust backup pruning | utils.py:206 / db.py:2249 | 30d default; test force=True path |
| Expose LAN | .streamlit/config.toml:12 | Already 0.0.0.0; firewall prompt still required (caption 175) |
| Add sidebar widget | app.py:104-179 | Inside with st.sidebar:; persist via q.save_settings |
