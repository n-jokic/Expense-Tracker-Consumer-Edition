# Read/Calculation Inventory (Part C) — Finance Aggregations & Duplication Map

> Scope: read-only inventory of **finance calculations** duplicated across the
> Streamlit shell (`app.py`), `app_pages/*`, `mcp_server.py`, `insights.py`,
> `llm.py`, `forecasting.py`, `queries.py`, `finance.py`, plus the supporting
> call sites in `notifications.py`, `gamification.py`, `utils.py`, and `db.py`.
> Purpose: identify what belongs in a canonical shared service
> (`services/finance_queries.py` etc.) and what is pure formatting vs. pure math.
>
> No files were modified. `services/` does not exist yet — this inventory is the
> specification for creating it.

---

## 1. Layering summary (what lives where today)

| Layer | Module | Role |
|-------|--------|------|
| Persistence | `db.py` | Raw reads (`get_expenses`, `get_income`, `get_savings`, `get_budgets`, `get_recurring`, `get_loans`, `get_loan_payments`, `get_holdings`, `get_holding_prices`, `get_savings_accounts`, `get_big_purchases`, `get_household_expenses`, …). One **derived** calculation lives here: `_recompute_savings_balances` (`db.py:1262-1316`) — the running savings balance chain. |
| Cache glue | `queries.py` | Thin `st.cache_data` wrappers + `db_version()`/`bump_db_version()`. **No calculations** — pure passthrough of DataFrames. |
| Domain math | `finance.py` | Pure, unit-tested math: `annuity_payment`, `loan_schedule`, `derive_hourly_rate`, term-deposit math (`compound_months`, `maturity_value`, `accrued_value`, `months_between`), `portfolio_metrics`, `calculate_early_repayment_surcharge`. **This is the correct home for pure finance math.** |
| Shared helpers | `utils.py` | Currency engine (`to_eur`, `to_display`, `fmt`, `fmt_row`), `effective_category_budgets`, `fun_spent`, `travel_spent`, `compute_salary_cycle`, `classify_quadrant`, `filter_started_templates`. These are mostly **canonical already** but import `streamlit` (see §5). |
| Intelligence | `insights.py` | **Analysis functions** (`month_over_month`, `top_category_this_month`, `unusual_expenses`, `days_until_budget_depleted`, `savings_projection`, `build_narrative_stats`) + `render_insights()` which inlines 14 more ad-hoc calculations. |
| ML | `forecasting.py` | `_monthly_totals`, `_elapsed_months`, `_ets_forecast`, `forecast_next_month`, `detect_anomalies`, `detect_subscriptions`, `cluster_month_patterns`, `suggest_budgets`, categorizer. |
| LLM context | `llm.py` | `build_data_context()` **re-implements** many month/category/balance aggregations inline; `generate_summary`/`generate_narrative` consume pre-built stats. |
| Surface | `mcp_server.py` | Read tools that **re-implement** expense/income/budget/savings aggregations inline, plus thin wrappers that delegate to `insights.py`/`llm.py`. |
| Notifications | `notifications.py` | `check_and_send_budget_alerts` (budget vs actual), weekly summary (window totals + top categories), loan reminders (annuity payment). |
| Gamification | `gamification.py` | `get_budget_adherence_streak` (budget vs actual over months), `get_logging_streak`, `detect_raise`, milestone checks. |
| Pages | `app_pages/*.py` | Dashboard, Budgets, Forecast, Insights, Savings, Loans, Portfolio, Rewards, Travel, Household each inline their own aggregations. |

---

## 2. Calculation patterns — where implemented, duplication, canonical home

For each pattern: **inputs → outputs**, **implementations**, **duplication**, **should it be in a canonical service?**

### 2.1 Expense summary for a period (spent / income / net / budget)

- **Pattern:** For a calendar month: total spent (`amount_eur.sum()`), total income
  (`actual_eur.sum()`), net (`income − spent`), budget total + remaining.
- **Canonical-ish today:** `insights.month_over_month` (partial) + ad-hoc sums.
- **Implementations:**
  - `mcp_server._expense_summary_impl` (`mcp_server.py:170-195`) — spent, earned,
    net, budget_total, budget_remaining, top_category, fun money, monthly budget.
  - `app_pages/dashboard.py:217-221` — `ie` (income), `ee` (expenses), `sd` (saved),
    `ne = ie - ee - sd`, `sr` savings rate.
  - `app_pages/dashboard.py:472-497` — monthly `mv()` helper per month for
    Income/Expenses/Savings trends and cumulative net cash flow.
  - `llm.build_data_context` (`llm.py:358-365, 410-412`) — current/prev month
    expense+income sums and top categories.
  - `app_pages/budgets.py:53-60` — current-month spent vs overall budget.
  - `insights.py` Insight 1/6 (`month_over_month` on expenses and income).
