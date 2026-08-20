# Planning & Wealth — Domain Guide

> **Scope:** Budgets, Savings, Loans, Portfolio, Big Purchases, Travel, Forecast.  
> **Audience:** Agent 5 — Planning & Wealth specialist. All amounts stored as `_eur` base values; display conversion via `utils.get_rates / to_display`.  
> **Source of truth:** `finance.py`, `market_data.py`, `utils.py`, `db.py`, `app_pages/{budgets,savings,loans,portfolio,big_purchases,travel,forecast,rewards,dashboard}.py`, `insights.py`, `forecasting.py`.

---

## 1. Overview & Design Principles

### 1.1 What "Planning & Wealth" owns
| Pillar | Page | DB table(s) | EUR base column |
|--------|------|-------------|-----------------|
| Budgets | `app_pages/budgets.py` + dashboard | `budgets` + `user_settings.monthly_budget` | `budgeted_eur` |
| Savings | `app_pages/savings.py` | `savings` + `savings_accounts` | `target_eur`, `deposited_eur`, `balance_eur`, `amount_eur` |
| Loans | `app_pages/loans.py` | `loans` + `expenses` (payments via `loan_id`) | `principal_eur` |
| Portfolio | `app_pages/portfolio.py` | `holdings` + `holding_prices` | `cost_eur`, `value_eur` in snapshots |
| Big Purchases | `app_pages/big_purchases.py` | `big_purchases` | `price_eur` |
| Travel | `app_pages/travel.py` | `user_settings.travel_budget` + `travel_categories` | — |
| Forecast | `app_pages/forecast.py` | reads expenses + budgets | — |

### 1.2 Currency invariant
Every monetary write snapshots both the original `amount / currency` and the EUR base `amount_eur = to_eur(amount, currency, rates)` where `rates = get_rates(settings)` (`1 EUR = X` for that currency). Display never mutates history: `to_display_row` returns the original amount when `orig_currency == display_currency`; aggregates are converted with `to_display(eur, DC, rates)`. Invalid rates (zero, negative, NaN, infinite) are rejected by `_valid_rate`.

### 1.3 Planning horizon
- **Monthly:** Budgets, fun money, forecast burn-rate (salary cycle).
- **Yearly:** Travel budget, savings year-to-date, monthly trends.
- **Open-ended:** Loan amortization (up to 1200 months simulated, then analytic tail), term-deposit maturity, portfolio history.

---

## 2. Budget Architecture — Overall + Per-Category

### 2.1 Overall monthly budget (`user_settings.monthly_budget`)
- Single scalar `monthly_budget` (EUR, stored via `queries.save_settings`) edited in **Budgets page — Overall monthly budget** (`app_pages/budgets.py`).
- Form input is in the display currency (`to_display / to_eur` round-trip); cap `MAX_SAVINGS_TARGET` (10M) is also converted to display units to avoid `StreamlitValueAboveMaxError` for weak currencies (e.g. RSD).
- Dashboard KPI fallback: forecast uses `overall_bud` when >0; otherwise falls back to summed `effective_category_budgets` for the period's month (see §3, §13).
- Live progress in Budgets page: `spent_eur = expenses[year==today.year & month==today.month].amount_eur.sum()`; bar shows `spent / cur_eur` with remaining = `max(cur_eur - spent, 0)`.

### 2.2 Per-category budgets (`Budget` table)
```python
class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id","year","month","category","subcategory",
                                       name="uq_budget_scope"),)
    id, user_id, year, month, category, subcategory = ""  # "" = whole category
    budgeted_eur: float
```
- One row per **(user, year, month, category, subcategory)** scope. Rows are upserted by `db.add_budget` (`insert … on conflict update`); the unique constraint is enforced after a legacy dedupe migration (`tests/test_budget_scope.py` covers this).
- UI in `budgets.py`: pickers for category (`CAT_LIST`), subcategory (empty = entire category), year/month, amount in any `SUPPORTED_CURRENCIES` converted to EUR before insert. Stored via `to_eur`.
- Historical: taxonomy migration remaps legacy categories (e.g. `"Housing" → "Housing & Utilities"`, `"Food & Dining" → "Groceries"/"Dining Out"`) so old budget rows still match.

### 2.3 Where budgets surface
- Budgets page table + "This month's category progress" (current month rows → §4).
- Dashboard budget alerts + budget progress bars (selected period — §4).
- Insights: `month_over_month` and anomaly detection.
- Forecast: `total_budget` is the denominator for on-track checks (§13).

---

## 3. Effective Category Budgets & Budget-Scope Semantics

### 3.1 The overlap problem
A user can create both a whole-category row (`subcategory == ""`) and subcategory-specific rows for the same category/month. Naively summing all rows double-counts. The domain rule (enforced everywhere) is **subcategory rows are authoritative when present**.

