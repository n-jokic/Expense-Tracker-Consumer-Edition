# Currency & Taxonomy — Domain Reference

> **Sources:** `utils.py` (731 lines), `rates.py` (117 lines), `db.py` (`_migrate_taxonomy`, `_migrate_settings_taxonomy`, `_migrate_budgets_taxonomy`, `sync_core` remap), `sync_core.py` (`validate_fields`), tests: `test_taxonomy_migration.py`, `test_currency.py`, `test_rates.py`, `test_rate_validation.py`, `test_formula_injection.py`, `test_fun_travel.py`

---

## 1. Purpose & Scope

This document is the authoritative reference for the Expense Tracker **category taxonomy** and **currency subsystem**. It covers:

- The single canonical `CATEGORIES` dict (13 groups, ~45 subcategories) and its derived sets (`CAT_LIST`, `ALL_SUBCATS`).
- The complete legacy→current taxonomy migration table (`TAXONOMY_MIGRATION`, 40+ rows) and how it is applied at rest (`db._migrate`) and on the wire (`sync_core.validate_fields`).
- The supported-currencies map, default rates table, and symbol helper.
- The currency engine: rate validation, EUR-base storage, display conversions, and formatting.
- Live-rate refresh: Frankfurter → open.er-api fallback, 3-day staleness, 30-minute failure cache, and the force flag.
- Salary-cycle math, fun-money / travel pools, quadrant classification, Excel injection guard, and grouped-order validation.

All code references point at `utils.py` unless noted as `rates.py`, `db.py`, or `sync_core.py`.

---

## 2. Canonical Category Taxonomy — `CATEGORIES`

`utils.CATEGORIES: dict[str, list[str]]` — **12 top-level groups** (the code comment says 13 including the legacy count; the live dict has 12 keys). Keys are category names; values are ordered subcategory lists. Derived helpers:

```python
CAT_LIST    = list(CATEGORIES.keys())                          # 12 categories, insertion order
ALL_SUBCATS = sorted({s for subs in CATEGORIES.values() for s in subs})  # ~45, alphabetically sorted
```

| # | Category | Subcategories |
|---|----------|---------------|
| 1 | **Housing & Utilities** | `Rent / Mortgage`, `Electricity`, `Gas & Heating`, `Water`, `Internet & Phone`, `Home Insurance`, `Building Maintenance`, `Furniture & Appliances` |
| 2 | **Groceries** | `Groceries` |
| 3 | **Dining Out** | `Restaurants & Takeaway`, `Coffee & Snacks`, `Food Delivery`, `Work Lunch` |
| 4 | **Transport** | `Fuel`, `Public Transit`, `Taxi / Uber`, `Car Insurance`, `Car Maintenance`, `Parking`, `Tolls` |
| 5 | **Travel** | `Flights & Trains`, `Hotels & Lodging`, `Tours & Activities` |
| 6 | **Health** | `Gym & Fitness`, `Pharmacy`, `Doctor / Specialist`, `Dental`, `Supplements`, `Mental Health` |
| 7 | **Entertainment** | `Streaming Services`, `Cinema & Theater`, `Concerts & Events`, `Going Out`, `Hobbies`, `Books & Courses` |
| 8 | **Shopping** | `Clothing & Accessories`, `Beauty & Skincare`, `Haircut & Grooming`, `Gifts` |
| 9 | **Subscriptions & Software** | `Subscriptions & Software` |
| 10 | **Fees & Taxes** | `Taxes & Fees`, `Bank & ATM Fees` |
| 11 | **Loans & Debt** | `Loan Repayment`, `Interest`, `Credit Card`, `Other Debt` |
| 12 | **Other** | `Charity & Donations`, `Miscellaneous` |

Related constants: `INCOME_SOURCES` (5), `INCOME_TYPES` (7), `SAVINGS_GOALS` (5), `CHART_COLORS` (8 hex values), `CAT_LIST`, `ALL_SUBCATS`.

### 2.1 Default pools

```python
DEFAULT_FUN_CATEGORIES    = ["Entertainment", "Dining Out"]
DEFAULT_TRAVEL_CATEGORIES = ["Travel"]   # bare category = whole category counts; pairs use "Category › Subcategory"
```

