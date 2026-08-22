# Expense Tracker — Improvement Wave: Items 12–27

Baseline: HEAD 036d7e6 (819 tests green, Streamlit 1.61.1). Every item below was investigated against current code (file:line cites held in session research notes); owner decisions locked via Q&A round 2026-08-22.

Progress legend: `[ ]` todo · `[~]` in progress · `[x]` done (append commit hash when landed). Each phase ends with: full pytest green + page smoke + one commit per item (feat/wix(#NN): ...).

---

## Owner decisions (locked)

| Item | Decision |
|---|---|
| #12 Loans drag | Keep rich panels — won't-fix dragging; remove misleading glyph only |
| #13 Purchases | Atomic create-and-link command |
| #14 Travel | All 4 features · trips table · integrate Open-Meteo + Nominatim (no Google APIs — credit-card wall) |
| #15 Portfolio | FIFO lots · unrealized tax = info projection + opt-in annual booking |
| #16 Categories | Delete = force-remap dialog · strict validation vs effective taxonomy |
| #17 Forecast | All 4 improvements |
| #18 Insights | Errors expanded, rest collapsed |
| #21 Households | Stale-fix + admin kick + sharing beyond expenses, per-area visible/editable config |
| #22 AI providers | Add Gemini + fix ai_api_kind silent-drop |
| #24 Pie | "Where it is allocated" becomes donut chart |
| #25 Income | IncomeTemplate table mirroring Recurring · salary stays owned by UserSettings (template syncs from it) |
| #26 Agent tools | Reversible mutation framework · hybrid confirm (>= EUR 500 configurable) · paste-mail staging first, IMAP poller optional later |
| #27 UI consistency | Dashboard idiom = canonical kit; migrate all pages |

Defaults locked where Q&A did not cover (veto anytime): income email reminders deferred (board checklist shows unlogged state instead) · compound fun-money bonuses NOT clawed back on undo (documented limitation) · undo window 30 days.

---

## Phase A — Bug fixes

### [x] #20 Rewards crash (6a9aaf9) (naive vs aware datetime)
Root cause verified: earned_at reads back tz-aware; NULL row -> naive pd.Timestamp.min sentinel -> mixed sort (rewards.py:270).
- [x] db.py: add utc_ts(v) near _parse_dates (~L1135): None/NaN -> Timestamp.min.tz_localize('UTC'); naive -> localize; aware -> convert.
- [x] rewards.py:270: sort key -> utc_ts(item[1]).
- [x] Test tests/test_rewards_tz.py: None/naive/aware contract + mixed-dict sort regression.
- Accept: badges tab renders with a NULL earned_at row present.

### [x] #13 Big purchases atomic (6a9aaf9) create-and-link (+ same-class stale renders)
Bug: create-new-goal branch (big_purchases.py:180-185) never invalidates q.savings cache nor reruns.
- [x] services/purchase_commands.py::create_target_and_fund_purchase(...) — one transaction: savings target + wishlist purchase linked via existing funding_goal_ref; reuse validators; audit; single revision bump.
- [x] UI _OPT_NEW branch calls the atomic command directly.
- [x] Stale-render fixes: savings.py deposit branch -> flash+rerun; log_expense.py manual save -> flash+rerun. Term deposit + OCR paths already reran (investigator claim outdated).
- [x] Tests tests/test_purchase_create_and_fund.py: atomic create+link, duplicate-goal rollback, empty-name rejection (unit-level, mirrors existing command-test pattern).
- Accept: new goal appears funded in a single submit.

### [x] #21a Household stale membership (6a9aaf9)
Bug: dashboard.py:40 trusts st.session_state.household_id; leaving on device B leaves device A stale.
- [x] queries.current_household_id(user_id) cached on shared revision; dashboard re-derives each run.
- [ ] Manual QA: 7-step two-browser script (join/share/log/leave/stale/rotate/orphan) — leave-on-A case now passes.

### [x] #12 Loans — closed honestly (6a9aaf9)
- [x] ui/panel.py: decorative glyph removed (collapse unchanged); loans keeps rich panels per owner decision. arrow glyph (collapse persists unchanged).
- Accept: no fake drag affordance on loans.

## Phase B — Quick wins

### [x] #24 'Where it is allocated' donut
- [x] dashboard.py: donut via services/finance_queries.allocation_donut_slices (goals → term → holdings, zero wedges dropped); reconciliation caption kept.
- [x] Test: tests/test_allocation_donut.py — slice sum == allocated parts, empty-goal wedge dropped.
- Accept: donut renders inside existing expander; totals reconcile.

### [x] #18 Insights collapsible (errors expanded)
- [x] insights.py: severity-icon expander per card, errors expanded, _summary_for() helper.
- [x] ML-anomaly + subscription sections collapsed; MoM table untouched.
- [x] Tests: tests/test_insights_expanders.py — 11 tests green.
- Accept: wall-of-banners gone; errors still visible immediately.

### [x] #19 Ask-your-data charts inline
Bugs: prompt never mentions rendered charts; figure hides in Sources.
- [x] ai/prompts.py ADVISOR_SYSTEM: chart-below sentence added.
- [x] ask.py: _render_chart_from_result() renders inline after the assistant message; Sources keeps text only.
- [x] Tests: tests/test_ask_inline_chart.py — 11 tests green.
- Accept: 'make me a plot of my spendings' answers with text + inline chart.

### [x] #23 Make expense_categorizer visible
Model exists (TF-IDF + LogReg, needs manual activation; wired only into CSV import / OCR).
- [x] log_expense.py: ✨ Suggest button above Category pre-fills cat/subcat + confidence caption; saved rows carry suggest_source/category/confidence/accepted telemetry (pick_manual_suggestion in forecasting.py).
- [x] settings.py ML tab: ml_status_line(None, labelled) keyword-only countdown / active-version line under the model header.
- [x] Test: tests/test_ml_status_and_suggest.py — pure-level coverage of picker + status chooser (source inference, sub-dash cleanup, missing-count, ready-to-train); AppTest prefill covered indirectly by test_ocr_review page-render smoke (no exception).
- Accept: manual expense form visibly suggests categories.

### [x] #17 Forecast improvements (all four)
Line refs shift +14 (ML accuracy expander landed today).
- [x] Band: projection_band ±15% caption for non-ML methods (naive fixed band per ponytail note).
- [x] Pacing st.info: €/day pace + on-track projection to month end.
- [x] Fixed vs discretionary: recurring templates split projection_breakdown into fixed/discretionary/under-fixed expander metrics.
- [x] Savings scenarios: salary + recurring-delta sliders -> savings_scenario monthly-savings/rate metrics.
- [x] tests/test_forecast_scenarios.py — 7 unit tests green (band math, breakdown split, clamp-after-multiplier, rate None guard).
- Accept: all three methods show uncertainty; projection names its fixed component.

## Phase C — Structural foundation

### [x] #16 User-configurable categories/subcategories
- [x] db.py: UserTaxonomy table + ensure_user_taxonomy_seeded (idempotent, from CATEGORIES) + TAXONOMY_RESERVED_CATEGORY guard; delete_user_account cleanup integrated.
- [x] CRUD: get_user_taxonomy / upsert / rename (atomic registry+data move) / soft_delete / remap (+reorder_user_categories) / can_delete census; all audited.
- [x] queries.effective_categories cached on (uid, db_version); db.effective_taxonomy is the uncached core.
- [x] Picker swap via page-top effective shadow in log_expense/budgets/recurring/big_purchases/dashboard/travel/rewards (+ fun-pool merge keeps stored entries selectable); set_budget validates against the effective list.
- [x] validate_category_in / validate_category_subcategory_in + map_unknown_category (exact -> keywords -> 'Uncategorized').
- [x] Settings → Categories tab: add form, up/down reorder, inline rename+subcat editor, plain delete when unused, force-remap dialog when in use.
- [x] tests/test_user_taxonomy_db.py (6) + tests/test_effective_taxonomy.py (5): seed idempotency, rename/remap counts, reserved guards, keyword-fallback chain incl. monkeypatched outcomes, set_budget on a custom category.
- Accept: user adds a category in Settings and can immediately log/budget against it everywhere.

## Phase D — Larger features

### [ ] #15 Portfolio sells — FIFO + tax model
- [ ] Schema: HoldingLot(holding_id, lot_date, qty, cost_total, cost_eur, rate_at_buy); migration backfills one initial lot per existing holding.
- [ ] Sell dialog (@st.dialog, mirror remove_holding_dialog): date, qty <= held, live proceeds (reject stale/zero price), tax-rate prefilled from settings. FIFO consumes oldest lots; gain = SUM(proceeds/unit - lot cost/unit)*qty; tax = max(0,gain)*rate; net -> unallocated via audited Income leg (zero-sum invariant). Qty rounds 4 dp; <1e-6 deletes holding (snapshots kept). Loss => tax clamped 0. ponytail: no loss-carryforward ledger yet.
- [ ] UserSettings.tax_model JSON column (+ column migration): realized_default_rate, country DE/NL/none/custom presets (DE flat 26.375% / EUR 1,000 exemption; NL Box 3 ~5.6% deemed / exemption).
- [ ] Unrealized tax: Metrics panel shows projected annual accrual (SUM max(0, value-cost)*rate). ponytail: ignores Vorabpauschale basis mechanics — upgrade path commented. Opt-in 'Book <year> accrual': deduped per (user, holding, year), nudges cost basis. Off by default. Settings section + 'not tax advice'.
- [ ] Tests: FIFO order, partial sells, drift chunk-sells drain cost exactly, invariant delta (unallocated += net), zero-price rejection, accrual idempotency, lot backfill.
- Accept: sell flows end-to-end; unallocated rises by exactly proceeds - tax.

### [ ] #14 Travel vacation planner
- [ ] Schema: Trip(user_id, name, start_date, end_date, envelope_eur, dest_currency, participants_json, checklist_json, timestamps) + CRUD + migration.
- [ ] utils.travel_spent_in_range() (windowed variant); travel.py: trip list + active-trip view — envelope vs spend, per-day pacing (zero-days guard), cumulative-vs-ideal pace chart, countdown, persisted packing checklist.
- [ ] Savings-gap card: Vacation goal balance vs envelope -> monthly top-up (div-by-zero guarded).
- [ ] Companion splitting: participant list, equal-split tallies. ponytail: weights later.
- [ ] Integrations: Open-Meteo destination weather (lat/lon via Nominatim; cached; offline fallback) + Nominatim destination search (1 req/s, UA header). Thin adapters mocked in tests — CI does no network.
- [ ] Envelope canonical EUR; dest-currency display via existing rates.
- [ ] Tests: window filtering, pacing/top-up zero-guards, countdown, checklist persistence, adapter mocks.
- Accept: define a trip -> see pacing while travelling; weather + destination search work without keys.

### [ ] #22 Gemini provider + settings honesty
- [ ] ai/providers/gemini.py (generateContent, x-goog-api-key, retry cloned from OpenAI provider); orchestrator dispatch; resolve_provider recognition.
- [ ] Bug fix: ai_api_kind becomes a real column (_add_missing_columns + defaults + save path); one-time derivation from legacy URL-sniff persisted; sniffing retired.
- [ ] settings_ai.py: gemini in selectbox; label 'Platform API key'; copy states ChatGPT/Claude subscriptions != API billing (Claude OAuth barred for third-party apps); Gemini free tier ~1,500 req/day via AI Studio. README updated.
- [ ] Tests: response shape (mocked HTTP), dispatch, kind-column persistence.
- Accept: free-tier Gemini key works end-to-end; provider selection survives restart.

### [ ] #25 Income tab rework — recurring incomes
Reality: income has NO recurrence concept — only 4 flat UserSettings.salary_* columns powering one button. Expenses have the full template/board stack to lift.
- [ ] Schema: IncomeTemplate(id, user_id, description, income_type, amount, currency, amount_eur, due_day, start_month, active, sort_order, notes) mirroring Recurring; nullable Income.template_id.
- [ ] Idempotent seeding: salary_active users get a 'Fixed salary' template (due_day=salary_day, amount=salary_amount). UserSettings.salary_* stays source of truth; template syncs from settings on load; record_salary_raise keeps working unchanged (test_salary_raise.py untouched).
- [ ] Lift recurring-expense patterns: grouped_board (grouped by income_type), log_income_template_dialog (month-bucket dedup, amount adjust, sets template_id), template editor, _unlogged_income_templates checklist state ('expected — not logged'). Email reminders for income: deferred.
- [ ] 'Log my salary' button becomes the salary card's Log-now action. Forecast cycle detection (forecast.py:36-51 reads Income rows) unaffected.
- [ ] db fns: add/update/get_income_templates; q.income_templates(uid).
- [ ] Tests: seeding idempotency, dedup logging, unlogged detection, raise syncs board card.
- Accept: user adds 'Rent income, day 5' once; board shows due/unlogged; one tap logs it.

## Phase E — Platform

### [ ] #26 Rich agent capabilities — reversible logging + mail
Today: 18 read-only tools; mutations limited to one hardcoded budget proposal; MCP add_expense/add_income bypass commands (no undo/audit parity); zero mail ingestion.
- [ ] E1 Framework services/undo.py: CommandResultWithUndo(.undo_token/.undo_command/.undo_args); UndoToken(token_id, inverse_command, inverse_args, expires_at=30d, description); _execute_undo(token) idempotent (already-deleted -> ok-noop). Milestone/fun-money side effects NOT clawed back (next load re-evaluates naturally) — documented limitation.
- [ ] E2 Commands in services/commands.py wrapping existing writers, each returning undo tokens: add_expense / add_income / update_expense / delete_expense (undo = soft-delete/restore), recurring-template CRUD (needs is_deleted/deleted_at on Recurring), link/unlink purchase-to-goal. Route MCP paths through commands (audit parity).
- [ ] E3 Tools: register the 8 mutation tools in ai/tool_registry.py with schemas + dry_run flag; populate ALLOWED_MUTATIONS (ai/safety.py:46); orchestrator gate becomes TOOLS ∪ ALLOWED_MUTATIONS; redact undo_token/undo_command in sanitizer ID keys.
- [ ] E4 Feedback loop (ask.py): success card 'stored: expense EUR X @ Y — [Undo]'; undo -> 'undone — [Redo]'; toasts; dry-run preview cards >= threshold; agent_confirm_threshold_eur setting default 500; rate cap 20 agent mutations/24h (UserSettings.agent_call_counts); audit_log page gains 'via agent' filter.
- [ ] E5 Mail staging (paste flow): mail_ingestion.py parses pasted email text via existing extractors (ocr.guess_total_amount, line_item_extractor, bank_import keyword map) -> structured candidates {description, amount_eur, category, date, confidence} -> staging cards with per-item Accept/Discard. Nothing books silently.
- [ ] E6 (optional, later) local IMAP poller: stdlib imaplib/email; UNSEEN fetch; Fernet app-password; background thread like github_backup.maybe_auto_backup; feeds same staging. Build only after E5 proves out.
- [ ] Tests: undo idempotency/expiry, threshold confirmation, rate limit, sanitizer redaction, mail-parse edge cases (no amount, multi-line), milestone-after-undo behavior.
- Accept: 'Log this: coffee EUR 3.50 yesterday' books instantly with an Undo card; a EUR 600 expense returns a confirm card; pasted order-email produces accept/discard candidates.

### [ ] #21b Household admin kick + configurable sharing
- [ ] Kick: owner-only remove_member command (clears member household_id, bumps ALL members' revisions incl. removed, audit row); member-list UI; owner cannot kick self.
- [ ] Sharing config: households.share_prefs JSON — areas expenses(always) | budgets | income | loans, each hidden | visible | editable (default hidden except expenses); dashboard/household queries respect prefs; edits under editable go through audited commands. Single-machine SQLite documented in-page.
- [ ] Tests: prefs gating, kick revision bump, owner-only enforcement, prefs survive join/leave.
- Accept: owner toggles 'budgets: visible'; members see budgets without expenses-side changes.

### [ ] #27 UI consistency — dashboard kit everywhere
Audit result: palette already shared (CHART_COLORS via utils re-export); inconsistency is structural idioms.
- [ ] Codify kit in ui/: page_kpi_band(metrics) helper + PanelSpec/panel defaults (icon + summary + collapsible) + board-card CSS tokens from palette (ui/board.py CCv2 inline CSS).
- [ ] Migrate pages: loans (KPI band: total debt/debt-free date; panels stay), big_purchases + recurring (board headers/KPI band alignment), budgets/travel/savings/portfolio/household (ad-hoc st.markdown sections -> panel shells).
- [ ] Sweep: no hex literals outside ui/styles.py (currently clean); every chart uses CHART_COLORS.
- [ ] Per-page before/after smoke screenshots; mobile CSS sanity pass.
- Accept: any page reads as the same product; dashboard components recognizable everywhere.

---

## Known limitations (documented in-app where relevant)
- Undo does not claw back fun-money bonuses; milestones re-evaluate on next load.
- Unrealized-tax projection is a simplified preset model (not Vorabpauschale/Box-3 precise).
- Households are single-machine; no cross-device sync (sync_core is per-user phone pairing).
- Agent mutations cap at EUR-500-confirm / 20-per-day by default; both user-tunable.

## Suggested execution order
A (#20 -> #13 -> #21a -> #12) -> B (#24 -> #18 -> #19 -> #23 -> #17) -> C (#16) -> D (#15 -> #25 -> #14 -> #22) -> E (#26 E1-E5 -> #21b -> #27 -> #26 E6 optional).
Rationale: bugs first; quick wins build momentum; taxonomy before anything validating categories; heavy features before the platform layer; restyle last so pages are not styled twice.