- **Duplication:** 5+ independent "sum this month's expenses" implementations,
  each with slightly different empty-frame and future-date handling.
- **Canonical service:** yes — `get_expense_summary(uid, year, month)` and
  `get_monthly_totals(uid, year)` (see §6).

### 2.2 Monthly totals / monthly series (time series of sums)

- **Pattern:** Group expenses (or income/savings) by calendar month and sum.
- **Implementations:**
  - `forecasting._monthly_totals` (`forecasting.py:26-33`) — `date.dt.to_period("M")` → `groupby("ym")["amount_eur"].sum()`.
  - `app_pages/dashboard.py:472-497` — `mv()` loops `m in range(1,13)` re-filtering per month.
  - `forecasting.cluster_month_patterns` (`forecasting.py:395-396`) — pivot table by month/category.
  - `forecasting.suggest_budgets` (`forecasting.py:442-445`) — pivot by month/category.
  - `gamification._saver_streak` (`gamification.py:205`) — `groupby(date.dt.to_period("M"))["deposited_eur"].sum()`.
- **Duplication:** `_monthly_totals` in forecasting is the cleanest; dashboard's
  `mv()` is a slower re-scan (12 scans vs one groupby).
- **Canonical service:** yes — `aggregate_spending(uid, freq="M")` / `get_monthly_totals`.

### 2.3 Category breakdown (spending by category)

- **Pattern:** `expenses.groupby("category")["amount_eur"].sum()` for a period.
- **Implementations:**
  - `app_pages/dashboard.py:419` — pie chart `ct`.
  - `app_pages/dashboard.py:434` — budget-vs-actual `ac` (renamed to `ae`).
  - `app_pages/dashboard.py:353, 378` — budget alerts/progress `ca`/`ca3`.
  - `app_pages/household.py:142` — `ct` donut; `:157` `pm` by member.
  - `insights.top_category_this_month` (`insights.py:51-53`) — `groupby(...).idxmax()`.
  - `insights.py` Insight 7 top merchants (`:306`) — `groupby("description").nlargest(3)`.
  - `llm.build_data_context` (`llm.py:361-364`) — `groupby(...).nlargest(5)`.
  - `notifications.build_weekly_summary_email` (`notifications.py:144`) — `nlargest(3)`.
  - `notifications.check_and_send_weekly_summary` (`notifications.py:519`) — `nlargest(3)`.
  - `notifications.check_and_send_budget_alerts` (`notifications.py:307`) — `ca`.
  - `mcp_server` (via `insights.top_category_this_month`, `mcp_server.py:184-185, 309`).
- **Duplication:** Highest-duplication pattern — the same `groupby("category")...sum()`
  is written ~11 times. `nlargest(N)` is repeated for N∈{3,5}.
- **Canonical service:** yes — `get_category_breakdown(uid, year, month)` and
  `get_top_categories(uid, n, period)`, `get_top_merchants(uid, n, period)`.

### 2.4 Merchant / description breakdown

- **Pattern:** `groupby("description")["amount_eur"].sum().nlargest(k)`.
- **Implementations:**
  - `insights.py:306-309` — top 3 merchants this month.
- **Duplication:** Only one real implementation today, but conceptually the same
  as the category breakdown (group key differs).
- **Canonical service:** yes — `get_merchant_breakdown(uid, n, period)`.

### 2.5 Budget vs actual

- **Pattern:** For a month: actual per-category spend vs `effective_category_budgets(month_rows)`;
  produce over/near/on-track statuses and remaining.
- **Canonical helper:** `utils.effective_category_budgets` (`utils.py:299-316`) —
  already shared for the "effective budget" derivation. The *comparison* is not shared.
- **Implementations:**
  - `app_pages/dashboard.py:349-370` (budget alerts) and `:373-386` (progress bars).
  - `notifications.check_and_send_budget_alerts` (`notifications.py:285-340`).
  - `gamification.get_budget_adherence_streak` (`gamification.py:78-109`) — sums
    effective budgets across months.
  - `mcp_server._expense_summary_impl` (`mcp_server.py:176-191`) — budget_total +
    budget_remaining (sums `effective_category_budgets`).
  - `app_pages/forecast.py:121-125` — `total_budget = sum(effective_category_budgets(...))`.
  - `app_pages/budgets.py:123-160` — per-category and per-subcategory progress.
- **Duplication:** The `sum(effective_category_budgets(b).values())` line is
  copy-pasted in `mcp_server.py:178`, `gamification.py:99`, `forecast.py:125`.
  The over/near/on-track comparison is reimplemented in dashboard, notifications,
  budgets (all three use `NEAR_LIMIT_THRESHOLD = 0.85`).