### 3.2 `utils.effective_category_budgets(m_bud) -> dict[category, eur]`
```python
def effective_category_budgets(m_bud):
    if m_bud is None or m_bud.empty: return {}
    df["_sub"] = df["subcategory"].fillna("").astype(str).str.strip()
    for cat, g in df.groupby("category"):
        subs = g[g["_sub"] != ""]
        if not subs.empty:
            eff[cat] = float(subs["budgeted_eur"].sum())   # authoritative
        else:
            eff[cat] = float(g["budgeted_eur"].sum())       # whole-category applies
```
- Callers pass **only the month of interest** (already filtered to `year==Y & month==M`). Return is per-category, never per-subcategory.
- Test: `"Food & Dining" with {("","100"),("Groceries","30"),("Coffee & Snacks","20")}` ⇒ `eff["Food & Dining"]==50`, not 150 (`test_budget_scope.test_subcategory_rows_authoritative_over_category_row`).
- Empty / null subcategory handling: `fillna("")` + `strip()` so `None` and `""` and `"  "` all mean "whole category".

### 3.3 Budget-scope filtering (budget_scope = year × month × category × subcategory)
- Write path: `add_budget(user_id, {"year","month","category","subcategory","budgeted_eur"})` — normalize subcategory to `""` for whole category; callers (budgets form) translate `"(entire category)"` sentinel to `""`.
- Read path via `queries.budgets(user_id)` (cached on `db_version`); callers slice:
  ```python
  dfb[(dfb["year"]==Y) & (dfb["month"]==M)]          # Budgets page current-month progress
  dfb[(dfb["year"]==sy) & (dfb["month"]==sm)]        # Dashboard selected month
  dfb[dfb["year"]==sy] & then filter by month       # Dashboard budget-vs-actual outer join
  ```
- Deltas: `delete_budget(uid,bid)` (hard delete) via dialog; no soft-delete for budgets.
- Migration dedupe: pre-migration tables lacked the unique index; `init_db(force_migrate=True)` keeps the newest row per scope and creates `uq_budget_scope` (`tests/test_budget_scope.test_migration_dedupes_existing_overlaps`).

### 3.4 Fun & travel pools are NOT budgets
`fun_categories` and `travel_categories` are **pools** used only for `fun_spent` / `travel_spent` (see §12–13). They do not interact with `effective_category_budgets`.

---

## 4. Budget Progress, Alerts & Dashboard Integration

### 4.1 Budgets page — "This month's category progress"
```python
cur_rows = dfb[(dfb["year"]==_today.year) & (dfb["month"]==_today.month)]
eff = effective_category_budgets(cur_rows)
exp = dfe[(dfe["date"].dt.year==_today.year) & (dfe["date"].dt.month==_today.month)]
cats_with_sub = set(cur_rows[cur_rows["subcategory"].str.strip()!="" ]["category"])
for category, budgeted in eff.items(): ...
  pct = min(spent_for_cat / budgeted, 1.0); st.progress(pct)
```
- Only categories with a budget row render a bar; overspend is clamped to 100% in the bar but shown as `spent of budgeted (pct%)`.

### 4.2 Dashboard — year/month picker + budget alerts
- Filters helpers `flt(df)` / `prev_flt(df)` slice expenses/income by the selected `sy` + optional `sm` ("All months").
- **Alerts** (personal view only, non-empty budgets + non-empty selected-month expenses):
  ```python
  bf = dfb[dfb["year"]==sy];  bf = bf[bf["month"]==sm] if sm>0 else bf
  cb = effective_category_budgets(bf);  ca = exp.groupby("category")["amount_eur"].sum()
  for c in ca.index:
    if cb.get(c,0)>0 and ca[c] >= cb[c]*NEAR_LIMIT_THRESHOLD (0.85):
      level = "error" if ca[c]>cb[c] else "warning"
      # error: "Over by X", warning: "N% used"
  ```
  Uses `NEAR_LIMIT_THRESHOLD = 0.85` from `utils`.
- **Progress bars** (personal view, `sm>0`):
  ```python
  bf3 = dfb[(dfb["year"]==sy) & (dfb["month"]==sm)]; cb3 = effective_category_budgets(bf3)
  for c in ca3.index: if cb3.get(c,0)>0: pct=min(ca3[c]/cb3[c],1.0); st.progress(pct)
  ```
  Household view hides all budgets/progress (personal-only section with caption).

### 4.3 Dashboard — Budget vs Actual chart + metrics
- `Budget vs actual` grouped bars: outer join of `exp.groupby("category")` vs `effective_category_budgets(bf2)`; categories with no budget get `budgeted_eur=0`. Color by status: `Over budget (#E94560) / Near limit (#F4A261) / On track (#00B050)`.
- KPIs: `Income = inc.actual_eur.sum()`, `Expenses = exp.amount_eur.sum()`, `Saved = svyr.deposited_eur.sum()`, `Net = ie - ee - sd`, `Savings rate = sd/ie*100` (all EUR, formatted with `fmt(eur,DC,rates)`).
- Progress semantics note: Budget progress uses **calendar month** (`date.dt.year/month`). Forecast progress uses **salary cycle** (`compute_salary_cycle` — §5). An agent must not conflate them.