---

## 3. Full `TAXONOMY_MIGRATION` Table (old → new)

`utils.TAXONOMY_MIGRATION: list[tuple[str,str,str,str]]` — each row is `(old_cat, old_sub, new_cat, new_sub)`. An empty subcategory `""` means **the whole category** (used for bare-category rows). Identity rows (old == new) are included for completeness but skipped during `db._migrate`.

| # | old_cat | old_sub | new_cat | new_sub | Notes |
|---|---------|---------|---------|---------|-------|
| 1 | `Housing` | `Rent / Mortgage` | `Housing & Utilities` | `Rent / Mortgage` | Housing → Housing & Utilities, sub unchanged |
| 2 | `Housing` | `Electricity` | `Housing & Utilities` | `Electricity` | |
| 3 | `Housing` | `Gas & Heating` | `Housing & Utilities` | `Gas & Heating` | |
| 4 | `Housing` | `Water` | `Housing & Utilities` | `Water` | |
| 5 | `Housing` | `Internet & Phone` | `Housing & Utilities` | `Internet & Phone` | |
| 6 | `Housing` | `Home Insurance` | `Housing & Utilities` | `Home Insurance` | |
| 7 | `Housing` | `Building Maintenance` | `Housing & Utilities` | `Building Maintenance` | |
| 8 | `Housing` | `Furniture & Appliances` | `Housing & Utilities` | `Furniture & Appliances` | |
| 9 | `Housing` | `""` | `Housing & Utilities` | `""` | Bare category rename |
| 10 | `Food & Dining` | `Groceries` | `Groceries` | `Groceries` | Food & Dining splits |
| 11 | `Food & Dining` | `Restaurants & Takeaway` | `Dining Out` | `Restaurants & Takeaway` | |
| 12 | `Food & Dining` | `Coffee & Snacks` | `Dining Out` | `Coffee & Snacks` | |
| 13 | `Food & Dining` | `Food Delivery` | `Dining Out` | `Food Delivery` | |
| 14 | `Food & Dining` | `Work Lunch` | `Dining Out` | `Work Lunch` | |
| 15 | `Food & Dining` | `""` | `Groceries` | `Groceries` | Bare → Groceries (**documented default**) |
| 16 | `Transport` | `Fuel` | `Transport` | `Fuel` | Identity (stays) |
| 17 | `Transport` | `Public Transit` | `Transport` | `Public Transit` | |
| 18 | `Transport` | `Taxi / Uber` | `Transport` | `Taxi / Uber` | |
| 19 | `Transport` | `Car Insurance` | `Transport` | `Car Insurance` | |
| 20 | `Transport` | `Car Maintenance` | `Transport` | `Car Maintenance` | |
| 21 | `Transport` | `Parking` | `Transport` | `Parking` | |
| 22 | `Transport` | `Tolls` | `Transport` | `Tolls` | |
| 23 | `Transport` | `Flights & Trains` | `Travel` | `Flights & Trains` | Travel subcategory moves to Travel |
| 24 | `Transport` | `""` | `Transport` | `""` | Bare identity |
| 25 | `Health` | `Gym & Fitness` | `Health` | `Gym & Fitness` | Health unchanged |
| 26 | `Health` | `Pharmacy` | `Health` | `Pharmacy` | |
| 27 | `Health` | `Doctor / Specialist` | `Health` | `Doctor / Specialist` | |
| 28 | `Health` | `Dental` | `Health` | `Dental` | |
| 29 | `Health` | `Supplements` | `Health` | `Supplements` | |
| 30 | `Health` | `Mental Health` | `Health` | `Mental Health` | |
| 31 | `Health` | `""` | `Health` | `""` | |
| 32 | `Entertainment` | `Streaming Services` | `Entertainment` | `Streaming Services` | |
| 33 | `Entertainment` | `Cinema & Theater` | `Entertainment` | `Cinema & Theater` | |
| 34 | `Entertainment` | `Concerts & Events` | `Entertainment` | `Concerts & Events` | |
| 35 | `Entertainment` | `Going Out` | `Entertainment` | `Going Out` | |
| 36 | `Entertainment` | `Hobbies` | `Entertainment` | `Hobbies` | |
| 37 | `Entertainment` | `Books & Courses` | `Entertainment` | `Books & Courses` | |
| 38 | `Entertainment` | `Vacation / Travel` | `Travel` | `Tours & Activities` | Renamed on move |
| 39 | `Entertainment` | `Hotels & Lodging` | `Travel` | `Hotels & Lodging` | |
| 40 | `Entertainment` | `""` | `Entertainment` | `""` | |
| 41 | `Personal` | `Clothing & Accessories` | `Shopping` | `Clothing & Accessories` | Personal → Shopping |
| 42 | `Personal` | `Beauty & Skincare` | `Shopping` | `Beauty & Skincare` | |
| 43 | `Personal` | `Haircut & Grooming` | `Shopping` | `Haircut & Grooming` | |
| 44 | `Personal` | `Gifts` | `Shopping` | `Gifts` | |
| 45 | `Personal` | `""` | `Shopping` | `""` | |
| 46 | `Loans & Debt` | `Loan Repayment` | `Loans & Debt` | `Loan Repayment` | Unchanged |
| 47 | `Loans & Debt` | `Interest` | `Loans & Debt` | `Interest` | |
| 48 | `Loans & Debt` | `Credit Card` | `Loans & Debt` | `Credit Card` | |
| 49 | `Loans & Debt` | `Other Debt` | `Loans & Debt` | `Other Debt` | |
| 50 | `Other` | `Subscriptions & Software` | `Subscriptions & Software` | `Subscriptions & Software` | Software moves out |
| 51 | `Other` | `Taxes & Fees` | `Fees & Taxes` | `Taxes & Fees` | Taxes move out |
| 52 | `Other` | `Charity & Donations` | `Other` | `Charity & Donations` | |
| 53 | `Other` | `Miscellaneous` | `Other` | `Miscellaneous` | |
| 54 | `Other` | `""` | `Other` | `Miscellaneous` | Bare Other → Miscellaneous |