- **Canonical service:** yes — `get_budget_vs_actual(uid, year, month)` returning
  per-category `{budgeted, actual, status, pct_used}` + `get_budget_summary`.

### 2.6 Cashflow / net (income − expenses − savings)

- **Pattern:** `income − expenses` or `income − expenses − savings` over a period.
- **Implementations:**
  - `app_pages/dashboard.py:217-221` — `ie/ee/sd/ne/sr` KPIs.
  - `app_pages/dashboard.py:491-506` — cumulative net cash flow by month.
  - `mcp_server._expense_summary_impl` (`mcp_server.py:189`) — `net = earned - spent`.
  - `insights.py` Insight 6 (`:283-299`) — income vs expense ratio / savings rate.
- **Duplication:** Same subtraction with different operand sets (dashboard includes
  savings deposits; MCP excludes them). This is a correctness hazard — two "net"
  numbers with different meanings.
- **Canonical service:** yes — `get_cashflow(uid, year, include_savings=True)`.

### 2.7 Savings projection (goal ETA)

- **Pattern:** From a goal's deposit history → current balance, target, avg monthly
  deposit, months-to-goal (with monthly compounding), projected date.
- **Canonical:** `insights.savings_projection` (`insights.py:90-140`) — the single
  correct implementation (600-month cap, withdrawal/NaN guards, `MS` resample tail(3)).
- **Implementations:**
  - `insights.savings_projection` (canonical).
  - Used by `app_pages/savings.py:579, 758` and `insights.render_insights` Insight 4 (`:244`).
- **Duplication:** None significant — this one is already shared. But it lives in
  `insights.py`, which is a render module, so it should move to the finance service
  (or `finance.py`) for layering hygiene.
- **Canonical service:** yes — `project_savings_goal(savings_df, goal_name)` (pure;
  move from `insights.py`).

### 2.8 Loan amortization / payoff

- **Canonical math:** `finance.annuity_payment` (`finance.py:11-18`),
  `finance.loan_schedule` (`finance.py:99-227`), `_first_due`/`_next_due` (`:72-96`),
  `calculate_early_repayment_surcharge` (`:62-69`).
- **Implementations (callers of the canonical math):**
  - `app_pages/loans.py:322-325` — `loan_schedule` per loan; `:416-422` after payment;
    `:284-289` early repayment; `:103` `annuity_payment` for the "saved" toast.
  - `app_pages/dashboard.py:292-320` — `loan_schedule` per active loan → `total_debt`,
    `debt-free-by`.
  - `insights.py` Insight 12 (`:376-389`) — `annuity_payment` per active loan → monthly total.
  - `notifications.check_loan_reminders` (`notifications.py:444-457`) — `annuity_payment`.
- **Duplication:** The *math* is shared, but the **"monthly scheduled payments total
  across active loans"** and **"total remaining debt"** aggregates are reimplemented
  in `dashboard.py` and `insights.py` and `loans.py` separately (each iterates loans
  and sums). There is no `get_debt_summary()`.
- **Canonical service:** yes — `get_debt_summary(uid)` returning
  `{total_debt, monthly_payments, debt_free_date}` (wraps `finance.loan_schedule`).

### 2.9 Recurring monthly / yearly total (fixed costs)

- **Pattern:** Sum `amount_eur` of active recurring templates (optionally prorated by
  `start_month`, `active` flag).
- **Implementations:**
  - `insights.py` Insight 9 (`:323-330`) — `rec_active["amount_eur"].sum()` monthly + ×12 yearly.
  - `app_pages/dashboard.py:269-289` — yearly total **with start_month proration**
    (months remaining in the current year).
  - `llm.build_data_context` (`llm.py:399-408`) — lists active bills (top 8) + count.
  - `notifications._unlogged_templates` (`notifications.py:242-282`) — "which templates
    are unlogged this month" (uses `filter_started_templates` + month-matching).
- **Duplication:** Two different "fixed costs" numbers exist (insights = full-year
  sum ×12; dashboard = prorated from start month) — both are "recurring totals" but
  disagree by design; a consumer reading both can see different figures.
- **Canonical service:** yes — `get_recurring_monthly_total(uid, prorate_start_month=True)`.

### 2.10 Spending comparison (period vs previous period)

- **Pattern:** Compare a window total against the preceding equal-length window:
  absolute delta + % change + trend.
- **Canonical:** `insights.month_over_month` (`insights.py:22-41`) — month granularity only.
- **Implementations:**
  - `insights.month_over_month` — current vs previous **calendar month**.
  - `insights.py` Insight 11 (`:360-374`) — last 30 days vs prior 30 days (inline).
  - `notifications.check_and_send_weekly_summary` (`notifications.py:512-520`) —
    this week vs prior 7 days (inline).
  - `app_pages/dashboard.py:195-209` — `_delta()`/`prev_flt()` — previous month/year
    for KPI deltas (inline, different shift logic).
  - `llm.build_data_context` (`llm.py:410-412`) — prev vs current month (inline).