---

## 5. Salary Cycle & Period Logic

### 5.1 `utils.compute_salary_cycle(today, salary_day=10, latest_salary=None) -> (period_start, period_end)`
- If `latest_salary` (date of most recent `income_type=="Salary"` or `source=="Primary Salary"`) is provided, `period_start = latest_salary` (real payroll date).
- Otherwise fallback to a fixed $salary_day$ anchor (default 10):  
  - `today.day >= salary_day` ⇒ start = clamped(today.year, today.month)  
  - `today.month>1` ⇒ start = clamped(today.year, today.month-1)  
  - else ⇒ start = clamped(today.year-1, 12)  
  Clamping uses `min(salary_day, monthrange(y,m)[1])` so days 29–31 never raise.
- `period_end = date(next_y, next_m, min(start.day, last_day))` minus one day — i.e. the day before the next cycle starts. Hence periods are half-open and month-end days are safe.
- Consumers: **Forecast** (§13) and **Expenses/Incomes salary-cycle grouping** (not budget progress).

### 5.2 Related helpers
- `filter_started_templates(df, year, month)`: recurring templates count only from their `start_month` (`"YYYY-MM"`; lexically comparable). `None`/blank = always active (legacy).
- `days_until_budget_depleted` in `insights.py` — burn-rate estimate `remaining_budget / daily_avg` where `daily_avg = spent / max((today-period_start).days+1,1)`; uses `today` date bounds to exclude future-dated expenses.

---

## 6. Savings — Goals (Savings table)

### 6.1 Data model
```python
class Savings(Base):
    id, user_id, date, goal_name          # goal_name is the partition key
    target_eur: float                     # latest target for that goal (copied to each entry)
    deposited, currency, deposited_eur    # one transaction (negative = withdrawal)
    interest_rate: float                  # annual %, latest value per goal
    balance_eur: float                    # rolled-forward value (computed on read by db layer)
    notes
    # soft-delete: is_deleted, deleted_at
class UserSettings.savings_goals  # optional list overriding SAVINGS_GOALS
```
- **Goals** are not a separate table — they are the distinct `goal_name` values in `savings`. Known names: `SAVINGS_GOALS = ["Emergency Fund","Vacation / Travel","Investment Account","Down Payment","Other"]` plus user-created names.
- Each row is a **deposit**; withdrawals are negative `deposited_eur` rows. First deposit creates the goal (form branch `gn_sel=="➕ New goal..."`).
- `goal_rows(df, goal_name)` = filtered + `sort_values("date")`; `goal_attrs(rows)` returns `(tgt, rate, currency)` from the latest row (target taken from last non-zero `target_eur`).

### 6.2 Transaction UX (`savings.py` dialogs)
- **Deposit dialog** (`deposit_dialog`): inherits goal's current `target_eur / interest_rate / currency`; amount entered in that currency and converted with `to_eur`. Duplicate prevention: check `date==d & goal_name==goal & deposited_eur==de`.
- **Withdraw dialog** (`withdraw_dialog`): amount capped by `balance_eur` (converted to display); guard `abs(de) > current_bal` prevents negative balance. Logged as negative deposit.
- **Entry form** (bottom of page): new-goal branch collects `target (display currency) + interest_rate`; existing-goal branch reuses `goal_attrs`. Protection: zero-amount rejected; balance check on negative `de`.
- **Edit goal** (`edit_goal_dialog`): renames via `rename_savings_goal(uid, old, new)` (fails if name collision — two goals would merge), then `update_savings_goal` bulk-updates `target_eur / interest_rate` across all rows for that goal. Balances recompute automatically on next read — deposited rows are never rewritten.
- **Delete/Restore**: `soft_delete_savings` / `restore_savings` by entry id; goal deletion is `soft_delete_savings_goal` (marks all rows as deleted).

### 6.3 Balance computation
Balances are **not stored naively** — they are recomputed per goal by chaining deposits with monthly compounding at the goal's latest `interest_rate`. The DB layer rolls each entry forward to today at the latest rate, so `rows.iloc[-1].balance_eur` is always the current value in EUR used for KPIs. The insight helper `savings_projection` then extrapolates from the last three deposits' average.

---

## 7. Term Deposits — SavingsAccounts