**Derived lookup:** `_TAXONOMY_LOOKUP = {(oc,os):(nc,ns) for oc,os,nc,ns in TAXONOMY_MIGRATION}`.

---

## 4. Category-Only Renames & Lookup Helpers

### 4.1 `CATEGORY_RENAMES` (big_purchases)

Tables that store **no subcategory** (e.g. `big_purchases`) use a smaller map:

```python
CATEGORY_RENAMES = {
    "Housing": "Housing & Utilities",
    "Food & Dining": "Groceries",   # bare Food & Dining → Groceries default
    "Personal": "Shopping",
}
```

### 4.2 `remap_category_subcategory(category, subcategory="")`

```python
def remap_category_subcategory(category, subcategory="") -> tuple[str,str]:
    cat = category or ""
    sub = subcategory or ""
    return _TAXONOMY_LOOKUP.get((cat, sub), (cat, sub))
```

- Unknown pairs pass through unchanged → re-running migration is a **natural no-op** (verified by `test_taxonomy_migration.test_migration_is_idempotent`).
- Used by `db._migrate_taxonomy` (rewrite stored rows) and `sync_core.validate_fields` (accept legacy names from syncing devices — remaps before validating against `CATEGORIES`).

### 4.3 `_TRAVEL_SUBCATS`

```python
_TRAVEL_SUBCATS = {"Vacation / Travel", "Hotels & Lodging", "Flights & Trains"}
```

Powers the old-travel-pool collapse in `remap_travel_categories`.

---

## 5. Fun / Travel Settings Remaps

### 5.1 `remap_fun_categories(entries)`

```python
def remap_fun_categories(entries):  # list[str] -> list[str], order-preserving dedup
```

| Input | Output | Rule |
|-------|--------|------|
| `"Food & Dining"` | `"Dining Out"` | Category split |
| `"Groceries"` | *(dropped)* | Former Food & Dining sub now a standalone category; not fun-money |
| any other non-empty | kept as-is | Unknown entries preserved |
| `""` / `None` | dropped | |

Duplicates removed via `dict.fromkeys` (stable order).

### 5.2 `remap_travel_categories(entries)`

