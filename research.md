# Research: LLM Data-Chat, ML, and Dashboard/UI Improvements

> Actionable improvement plan produced from a full code audit (LLM stack read
> line-by-line; two parallel subagent audits of the ML layer and the dashboard/UI
> against Streamlit 1.61 bundled references) plus web research on small-model
> tool-calling patterns. Sizes: S = small diff, M = medium.
>
> Status: proposed — not yet implemented. Wave 1 items are independent and can
> land one at a time; `pytest tests` must stay green after each.

## Goal
Make the three researched areas measurably better with the smallest diffs the
codebase allows: (1) the "Ask your data" advisor answers more questions
correctly and can safely act; (2) ML capabilities that are already trained
become visible and more accurate; (3) the dashboard gets faster reruns, dark
mode, a cleaner KPI story, and phone-usable editors.

**Success criteria**
- Advisor: planner prompt carries today's date + per-tool schemas; eval-harness
  routing accuracy printed in CI and not regressed; budget-change proposals
  applicable in-app with one audited command; chart answers for breakdowns.
- ML: backtest metrics rendered on Forecast page; AI/MCP anomalies match the
  Insights ML scan; IsolationForest features scaled; prediction intervals
  derived from backtest residuals instead of the hardcoded ±20%; budget
  recommender surfaced.
- UI: editor keystrokes no longer redraw all charts; dual light/dark theme
  config with zero hardcoded chart hexes on the dashboard; filters in sidebar;
  preset/AAR editors usable at phone width.
- `pytest tests` stays green after every item; AppTest smokes for
  dashboard/ask/budgets extended minimally.

**Ground truth verified in repo**
- Orchestrator planner prompt has NO current-date context (`ai/orchestrator.py:373`).
- Mutation proposals are always `budget_change` (`ai/safety.py:102-129`) and
  ask.py's Confirm button is disabled/unwired.
- `db.add_budget(user_id, row)` exists (`db.py:1790`) — reuse for confirm wiring.
- 100-case advisor eval harness exists (`tests/test_ai_eval.py` + `cases.yaml`).
- Chart spec validator exists (`ai/charts.py`); only one `@st.fragment` in the app.
- Streamlit 1.61.1 supports `[theme.light]` + `[theme.dark]` and metric sparklines.
- Dead/duplicated style code: `.kpi*` CSS unused, `inject_mobile_css` defined twice,
  9 hardcoded chart hexes in `dashboard.py`.
- Unsurfaced ML: backtest metrics, `suggest_budgets`, `cluster_month_patterns`,
  `structured_anomalies`, categorizer accept/correct telemetry.

---

## Phase L — LLM "Ask your data"

### L1. Planner context pack (S)
In `ai/prompts.py` + `ai/orchestrator.py` (~line 373): prepend
`Today is {today.isoformat()}.` to `planner_user`, and append one compact line
per tool derived from existing `TOOL_SCHEMAS`
(e.g. `aggregate_spending(year:int, month:int, category?:str)`). A 1B local
model cannot infer year/month reliably; keep loose-JSON + validate +
deterministic repair — just enrich context.
*Accept:* unit test asserts date + schema block present; eval suite green.

### L2. Eval-driven routing fixes (S)
Extend `tests/test_ai_eval.py`: parametrize all 100 cases through
`fast_route -> infer_deterministic_args -> validate_tool_call`, print hit-rate,
fix misroutes found (expected offenders: greedy `compare|vs` pattern hijacking
aggregate questions via pattern order in `ai/router.py`).
*Accept:* hit-rate printed; no regression vs current count.

### L3. Wire the proposal Confirm button (M)
Add `services/commands.py set_budget(user_id, category, amount_eur, year, month)`
reusing `db.add_budget` + audit record (same one-txn pattern as FIN-07/FIN-08
commands); ask.py's disabled Confirm calls it after RE-VALIDATING the stored
proposal server-side (re-run the safety regex on stored fields; reject
stale/tampered), then `bump_db_version`. Proposals stay model-never-executes.
*Accept:* AppTest propose->confirm->budget row changed + audit row; tampered
proposal rejected.

### L4. Chart answers beyond `__series__` (S)
Fast-routes for `category_breakdown`/`merchant_breakdown` attach `_chart` via
existing `ai.charts.validate_chart_spec` (pie/bar over canonical rows — no
model numbers). ask.py's per-call renderer already draws `_chart`.
*Accept:* extend `tests/test_ai_charts.py`; "biggest expense categories this
month" shows a pie.

### L5. Delete dead legacy path (S)
`llm.answer_query`, `build_data_context`, `_ASK_SYSTEM` have zero production
callers (only tests) since the orchestrator replaced them. Delete code + their
tests (~200 lines out).

### L6. Ask-page fragment (S, wave 2)
Wrap chat history + input handling in `@st.fragment` so each turn doesn't redraw
provider badges/suggestions. Keep current provenance expander logic.

*Skipped:* streaming responses (provider API churn, low value now); native
function-calling mode (hurts tiny models per research); new providers.

---

## Phase M — Machine learning

### M1. Render backtest accuracy (S)
`app_pages/forecast.py` after the ML caption (~105):
`st.expander("Model accuracy")` showing `model_metrics` / `backtest_origins` /
`selection_reason` already returned by `forecast_next_month`
(`forecasting.py:199-207`). Zero added compute.

