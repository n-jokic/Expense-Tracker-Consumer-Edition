# Final QA Bug Map — Expense Tracker Consumer Edition
## Knowledge-Routed Swarm Campaign (PASS 1-3)

> Generated: 2026-08-20 \n
> Source: `agent instructions/` (19 files, 8 domains D1-D8 + Architecture) routed swarm \n
> Workdir: `C:\Users\Nikita\Desktop\gitProjects\Expense-Tracker-Consumer-Edition` \n
> Rule: **DO NOT MODIFY APPLICATION CODE** — investigation / reproduction / validation only \n
> Artifacts: `qa/registry-findings.json`, `qa/registry-patterns.json`, this report \n

---

## 1. Executive Summary

| Metric | Count |
|--------|-------|
| Knowledge domains examined | **8** (D1 Shell/Auth, D2 Persistence/Crypto/Caching, D3 Currency/Taxonomy, D4 Ledger/Recurring/Audit, D5 Planning & Wealth, D6 Ingestion, D7 Intelligence, D8 Connectivity) + Architecture cross-cutting (G1-G13, 7 flows) |
| Stage-1 candidate findings | **36** (4× D1, 5× D2, 4× D3, 5× D4, 4× D5, 5× D6, 5× D7, 5× D8 — 36 unique IDs T1-T8-*) |
| Independent adversarial validators | **36** (one per candidate, isolated context) |
| Validated verdicts | **19 CONFIRMED** + **1 HIGH-CONFIDENCE** + **6 SUSPECTED** + **10 NOT-A-BUG** |
| Stage-2 pattern hunters | **6** (P1-P6, each narrow one mechanism, missions A+B) |
| Hunter-confirmed analogous manifestations | **18** additional CONFIRMED/HIGH-CONFIDENCE (systemic clones) |
| Stage-3 integration boundary teams | **3** (A Boot/Budgets, B CSV/Sync/Prices, C Loans/MCP/Household) |
| Integration boundary findings | **9** (3 per team, 3 duplicate primary already counted, 6 distinct cross-domain) |
| Total CONFIRMED/HIGH-CONFIDENCE manifestations (primary + hunters + integration-distinct) | **~38** (20 primary validated + 18 hunter analogs + 3 integration-distinct not already counted as primary*) |
| Cross-cutting bug patterns (dynamic taxonomy) | **6** systemic/repeated patterns + 2 coupling-leak patterns |
| Severity (primary validated 20) | CRITICAL 1 · HIGH 8 · MEDIUM 10 · LOW 1 |
| Severity with hunter analogs (38) | CRITICAL 1 · HIGH ~15 · MEDIUM ~18 · LOW ~4 |

*Three integration findings deduplicate primaries: INTEGRATION-A-003 == T5-PLANNING-001 (budgets per-sub vs eff), INTEGRATION-B-002(a) == T3-CURRENCY-004 (dual amount), INTEGRATION-B-002(b) overlaps T2-PERSISTENCE-002/003. New integration-distinct: INTEGRATION-A-001 (budget marker granularity), INTEGRATION-A-002 (recurring orphan lifecycle), INTEGRATION-B-001 (CSV column-wide vs PDF per-token locale), INTEGRATION-C-001 (sync drops loan surcharge), INTEGRATION-C-002 (MCP budget double-count), INTEGRATION-C-003 (household stale id + throttle mismatch) — counted in total.

**Top risks:** CRITICAL soft-delete NULL hides history (T4-003); HIGH sync whitelist bypasses caps and dual-amount poisoning is systemic across 4 tables (P1); HIGH budget progress masks overspend + phantom month interest + CSV corruption + MCP stale identity; HIGH migration lock race + editor sentinel. All are reachable under realistic conditions (fresh install upgrade, paired phone, household, EU bank CSV, budgets with 2 subs).

---

## 2. Confirmed and High-Confidence Bugs (prioritized by severity)

Severity is `impact × likelihood`; Confidence is independent (`CONFIRMED` = reproduced or logically unavoidable path, `HIGH-CONFIDENCE` = strong path evidence). Each entry has full causal chain.

### CRITICAL

#### T4-LEDGER-003 — Soft-delete filter excludes NULL legacy rows (history vanishes)
- **Domain:** D4 Ledger — **Pattern:** P3 sentinel NULL analog
- **Location:** `db.py:368` (`is_deleted Column(Boolean, default=False) nullable=True`), filters `db.py:1015` `q.filter(Expense.is_deleted == False)`, same at `1111` Income, `1184` Savings, `1403` SavingsAccount, `1705` LoanPayments, `2058` Household — 8 sites
- **Preconditions:** DB created before `is_deleted/deleted_at` or row inserted via raw SQL/sync with missing field leaving `is_deleted IS NULL`; user upgrades and opens history.
- **Causal chain:** Row `is_deleted=NULL` → `SELECT ... WHERE is_deleted = 0` → SQLite tri-valued `NULL=0 → NULL` excluded → `q.expenses(user_id)` returns truncated/no rows → dashboard/forecast/insights empty while file still contains rows.
- **Expected:** All non-deleted visible via `COALESCE(is_deleted,0)=0` or `IS NOT TRUE` / backfill `UPDATE SET is_deleted=COALESCE(is_deleted,0)`; sentinel contract `domain/transactions-and-recurring.md`.
- **Actual:** Legacy rows disappear; `_add_missing_columns` `db.py:677-766` never backfills `is_deleted`; `sync_core` allows missing bool leaving NULL.
- **Evidence:** DDL inspection `nullable=True server_default=None`, SQLite proof, `data-model.md:41` soft-delete contract, no backfill, `tests/test_db` only fresh fixtures.
- **Severity:** CRITICAL — silent data disappearance on upgrade, breaks forecasting baseline.
- **Confidence:** CONFIRMED — reproduced via DDL + SQLite tri-valued test.