```python
def remap_travel_categories(entries):  # list[str] (pairs or bare names) -> list[str]
```

| Input pattern | Output | Rule |
|---------------|--------|------|
| `"Cat › Sub"` where `Sub` in `_TRAVEL_SUBCATS` | `"Travel"` | Moved travel subcategories collapse to whole Travel |
| `"Entertainment"` (bare) | `"Travel"` | Old travel-pool whole-category collapses |
| `"Entertainment › Vacation / Travel"` | `"Travel"` | Subcategory match collapses |
| `"Transport › Flights & Trains"` | `"Travel"` | Same |
| any other `"… › …"` or bare name | kept as-is | |
| empty | dropped | |

Covered by `test_taxonomy_migration.test_migration_rewrites_settings_fun_travel_categories`.

### 5.3 DB application (`db._migrate_settings_taxonomy`)

Iterates all `UserSettings` rows; rewrites `fun_categories` / `travel_categories` in place via the two helpers. Only writes when the output differs.

---

## 6. Supported Currencies & Symbols

### 6.1 `SUPPORTED_CURRENCIES`

```python
SUPPORTED_CURRENCIES = {
    "EUR": "€",   "RSD": "din", "USD": "$",    "GBP": "£",
    "CHF": "CHF", "HRK": "kn",  "BAM": "KM",   "HUF": "Ft",
    "RON": "lei", "BGN": "лв",   "PLN": "zł",   "CZK": "Kč",
}
```

All UI currency pickers use `list(SUPPORTED_CURRENCIES.keys())` (12 codes). Every formatted amount appends/prepends the mapped symbol (see §8).

### 6.2 `get_currency_symbol(currency: str) -> str`

```python
def get_currency_symbol(currency: str) -> str:
    return SUPPORTED_CURRENCIES.get(currency, currency)  # unknown code echoes itself
```

---

## 7. Default Rates Table — `DEFAULT_RATES`

Rates are stored **per EUR** (`1 EUR = X local`). Editable fallbacks; the user's own values live in `user_settings.currency_rates`.

```python
DEFAULT_RATES = {
    "EUR": 1.0,
    "RSD": 117.0,
    "USD": 1.08,
    "GBP": 0.85,
    "CHF": 0.94,
    "HRK": 7.5345,
    "BAM": 1.9558,
    "HUF": 400.0,
    "RON": 5.0,
    "BGN": 1.9558,
    "PLN": 4.3,
    "CZK": 25.0,
}
```

Coverage invariant (`test_currency.test_default_rates_cover_all_supported_currencies`): every key in `SUPPORTED_CURRENCIES` exists in `DEFAULT_RATES`, and `EUR == 1.0`.

### 7.1 Rate validation — `_valid_rate(v) -> float | None`

```python
def _valid_rate(v):
    f = float(v)         # rejects TypeError / ValueError ("junk", None)
    if not math.isfinite(f) or f <= 0:
        return None      # rejects 0, negatives, NaN, inf
    return f
```

A zero rate must **never** be silently interpreted as 1:1 (`test_rate_validation`). `get_rates` drops invalid stored values and falls back to defaults; `to_eur`/`to_display` **raise `ValueError`** on an invalid rate instead of converting.

---

## 8. Currency Engine

All monetary amounts are stored **twice**: the original `(amount, currency)` and the EUR base value `amount_eur` snapshotted at entry time. Editing exchange rates later never rewrites history — display logic prefers the original when the display currency matches the row's currency.

### 8.1 `get_rates(settings: dict) -> dict`

Returns the per-currency rate table (`1 EUR = X`) for a settings dict.

- Starts from a copy of `DEFAULT_RATES`.
- If `settings["currency_rates"]` is a **non-empty dict**, each `(k, v)` is validated via `_valid_rate`; valid entries overwrite defaults, invalid are ignored.
- Otherwise (missing key, `None`, or empty dict) — **legacy path**: reads `settings["exchange_rate"]` as a single EUR→RSD rate; if valid, sets `rates["RSD"]`. This covers installs that predated the multi-currency table.
- Finally forces `rates["EUR"] = 1.0`.