### 7.1 Data model
```python
class SavingsAccount(Base):
    """Fixed-term deposit under a savings goal."""
    id, user_id, goal_name, name
    amount, currency, amount_eur           # locked principal
    annual_rate: float                     # fixed %, compounded monthly
    start_date, maturity_date: date
    status: "active" | "closed"
    notes; is_deleted, deleted_at
```
- Lives **under a goal** (`goal_name` FK-like); e.g. "1-year term @ 4%" under "Emergency Fund".
- Created in `savings.py` under "Term deposits" per goal; edited via `update_savings_account`, soft-deleted via `soft_delete_savings_account`.

### 7.2 Valuation math (`finance.py`)
```python
def months_between(start, end):  # whole calendar months, 0 when end<=start
    return (end.year-start.year)*12 + (end.month-start.month)
def compound_months(amount, annual_rate_pct, months):
    if amount<=0 or months<=0: return round(amount,2)
    return round(amount * (1+annual_rate_pct/100/12)**months, 2)
def maturity_value(amount, annual_rate_pct, start, maturity):
    return compound_months(amount, annual_rate_pct, months_between(start, maturity))
def accrued_value(amount, annual_rate_pct, start, asof=None):
    asof = asof or date.today()
    if asof<=start: return round(amount,2)
    return compound_months(amount, annual_rate_pct, months_between(start, asof))
```
- Compounding is **monthly**, whole months only — no partial-month pro-rata. Callers cap `asof` at `maturity_date`:
  ```python
  end = a["maturity_date"].date() if a["maturity_date"].date() < today else today
  accrued_value(float(a["amount_eur"]), float(a["annual_rate"]), a["start_date"].date(), end)
  ```
- Legacy rows lacking dates contribute principal only (`amount_eur`).

### 7.3 Lifecycle
- **active** → accrues until maturity; value grows via `accrued_value` and contributes to locked total.
- **closed** → excluded from locked totals; withdrawal into the parent goal is a separate savings deposit (handled by teaching, not by DB cascade).
- UI: per-goal accordion "Term deposits" showing start/maturity/rate/value; maturity display uses `maturity_value`; current value uses `accrued_value`.

---

## 8. Savings KPIs, Interest & Projections

### 8.1 Yearly KPIs (`savings.py` — when `dfs` non-empty)
```python
# per goal (sorted by date)
bal = rows.iloc[-1].balance_eur; dep_sum = rows["deposited_eur"].sum()
interest_total += bal - dep_sum;  total_balance += bal
saved_year = ydf["deposited_eur"].sum()   # ydf = dfs[date.year==today.year]
locked_eur = sum(accrued_value(a.amount_eur,a.annual_rate,a.start_date,end) for a in accs if status!="closed")
portfolio_value = sum(qty * (last_price/rate_if_not_EUR) for each holding)
```
Metrics row (6 columns):
| Metric | Source |
|--------|--------|
| Total balance | `total_balance + locked_eur` |
| Saved this year | `saved_year` |
| Interest earned | `interest_total` (balance minus net deposits, across goals) |
| Locked (term) | `locked_eur` |
| Portfolio | market value from holdings (see §11) |
| Active goals | `nunique goal_name` |

### 8.2 Goal progress cards
For each goal (`goals = sorted(nunique)`):
```python
bal, td = rows.iloc[-1].balance_eur, rows["deposited_eur"].sum()
interest = bal - td
tgtv, grat, gcur = goal_attrs(rows)
g_locked = sum(accrued_value(...) for accs where goal_name==g)
pct = min((bal+g_locked)/tgtv*100,100) if tgtv>0 else 0
avg_dep = rows["deposited_eur"].tail(3).mean()
```
Card shows: `balance (+ locked), target, interest earned, rate, ~avg/mo` plus `st.progress(pct/100)`. Projection string appended when available.

### 8.3 Projections (`insights.savings_projection(dfs, goal_name)`)
- Uses last 3 months' average deposit as monthly run-rate; at goal rate, solves for months to reach `target_eur` from current `balance_eur` (exponential when rate>0, linear otherwise).
- Returns `{current_balance, target, months_to_goal, projected_date}` rendered as `"🎯 Goal in ~Nmo (Mon YYYY)"`.
- Guard: returns `months_to_goal=None` when no target or insufficient history.

---

## 9. Loans — Data Model & Annuity

### 9.1 Data model
```python
class Loan(Base):
    id, user_id, name
    principal, currency, principal_eur
    annual_rate: float (%)               # fixed
    start_date: date; term_months: int; payment_day: int (1–31)
    status: "active" | "paid_off"
    early_repayment_surcharge_type: "fixed" | "percent"
    early_repayment_surcharge_value: float
    notes
```
- Form in `loans.py`: create loan with `principal (any currency → to_eur)`, `annual_rate`, `term_months (1–600)`, `payment_day (1–31 clamped)`, surcharge type/value. Duplicate prevention: same `name + principal_eur rounded 2dp` guarded.
- Edit via `update_loan` (all scalar fields rewritable); logged payments stay untouched — payoff recomputes. Delete via `delete_loan` (payments as expenses remain).
- Payments are **not** rows in `loans` — they are **expenses** with `loan_id` (see §10). This unifies cash-flow tracking.