---

### HIGH (8 primary + systemic analogs)

#### T2-PERSISTENCE-001 — Migration lock diverges from DB_PATH dir
- **Domain:** D2 Persistence — **Pattern:** P4 Lock Scope Divergence (systemic)
- **Location:** `db.py:36 BASE_DIR=str(state_dir())`, `39 DB_PATH=os.environ.get("DB_PATH") or BASE_DIR/expense_tracker.db`, `56 _ENCRYPTION_LOCK=BASE_DIR/.db-encrypting`, `139 O_CREAT|O_EXCL`, `145 DB_PATH.migrating/.enc-new`
- **Chain:** Override `DB_PATH=/custom/a.db` with default `BASE_DIR=data/` → lock in `data/` but temps in `/custom/` → two processes sharing same `DB_PATH` but different `BASE_DIR` both pass `_wait_for_migration_lock` → race on same temp files → corruption or spurious 600s denial.
- **Evidence:** `app_paths.py:12 state_dir`, `tests/test_crypto.py:66-68` must patch both paths; `tests/conftest.py:17` forces DB_PATH without BASE_DIR; `invariants.md G13`.
- **Severity:** HIGH · **Confidence:** CONFIRMED
- **Analog (P4):** `db.py:40 BACKUP_DIR=BASE_DIR/backups`, `2214 .last_backup`, `crypto.py:55 .secret_key` same split — secret/backup not traveling with custom DB, share one dir across different DBs.

#### T3-CURRENCY-001 — Sync whitelist finite-only bypasses MAX_AMOUNT and positivity
- **Domain:** D3 Currency — **Pattern:** P1 Unbounded Whitelist (systemic)
- **Location:** `sync_core.py:45-70 FIELD_SCHEMAS` (`amount/amount_eur/budgeted/actual/deposited/target_eur/amount ... : float`), `155-160` validates `finite` only, no `>0` / `<=1_000_000`
- **Chain:** Phone `POST /api/v2/sync {amount:5_000_000, amount_eur:5_000_000}` → finite passes → `create_record/_apply_update setattr` → poisoned EUR aggregates.
- **Evidence:** `utils.py:211 MAX_AMOUNT=1_000_000`, `bank_import.py:313` cap, `mcp_server.py:351` correct, `api.py:136` delegates unchecked, grep `MAX_AMOUNT` 0 hits in `sync_core`.
- **Severity:** HIGH · **Confidence:** CONFIRMED
- **Analogs (P1, 3 more CONFIRMED):** Income `hours/rate/budgeted/actual/budgeted_eur/actual_eur`, Savings `target_eur/deposited/deposited_eur/balance_eur`, SavingsAccounts `amount/amount_eur` — same finite-only helper reused for all 4 tables (12+ numeric fields).

#### T3-CURRENCY-004 — Dual amount trusts client amount_eur (no to_eur recompute)
- **Domain:** D3 Currency — **Pattern:** P1
- **Location:** `sync_core.py:45-50 FIELD_SCHEMAS amount_eur:float`, `230-235 create_record`, `265-268 _apply_update`, `db.py:1029 amount_eur=float(row.get("amount_eur",0))`
- **Chain:** `amount=10 RSD, amount_eur=99999` → both finites → persisted verbatim → aggregates inflated; local UI recomputes via `to_eur` but sync trusts poison.
- **Evidence:** grep `to_eur` 0 in `sync_core`; `mcp_server.py:370` recomputes, `log_expense.py:106` recomputes; aggregates sum `amount_eur`.
- **Severity:** HIGH · **Confidence:** CONFIRMED

#### T4-LEDGER-001 — Expense editor cannot write sentinel '' and leaves invalid pair
- **Domain:** D4 Ledger — **Pattern:** P3 Sentinel/Validation Asymmetry (systemic)
- **Location:** `app_pages/log_expense.py:295 SelectboxColumn(subcategory, options=ALL_SUBCATS)` (~45 global, no ""), `324-326` diff no `—→""`, `db.py:1050 update_expense` no `CATEGORIES[category]` check vs `db.py:1571 update_recurring` clears invalid to "".
- **Chain:** Change category Groceries without touching subcategory Rent/Mortgage → `update_expense({category:Groceries})` persists illegal pair.
- **Evidence:** `utils.py:41 ALL_SUBCATS`, `tests/test_recurring:161-166` proves intended clear for recurring, missing for expense; `mcp_server.py:357` correct per-category check.
- **Severity:** HIGH · **Confidence:** CONFIRMED
- **Analogs (P3):** Bank import review `bank_import.py:586` same global list; `sync_core.py:182-184` checks `ALL_SUBCATS` not per-category allowing Groceries+Fuel.