```python
rates = get_rates({"exchange_rate": 120.0, "currency_rates": None})  # RSD=120 legacy
rates = get_rates({"currency_rates": {"RSD": 118.0, "USD": 1.1}})   # prefer stored, fill gaps from defaults
```

### 8.2 Conversions

| Function | Signature | Behaviour |
|----------|-----------|-----------|
| `to_eur` | `(amount, currency, rates) -> float` | Local → EUR. `EUR` returns `round(float(amount),4)`. Others: `round(amount / rate, 4)`. Raises `ValueError` on invalid rate. |
| `to_display` | `(eur, currency, rates) -> float` | EUR aggregate → display currency. `EUR` passthrough. Others: `eur * rate`. Raises on invalid rate. |
| `to_display_row` | `(eur, orig_amount, orig_currency, currency, rates) -> float` | Per-row display. **Original wins** when `orig_currency == currency` (returns `float(orig_amount)`), otherwise delegates to `to_display`. History never mutates when rates change. |
| `fmt` | `(eur, currency, rates) -> str` | Format aggregate via `_fmt_number(to_display(...), currency)`. |
| `fmt_row` | `(eur, orig_amount, orig_currency, currency, rates) -> str` | Format stored row preserving original (via `to_display_row`). |
| `fmt_dual` | `(orig_amount, orig_currency, eur) -> str` | `'10,000 din / €85.47'` when `orig_currency != "EUR"`, else `'€85.47'`. Uses `_fmt_number`. |

### 8.3 Formatting — `_fmt_number(v, currency)`

```python
def _fmt_number(v, currency):
    sym = get_currency_symbol(currency)
    if currency in ("RSD", "HUF", "HRK"):
        return f"{v:,.0f} {sym}"   # zero decimals, space before symbol
    return f"{sym}{v:,.2f}"        # two decimals, symbol prefix
```

Examples (`test_currency`):

```python
fmt(10, "EUR", rates)  # "€10.00"
fmt(10, "USD", rates)  # "$10.80"  (rate 1.08)
fmt(10, "RSD", rates)  # "1,170 din"
fmt_row(10.0, 10.8, "USD", "USD", rates)  # "$10.80" (original wins)
fmt_dual(10000, "RSD", 85.47)             # "10,000 din / €85.47"
```

### 8.4 Supplemental helpers

- `effective_category_budgets(m_bud: DataFrame) -> dict[str,float]` — budget-scope semantics: when subcategory-specific rows exist for a category they are authoritative and the whole-category row (`subcategory == ""`) is ignored; overlapping rows are never summed.
- `filter_started_templates(df, year, month) -> DataFrame` — recurring templates whose `start_month` (`"YYYY-MM"`) is ≤ the given month; `None`/blank = always active; lexical compare.

---

## 9. Amount & Budget Guards

| Constant | Value | Meaning |
|----------|-------|---------|
| `MAX_AMOUNT` | `1_000_000.0` | Hard ceiling for any single amount field (expense `amount`, income `actual`/`budgeted`, saving deposits, big-purchase price, etc.). UI `number_input` uses `max_value=MAX_AMOUNT`; import and API paths reject `> MAX_AMOUNT` or non-finite. Verified in `test_bank_import`, `mcp_server` guards. |
| `MAX_SAVINGS_TARGET` | `10_000_000.0` | Ceiling for savings goal targets. |
| `NEAR_LIMIT_THRESHOLD` | `0.85` | Budget-alert band: `spent >= budget * 0.85` triggers "near limit" (used in `notifications.py`, `dashboard.py`, `insights.py`). |
| `SAVINGS_TARGET_PCT` / `SAVINGS_GOAL_PCT` | `15` / `20` | Suggested savings percentages. |
| `BACKUP_RETENTION_DAYS` | `30` | Encrypted backup retention (also consumed by `db.backup_db`). |

Currency-layer guard uses `math.isfinite` plus `> 0` and `<= MAX_AMOUNT` checks (e.g. `bank_import`: `if not (ae > 0) or ae > MAX_AMOUNT` — also rejects NaN). Rate guards are separate (see `_valid_rate` above).

---

## 10. Live Rates Refresh — `rates.py`

### 10.1 Fetch — `fetch_live_rates(timeout=3) -> dict | None`

