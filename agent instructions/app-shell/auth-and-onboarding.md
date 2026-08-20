# Auth and Onboarding — Login, Throttling, Registration Toggle & 2-Step Wizard

## 1. Purpose
Per-user authentication and first-run setup that hydrates Streamlit session state before the shell renders navigation. Owns bcrypt password lifecycle, shared-bucket login throttling, registration toggle (env vs secrets precedence), and the 2-step onboarding wizard gated by onboarding_complete (default False — never skip accidentally). Together with shell-and-navigation.md it explains how unauthenticated users never reach app_pages/*.py.

## 2. Source Scope
| File | Lines | Symbols |
|------|-------|----------|
| auth.py | 263 | hash_password 25, verify_password 29, _attempts/MAX_ATTEMPTS 40-43, _client_key 45, _throttled 57, _registration_enabled 68, _valid_email 87, _valid_password 91, register_user 103, login_user 134, change_password 154, logout 173, render_login_page 191, require_auth 257 |
| onboarding.py | 134 | render_onboarding 17, step 0 hero 22-42, step 1 currency&budget form 44-82, step 2 first expense/skip 84-134 |
| db.py | 2586 | User model 323, get_user_by_username (line ~), username_exists 2101, email_exists 2107, create_user, set_onboarding_complete 2112, update_user_password 2121, get_settings 1858, save_settings 1902, _SETTINGS_DEFAULTS 1838 |
| queries.py | 186 | save_settings 181 (refresh settings + bump_db_version), get_settings 176, bump_db_version 35, db_version 21 |
| utils.py | 731 | SUPPORTED_CURRENCIES 180, MAX_AMOUNT 211, get_rates 243, get_currency_symbol 226, CAT_LIST 40 |
| app.py | 226 | auth gate 43-44, onboarding gate 64-66, hydration 47-61, rate form race 132 |

No onboarding classes; wizard is three branch render_onboarding step 0/1/2 dispatched via session_state.onboarding_step.

## 3. Internal Architecture
```
       login flow                          register flow
auth.py:191 render_login_page                _registration_enabled 68
  tabulated header 192                     -> env ALLOW_REGISTRATION 74
  columns [1,2,1] centering 199              -> st.secrets fallback 77
  tabs: Login / Create Account 201           -> default true (open LAN) 81
      |                     |                -> checks "1/true/yes/on" 82
      v                     v
  login_form 208       register_form 233
  username lower 135    username lower 104
  password strip       email lower 105
       |               _valid_email 87 / _valid_password 91
       v                    | (8 chars, <=72 bytes, digit)
  _throttled 57             v
  key=local|username   username_exists/email_exists 119-121
  MAX_ATTEMPTS 5        hash_password 25 (bcrypt.gensalt)
  WINDOW 60s 42         create_user 126 (DB unique constraint is source of truth)
       |                     |  race -> "just taken" 130
       v                     v
  get_user_by_username 142  -> success toast 249 ("Please log in")
  verify_password 29/146
  pop throttle on success 150
       |
       v
  st.session_state 219-225: authenticated, user_id, username, display_name, household_id, onboarding_complete, onboarding_step=0
  st.rerun() 226 -> app.py gate falls through

onboarding.py:17 render_onboarding
  step = session_state.onboarding_step 20 (default 0)
  step 0 22: welcome hero + 3 border cards -> "Let's get started" -> step=1 rerun 41
  step 1 44: currency&budget form onboard_step1
          get_settings 48 + get_rates 49 + dc_default index 51
          selectbox DC 55; if DC != EUR show rate number_input 59-63
          budget number_input EUR 64
          Save -> validate rate >0 NaN-guard 68; q.save_settings({default_currency, monthly_budget, currency_rates}) 80 -> step=2 rerun 81
  step 2 84: first expense form onboard_exp or skip
          get_settings 88 + get_rates 89
          fields 93-100: date, CAT_LIST, amount MAX_AMOUNT, description
          Save 104: validate desc/amount; add_expense user_id {... amount_eur==amount ...} 115
                   q.bump_db_version() 124; set_onboarding_complete(user_id) 125 (DB flag True); session onboarding_complete=True 126 -> rerun
          Skip 131: set_onboarding_complete 132; session True 133 -> rerun (no expense)

app.py gate 64: if not st.session_state.get("onboarding_complete", False): render_onboarding(); st.stop()
                 default False so fresh install never leaks into dashboard
```

## 4. Important Symbols
| Symbol | File:Line | Role |
|--------|-----------|------|
| hash_password | auth.py:25 | bcrypt.hashpw + gensalt, utf-8 roundtrip |
| verify_password | auth.py:29 | bcrypt.checkpw; warns on exception, returns False; never echos raw error to UI |
| _attempts / MAX_ATTEMPTS / WINDOW_SECONDS | auth.py:40-42 | defaultdict(deque) / 5 / 60 — in-memory shared bucket |
| _client_key | auth.py:45 | returns "local" — X-Forwarded-For explicitly NOT trusted (doc 48-52) |
| _throttled | auth.py:57 | sliding window: popleft older than 60s; len>=5 -> True else append now and False |
| _registration_enabled | auth.py:68 | env var first 74, then st.secrets 77, default True 81; checks 1/true/yes/on 82 |
| _valid_email | auth.py:87 | regex ^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$ |
| _valid_password | auth.py:91 | 8 chars min, 72 bytes max (bcrypt limit 94), requires digit 96 |
| register_user | auth.py:103 | lowercases username/email, strip password, validates, checks existence, bcrypt hash, create_user; unique constraint race handled 128 |
| login_user | auth.py:134 | lower+strip, throttled check 139, get_user_by_username 142, verify_password 146, pop throttle on success 150 |
| change_password | auth.py:154 | get_session lookup, verify old, _valid_password, update_user_password |
| logout | auth.py:173 | pops 10 keys (authenticated,user_id,username,display_name,household_id,onboarding_complete,onboarding_step,settings,db_version,dc,rates,pair_code,weekly_summary_sent), cache_data.clear 179, clear_categorizers 183 |
| render_login_page | auth.py:191 | HTML header 192, columns, tabs conditional on _registration_enabled 201-204, login_form 208-228, register_form 232-251 |
| require_auth | auth.py:257 | init_db 259; if authenticated 260 return True else render_login_page 262 return False |
| set_onboarding_complete | db.py:2112 | flips User.onboarding_complete True + audit UPDATE |
| get_settings | db.py:1858 | returns dict(_SETTINGS_DEFAULTS) if no UserSettings row |
| save_settings | queries.py:181 | _db_save_settings + session refresh + bump_db_version |
| SUPPORTED_CURRENCIES | utils.py:180 | 12 codes for step 1 select |
| get_rates | utils.py:243 | DEFAULT_RATES merged with stored currency_rates |
| get_currency_symbol | utils.py:226 | SUPPORTED_CURRENCIES.get fallback |
| CAT_LIST | utils.py:40 | top-level categories for step 2 expense |

## 5. Inputs / Outputs
**Inputs:** Form fields (username/email/password/display_name, DC/budget/rate, expense date/category/amount/description), env/secrets ALLOW_REGISTRATION, EXPENSE_TRACKER_TLS, st.session_state, DB User/UserSettings rows, in-memory _attempts deque.

**Outputs:** DB rows (User, Expense, UserSettings), session_state mutations (10 keys), reruns, toast/success/balloons, audit_log entries, error messages (generic "Incorrect username or password" for both missing user and bad password to avoid enumeration).

**Contract with shell:** require_auth seeds session before any app.py hydration; render_onboarding mutates onboarding_step / onboarding_complete and persists via q.save_settings / set_onboarding_complete; app.py gate st.stop() ensures nav never renders mid-wizard.

## 6. State & Ownership
| Key | Written by | Read by | Lifecycle |
|-----|------------|---------|-----------|
| authenticated | render_login_page 219 | require_auth 260, app.py:43 | True until logout |
| user_id | render_login_page 220 | every DB query, queries.db_version 29 | FK scope |
| username | render_login_page 221 | — | lowercased |
| display_name | render_login_page 222 | sidebar 106, onboarding step0 19 | |
| household_id | render_login_page 223 | household checks | may be None |
| onboarding_complete | render_login_page 224 (from DB), onboarding 126/133 | app.py gate 64 | False until wizard finishes or skipped |
| onboarding_step | render_login_page 225 (0), onboarding 41/81 | render_onboarding 20 | reset on login; 0/1/2 |
| settings | queries.save_settings 184 | entire app | dict copy of _SETTINGS_DEFAULTS + DB |
| db_version | app.py:49 | queries.db_version 31 | mirrors data_revision |
| dc / rates | app.py 118/70 | display formatting | |
| pair_code / weekly_summary_sent | sync/notifications | — | cleared in logout |
| _attempts deque | _throttled 57 | login_user 139 | process-local, not persisted; shared "local" bucket |

User table onboarding_complete Boolean default False (db.py:323). UserSettings defaults: default_currency EUR, monthly_budget 0 (db.py:1839). Onboarding step 1 writes monthly_budget as EUR float (onboarding.py:74), even when DC != EUR.

## 7. Execution Flows

### Flow A — First registration then login
1. require_auth 257 sees no authenticated -> render_login_page shows 2 tabs (registration enabled).
2. User fills register_form 233: display/username/email/pass/confirm; submit 240 checks pass==confirm 244.
3. register_user 103: lower+strip, 3-char username, alnum+underscore, _valid_email, _valid_password, username_exists/email_exists, hash_password, create_user. Concurrent race caught by DB unique constraint 128 returns "just taken". Success 249 success toast "Please log in." — account not auto-logged in.
4. User switches to Login tab, submits login_form 208; login_user 134: lower+strip, _throttled 139 key local|username (all LAN clients share bucket), get_user_by_username, verify_password. On ok: set 6 keys (authenticated, user_id, username, display_name, household_id, onboarding_complete, onboarding_step 0) + rerun.
5. App hydrates 47-61 then onboarding gate sees onboarding_complete False -> render_onboarding step 0.

### Flow B — Returning login with throttling
1. Throttle warm: _attempts["local|alice"] has 4 recent entries. 5th bad login -> _throttled 57 len 4 -> append -> len 5 but not yet >=5 at check, so this attempt proceeds and fails normally.
2. 6th attempt within 60s -> _throttled sees len 5 -> returns True immediately -> "Too many attempts. Please wait a minute..." 140; deque left untouched (no append). Window slides: entries older than 60s popped 60.
3. Successful login pops throttle entry 150 — lockout resets on correct password. Note: X-Forwarded-For not read — comment 48-52 explains spoofing would bypass throttling; shared bucket is honest tradeoff for home-LAN app.
4. If onboarding_complete already True (returning user), gate 64 falls through to sidebar/nav.

### Flow C — Wizard salary_day/currency flow and skip guard
1. Step 0 hero 22-42: welcome display_name, 3 border cards (Track/Set budgets/Get insights), button -> onboarding_step=1 rerun 41.
2. Step 1 44-82: reads fresh get_settings 48 + get_rates 49; dc_default lookup 50-52 with index fallback 0; form onboard_step1 id 54 selectbox DC from SUPPORTED_CURRENCIES. If DC != EUR, number_input for rate 59 (value get_rates[DC], clamped 0.0001) with help "Used to convert...". Budget EUR 64 step 100. Submit validates NaN guard 68 (rate>0 and ==self) else error 69. Builds updates dict 72-79: default_currency, monthly_budget, plus currency_rates merged from rates dict when DC!=EUR. Calls q.save_settings 80 which bumps db_version and refreshes st.session_state.settings. Then onboarding_step=2 rerun 81. Re-entering step 1 re-reads persisted settings so DC/budget survive back navigation.
3. Step 2 84-134: get_settings 88 + get_rates 89 fresh; form onboard_exp 91 with date CAT_LIST amount MAX_AMOUNT. Two submit buttons in columns: Save & Finish 104 vs Skip 106. Save validates desc strip 109 and amount>0 111; try add_expense 115 with amount_eur==amount currency EUR (first expense always EUR base) then bump_db_version 124 + set_onboarding_complete 125 (DB True) + session True 126 + success toast/balloons 127 + rerun. Skip path 131-133 also calls set_onboarding_complete + session True but without add_expense. Guard: gate 64 defaults False so a direct URL or refresh cannot skip wizard by missing key — explicit complete flag required. Note: salary_day flow mentioned in task description refers to future salary_day setting (UserSettings.salary_day 1845 default 1) managed outside onboarding; onboarding does not collect salary_day.

## 8. Dependencies
- bcrypt for hash/verify; SQLAlchemy User model with unique username/email constraints as correctness backstop for throttling race.
- Streamlit: st.session_state, st.form, st.selectbox, st.number_input, st.columns, st.container, st.secrets, st.cache_data, st.rerun, st.stop.
- db.py get_user_by_username for login; create_user inside register_user; username_exists/email_exists pre-checks (non-authoritative).
- utils.get_rates used in both step 1 and 2 to seed rate input from stored rates.
- queries.save_settings for step 1 persistence (bump required for DB cache invalidation).

## 9. Cross-Subsystem Interfaces
| Consumer | Auth/Onboarding provides | Link |
|----------|-------------------------|------|
| Shell | require_auth gate 43, onboarding gate 64, session keys (user_id/display_name/settings/onboarding_*) | -> shell-and-navigation.md |
| Sidebar | display_name header 106, DC persistence via q.save_settings 114, rate race re-read 132 | shell 104-139 |
| Rate refresh | settings hydration before refresh_rates_if_due 54; onboarding step1 rate seeding | rates.py:93 |
| Expense persistence | add_expense 115 + bump_db_version 124 (first expense) | db.py:1021, queries.py:35 |
| Settings | _SETTINGS_DEFAULTS 1838 keys monthly_budget/default_currency/currency_rates | db.py:1858/1902, onboarding 72-80 |
| Navigation | onboarding_complete=False -> st.stop() before pg.run() 192 | shell 192-226 |

## 10. Architectural Invariants
1. onboarding_complete defaults False (User default, gate get with False 64) — wizard cannot be skipped by absent key.
2. Password policy 91 enforced both in register_user and change_password: 8 chars, 72 bytes (bcrypt limit 94), digit required.
3. Username canonicalized lower+strip 104/135; email lower+strip 105 — login normalization must match registration.
4. Generic login error "Incorrect username or password" for both missing user 145 and bad password 147 — no enumeration leak.
5. Throttling shared bucket "local": X-Forwarded-For not trusted 48-52; MAX_ATTEMPTS 5 WINDOW 60 per username suffix so different usernames do not collide except intentionally shared LAN lockout.
6. Registration toggle precedence: os.environ ALLOW_REGISTRATION 74 wins over st.secrets 77; missing both -> True 81; values checked case-insensitive "1/true/yes/on" 82. compose.yaml sets false for public hosting.
7. Wizard step progression only forward via session_state.onboarding_step 41,81; no back button — re-rendering step reads persisted settings so state is durable.
8. First expense amount_eur==amount EUR 118 (no rate conversion) — display conversion via get_rates later.
9. set_onboarding_complete writes DB 2112 and audit log; queries.save_settings in step 1 also audits; both mutate shared revision.
10. logout 173 pops exactly 10 keys plus pair_code/weekly_summary_sent and clears both st.cache_data and ML cache_resource (clear_categorizers) — previous user's model must not leak.

## 11. Change Impact
| Change | Surfaces | Risk |
|--------|----------|------|
| Password policy tweak | auth.py:91 + error strings 93-97 | Medium — must stay in sync with bcrypt 72-byte limit |
| Throttle window/count | auth.py:41-42, _throttled 57 | High — shared LAN lockout; test concurrent users |
| Registration toggle semantics | auth.py:68, compose.yaml env | Medium — public hosting accidentally open |
| Onboarding step fields | onboarding.py 54-82, 91-134 | Medium — _SETTINGS_DEFAULTS keys must exist |
| Add onboarding step 3 | onboarding.py branches 22/44/84 | Low — extend step integers, keep gate 64 default |
| Move wizard before hydration | app.py 47-66 | High — onboarding step1 reads get_settings 48 |

## 12. Agent Modification Rules
- Never change default of onboarding_complete gate to True or remove fallback False — would silently skip setup for new accounts.
- Keep throttling key as local|username; do not add X-Forwarded-For, forwarded headers, or cookies — spoofing bypass is real for single-machine app.
- Keep _registration_enabled precedence env then secrets then True; compose.yaml override to false is security boundary.
- Keep password 72-byte check (encoded length 94) — bcrypt truncates above 72 bytes silently.
- Preserve username lower+strip in both register and login paths — mismatch breaks login after mixed-case signup.
- Keep generic login error for both branches — do not reveal whether username exists.
- Wizard must set both DB flag (set_onboarding_complete) and session flag; setting only one leaves inconsistent state across reruns.
- First expense Save path must bump_db_version after add_expense; otherwise dashboard cache stale until next write.
- Rate validation 68 and sidebar 128 must keep NaN guard (==self) alongside >0 — "NaN > 0" is False so bare >0 check alone would still error but differently; keep both for explicitness.

## 13. Common Tasks Router
| Task | Entry point | Notes |
|------|-------------|-------|
| Change password rules | auth.py:91 _valid_password | Update message strings 93-97 together |
| Disable public registration | auth.py:68 + compose.yaml | Set ALLOW_REGISTRATION=false env; verify st.secrets not overriding |
| Customize welcome hero | onboarding.py 22-42 | 3 border cards + container horizontal_alignment center |
| Add salary_day to wizard | onboarding.py step1 44-82 | Add number_input 1-31 clamped with calendar.monthrange like utils.compute_salary_cycle; persist via q.save_settings |
| Add currency to wizard | utils.py:180 SUPPORTED_CURRENCIES | Also sidebar app.py:108 |
| Change throttle lockout | auth.py:41-42 | Keep shared bucket comment in sync |
| Modify first-expense defaults | onboarding.py 91-100 | CAT_LIST 40, MAX_AMOUNT 211, date.today() 94 |