#### T5-PLANNING-001 — Per-subcategory progress uses summed eff[cat] masking overspend
- **Domain:** D5 Planning — **Pattern:** scope-collapse misapplied
- **Location:** `app_pages/budgets.py:129 eff=effective_category_budgets(cur_rows)`, `135 b=eff.get(category, budgeted_eur)` per subcategory row, `141 spent` per-sub filtered, `146 min(spent/b,1.0)`
- **Chain:** Subs Groceries 30 + Coffee 20 → eff[Dining]=50; Groceries bar 25/50=50% vs true 25/30=83%, Coffee 40/20 over-budget shows 80%.
- **Evidence:** `utils.py:312 eff[cat]=sum(subs)`, `tests/test_budget_scope 95` eff=50, `dashboard.py:352` correct aggregate usage, budgets page diverges.
- **Severity:** HIGH · **Confidence:** CONFIRMED

#### T5-PLANNING-002 — months_between ignores day → phantom month interest
- **Domain:** D5 Planning — **Pattern:** P5 Time Heuristic
- **Location:** `finance.py:232 return (end.year-start.year)*12+(end.month-start.month)`, `249 maturity_value`, `259 accrued_value`, callers `app_pages/savings.py:216,528`
- **Chain:** Jan31→Feb01 =1 month → compound 1010 while 1 day elapsed; Jan10→Feb09 same per `tests/test_finance 312`; opposite Jan10→Jan31 same month =0 for 21 days.
- **Evidence:** repro 1-day phantom, `domain/planning-and-wealth.md:203` whole-months intent vs implementation.
- **Severity:** HIGH · **Confidence:** CONFIRMED

#### T6-INGESTION-001 — CSV dialect fixed comma corrupts EU ';' tables
- **Domain:** D6 Ingestion — **Pattern:** P5 EU Bias
- **Location:** `bank_import.py:427 pd.read_csv(uploaded)` no sniff, `156 _to_numeric_locale` correct after tokenization but starved
- **Chain:** `; ` + `,` decimal file → pandas splits wrong → amounts 50.0/56.0 vs expected 12.5/1234.56 (repro semicolon file).
- **Evidence:** `import-pipeline.md:29` admits no semicolon sniff; pandas repro; `execution-flows.md:83` claims sniff but absent.
- **Severity:** HIGH · **Confidence:** CONFIRMED

#### T8-CONNECTIVITY-002 — MCP _USER_ID cached forever stale after delete
- **Domain:** D8 Connectivity — **Pattern:** P4 Stale Cache (repeated)
- **Location:** `mcp_server.py:52 _USER_ID=None`, `57-60 if _USER_ID is not None: return`, `74` sole assignment import-time `MCP_USERNAME`
- **Chain:** Serve one request → cached → delete user or change env → all writes go to wrong id via `:mcp` audit + bump.
- **Evidence:** grep `_USER_ID =` only tests reset, `db.py:2142 delete_user_account` no signal, `external-surfaces.md:42` cached no invalidation.
- **Severity:** HIGH · **Confidence:** CONFIRMED

#### Hunter analogs systemic (add to HIGH tally)
- P1 income/savings/accounts whitelist bypass (3 CONFIRMED) — see Taxonomy §P1.
- P4 settings/fun/travel JSON clobber via stale snapshot not yet listed below but HIGH-equivalent; detailed in Pattern section.

---

### MEDIUM (10 primary + hunters)

#### T1-SHELL-AUTH-002 — Onboarding currency_rates lost-update
- **Domain:** D1 — **Pattern:** P2 Stale Derived
- **Location:** `onboarding.py:48 get_settings 49 get_rates 77 dict(rates) 80 save_settings({"currency_rates":new_rates})` vs `app.py:132 fresh_rates=dict((_db_get_settings or {}).get("currency_rates") or {})` race fix not propagated
- **Severity:** MEDIUM · **Confidence:** HIGH-CONFIDENCE
- **Analogs (P2):** `app_pages/settings.py:73-99` (`st.session_state.rates` snapshot), `app_pages/rewards.py:74-87 fun_categories`, `app_pages/travel.py:39-67 travel_categories` — all whole-JSON overwrites without fresh re-read; Follows same snapshot clobber; `atomic_update_setting_json` exists but not used for these JSON cols (contrast `notifications.py:200` correct).