1. **Frankfurter** (`https://api.frankfurter.app/latest?from=EUR`, ECB data) — primary.
2. Compute `missing = [c for c in SUPPORTED_CURRENCIES if not (EUR excluded and rate > 0 present)]`.
3. If any missing, call **open.er-api** (`https://open.er-api.com/v6/latest/EUR`) — only when `data["result"] == "success"`; merges fallback rates for the missing currencies (`RSD`, `BAM`, etc. that ECB does not publish).
4. Build `out = {"EUR": 1.0} + positive entries`; return `out` only if `len(out) > 1`, else `None` (all providers failed or garbage shape). Garbage/invalid rates are filtered (`isinstance(v,(int,float)) and v > 0`). Network failures are logged and ignored.

Tests (`test_rates.py`):

- `test_fetch_merges_frankfurter_and_er_api` — primary provides USD/GBP, fallback fills RSD/BAM.
- `test_fetch_skips_fallback_when_all_currencies_present` — no fallback call when Frankfurter is complete.
- `test_fetch_returns_none_when_all_providers_fail` / `test_fetch_returns_none_when_rates_are_garbage`.

### 10.2 Failure cache — `_fetch_cached`

```python
@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_cached():
    return fetch_live_rates()   # caches including None
```

TTL **1800 s (30 min)** — including failures (`None`). A broken network does not slow down every Streamlit rerun; the cached `None` is reused until expiry. `refresh_rates_if_due(force=True)` calls `_fetch_cached.clear()` first.

### 10.3 Staleness — `rates_are_stale(settings)`

```python
RATES_MAX_AGE_DAYS = 3
def rates_are_stale(settings):
    updated = settings.get("rates_updated_at")  # datetime | date | ISO string | None
    if updated is None: return True
    # datetime -> date, date -> date, str -> fromisoformat -> date, else True
    return (date.today() - d).days >= RATES_MAX_AGE_DAYS
```

Handles `None`, `datetime`, `date`, and ISO string forms. Tested against `None`, now, old dates, and string dates in `test_rates.py`.

### 10.4 Refresh lifecycle — `refresh_rates_if_due(user_id, settings, force=False) -> (settings, updated)`

```
if not (force or rates_are_stale(settings)): return (settings, False)
if force: _fetch_cached.clear()
fresh = _fetch_cached()
if not fresh: return (settings, False)        # last known survives any network failure
fresh_settings = get_settings(user_id) or settings
current = dict(fresh_settings.get("currency_rates") or {})
current.update(fresh)                          # merge live on top of stored
new_settings = save_settings(user_id, {
    "currency_rates": current,
    "rates_updated_at": datetime.now(timezone.utc),
})
return (new_settings, True)
```

- **3-day staleness** triggers refresh (or `force=True` from Settings → Currency).
- **30-min failure cache** prevents hammering a down provider — `_fetch_cached` already memoised `None`.
- **Last known survives failure** — on `fresh is None` the stored `currency_rates` are untouched.
- Re-reads settings from DB before merging to avoid overwriting a concurrent write.
- Invoked on login flow; also directly from `app_pages/settings.py` currency tab with `force=True`.

Storage: `user_settings.currency_rates: JSON` (dict) + `user_settings.rates_updated_at: TIMESTAMP` — additive columns in `db._migrate`.

---

## 11. Salary Cycle & Time Math

### 11.1 `compute_salary_cycle(today: date, salary_day: int=10, latest_salary: date|None=None) -> (date, date)`

Returns `(period_start, period_end)` for the salary cycle.

- Internal `_clamped(y,m)` uses `min(salary_day, calendar.monthrange(y,m)[1])` at **every** construction — month-end days 29/30/31 never raise.
- If `latest_salary` is provided, `period_start = latest_salary` (actual payout date wins).
- Else if `today.day >= salary_day` → start = clamped current month.
- Else → start = clamped previous month (handles January wrap to December previous year).
- `period_end` = day **before** the next cycle start: advance one month from `period_start`, clamp again with `min(period_start.day, monthrange(next_y,next_m)[1])`, minus one day.

Forecast pages use this to bucket expenses/income into salary cycles rather than calendar months.

---