### M2. One anomaly definition everywhere (S)
`services/finance_queries.anomalies()` (line 561) switches from rule-based
`unusual_expenses` to `forecasting.structured_anomalies` so AI-tool/MCP answers
carry ML scores + reasons and stop contradicting the Insights UI. Map
`multiplier` param or note it in provenance. *Accept:* wrapper test.

### M3. Scale IsolationForest features (S)
`StandardScaler().fit_transform` around the feature matrix
(`forecasting.py:349-353`) — raw `amount_eur` currently dominates splits and
masks behavioral features. *Accept:* synthetic test where amount-dominated raw
features miss a behavioral outlier, scaled catches it.

### M4. Backtest-calibrated intervals (S/M)
Replace hardcoded ±20% (`forecasting.py:198`) with band from backtest residuals
already computed (~1.28x MAE for ~80%); keep ±20% fallback when backtest too
short (<3 origins). *Accept:* test monotonicity + fallback path.

### M5. Balanced classifiers (S)
`class_weight="balanced"` on both LogisticRegressions (`forecasting.py:538,
611`) so rare categories get suggested. Two lines + regression check on an
imbalanced fixture.

### M6. Suggest-budgets button (S)
`app_pages/budgets.py`: button calls existing `forecasting.suggest_budgets`
(`:1018`), prefills next-month inputs for review before save (no auto-save).

### M7. Cache the anomaly scan (S)
`@st.cache_data` keyed on (row count, max date) around the Insights-path
`detect_anomalies` call (queries are ttl-cached; this recompute isn't).

### Wave 2 (optional)
- By-category forecast uses the backtest-selected candidate (`:210-214`,
  ETS-only today).
- Cluster card in Insights (`cluster_month_patterns`).
- Settings ML-tab telemetry panel consuming recorded accept/correct feedback +
  auto-threshold refresh.
- Dedupe `suggest_threshold_for_precision` (`forecasting.py:668` -> import from
  `ml.evaluation`).

### Test debt (one small file)
First-ever tests for `ml/evaluation.py` (score_forecast, rolling_origin_backtest
happy + short-history), the hybrid backtest candidate (`:108-121`), and
`finance_queries.anomalies()/forecast()` wrappers.

---

## Phase U — UI & dashboard

### U1. Dark-mode-ready theme + color centralization (S)
`.streamlit/config.toml`: split into `[theme.light]` + `[theme.dark]` (per
Streamlit 1.61 theme reference); move categorical palette into
`chartCategoricalColors`; replace the 9 hardcoded hex sites in `dashboard.py`
(578, 769, 773, 803, 820, 822, 839, 841, 843) with `ui.styles.CHART_COLORS`
refs; delete dead CSS `.kpi*` and `.pw/.pb`; keep ONE `inject_mobile_css`
(`utils.py:229` vs `ui/styles.py:26` duplicate).

### U2. Fragments where interaction is dense (M)
`@st.fragment` around the auto-allocation editor (`dashboard.py:183-280`) and
preset editor (`317-462`) — every keystroke there currently redraws all 7
Plotly charts; extract loan-amortization loop (`615-634`) and portfolio
valuation (`651-656`) into `@st.cache_data` helpers. Chart block keyed by
(year, month, db_version). Panel-toggle fragmentization deferred until measured.

### U3. Filters to sidebar (S)
Year selectbox + Month slider (`:501-507`) move into `st.sidebar` above the
currency control (dashboards guidance); frees a full row.

### U4. Consolidate the KPI story (S/M)
Merge lone cards — Fixed costs/year (`608-609`), Total debt/Debt-free
(`635-640`), Net worth strip (`658-664`) — into the horizontal KPI strip
(`547-553`); add sparklines to Income/Expenses metrics (`st.metric`
chart_data) and delete the standalone 7-day sparkline panel (`567-587`).
All merges guarded by existing `personal_view`.

### U5. Demote heavy content (S)
Cumulative net cash flow behind an expander(`on_change` rerun) or paired
columns(2) with Monthly trends; Top-10 table behind a "Raw data" expander.

### U6. Mobile fixes (S)
Preset editor's 6-column rows -> stacked 2-per-row layout; AAR's 4-col rows
likewise; scope global `width:100%` button CSS (`styles.py:39`) to exclude
small icon buttons (edit pencils, panel chevrons).

### U7. Dashboard <-> Ask cross-link (S)
"Ask AI" pill row on the dashboard linking to ask.py with question prefilled
via `st.query_params` (ask reads param once on load) — ties the workstreams
together.

### Wave 2 (optional)
Sidebar slimming (rate form/QR -> `st.popover`), remove heavy divider at
`:666`, touch-friendly chart modebar config.

---

## Execution order & gates
1. **Wave 1 (independent S items):** L1, L2, L4, L5 · M1, M2, M3, M4, M5, M6,
   M7 + evaluation tests · U1, U3, U5, U6, U7.
2. **Wave 2:** L3 (confirm wiring), L6 · M wave-2 items · U2 fragments/caching, U4.

Gate after every item: `pytest tests` green; minimal AppTest additions to
`tests/test_app_smoke.py` for touched pages; manual smoke notes appended to
`tasks/todo.md`.

## Assumptions & non-goals
- No new dependencies (sklearn/statsmodels/pandas/plotly/Streamlit built-ins only).
- Local Gemma 1B stays the primary planner target; API providers benefit automatically.
- Privacy boundaries untouched — `ai.safety.sanitize_outbound_text` remains the
  single egress choke point (AI-01).
- No orchestrator architecture rewrite; no custom components; household view
  unaffected (all KPI merges behind `personal_view`).