- **Duplication:** 4 separate "compare two periods" implementations with different
  period definitions (calendar month, 30-day rolling, 7-day rolling, year).
- **Canonical service:** yes — `compare_spending_periods(uid, end, length, prev_length)`
  and `month_over_month` (move from `insights.py`).

### 2.11 Runway / burn rate (days until budget depleted)

- **Pattern:** `daily_avg = spent / days_elapsed`; `days_left = remaining / daily_avg`.
- **Canonical:** `insights.days_until_budget_depleted` (`insights.py:67-87`).
- **Implementations:**
  - `insights.days_until_budget_depleted` (canonical, calendar month).
  - `app_pages/forecast.py:85-114` — salary-cycle burn rate (period avg / 7-day avg / ML).
- **Duplication:** Same "burn rate → projection" logic written twice at different
  granularities (month vs salary cycle).
- **Canonical service:** yes — `project_spending(uid, start, end, method)` (unify
  burn-rate methods used by the forecast page).

### 2.12 Forecast (next-month ML + per-category)

- **Canonical:** `forecasting.forecast_next_month` (`forecasting.py:74-94`) with
  `_monthly_totals`, `_elapsed_months`, `_ets_forecast` (`:26-71`).
- **Implementations:**
  - `forecasting.forecast_next_month` (ETS + per-category).
  - `forecasting.suggest_budgets` (`:434-459`) — separate linear-trend budget suggestion.
  - `forecasting.cluster_month_patterns` (`:389-429`) — KMeans month clustering.
  - `app_pages/forecast.py:87-114` — fallback period-average / 7-day-average projection.
- **Duplication:** `suggest_budgets`, `cluster_month_patterns`, and `_monthly_totals`
  each independently build the month×category pivot. The "period average" fallback in
  the page duplicates the burn-rate logic in §2.11.
- **Canonical service:** keep ML in `forecasting.py`; expose a thin
  `get_forecast(uid, method)` facade. The pivot-building helper should be shared.

### 2.13 Fun-money spent

- **Canonical:** `utils.fun_spent` (`utils.py:405-421`).
- **Implementations:** all three call sites use the shared helper —
  `app_pages/dashboard.py:396`, `app_pages/rewards.py:53`, `insights.py:396`.
- **Duplication:** none (good); but the surrounding "allowance + milestone bonus"
  computation is copy-pasted between `dashboard.py:389-412` and `rewards.py:49-71`
  (bonus lookup from `fun_bonuses` map + legacy `fun_bonus_month`).
- **Canonical service:** yes — `get_fun_money_status(uid, settings)` (spent vs
  allowance + bonus).

### 2.14 Travel spent / pace

- **Canonical:** `utils.travel_spent` (`utils.py:424-458`).
- **Implementations:**
  - `app_pages/travel.py:80` — `travel_spent`.
  - `insights.py` Insight 14 (`:408-421`) — `travel_spent` + year-pace vs budget-pace.
  - `app_pages/travel.py:111-137` — **re-implements** the pool-membership logic
    (`_is_travel` per-row matcher) that `utils.travel_spent` already encodes, for the
    monthly chart. This is a real logic duplication (category/subcategory/pair matching).
- **Duplication:** pace comparison (`budget_pct vs year_pct`) appears in both
  `travel.py:82-102` and `insights.py:414-421`.
- **Canonical service:** yes — `get_travel_spending(uid, year, pairs)` +
  `get_travel_pace(uid, year)`.

### 2.15 Portfolio value / gain

- **Canonical:** `finance.portfolio_metrics` (`finance.py:270-300`) — value, invested,
  gain, gain_pct, live_count from `{quantity, last_price_eur, cost_eur}` dicts.
- **Implementations:**
  - `app_pages/portfolio.py:114-132` — per-holding EUR conversion loop, then calls
    `portfolio_metrics`. The conversion (`last_price / rate`) is done in the page.
  - `app_pages/dashboard.py:323-337` — **re-implements** portfolio value inline
    (`price * qty / rt`) for the net-worth strip.
  - `app_pages/savings.py:532-540` — **re-implements** portfolio value inline
    (`price_eur = last_price / rate`) for the savings KPI.
- **Duplication:** Three implementations of "holdings → EUR value", two of which
  (dashboard, savings) bypass `finance.portfolio_metrics` entirely. Also `finance.py`
  expects `last_price_eur` while pages compute `price_eur` and rename — a fragile
  naming seam.
- **Canonical service:** yes — `get_portfolio_metrics(uid, rates)` that fetches
  holdings, converts to EUR, and delegates to `finance.portfolio_metrics`.

### 2.16 Net worth (savings + portfolio − debt)