## 12. Fun-Money & Travel Pools + Priority Quadrant

### 12.1 Pool matching — `_pool_members(entries) -> (cats, subs)`

Splits `fun_categories` entries into `([category names], [subcategory names])` using `CATEGORIES` / `ALL_SUBCATS` membership. Bare subcategory names accepted for backward compatibility.

### 12.2 `fun_spent(expenses_df, categories, year, month) -> float`

EUR spent this month across fun-money categories.

```python
m = expenses_df[(expenses_df["date"].dt.year == year) & (expenses_df["date"].dt.month == month)]
cats, subs = _pool_members(categories)
mask = m["category"].isin(cats)                     # whole-category match
if subs: mask |= m["subcategory"].fillna("").isin(subs)  # bare subcat fallback
return float(m[mask]["amount_eur"].sum())
```

Tests (`test_fun_travel.py`): month filter, multi-category sum, bare-subcategory backward compat.

### 12.3 `travel_spent(expenses_df, pairs, year) -> float`

EUR spent this year on travel pairs. Each entry may be:

| Entry form | Semantics |
|------------|-----------|
| `"Travel"` (bare category) | Whole category counts |
| `"Category › Subcategory"` (with `›`) | Exact pair match when `sub` non-empty |
| `"Shopping › "` (trailing empty sub) | Whole category counts (check `›` **before** stripping trailing space) |
| Bare subcategory name | Subcategory match (backward compat) |

Overlapping pairs (e.g. `"Travel › "` plus `"Travel › Flights & Trains"`) are **unioned** — `mask = mask | pair_match` — an expense is never counted twice (regression test `test_travel_spent_overlapping_pairs_not_double_counted`).

Defaults: `DEFAULT_FUN_CATEGORIES` and `DEFAULT_TRAVEL_CATEGORIES` as above; stored per-user as `user_settings.fun_categories`, `travel_categories`, `fun_money`, `travel_budget`, `fun_bonus_amount`/`fun_bonus_month`/`fun_bonuses` (migrated via §5.3).

### 12.4 Priority quadrant — `classify_quadrant(work_hours, usage_hours, median_work, median_usage) -> str`

Big-purchase 4-square matrix (expected usage vs work-hours needed to buy):

| Condition | Result | Color (`QUADRANT_COLORS`) |
|-----------|--------|-----------------------------|
| `usage > median_usage` and `work ≤ median_work` | `"Quick wins"` | `#00B050` |
| `usage > median_usage` and `work > median_work` | `"Plan & save"` | `#0F3460` |
| `usage ≤ median_usage` and `work ≤ median_work` | `"Maybe later"` | `#A8A8A8` |
| otherwise (`low usage, high work`) | `"Reconsider"` | `#E94560` |

Comparison is strict `>` (equal to median counts as "low"/"not high").

---

## 13. Excel Injection Guard & Grouped-Order Validation

### 13.1 Excel formula injection guard — `to_excel(df) -> bytes` / `_xl_safe`

Cells beginning with certain characters are **formulas** when the spreadsheet opens (or when a CSV is re-imported) — e.g. `"=HYPERLINK(...)"` would execute.

```python
_XL_UNSAFE_PREFIXES = ("=", "+", "@")

def _xl_safe(v):
    if isinstance(v, str) and v.startswith(_XL_UNSAFE_PREFIXES):
        return "'" + v
    if isinstance(v, str) and len(v) > 1 and v[0] == "-" and v[1].isdigit():
        return "'" + v          # "-" alone only dangerous when followed by a digit
    return v

def to_excel(df):
    safe = df.copy()
    for col in safe.columns:
        if is_string_dtype(col) or col.dtype == object:
            safe[col] = safe[col].astype(object).map(_xl_safe)
    safe.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()
```

- Prefixes guarded: `=`, `+`, `@` **always**, and `-` **only** when followed by a digit (plain `"-rebate"` notes stay untouched — no stray quote).
- Guard is a leading single quote `'` — openpyxl writes the cell as **text** (`data_type 's'`), never `'f'` (formula). Verified by `test_formula_injection.py`: every `description` starting with `= + - @` exports as `"'=SUM(A1:A2)"` etc. with `data_type == "s"`; non-string cells (numbers, booleans) are untouched.
- Applies to string-typed and object-typed columns (pandas 3 uses `"str"` dtype).