### 9.2 Annuity payment (`finance.annuity_payment`)
```python
def annuity_payment(principal, annual_rate_pct, term_months):
    if principal<=0 or term_months<=0: return 0.0
    r = annual_rate_pct/100/12
    if r==0: return principal / term_months
    return principal * r*(1+r)**term_months / ((1+r)**term_months - 1)
```
- Classic fixed-rate amortized payment. Used to label "monthly payment ~X" on loan creation and as the baseline in `loan_schedule`.
- Zero-rate branch is exact division (no division-by-zero). Non-finite inputs coerced upstream.

### 9.3 Early-repayment surcharge
```python
def calculate_early_repayment_surcharge(amount, mode, value):
    amount, value = max(float(amount or 0),0), max(float(value or 0),0)
    return amount*value/100 if mode=="percent" else value
```
- Per-loan setting: `surcharge_type` in {`fixed`, `percent`} + `surcharge_value` (percent 0–100, fixed up to `MAX_SAVINGS_TARGET`). Stored on `Loan` and collected per extra payment as `surcharge_eur` (see §10). Surcharge counts as interest, not principal reduction.

---

## 10. Loans — Due Dates & Amortization Schedule

### 10.1 Due-date clamping — `_first_due` / `_next_due`
```python
def _first_due(start, payment_day):
    if payment_day >= start.day: anchor = start
    else: anchor = date(next_month(start.year,start.month),1)  # never phantom month
    return date(anchor.year, anchor.month, min(payment_day, monthrange(...)[1]))
def _next_due(start, payment_day, k):
    first = _first_due(start, payment_day)
    year, month = first.year + (first.month-1+k)//12, (first.month-1+k)%12 +1
    return date(year, month, min(payment_day, monthrange(year,month)[1]))
```
- `payment_day=31` in February clamps to 28/29; `loan start Jan 31 + payment_day 1` ⇒ first due **Feb 1** (not Jan), preventing a phantom accrual month before the loan existed.
- `_next_due` indexes from `first_due + k months` where each month clamps independently.

### 10.2 `loan_schedule(principal, annual_rate_pct, term_months, start_date, payment_day, payments, asof=None) -> dict`
**Signature:** `payments` may be legacy tuples `(date, amount_eur[, surcharge_eur])` or dicts `{date, amount_eur, surcharge_eur, principal_eur}` (where `principal_eur = amount - surcharge`). `asof` defaults to today; future payments (`p_date > asof`) are ignored.

**Bucketing:** Payments land in the calendar-month bucket anchored to `first_due`, not `start`:
```python
k = (p_date.year - first_due.year)*12 + (p_date.month - first_due.month)
due = _next_due(start, payment_day, max(k,0))
by_due[due] += principal_paid; surcharge_paid += surcharge
```

**Simulation (through the current calendar month):**
```python
cur_k = (asof.year-first_due.year)*12 + (asof.month-first_due.month)
k=0; bal=principal; interest_paid=0; months_paid=0
while bal>0.005 and k<=max(cur_k,0) and k<1200:
  due=_next_due(...,k); bucket_pay=by_due.get(due,0)
  if due<=asof or bucket_pay>0.005:
    interest_due=bal*r; interest_paid+=interest_due; bal+=interest_due; months_paid+=1
  bal-=bucket_pay
  if bal<=0.005: bal=0; payoff=due; break
  k+=1
```
- **Interest booking rule:** a month's interest is booked when **either** its due date has passed **or** a payment is applied to it (whichever first). This fixes the "burst payments before first due accrue zero interest" regression: every applied payment books one month's interest immediately, even before the due day. Correctness guard: early payments are idempotent across snapshots — a Jan 20 payment for a Jan 25 due books 12€ interest both `asof=Jan 21` and `asof=Jan 26` (single booking test in `test_finance`).