- **Pattern:** today's savings balances + portfolio value − total debt.
- **Implementations:**
  - `app_pages/dashboard.py:322-344` — the only full implementation (uses inline
    savings `last()` balances + inline portfolio + `total_debt`).
- **Duplication:** none (single site), but it composes three other duplicated
  patterns (savings total §2.17, portfolio value §2.15, debt §2.8) inline.
- **Canonical service:** yes — `get_net_worth(uid, rates)` composed from the three
  sub-services.

### 2.17 Savings total / interest earned

- **Pattern:** For each goal: latest `balance_eur`; interest = balance − deposited_sum.
- **Canonical balance chain:** `db._recompute_savings_balances` (`db.py:1262-1316`).
- **Implementations:**
  - `app_pages/savings.py:507-517` — `interest_total = bal - dep_sum`, `total_balance`,
    `saved_year`.
  - `app_pages/savings.py:554-572` — per-goal balance/deposited/interest.
  - `app_pages/dashboard.py:324-328` — `last_bal` by goal sum.
  - `mcp_server._list_savings_goals_impl` (`mcp_server.py:249-270`) — latest
    balance/target per goal (read-only listing).
  - `llm.build_data_context` (`llm.py:374-384`) — latest balance/target per goal.
- **Duplication:** "latest balance per goal" computed in 4+ places.
- **Canonical service:** yes — `get_savings_summary(uid)` (total balance, interest,
  per-goal latest balance/target).

### 2.18 Term-deposit value (accrued / maturity)

- **Canonical:** `finance.compound_months`/`months_between`/`maturity_value`/`accrued_value`
  (`finance.py:232-265`).
- **Implementations:**
  - `app_pages/savings.py:519-529` — locked value (accrued up to today, capped at maturity).
  - `app_pages/savings.py:562-570` — per-goal locked value (same loop).
  - `app_pages/savings.py:237-247, 687-690` — withdraw / display values.
  - `mcp_server._list_savings_goals_impl` (`:266-270`) — lists raw `amount_eur` (no accrual).
- **Duplication:** The "locked value = accrued_value capped at today" loop appears
  twice in `savings.py` (total and per-goal) — should be one helper.
- **Canonical service:** yes — `get_term_deposit_value(account_row, asof)` and
  `get_locked_savings(uid, asof)`.

### 2.19 Hourly rate derivation

- **Canonical:** `finance.derive_hourly_rate` (`finance.py:21-59`).
- **Implementations:**
  - `app_pages/big_purchases.py:53-70` — calls `derive_hourly_rate`.
- **Duplication:** none. Already pure and shared.
- **Canonical service:** keep in `finance.py`; optionally a facade
  `get_hourly_rate(uid, settings)`.

### 2.20 Unusual expenses / anomaly detection

- **Canonical:**
  - `insights.unusual_expenses` (`insights.py:56-64`) — category-mean multiplier heuristic.
  - `forecasting.detect_anomalies` (`forecasting.py:99-127`) — IsolationForest + median multiplier.
- **Implementations:**
  - Both are used by `insights.render_insights` (`:229`, `:440`) and MCP
    `_get_insights_impl` uses `unusual_expenses` only.
- **Duplication:** Two different "unusual" notions (mean-based vs ML) coexist by
  design, but `unusual_expenses` lives in `insights.py` and should be in the service.
- **Canonical service:** yes — `get_unusual_expenses(uid, method="ml"|"mean")`.

### 2.21 Logging streak / budget adherence streak

- **Canonical:** `gamification.get_logging_streak` (`:64-75`),
  `get_budget_adherence_streak` (`:78-109`).
- **Implementations:**
  - `llm.build_data_context` (`llm.py:414`) calls `get_logging_streak`.
  - `gamification` itself.
- **Duplication:** `get_budget_adherence_streak` re-implements the budget-vs-actual
  monthly comparison (§2.5) inline.
- **Canonical service:** `get_logging_streak` (pure, on DataFrames) + reuse the
  budget-vs-actual service for the adherence streak.

### 2.22 Subscription detection

- **Canonical:** `forecasting.detect_subscriptions` (`forecasting.py:338-384`).
- **Duplication:** none (single ML helper). Stays in `forecasting.py`.

---

## 3. MCP server: finance calculation vs. formatting — exact catalog

`mcp_server.py` (586 lines) mixes JSON serialization, date parsing, data reads, and
finance aggregation. The **finance-calculation** functions should become shared
services; the **formatting/plumbing** functions are MCP-specific.

### 3.1 Pure formatting / plumbing (NOT finance calculations — stay in MCP)