#### T2-PERSISTENCE-003 — db_version() N+1 per helper intra-rerun tear
- **Domain:** D2 — **Pattern:** P2+P4
- **Location:** `queries.py:21 db_version() -> _db_get_revision` fresh SELECT, `48-173` 14 wrappers each call independently
- **Chain:** Dashboard calls 5–15 helpers sequentially; concurrent WAL bump between calls → view torn (expenses at r, budgets at r+1).
- **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Analogs:** Same mechanism for `rates.py:107 fresh_settings`, `market_data.py:125 _db_get_settings`.

#### T4-LEDGER-002 — Dedup whitespace/case + Log-now exact-date duplicate monthly
- **Domain:** D4 — **Pattern:** P3 deduplication divergence
- **Location:** `log_expense.py:171 == desc` strict vs `bank_import.py:316 normalized lower+\s+`, `recurring.py:115 rec_template_id+date==paid_on` not month vs `notifications.py:261` month filter
- **Severity:** MEDIUM · **Confidence:** CONFIRMED

#### T4-LEDGER-004 — NaN leaks into String cols violating '' invariant
- **Domain:** D4 — **Pattern:** P3
- **Location:** `log_expense.py:276 _same 324 upd 330-335` only amount/description guarded, `db.py:1050 setattr` persists NaN into String
- **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Analogs (P3):** `sync_core.py:149 str(float('nan'))->"nan"` same leak; budget `uq_budget_scope` mismatch.

#### T4-LEDGER-005 — Drag board N+1 partial commit
- **Domain:** D4 — **Pattern:** P6 Atomicity (repeated)
- **Location:** `app_pages/recurring.py:199 _persist_grouped_order` loop per `update_recurring` own session, single bump after
- **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Analogs (P6 CONFIRMED):** `app_pages/big_purchases.py:267`, `log_expense.py:317-345 expense edit batch`, `log_expense.py:360-367 trash batch`, `market_data.py:130-145 per-holding update+snapshot`, `bank_import.py:618 bulk import` — 5 clones systemic.

#### T6-INGESTION-002 — PDF suffix codes not stripped headerless 0 rows
- **Domain:** D6 — **Pattern:** P5
- **Location:** `pdf_import.py:231 _is_pure_amount_cell strip _CURRENCY_SYMBOLS €$¥ only not EUR/RSD`, `261 _classify_columns header-only`, `431 branch`
- **Severity:** MEDIUM · **Confidence:** CONFIRMED

#### T7-INTELLIGENCE-002 — Insights pure claimed but render_insights writes DB
- **Domain:** D7 — **Pattern:** coupling leak
- **Location:** `insights.py:17 import queries as q 459 from db import add_recurring 473 add_recurring 481 bump` inside render loop
- **Severity:** MEDIUM · **Confidence:** CONFIRMED

#### T8-CONNECTIVITY-001 — Sync auth before init_db cold 500
- **Domain:** D8 — **Pattern:** P4 initialization ordering
- **Location:** `api.py:110 dev=_auth then 111 init_db`, `131 same` vs `93 pair does init first`; repro fresh DB 500 not 401
- **Severity:** MEDIUM · **Confidence:** CONFIRMED

#### T8-CONNECTIVITY-003 — Budget marker before SMTP ghosts month
- **Domain:** D8 — **Pattern:** optimistic dedup vs guaranteed delivery
- **Location:** `notifications.py:332-335 _persist_marker before 340 send_email_async 345 on_done`, failure leaves ghost via `_fresh_markers` suppressing retry vs bill/loan only on_done
- **Severity:** MEDIUM · **Confidence:** CONFIRMED
- **Cross-granularity duplicate (INTEGRATION-A-001, HIGH):** Same mechanism but granularity mismatch `cat:level` vs bare cat across session/persisted dedupe — fresh process duplicate exceeded.

#### T8-CONNECTIVITY-004 — Background price refresh bypasses 30m failure memo
- **Domain:** D8 — **Pattern:** P4 memo bypass
- **Location:** `market_data.py:80 _fetch_cached ttl1800 includes None, 124 cached branch, 165 maybe_refresh_in_background cached=False`
- **Severity:** MEDIUM · **Confidence:** CONFIRMED

---

### LOW (1 primary + hunters)

#### T7-INTELLIGENCE-004 — ETS sparse→None hides valid per-category forecast
- **Domain:** D7 — **Pattern:** feature loss
- **Location:** `forecasting.py:57 isna->None, 81-88 early return if total is None before per-category loop`; repro Freelance 6 months dense hidden when global July gap
- **Severity:** LOW · **Confidence:** CONFIRMED
- **Also:** INTEGRATION-A-002 recurring orphan lifecycle (dedup guard outside try, recycle no bump, orphan active bill reminder) — MEDIUM variant listed below in Integration for visibility.

---

## 3. Suspected Bugs (require additional evidence)