**Analytic tail (remaining balance >0):**
- Zero rate: `remaining_months = ceil(bal/monthly)`
- Non-zero rate with `monthly <= bal*r` (payment doesn't cover interest): `remaining_months=0` (no finite payoff)
- Otherwise: `ceil(-log(1 - bal*r/monthly) / log(1+r))`; clamp to ≥1.
- Payoff date: `_next_due(start, payment_day, next_idx + remaining-1)` where `next_idx` is the next unpaid month slot (checks if current due's payment already landed).

**Returns (all rounded to 2dp unless date):**
```python
{
  "monthly_payment": annuity(principal,rate,term),
  "remaining_balance": bal,
  "remaining_months": ...,
  "payoff_date": date | None,
  "total_interest_paid": interest_paid + surcharge_paid,
  "scheduled_interest_paid": interest_paid,
  "total_surcharge_paid": surcharge_paid,
  "total_interest_remaining": monthly*remaining - bal,
  "next_payment_interest": min(max(bal*r,0), monthly),
  "next_payment_principal": min(max(monthly-next_interest,0), bal),
  "months_paid": int,
  "total_cost": principal + total_interest + interest_remaining,
}
```
- **Surcharge as interest:** `total_interest_paid = scheduled + surcharge`; only `principal_paid = amount - surcharge` reduces `bal`. Overpay with surcharge (e.g. 1000 loan, 1010 payment with 10 surcharge) pays off with 810 bal after 200 total payment, not 800.
- **Payoff recomputes from real payments:** loans page builds `payments` from **expenses where `expense.loan_id == loan.id`** (via `get_loan_payments` / `queries.loan_payments`). Missed/partial/extra payments automatically extend or shorten `remaining_months` / `payoff_date`. Editing loan terms re-simulates against the same expense history.

### 10.3 Loans page wiring
- KPI per loan: remaining balance / remaining months / payoff date / interest paid so far.
- Payment logging: expenses form with linked `loan_id`; surcharge field computed from loan's surcharge type/value via `calculate_early_repayment_surcharge(amount, type, value)`.
- Reminder integration: loans expose `payment_day` to notification scheduling (Settings → Notifications, `due_day` within 7 days via `_next_due`).

---

## 11. Portfolio — Holdings, Prices & Market Data

### 11.1 Data model
```python
class Holding(Base):
    id, user_id, symbol                  # normalized (uppercased), e.g. "AAPL", "VWCE.DE"
    name, quantity: float, currency
    cost_total: float                    # original currency
    cost_eur: float                      # EUR base
    last_price: float                    # last known price in holding's currency
    last_price_date: datetime | None     # UTC
class HoldingPrice(Base):
    id, holding_id, date: date, price: float
    quantity: float                      # quantity at snapshot time
    rate: float                          # 1 EUR = X at snapshot time
    value_eur: float                     # quantity*price/rate (exact EUR value then)
```
- **avg_cost** is not stored as a column — derived as `cost_total / quantity` (or `cost_eur / quantity`).
- Write: `add_holding` normalizes symbol to upper, stores `cost_eur` via `to_eur`, optionally seeds `last_price` with an immediate fetch. Duplicate guard: same `symbol` per user blocked.

### 11.2 Pricing — Yahoo primary, Stooq fallback
```python
# market_data.py
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
STOOQ_URL = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"
def fetch_price_yahoo(sym): ... # meta.regularMarketPrice else last close; reject <=0
def fetch_price_stooq(sym): ... # CSV row "Close"; reject None/""/"N/D" and <=0
def fetch_price(sym): return fetch_price_yahoo(sym) or fetch_price_stooq(sym)
def prices_are_stale(df): # true when any holding missing date or (now_utc - ts).days >= PRICES_MAX_AGE_DAYS (1)
def refresh_prices_if_due(user_id, force=False, cached=True):
    if empty or (not force and not stale): return 0, False
    get = _fetch_cached if cached else fetch_price
    # batch loops holdings: update_holding(last_price, last_price_date=utcnow) + add_holding_price(snapshot)
    return updated, success
```
- **Last known survives failure:** any network exception or bad Close logs a warning and returns `None`; caller skips `update_holding`, so previous price persists. CSV parsing tolerates missing rows. `.01`
- **Failure cache:** `_fetch_cached = st.cache_data(ttl=1800)` caches per-symbol including failures (`None`) for 30 minutes, avoiding hammering.
- **Rate snapshot:** each price write also records `quantity` and `get_rates()[currency]` so `value_eur` in `holding_prices` stays exact even if holdings or rates change later.

### 11.3 Background refresh
```python
def maybe_refresh_in_background(user_id):
    holdings = get_holdings(user_id)
    if empty or not stale: return
    if not _refresh_lock.acquire(blocking=False): return
    def _worker():
        try:
            updated, changed = refresh_prices_if_due(user_id, force=False, cached=False)
            if changed: bump_data_revision(user_id)
        finally: _refresh_lock.release()
    threading.Thread(target=_worker, daemon=True).start()
```
- Called on login when stale (≥1 day old or missing). Guarded by a process-wide `threading.Lock` preventing overlapping refreshes; daemon thread never blocks UI. On success, `bump_data_revision` invalidates cached query readers so new prices render immediately.
- Manual refresh button on Portfolio page calls `refresh_prices_if_due(force=True, cached=False)` with spinner and success/error toasts — failures keep last known values.

### 11.4 Portfolio math (`finance.portfolio_metrics`)
```python
def portfolio_metrics(holdings):
    for h in holdings:
        qty = float(h.get("quantity") or 0); price = float(h.get("last_price_eur") or 0); cost=float(h.get("cost_eur") or 0)
        value += qty*price; invested += cost; live_count += (price>0)
    gain = value - invested; gain_pct = gain/invested*100 if invested>0 else 0
    return {"value": value, "invested": invested, "gain": gain, "gain_pct": gain_pct, ...}
```
- Portfolio page computes per-holding `price_eur = last_price / rates[cur]` (when `cur != EUR`) then `value_eur = qty*price_eur`; feeds dicts with `last_price_eur=price_eur` into `portfolio_metrics` for totals, plus allocation pie and value-over-time chart from `holding_prices`.

---

## 12. Big Purchases — Priority Matrix & Lifecycle

### 12.1 Data model
```python
class BigPurchase(Base):
    id, user_id, name, category="Other"   # category from CAT_LIST
    price (original), currency, price_eur
    usage_hours: float                     # expected use, hours per month
    importance: int 1–5                    # slider (1 nice-to-have → 5 life-changing)
    status: "wishlist" | "saving" | "bought"
    sort_order: int                        # draggable board position
    notes
```
- Created in `big_purchases.py` with price in any currency (converted to EUR). Duplicate guard on `name+category`.

### 12.2 4-Quadrant priority (`utils.classify_quadrant` + finance hourly rate)
```python
def derive_hourly_rate(income_rows, salary_eur=0):
    # Only Hourly rows with hours>0 and finite actual_eur contribute, weighted total.
    # Fallback: salary_eur/160 when no valid hourly income.
    return total_actual_eur/total_hours if total_hours>0 else (salary_eur/160 or 0), source

def classify_quadrant(work_hours, usage_hours, median_work, median_usage):
    high_usage = usage_hours > median_usage; high_work = work_hours > median_work
    if high_usage and not high_work: return "Quick wins"    # 🟢
    if high_usage and high_work:     return "Plan & save"   # 🔵
    if not high_usage and not high_work: return "Maybe later" # ⚪
    return "Reconsider"                                      # 🔴
```
- **work_hours** = `price_eur / hourly_rate` where hourly_rate comes from `derive_hourly_rate(income_rows, salary_eur)` (weighted hourly income, else salary/160). If `hourly_rate==0`, matrix is suppressed (no meaningful cost).
- **Medians** are computed over pending wishlist items (`status!="bought"`); with <2 items defaults `med_work=20, med_usage=10` so single items still land in a quadrant.
- Scatter matrix: `x=usage_hours`, `y=work_hours`, `color=quadrant`, `size=importance`, median lines as dashed reference. Colors from `QUADRANT_COLORS`.
- Caption key: 🟢 Quick wins (high use, cheap), 🔵 Plan & save (high use, expensive), ⚪ Maybe later (low use, cheap), 🔴 Reconsider (low use, expensive).

### 12.3 Wishlist → Expense handoff
- Confirm dialog (`confirm_purchase_dialog`): marks `status="bought"` and logs an **expense** on today's date: `category` = big purchase category, `description = "{name} (big purchase)"`, `amount/price_eur` recomputed at confirm time with **current rates** (not the stale snapshotted `price_eur`) via `to_eur(price, currency, rates)`.
- Draggable board: grouped by category with `draggable_card_board(groups)`, validated by `validate_grouped_order` (complete, no duplicates, correct family). Persists `sort_order` + category reassignments via `update_big_purchase`.

---

## 13. Travel Budget & Forecast Integration

### 13.1 Travel — yearly pool vs yearly budget
**Settings:** `user_settings.travel_budget` (yearly EUR cap) + `travel_categories` (list of `Category › Subcategory` pairs, bare category, or bare subcategory for backward compat). Defaults `DEFAULT_TRAVEL_CATEGORIES = ["Travel"]`.

**Pool matching (`utils.travel_spent`):**
```python
def travel_spent(expenses_df, pairs, year):
    if empty or no pairs: return 0.0
    y = expenses[date.year==year]
    mask = False
    for pair in pairs:
      if " › " in pair: cat,sub = pair.split(" › ",1)  # trailing " › " means whole category
        if sub: mask |= (y.category==cat & y.subcategory==sub)
        else:   mask |= (y.category==cat)
      else:
        bare=pair.strip()
        if bare in CATEGORIES: mask |= (y.category==bare)
        elif bare in ALL_SUBCATS: mask |= (y.subcategory==bare)
    return float(y[mask].amount_eur.sum())  # union — never double-count overlapping pairs
```
- Overlapping selectors e.g. `["Travel › ", "Travel › Flights & Trains"]` count flights exactly once (union mask). Stored forms: `"Category › (all)"` in UI ↔ `"Category › "` in DB (mapping helpers `_to_display` / reverse).

**On-pace check (`travel.py`):**
```python
budget = float(settings.travel_budget or 0); spent = travel_spent(dfe, pairs, year)
year_pct   = today.timetuple().tm_yday / (366 if isleap(year) else 365) *100
budget_pct = spent/budget*100 if budget>0 else 0
# >100% budget_pct → error "exceeded by X"
# elif budget_pct > year_pct → warning "spending faster than year passing"
# else → success "On pace! X left"
st.progress(min(budget_pct,100)/100, text=f"{budget_pct:.0f}% used")
```
- Monthly breakdown bar chart: groups travel expenses by `date.to_period("M")`.

**Link to Vacation savings goal:**
Panel reads `savings` where `goal_name in ["Vacation / Travel","Vacation"]`; shows latest `balance_eur` as "Saved towards vacation" and caption "Deposit into Vacation / Travel to grow this." No automatic transfer — purely informational linkage. Encourages dual tracking: expense pool (consumption) vs savings goal (pre-funding).

### 13.2 Forecast — salary-cycle projection vs budget
**Page:** `app_pages/forecast.py` — projects **current salary-cycle** spending and compares against the monthly budget.

**Salary detection:** `salary_rows = income[income_type=="Salary"]; fallback source=="Primary Salary"; if empty → SALARY_DAY=10 fixed`. `compute_salary_cycle(today, SALARY_DAY, latest_salary_date)` gives `(period_start, period_end)`; display includes `days_in_period / days_elapsed / days_remaining`.

**Projection methods** (segmented control):
| Method | Formula | Fallback |
|--------|---------|----------|
| Period average (default) | `daily_avg = total_spent / days_elapsed`; `projected = daily_avg * days_in_period` | — |
| 7-day average | `daily_avg = sum(last 7d) / min(days_elapsed,7)`; empty window falls back to period average (prevents 0 projection) | period average |
| ML model (ETS) | `forecast_next_month(dfe)["total"]` over ≥6 months history; shows `80% range lower–upper` | period average when `fallback==True` or insufficient history |

All methods operate **only on `period_exp`** = expenses where `period_start <= date <= period_end` (not calendar month).

**Budget comparison:**
```python
total_budget = overall_bud if overall_bud>0 else sum(effective_category_budgets(bf_m).values())
# where bf_m = budgets for period_start.year/month
over_under = projected - total_budget; on_track = total_budget==0 or projected<=total_budget
# metrics: Spent so far, Daily average, Projected total, Monthly budget (each with alt-currency delta)
# progress: pct_spent = min(total_spent/total_budget*100,100); st.progress(pct_spent/100)
```
- No budget → warning "Go to Settings → Budget to set one."
- On track → success "X under budget."
- Overspend risk → error "X over budget. Target: (total_budget - total_spent)/max(days_remaining,1) per day".
- Per-category ML table: `ml_result["by_category"]` rendered as dataframe when not fallback.

**Relation to other projections:**
- Forecast §13 is a **burn-rate extrapolation** of the *current* period.
- `days_until_budget_depleted` in `insights.py` is the same burn rate expressed as days-left.
- `savings_projection` in §8 is a *term* projection (months to goal), not a spend projection — do not mix them.

---

## Appendix — Key Imports & Queries

```python
# queries (cached on bump_data_revision)
from queries import expenses, budgets, savings, savings_accounts, holdings, holding_prices, loans, loan_payments

# utils
from utils import (effective_category_budgets, compute_salary_cycle, fun_spent, travel_spent,
                   classify_quadrant, DEFAULT_FUN_CATEGORIES, DEFAULT_TRAVEL_CATEGORIES,
                   CATEGORIES, CAT_LIST, ALL_SUBCATS, get_rates, to_eur, to_display, fmt,
                   NEAR_LIMIT_THRESHOLD, CHART_COLORS, QUADRANT_COLORS)

# finance
from finance import (annuity_payment, derive_hourly_rate, calculate_early_repayment_surcharge,
                     _first_due, _next_due, loan_schedule,
                     months_between, compound_months, maturity_value, accrued_value, portfolio_metrics)

# market_data
from market_data import (fetch_price_yahoo, fetch_price_stooq, fetch_price,
                         prices_are_stale, refresh_prices_if_due, maybe_refresh_in_background)

# insight/forecasting
from insights import savings_projection
from forecasting import forecast_next_month
```

**Tests of record:** `test_finance.py` (annuity, surcharge, loan schedule edge cases incl. burst payments + idempotent early booking + Feb 31 clamping), `test_savings.py` / `test_savings*` (goal roll-forward), `test_budget_scope.py` (effective scope), `test_portfolio_snapshots.py` (quantity/rate snapshot), `test_fun_travel.py` (pool union), `test_market_data.py` (Yahoo→Stooq fallback).

> When in doubt, trust EUR base values and the salary cycle. Calendar-month budgets and salary-cycle forecasts are intentionally different periods — bridging them ad hoc creates phantom overspend or phantom headroom.