| Function | Lines | What it does |
|----------|-------|--------------|
| `_clean(v)` | `90-110` | JSON-safe conversion (NaN→None, dates→ISO, numpy scalars). Formatting. |
| `_records(df, columns)` | `113-119` | DataFrame → list-of-dicts with column selection. Formatting. |
| `_month_bounds(month)` | `122-137` | Parse `'current'|'last'|'YYYY-MM'` → `(first, next_first)`. Date parsing. |
| `_parse_date(d)` | `140-151` | Parse `'today'|'yesterday'|'YYYY-MM-DD'`. Date parsing. |
| `_user_rates()` | `154-155` | `get_rates(get_settings(user))`. Plumbing. |
| `_err(e)` | `158-159` | `{"ok": False, "error": ...}`. Error shaping. |
| `_in_month(df, start, end)` | `162-165` | Month filter on a DataFrame. Thin date filter (borderline — see §3.2). |
| `_resolve_user()` | `57-81` | Resolve MCP target user (env or first account). Plumbing. |
| `_list_budgets_impl` | `243-246` | Pure listing + column selection. Formatting. |
| `_list_recurring_bills_impl` | `273-277` | Pure listing. Formatting. |
| `_list_loans_impl` | `280-284` | Pure listing. Formatting. |
| `_get_milestones_impl` | `287-292` | Listing earned milestone ids + index join. Formatting. |
| `_add_expense_impl` | `355-395` | **Write** path: validation + currency conversion + audit. Write logic (currency conversion via `utils.to_eur` is already shared). |
| `_add_income_impl` | `398-434` | **Write** path: validation + currency conversion. Write logic. |

### 3.2 Finance calculations (should become shared services)

| Function | Lines | Finance calculation | Duplicated logic |
|----------|-------|---------------------|------------------|
| `_expense_summary_impl(month)` | `170-195` | Month spent/income/net, budget_total + budget_remaining (`sum(effective_category_budgets)`), top category, fun money. | Budget-total sum duplicated with `gamification.py:99`, `forecast.py:125`. Spent/income/net duplicated with `dashboard.py:217-221`. |
| `_list_expenses_impl(month, category, limit)` | `198-210` | Month + category filter, `total_eur = amount_eur.sum()`. | Month-filter + sum duplicated with `llm.build_data_context`, `dashboard.py:218`, `insights.month_over_month`. |
| `_list_income_impl(month)` | `231-240` | Month filter, `total_eur = actual_eur.sum()`. | Duplicated with `dashboard.py:217`, `llm.build_data_context`. |
| `_list_savings_goals_impl` | `249-270` | Latest balance/target/rate per goal. | Duplicated with `savings.py:511-516`, `dashboard.py:326-328`, `llm.py:376-384`. |
| `_get_insights_impl` | `295-329` | Orchestrates `insights.month_over_month`, `top_category_this_month`, `unusual_expenses`, `days_until_budget_depleted`, `build_narrative_stats`. | Not new math, but re-fetches `get_expenses/get_income/get_settings` and re-derives the narrative stats that `insights.build_narrative_stats` already produces — a second, MCP-specific path to the same numbers. |
| `_search_expenses_impl(query, limit)` | `213-228` | Text search (no aggregation — returns rows + count). | Search is retrieval, but it is the canonical `search_expenses` the service should expose (currently only MCP has it). |