| ID | Location | Short | Missing evidence / next check |
|----|----------|-------|------------------------------|
| T1-SHELL-AUTH-001 | auth.py:40 defaultdict | Per-username dict-key leak real but home-LAN bounded deque to 5, volatile, no persistence; need load test of 10k distinct usernames to size OOM | SUSPECTED |
| T1-SHELL-AUTH-003 empty-string ALLOW_REGISTRATION | auth.py:74 | Env="" blocks secrets only if attacker controls env; Docker :-false mitigates; needs env precedence decision | SUSPECTED |
| T2-PERSISTENCE-002 household RETURNING arbitrary | db.py:923 | Return value wrong but cache key re-reads DB so only mirror torn one rerun; needs household-diverged-state repro | SUSPECTED |
| T3-CURRENCY-003 taxonomy remap partial | sync_core:175 | Subcategory-only Vacation/Travel correctly rejected not silent persist; import gap unreachable; needs phone subcategory-only payload repro | SUSPECTED (hardening) |
| T7-INTELLIGENCE-005 Ask doc overclaim | llm.py:327 ask.py:21 | Code ships capped 100-char names/descs but README/insights-and-llm accurately disclose; ask caption overclaims numbers-only; needs UI copy decision | SUSPECTED |
| T8-CONNECTIVITY-005 pairing limiter | api.py:37-48 | Global clear >1000 + per-IP not exploitable on single-worker 8501-only Caddy; needs multi-worker/botnet threat model | SUSPECTED |

---

## 4. Rejected Candidates (concise — why safe)

| ID | Short | Why NOT-A-BUG (evidence) |
|----|-------|--------------------------|
| T2-PERSISTENCE-004 | 64-char hex hijack | Self-consistent master == Fernet decode (crypto.py 101-125, 141); precedence documented, tests pin; UX surprise not corruption |
| T2-PERSISTENCE-005 | Backup PRAGMA on plaintext | ``_ensure_db_encrypted`` guarantees ciphertext before `_raw_connect`; Postgres early return; app.py init ordering |
| T3-CURRENCY-002 | Import vs canonical to_eur divergence | Intentional tightening: bank_import NaN skip prevents silent 1:1 mispricing (import-pipeline.md:160); tests prove expected |
| T5-PLANNING-003 | Loan bucket max(k,0) | Intentional burst-before-first_due fix; tests expect months_paid==1; domain docs bucket block |
| T5-PLANNING-004 | Portfolio live vs snapshot + days | Snapshot freeze intentional per planning-and-wealth.md 11.2; .days 24h trigger not 47h; manual force bypass |
| T6-INGESTION-003 | Tesseract Windows-only/bomb/block | PATH-first covers Linux Docker (`Dockerfile tesseract-ocr`), maxUploadSize 10, 30s join documented |
| T6-INGESTION-004 | Categorizer stale within-batch | Fingerprint retrain handles; tests retrain without clear passes |
| T6-INGESTION-005 | EU heuristics US edge | Single-sign fallback + fillna+warning + day-first regression + review-first before save |
| T7-INTELLIGENCE-001 | requests top-level | Mandatory dep (`requirements.txt`), not optional like llama_cpp per docs |
| T7-INTELLIGENCE-003 | _local_lock incomplete | All prod callers via ``_local_chat`` lock; ``_last_result`` diagnostic only benign |

---

## 5. Dynamic Bug Taxonomy (failure mechanisms, not areas)

Mechanism clusters derived from 20 validated findings (not pre-baked).