### 13.2 Grouped-order validation — `validate_grouped_order(order, expected) -> dict|None`

Validates a drag-and-drop board order (dashboard / big-purchases / savings boards via `draggable_card_board`).

```python
def validate_grouped_order(order: dict, expected: dict) -> dict | None:
    if not isinstance(order, dict) or set(order) != set(expected):
        return None                                        # wrong groups
    wanted   = [str(id) for ids in expected.values() for id in ids]
    received = [str(id) for cat in expected for id in order.get(cat, [])]
    if len(received) != len(wanted) or len(set(received)) != len(received):
        return None                                        # missing / duplicated id
    if set(received) != set(wanted):
        return None                                        # unknown / foreign id
    return {str(cat): [str(id) for id in order[cat]] for cat in expected}
```

- Accepts **one complete, non-duplicated** board order or rejects it (returns `None`).
- Group keys must exactly match `expected` (order-insensitive membership, but iteration order preserved in return).
- Every id must appear **exactly once** — no omissions, no duplicates, no foreign ids.
- Normalises all ids/keys to strings.
- Called as `validate_grouped_order(result.order, original) or original` in `draggable_card_board` — invalid payloads silently fall back to the original order.
- The board component itself (`utils._CARD_BOARD` + JS drag/drop + `Alt+Up/Down` keyboard) emits `setStateValue('order', ...)` and actions via `setTriggerValue('action', {id, action, value})`; the Python wrapper sanitises the action dict (must have exactly `{id,action,value}` and `id` in `original`).

Also see `utils.effective_category_budgets` (scope semantics, §8.4) and `filter_started_templates` (§8.4) which live alongside the guards.

---

## Appendix — Where Things Are Wired

| Concern | Primary file | DB / Sync glue | Tests |
|---------|--------------|----------------|-------|
| Categories | `utils.CATEGORIES` | `db._migrate_taxonomy`, `sync_core.validate_fields` remaps legacy pairs before `CATEGORIES` check | `test_taxonomy_migration` |
| Budgets merge | `utils.remap_category_subcategory` | `db._migrate_budgets_taxonomy` (colliding scopes merged, `sum(budgeted_eur)`, newest id kept) | `test_taxonomy_migration` |
| Settings pools | `utils.remap_fun/travel_categories` | `db._migrate_settings_taxonomy` | `test_taxonomy_migration` |
| Currencies | `utils.SUPPORTED_CURRENCIES`, `DEFAULT_RATES`, `get_rates` | `db.user_settings.currency_rates` JSON + `rates_updated_at` | `test_currency`, `test_rate_validation` |
| Conversions | `utils.to_eur / to_display / fmt* / _valid_rate` | Stored `amount`+`amount_eur` dual write | `test_currency` |
| Rates refresh | `rates.fetch_live_rates / _fetch_cached / rates_are_stale / refresh_rates_if_due` | `rates.py` + `db.save_settings` | `test_rates` |
| Salary cycle | `utils.compute_salary_cycle` | App settings `salary_day`, `salary_active` | — |
| Fun / Travel | `utils.fun_spent / travel_spent` | `user_settings.fun_money / fun_categories / travel_*` | `test_fun_travel` |
| Quadrant | `utils.classify_quadrant` | `big_purchases` work/usage hours | — |
| Excel guard | `utils.to_excel / _xl_safe` | Export path | `test_formula_injection` |
| Board order | `utils.validate_grouped_order / draggable_card_board` | Persisted `sort_order` columns | — |
| Constants | `MAX_AMOUNT`, `BACKUP_RETENTION_DAYS`, `NEAR_LIMIT_THRESHOLD`, `RATES_MAX_AGE_DAYS`, `_XL_UNSAFE_PREFIXES` | Spread across `utils.py` / `rates.py` | — |

> Historical note: Frankfurter covers ECB currencies; open.er-api fills RSD/BAM/HUF/etc. Never fetch without the `User-Agent: ExpenseTracker/1.0 (+local personal app)` header (set in `rates._open`).