**Summary answer to the key question:** the MCP functions that do **finance
calculations** (vs. formatting) are exactly `_expense_summary_impl`,
`_list_expenses_impl` (the `total_eur` sum), `_list_income_impl` (the `total_eur`
sum), and `_list_savings_goals_impl` (latest-per-goal balance/target). `_get_insights_impl`
and `_ask_data_impl` are thin delegators to `insights.py`/`llm.py` but duplicate the
data-fetch + stat-building path. Everything else (`_clean`, `_records`, `_month_bounds`,
`_parse_date`, `_err`, `_in_month`, `_resolve_user`, the `list_*` listing tools, and the
two write tools) is plumbing/formatting or write logic and should stay in the MCP layer
(though the write tools' currency conversion already delegates to `utils.to_eur`).

---

## 4. `queries.py` and `finance.py` finance-aggregation assessment

- **`queries.py`** contains **zero** finance aggregation. It is a cache wrapper
  (14 cached readers) plus the `db_version()`/`bump_db_version()` revision glue.
  It is the right place to hang a cached `services/finance_queries.py` facade
  (the service can call `queries.*` to get DataFrames, then compute).
- **`finance.py`** contains **pure math** and is the correct canonical home for
  domain math already: `annuity_payment`, `loan_schedule`, `derive_hourly_rate`,
  `calculate_early_repayment_surcharge`, term-deposit math, `portfolio_metrics`.
  It does **not** do DB reads (good). It should additionally host the pure
  DataFrame-level aggregations currently in `insights.py`
  (`month_over_month`, `top_category_this_month`, `unusual_expenses`,
  `days_until_budget_depleted`, `savings_projection`) so the render layer and MCP
  both call the same code.
- **`db.py`** already owns one derived calculation — `_recompute_savings_balances`
  (`db.py:1262-1316`) — which is correct (it is the persistence-layer invariant for
  balances). No change needed; the service reads its output.

---

## 5. Layering notes / blockers for a shared service

1. **Streamlit dependency.** `insights.py`, `utils.py`, `forecasting.py`, and
   `queries.py` all `import streamlit`. A `services/finance_queries.py` used by
   `mcp_server.py` (stdio/HTTP, non-Streamlit) **must not** import Streamlit at
   module top. Today `insights.py` (needed by MCP) imports `streamlit as st` at
   `insights.py:11` and `queries as q` at `:17`. Extraction must move the pure
   functions (`month_over_month`, `top_category_this_month`, `unusual_expenses`,
   `days_until_budget_depleted`, `savings_projection`, `build_narrative_stats`)
   out of the Streamlit-importing module, or MCP will keep a fork of the logic.
2. **`queries.db_version()` is Streamlit/session-bound.** `mcp_server.py` bypasses
   `queries.py` and reads `db.get_*` directly (it has no session state). The shared
   service must take **DataFrames or a user_id + explicit version** and call the
   uncached `db.get_*` readers, leaving caching to `queries.py` only on the UI side.
3. **Currency is dual-typed.** Aggregations are in `amount_eur`/`actual_eur`
   (canonical base); display conversion (`to_display`, `fmt`) is presentation-only.
   The service should return **EUR floats** and let each surface format them
   (MCP already does this; the UI passes `DC`/`rates` at render time).
4. **Future-date handling is inconsistent.** `insights.days_until_budget_depleted`
   and `notifications` explicitly exclude future-dated rows (`date <= today`);
   `dashboard.py`/`mcp_server` month sums do **not** (they sum the whole month
   including future rows). A canonical service must standardize this with a
   documented `include_future` flag (the existing tests in `tests/test_insights.py`
   pin the "future-dated rows don't count" behavior for the burn-rate path).
5. **Two "net" definitions.** `dashboard.py` net = income − expenses − savings;
   `mcp_server` net = income − expenses. The canonical `get_cashflow`/`get_expense_summary`
   must make the operand set explicit (see §2.6).

---

## 6. Proposed canonical service API surface

Target module: `services/finance_queries.py` (DB-read + aggregation) over the pure
math already in `finance.py` and the ML in `forecasting.py`. All functions take
`user_id` (and where useful, already-fetched DataFrames for reuse), return plain
dicts / DataFrames / floats in EUR, and contain **no** Streamlit import.

### Read / search

- `search_expenses(user_id, query, limit=20, month=None, category=None)` —
  case-insensitive over description/category/subcategory/notes (absorbs
  `mcp_server._search_expenses_impl:213-228`).
- `list_expenses(user_id, month=None, category=None, limit=None)` —
  absorbs `mcp_server._list_expenses_impl:198-210`.
- `list_income(user_id, month=None)` — absorbs `mcp_server._list_income_impl:231-240`.

### Summaries & aggregates

- `get_expense_summary(user_id, year, month)` — `{spent_eur, income_eur, net_eur,
  budget_total_eur, budget_remaining_eur, top_category, fun_money_eur,
  monthly_budget_eur}` (absorbs `mcp_server._expense_summary_impl:170-195`).
- `get_monthly_totals(user_id, year)` — per-month `{income, expenses, savings}` +
  cumulative (absorbs `dashboard.py:472-506` and `forecasting._monthly_totals`).
- `aggregate_spending(user_id, period, freq="M", dimension="category"|"merchant"|"subcategory")`
  — generic grouped sum (absorbs the ~11 `groupby(...).sum()` sites).
- `get_category_breakdown(user_id, year, month)` — `{category: amount_eur}`.
- `get_merchant_breakdown(user_id, year, month, n)` — top merchants.
- `get_top_categories(user_id, year, month, n)` — top-N by category.

### Comparisons

- `compare_spending_periods(user_id, end, current_len_days, prev_len_days)` →
  `{current, previous, change_pct, trend}` (absorbs `insights.py:360-374`,
  `notifications.py:512-520`, `dashboard.py:205-209`).
- `month_over_month(user_id, col, year, month)` — move pure logic from
  `insights.py:22-41`.

### Budgets

- `get_budget_vs_actual(user_id, year, month)` → per-category
  `{budgeted_eur, actual_eur, status, pct_used}` + a total row (absorbs
  `dashboard.py:349-386`, `notifications.py:307-340`, `budgets.py:123-160`).
- `get_budget_summary(user_id, year, month)` → `{budget_total_eur, spent_eur,
  remaining_eur}` (absorbs the `sum(effective_category_budgets)` triplication).

### Planning / wealth

- `get_cashflow(user_id, year, include_savings=True)` (absorbs §2.6).
- `get_savings_summary(user_id)` → total balance, interest earned, per-goal latest
  balance/target (absorbs §2.17).
- `project_savings_goal(savings_df, goal_name)` (move from `insights.py:90-140`).
- `get_locked_savings(user_id, asof=None)` / `get_term_deposit_value(account, asof)`
  (absorbs §2.18).
- `get_debt_summary(user_id)` → `{total_debt, monthly_payments, debt_free_date}`
  (absorbs §2.8).
- `get_recurring_monthly_total(user_id, prorate_start_month=True)` (absorbs §2.9).
- `get_net_worth(user_id, rates)` (absorbs §2.16).
- `get_portfolio_metrics(user_id, rates)` (absorbs §2.15; delegates to
  `finance.portfolio_metrics`).
- `get_hourly_rate(user_id, settings)` (facade over `finance.derive_hourly_rate`).
- `get_fun_money_status(user_id, settings)` (absorbs the dashboard/rewards
  allowance+bonus duplication).
- `get_travel_spending(user_id, year, pairs)` / `get_travel_pace(user_id, year)`
  (absorbs §2.14, including `travel.py`'s duplicated `_is_travel`).

### Intelligence facades

- `get_unusual_expenses(user_id, method="mean"|"ml")` (absorbs §2.20).
- `get_forecast(user_id, method)` — facade over `forecasting.forecast_next_month`
  plus the burn-rate fallbacks (absorbs §2.11/§2.12).
- `get_insights(user_id, settings)` — one entry point that builds the stats once
  and shares them between the Insights page, `llm.generate_narrative`, and MCP
  `_get_insights_impl` (removes the current MCP re-fetch + re-derive path).

### Where each existing symbol moves (quick reference)

| Current | New home |
|---------|----------|
| `insights.month_over_month`, `top_category_this_month`, `unusual_expenses`, `days_until_budget_depleted`, `savings_projection`, `build_narrative_stats` | `services/finance_queries.py` (or pure part of `finance.py`) |
| `mcp_server._expense_summary_impl` / `_list_expenses_impl` / `_list_income_impl` / `_list_savings_goals_impl` / `_search_expenses_impl` | `services/finance_queries.py`; MCP keeps only the `@server.tool()` wrappers + `_clean`/`_records` formatting |
| `dashboard.py` inline sums (KPI, monthly trends, cashflow, budget alerts/progress, fixed costs, debt, net worth) | call `services/finance_queries.py` |
| `notifications.check_and_send_budget_alerts` / weekly summary stats / loan reminders | call the service for numbers; keep email/HTML only |
| `gamification.get_budget_adherence_streak` | reuse `get_budget_vs_actual` per month |
| `llm.build_data_context` | build from `get_expense_summary` / `get_category_breakdown` / `get_savings_summary` / `get_debt_summary` / `get_recurring_monthly_total` instead of re-aggregating inline |

---

## 7. Duplication index (most-duplicated → least)

| Rank | Pattern | # implementations | Key sites |
|------|---------|-------------------|-----------|
| 1 | Category `groupby("category")["amount_eur"].sum()` | ~11 | dashboard 353/378/419/434, household 142, insights 51/306, llm 361, notifications 144/307/519 |
| 2 | "Sum this month's expenses" | ~6 | dashboard 218, insights 31, llm 359, budgets 53, mcp 181, notifications 294 |
| 3 | Budget total `sum(effective_category_budgets)` | 3 | mcp 178, gamification 99, forecast 125 |
| 4 | Budget vs actual comparison (`NEAR_LIMIT_THRESHOLD`) | 3 | dashboard 349-386, notifications 307-340, budgets 123-160 |
| 5 | Portfolio value (holdings → EUR) | 3 | portfolio 114-132, dashboard 323-337, savings 532-540 |
| 6 | Period-vs-previous comparison | 4 | insights 22/360, notifications 512, dashboard 205, llm 410 |
| 7 | Latest savings balance/target per goal | 4 | savings 511-516, dashboard 326, mcp 259-265, llm 376-384 |
| 8 | Active-loan payment/debt aggregate | 3 | dashboard 292-320, insights 376-389, loans 312-331, notifications 444-457 |
| 9 | Recurring fixed-cost total | 3 | insights 323-330, dashboard 269-289, llm 399-408 |
| 10 | Burn-rate projection | 2 | insights 67-87, forecast 85-114 |
| 11 | Travel pool membership match | 2 | utils.travel_spent 424-458, travel.py 111-137 |
| 12 | Fun-money allowance+bonus | 2 | dashboard 389-412, rewards 49-71 |
| 13 | Term-deposit locked value | 2 | savings 519-529, savings 562-570 |