| Pattern ID | Name | Mechanism (precise) | Seed findings | Analogous findings | Domains | Prevalence |
|------------|------|---------------------|---------------|--------------------|---------|------------|
| **PATTERN-01** | Unbounded Whitelist without Business Invariant | `FIELD_SCHEMAS` checks type/finite/STR_MAX but never `>0`, `<=MAX_AMOUNT`, or `amount_eur == to_eur(amount,currency)`; derived EUR field trusted verbatim via `setattr` across ``create_record/_apply_update`` | T3-CURRENCY-001, T3-CURRENCY-004 | Income hours/rate/budgeted_eur/actual_eur, Savings target/deposited/balance_eur, SavingsAccounts amount_eur (P1 hunter 3 CONFIRMED) | Currency/Sync/Audit | **SYSTEMIC** — one helper reused for 4 tables, 12+ numeric fields, all sync create+update paths |
| **PATTERN-02** | Stale Derived State / Version Tear / Lost-Update Clobber | Mutation (bump/rate JSON merge) without atomic snapshot or fresh re-read; whole-JSON column overwrite from stale snapshot; per-helper `db_version()` re-read without rerun memo; `RETURNING` arbitrary fetch | T1-SHELL-AUTH-002, T2-PERSISTENCE-003, T2-PERSISTENCE-002 (partial) | Settings currency, fun_categories, travel_categories JSON clobber; household RETURNING; 14 query wrappers + app.py 5 reads + 15 pages tearing (P2) | Persistence/Shell/Planning | **SYSTEMIC** — 4 JSON cols + 38 call sites |
| **PATTERN-03** | Sentinel/NaN Validation Asymmetry across Surfaces | Form enforces `—→""`, per-`CATEGORIES[cat]` whitelist, `NaN→""`, `lower/\s+` dedup; editor/MCP/sync skip: global `ALL_SUBCATS`, no ``—`` coercion, no invalid-sub clear, NaN into String, strict `==` | T4-LEDGER-001, -002, -004, -003 (NULL) | Sync global ALL_SUBCATS, Bank import review same list, String NaN "nan" leak, 7 strict dedup vs 1 normalized (P3) | Ledger/Ingestion/Sync | **SYSTEMIC** — 3 surfaces + 7 dedup paths diverge, correct refs exist |
| **PATTERN-04** | Lock/Memoization Scope Divergence | Artifact (lock, backup dir, secret, failure memo, revision) lives in `BASE_DIR/state_dir` not `DB_PATH` dir, or background `cached=False` bypasses `@st.cache_data ttl1800` memo that foreground relies on | T2-PERSISTENCE-001, T8-CONNECTIVITY-004, T2-PERSISTENCE-003 | BACKUP_DIR/.last_backup, .secret_key, rates vs market_data per-symbol vs global memo, revision per-helper (P4) | Persistence/Connectivity | **REPEATED/SYSTEMIC** — 3 artifacts + 2 memo paths |
| **PATTERN-05** | Time/Calendar/EU Heuristic Drift | Floor(`days`), month-index without day, delimiter/suffix/locale heuristic EU-centric applied globally | T5-PLANNING-002, T6-INGESTION-001, T6-INGESTION-002, INTEGRATION-B-001 (column-wide thousands) | rates `rates_are_stale 3d`, backup retention 30d, .days>= threshold trio (P5) | Planning/Ingestion/Market | **REPEATED** — 3 staleness/retention + 3 EU-sniff + 2 amortisation |
| **PATTERN-06** | N+1 Write without Transaction (partial commit) | Loop over items each `with get_session(): commit` without `with engine.begin():`; single bump after loop; mid-loop failure leaves prefix | T4-LEDGER-005 | Big purchases board, expense edit batch, trash batch, market multi-holding snapshot, bank bulk import (P6 5 CONFIRMED), household bump arbitrary | Ledger/Planning/Ingestion | **SYSTEMIC** — 6 pages/domains clone, `atomic_update_setting_json` proves fix known |
| *(LEAK)* | Coupling / Hidden Write | Pure-claimed module performs DB write + bump + rerun inside intelligence | T7-INTELLIGENCE-002 | Insight delegate should live in `insights_view.py` | Intelligence | Repeated (insights) |
| *(OPTIMISTIC)* | Optimistic Dedup vs Guaranteed Delivery | Persist marker before delivery then ghost on failure suppressing retry, plus granularity mismatch `cat:level` vs bare cat | T8-CONNECTIVITY-003 + INTEGRATION-A-001 | Bills/loans correct via `on_done` only prove pattern known | Notifications | Repeated (budget vs bill/loan) |

---

## 6. Systemic Patterns (multi-evidence only)

Only patterns with ≥2 independent concrete findings are systemic. Two dominate.

### P1+P3 — Sync/I/O trust-boundary validation gap is systemic
- **Root:** Single helper `validate_fields` whitelists syntax (type, finite, 500ch) but business rules (amount caps, positivity, dual-amount consistency, per-category taxonomy) live only in UI/MCP/bank_import and are not re-checked at persistence boundary. Every future table/field added to `FIELD_SCHEMAS` inherits same hole.
- **Evidence:** `mcp_server.py:351` vs `sync_core:155` divergence; 12 fields across 4 tables share defect; analogs confirmed by hunter grep.

### P2+P6+P4 — Stale-snapshot and transaction-atomism gap is systemic
- **Root:** Streamlit's per-rerun snapshot is not captured (revision, settings JSON) and not passed through; each helper re-reads fresh or overwrites whole JSON. Drag/market/bank loops commit per-item instead of per-batch via `engine.begin()`. Lock/memo live in different scope than operation.
- **Evidence:** 38 revision read sites, 4 JSON cols, 6 N+1 loops, correct patterns (`atomic_update_setting_json`, `app.py:132` fresh re-read) exist nearby but not propagated. Fix leverage high: centralizing `snapshot = db_version()` per rerun and using `engine.begin()` for batches would resolve many manifestations together.

### P3+P5 — Localization/sentinel gap is repeated across I/O
- Column-wide thousands, global ALL_SUBCATS, NaN→"" and day-first defaults all reflect same EU-centric or global-list heuristic applied where per-value/per-category handling required. Rarely corrupts silently due to review-first before save, but leaks into stored rows via editor/sync.

---

## 7. Cross-Domain / Interface Findings (kept visible)

These are not single-domain; they require both sides to be inspected. Starred already in §2, listed here for interface visibility.

- **INTEGRATION-A-001 — Budget marker granularity mismatch (HIGH, CONFIRMED)** — `notifications.py:316 key=f"{cat}:{level}"` vs `333 _persist_marker(cat bare)` → fresh process duplicate exceeded vs suppressed near; cross-restart dedupe never matches. Interface: session dedupe (composite) ↔ persisted dedupe (bare) via `atomic_update_setting_json`. New HIGH.
- **INTEGRATION-A-002 — Recurring orphan lifecycle (MEDIUM, CONFIRMED)** — `log_expense.py:170-177` dedup `to_eur` outside try crashes, `178 ae` outside try, `197-205` recycle no bump/check return, `else: bump` only on full success → active orphan false bill reminder, stale household cache up to 5m. Interface: form ↔ persistence ↔ caching.
- **INTEGRATION-B-001 — CSV column-wide vs PDF per-token locale 1000× (MEDIUM, HIGH-CONFIDENCE)** — `bank_import:173` all-or-nothing thousands vs `pdf_import:160` per-token → 1.200 misparses 1.2 vs 1200. Interface: Ingestion CSV ↔ PDF ↔ forecasting categorizer cycle (stale snapshot fingerprint).
- **INTEGRATION-B-002 — Sync interfaces (HIGH/MEDIUM)** — (a) dual amount, (b) household bump read-then-write not atomic, (c) caps 422 vs silent truncate, batch not atomic. Interface: api ↔ sync_core ↔ db. (a) duplicates P1, (b)(c) add household/cache gaps.
- **INTEGRATION-B-003 — Price vs rates failure memo divergence (MEDIUM)** — `market_data maybe_refresh cached=False` hammers every login vs `rates _fetch_cached` respects 30m; per-symbol vs global, process-wide lock. Interface: market_data ↔ rates ↔ cache ↔ db bump household semantics.
- **INTEGRATION-C-001 — Sync drops loan surcharge (HIGH, CONFIRMED)** — `FIELD_SCHEMAS["expenses"]` whitelists `loan_id` but not `loan_surcharge_eur/loan_payment_type` → early-repayment fee lost, schedule misclassifies principal vs interest. Interface: sync ↔ db.Expense loan cols ↔ finance.loan_schedule.
- **INTEGRATION-C-002 — MCP budget double-count (MEDIUM, HIGH-CONFIDENCE)** — `mcp_server.py:165 sum(budgeted_eur)` vs `effective_category_budgets` authoritative subs → AI advice inflated. Interface: MCP ↔ budgets ↔ dashboard/notifications (correct).
- **INTEGRATION-C-003 — Throttle trust + household stale id (MEDIUM, CONFIRMED+P)** — Auth shared bucket vs API per-IP achievable diverged; `get_household_expenses(household_id)` trusts supplied id & stale `session_state.household_id`. Interface: auth/api ↔ household aggregate.

---

## 8. Out-of-Scope Leads (observed, not investigated per narrow hunter scope)

Routed back to coordinator for future triage; not validated, no severity assigned.

- P1: `STR_MAX 500` truncation DoS, `is_deleted` bool toggling without ownership re-check beyond user_id, `date` ISO future-date guard.
- P2: Drag board ordering (P6), pairing invite rotation, budget scope dedupe.
- P3: Amount rounding divergence at `log_expense:100`, caps, pdf date locale.
- P4: Sync whitelist caps, taxonomy triple remap, amount injection, audit bump.
- P5: Categorizer thresholds, audit/bump, encryption, sync caps, effective budgets, soft-delete.
- P6: Taxonomy remap divergence, income legacy types, rate fetch memo, sync caps, encryption, portfolio pie.
- Integration: GitHub second-backup same-day 409, secret never uploaded holds, invite rotation, scope dedupe, portfolio allocation.

Coordinator disposition: P1+P2+P6+P4 are systemic → new focused hunters justified if additional validation needed; leads above remain low-priority hardening unless new evidence.

---

## 9. Recommended Remediation Order (no code fixes in this campaign)

Order by `severity × confidence × shared root leverage × dependency`. Earlier items fix many manifestations.

1. **T4-LEDGER-003 (CRITICAL) — backfill soft-delete NULL** — `UPDATE ... SET is_deleted=COALESCE(is_deleted,0)` + `NOT NULL DEFAULT 0` migration and change filters to `COALESCE(is_deleted,0)=0` or `IS NOT TRUE` at all 8 sites; add CHECK. One migration fixes hidden history across all readers.

2. **PATTERN-01 (P1) — centralize sync business validation + recompute derived EUR** — In `sync_core.validate_fields/_apply_update` add `>0`, `<=MAX_AMOUNT` (and `MAX_SAVINGS_TARGET` for savings), and overwrite `amount_eur = to_eur(amount,currency,rates)` (same for `budgeted_eur/actual_eur/deposited_eur/balance_eur/amount_eur`) mirroring `mcp_server`. Fixes T3-001/004 and hunter analogs across 4 tables + INTEGRATION-C-001 (add `loan_surcharge_eur/loan_payment_type` to whitelist with validation).

3. **PATTERN-02 (P2) — snapshot revision per rerun + JSON fresh re-read** — Capture `version = db_version()` once at top of each page/rerun and thread through all `q.*` helpers; for JSON cols use `_fresh_get_settings` before merge (`app.py:132` pattern) or use `atomic_update_setting_json` for `currency_rates/fun_categories/travel_categories`. Fixes T2-003, T1-002, analogs in settings/rewards/travel, and INTEGRATION-B-002(b) household bump.

4. **PATTERN-03 (P3) — propagate per-category whitelist + NaN→"" + dedup normalization** — Replace global `ALL_SUBCATS` checks with `CATEGORIES[category]` in `sync_core:182`, `log_expense editor 295`, `bank_import 586`; add `pd.isna→""` coercion in `update_expense` (like `bank_import:351`) and ; add `strip/lower/\s+` normalization to `log_expense:171` dedup (reuse `bank_import:316`). Fixes T4-001/002/004 and hunters + INTEGRATION-C-002.

5. **T2-PERSISTENCE-001 + P4 lock scope** — Move `_ENCRYPTION_LOCK` (and `BACKUP_DIR/.last_backup`, `.secret_key` if custom DB supported) to `os.path.dirname(DB_PATH)`; or forbid `DB_PATH` override unless `BACKUP_DIR` colocated. Fixes migration race + backup/secret split.

6. **PATTERN-06 (P6) — batch atomism** — Wrap `_persist_grouped_order` (recurring + big_purchases), ledger edit/trash batches, `market_data` per-holding snapshot, and `bank_import` bulk import in `with engine.begin():` and single bump; for sync batch decide all-or-none vs per-record but document. Fixes T4-005 and 5 clones + INTEGRATION-A-002 orphan bump.

7. **D5/T6 locale & time drift (P5)** — `finance.months_between` day-aware, `market_data.prices_are_stale`/`rates`/`backup` use total_seconds threshold, CSV sniff `csv.Sniffer`/`sep=None`, PDF `_CURRENCY_SYMBOLS` + code suffix. Fixes T5-002, T6-001/002, B-001 column-wide.

8. **T8-CONNECTIVITY-002 MCP stale identity + T8-001 init ordering** — Add `_USER_ID` TTL/invalidation on user delete or re-resolve per request; swap `init_db` before `_auth` in `api.py:110,131` (parity with `pair()`).

9. **T8-CONNECTIVITY-003/INTEGRATION-A-001 budget ghost + T7-INTELLIGENCE-002 coupling** — Persist budget marker only via `on_done` (like bill/loan) and unify granularity to `cat:level`; move `add_recurring` write from `insights.py` to `insights_view.py` delegate.

10. **T5-PLANNING-001 per-sub progress (duplicate of 9's pattern but UI-only)** — Change `budgets.py:135` to per-row `budgeted_eur` for subcategory rows (keep `eff` for whole-category).

11. **Remaining LOW** — ETS per-category fallback (show dense cats despite global gap), Ask doc overclaim (fix caption), pairing limiter hardening — cosmetic/hardening after above.

*Note: Items 2 and 6 each fix ~6-8 manifestations together; doing them early maximizes leverage.*

---

## 10. Evidence Standards & Limitations

- Strong evidence: reproduction (`pd.read_csv` semicolon, headerless EUR suffix probe, fresh-DB 500), logically unavoidable path (`==False` excludes NULL, finite-only whitelist), contract contradiction (`FIELD_SCHEMAS` vs `mcp_server`+`bank_import`), state inconsistency (torn revision, ghost marker), multi-site proof (38 call sites).
- Weak evidence flagged SUSPECTED not promoted (burst loan, throttle OOM, household RETURNING mirror-only).
- Limitations: No live Streamlit run needed; static path + existing `tests/test_*.py` (39 files, ~369 tests) as semantic intent; optional deps (`llama-cpp`) treated as graceful None; production DB not required.

## 11. Artifacts & Registries

- `qa/registry-findings.json` — 36 stable IDs with verdict/severity/pattern (machine-readable)
- `qa/registry-patterns.json` — 6 patterns with seeds/analogs/prevalence
- `qa/reports/final-qa-bug-map.md` — this report
- Temporary repro scripts under `qa/_tmp/` (ephemeral, gitignored)

---

## 12. Swarm Governance Compliance

- No production code modified (read-only audit).
- Every D1-D8 received 1-2 specialist teams (8 domains → 8 investigators + 36 validators + 6 hunters + 3 integration teams = 53 subagents, all via `subagent` with constrained context per router row).
- Adversarial validation independent, attempted falsification with file:line cite; 10 rejected kept with evidence.
- Dynamic taxonomy derived from actual findings, not pre-baked; hunter spawning justified by ≥2 related or 1 high-impact reusable.
- Each hunter one narrow mechanism, syntactic+semantic search, out-of-scope → lead.
- Cross-domain interfaces received dedicated pass (both sides validated).
- Findings deduplicated by root cause, causal chains every CONFIRMED/HIGH-CONFIDENCE.

---

*Prepared by coordinated swarm per `agent instructions/` routing. Next phase: remediation PRs in order above; add tests for each fixed path (soft-delete NULL, MAX_AMOUNT on sync, sentinel "" in editor, PD CSV/PDF EUR suffix, month phantom, household RETURNING).*

