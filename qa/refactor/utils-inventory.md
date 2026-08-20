# utils.py Inventory (Part D) — Symbol Classification & R6 Migration Map

> **Source file:** `utils.py` (731 lines, flat root module)
> **Scope:** Read-only inspection. No other files modified.
> **Method:** Every module-level symbol (public + private helpers) in `utils.py` was read line-by-line and cross-referenced against its call sites (`app.py`, `db.py`, `sync_core.py`, `mcp_server.py`, `notifications.py`, `gamification.py`, `insights.py`, `app_pages/*`, `tests/*`).
> **Goal:** Classify each symbol for the R6 decomposition and produce a migration map.

---

## 0. Classification Buckets (legend)

| Bucket | Covers |
|--------|--------|
| **domain** | money/currency conversion, category taxonomy, period/date, merchant normalization, validation |
| **UI** | Streamlit helpers, styling, grouped_board, formatting-for-display, CSS, panel helpers |
| **exporting** | Excel export, CSV, download helpers |
| **networking** | HTTP, URL handling, tunnel, network helpers |
| **formatting** | `fmt_row`, `fmt_dual`, currency formatting, display helpers (overlaps with UI) |
| **legacy/unknown** | unclear purpose, possibly dead code, or mixed concerns |

**Flag legend:** `⚠ DOMAIN` = symbol is currently doing **domain calculations while living in `utils.py`** and must move to `domain/` per spec (see §7). A symbol marked `(private)` has a leading underscore but is still a module-level decomposition surface (tests / other modules depend on its public wrapper, or it must move together with it).

**Merchant normalization:** none exists in `utils.py`. Merchant/counterparty normalization lives in the ingestion layer (`bank_import.py` / `pdf_import.py`), so no symbols are classified under that domain sub-bucket here.

---

## 1. `domain` bucket

### 1a. Money / currency conversion

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `SUPPORTED_CURRENCIES` | 180–184 | Currency-code → display-symbol map (12 codes). Powers every UI currency picker. | `domain/money.py` |
| `DEFAULT_RATES` | 188–201 | Fallback `1 EUR = X` rate table; overridden by `user_settings.currency_rates`. | `domain/money.py` |
| `get_currency_symbol` | 226–227 | Symbol lookup; unknown code echoes itself. Used by formatters and directly by many pages. | `domain/money.py` |
| `_valid_rate` (private) | 230–240 | Rate guard: finite and strictly `> 0` only; rejects 0/NaN/inf. | `domain/money.py` |
| `get_rates` | 243–265 | Builds per-currency rate table from a settings dict, with legacy `exchange_rate` → RSD fallback; forces `EUR = 1.0`. | `domain/money.py` |
| `to_eur` | 268–276 | Local amount → EUR base (`round(x / rate, 4)`). Raises `ValueError` on invalid rate. | `domain/money.py` |
| `to_display` | 279–287 | EUR aggregate → display currency (`x * rate`). Raises on invalid rate. | `domain/money.py` |
| `to_display_row` | 290–296 | Per-row display: original amount wins when `orig_currency == currency`, else delegates to `to_display`. | `domain/money.py` |
| `MAX_AMOUNT` | 211 | Hard ceiling for any single amount (expense/income/saving/deposit). Validation guard. | `domain/money.py` (validation) |

### 1b. Category taxonomy

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `CATEGORIES` | 17–34 | Canonical 12-category → ordered-subcategory dict. | `domain/taxonomy.py` |
| `INCOME_SOURCES` | 36 | Income source options (5). | `domain/taxonomy.py` |
| `INCOME_TYPES` | 37 | Income type options (7). | `domain/taxonomy.py` |
| `SAVINGS_GOALS` | 38 | Savings-goal name options (5). | `domain/taxonomy.py` (or `domain/planning.py`) |
| `CAT_LIST` | 40 | `list(CATEGORIES.keys())` — 12 categories, insertion order. | `domain/taxonomy.py` |
| `ALL_SUBCATS` | 41 | Sorted flat set of all subcategories (~48). | `domain/taxonomy.py` |
| `TAXONOMY_MIGRATION` | 50–113 | Legacy→current taxonomy rows `(old_cat, old_sub, new_cat, new_sub)`; `""` sub = whole category. | `domain/taxonomy.py` |
| `_TAXONOMY_LOOKUP` (private) | 115 | Derived `{(oc,os):(nc,ns)}` lookup. | `domain/taxonomy.py` |
| `CATEGORY_RENAMES` | 118–122 | Category-only renames for subcategory-less tables (`big_purchases`). | `domain/taxonomy.py` |
| `remap_category_subcategory` | 125–133 | Remap one (old) pair to new names; unknown pairs pass through (idempotent). | `domain/taxonomy.py` |
| `_TRAVEL_SUBCATS` (private) | 137 | Moved travel subcategory set (collapses old travel-pool entries). | `domain/taxonomy.py` |
| `remap_fun_categories` | 140–155 | Rewrite `fun_categories`: `Food & Dining`→`Dining Out`, drop `Groceries`, stable dedup. | `domain/taxonomy.py` |
| `remap_travel_categories` | 158–178 | Rewrite `travel_categories`: moved travel subcats / bare `Entertainment` collapse to `Travel`. | `domain/taxonomy.py` |

### 1c. Period / date

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `compute_salary_cycle` | 358–382 | Returns `(period_start, period_end)` for a salary cycle; clamps month-end days via `calendar.monthrange`. | `domain/date.py` |
| `filter_started_templates` | 319–327 | Filters recurring templates whose `start_month` (`YYYY-MM`) is ≤ target month; blank = always active. | `domain/date.py` (or `domain/recurring.py`) |

### 1d. Planning / budget / pools (domain calculations)

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `effective_category_budgets` | 299–316 | Budget-scope semantics: subcategory rows are authoritative over the whole-category row; no double-summing. | `domain/budget.py` |
| `NEAR_LIMIT_THRESHOLD` | 203 | Budget-alert band (`spent >= budget * 0.85`). | `domain/budget.py` |
| `SAVINGS_TARGET_PCT` | 204 | Suggested savings target % (15). | `domain/planning.py` |
| `SAVINGS_GOAL_PCT` | 205 | Suggested savings goal % (20). | `domain/planning.py` |
| `MAX_SAVINGS_TARGET` | 212 | Ceiling for savings-goal / loan amounts. | `domain/planning.py` |
| `DEFAULT_FUN_CATEGORIES` | 214 | Default fun-money pool (`["Entertainment","Dining Out"]`). | `domain/pools.py` (or `domain/taxonomy.py`) |
| `DEFAULT_TRAVEL_CATEGORIES` | 216 | Default travel pool (`["Travel"]`). | `domain/pools.py` (or `domain/taxonomy.py`) |
| `_pool_members` (private) | 387–402 | Splits pool entries into `(category_names, subcategory_names)`. | `domain/pools.py` |
| `fun_spent` | 405–421 | EUR spent this month across fun-money categories (whole-category + bare-subcat fallback). | `domain/pools.py` |
| `travel_spent` | 424–458 | EUR spent this year on travel pairs (pair / bare category / bare subcat; unioned, never double-counted). | `domain/pools.py` |
| `classify_quadrant` | 471–482 | Big-purchase 4-square priority matrix (usage × work-hours vs medians). | `domain/planning.py` |

---

## 2. `UI` bucket

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `CHART_COLORS` | 39 | 8-color chart palette shared across pages. | `ui/styles.py` |
| `QUADRANT_COLORS` | 463–468 | Quadrant label → hex map used for `color_discrete_map`. *Semantically coupled to `classify_quadrant` labels.* | `ui/styles.py` (keep labels in `domain/planning.py`) |
| `validate_grouped_order` | 485–496 | Accepts one complete, non-duplicated board order (group keys + ids exactly match) or returns `None`. | `ui/board.py` |
| `_CARD_BOARD` (private) | 499 | Memoized `st.components.v2` draggable-card component instance (module global). | `ui/board.py` |
| `draggable_card_board` | 502–553 | Renders accessible drag/drop card board (HTML/CSS/JS) and returns `(order, action)`; sanitizes the action payload. | `ui/board.py` |
| `safe_error` | 587–588 | `st.error` wrapper with a fixed material icon. | `ui/panels.py` |
| `safe_warning` | 591–592 | `st.warning` wrapper with a fixed material icon. | `ui/panels.py` |
| `help_expander` | 595–597 | `st.expander` + markdown help panel. | `ui/panels.py` |
| `inject_mobile_css` | 602–667 | Injects global mobile/responsive CSS (KPI, colors, progress, badges, forms, sidebar). | `ui/styles.py` |

---

## 3. `exporting` bucket

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `_XL_UNSAFE_PREFIXES` (private) | 560 | Prefixes treated as spreadsheet formulas (`=`, `+`, `@`). | `infra/exporting.py` |
| `_xl_safe` (private) | 563–570 | Prefixes formula-like strings with `'` (also `-` followed by a digit) so openpyxl writes literal text. | `infra/exporting.py` |
| `to_excel` | 573–582 | DataFrame → `.xlsx` bytes with the formula-injection guard applied to string/object columns. | `infra/exporting.py` |

> Note: `utils.py` contains **no CSV exporter** — only the Excel path. CSV/statement parsing (input) lives in `bank_import.py`/`pdf_import.py`, not here.

---

## 4. `networking` bucket

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `APP_PORT` | 207 | Default Streamlit server port fallback. *Duplicated: `launcher.py:17` redefines its own `APP_PORT`.* | `infra/networking.py` (or `infra/config.py`) |
| `TLS_ENABLED` | 210 | `EXPENSE_TRACKER_TLS=1` → HTTPS flag; drives `http/https` scheme in `get_lan_urls` and `app.py`. | `infra/networking.py` |
| `get_server_port` | 672–681 | Resolves the running server port (`st.get_option` → env → `APP_PORT`). | `infra/networking.py` |
| `get_lan_urls` | 684–716 | `@st.cache_data(ttl=60)` — returns `(urls, hostname)` for LAN addresses (UDP hint + hostname fallback), filtering loopback/link-local. | `infra/networking.py` |
| `qr_png` | 719–731 | PNG QR code for a URL (LAN phone-access QR). | `infra/networking.py` (could also be `ui`; encodes a network URL) |

---

## 5. `formatting` bucket (overlaps with UI)

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `_fmt_number` (private) | 330–334 | Symbol placement + decimals: `RSD/HUF/HRK` → `"1,170 din"`, others → `"€10.00"`. | `ui/formatting.py` |
| `fmt` | 337–339 | Format a EUR aggregate: `_fmt_number(to_display(...))`. | `ui/formatting.py` |
| `fmt_row` | 342–346 | Format a stored row preserving original: `_fmt_number(to_display_row(...))`. | `ui/formatting.py` |
| `fmt_dual` | 349–353 | `"10,000 din / €85.47"` (original + EUR equivalent). | `ui/formatting.py` |

> **Overlap note:** these composite helpers call the domain conversion functions (`to_display` / `to_display_row`). They are *formatting* (display), so they belong in `ui/formatting.py`, but after the split they must import from `domain/money.py` rather than re-implement conversion. They are **not** flagged as domain moves (see §7 nuance).

---

## 6. `legacy/unknown` bucket

| Name | Lines | Description | Suggested destination |
|------|-------|-------------|----------------------|
| `BACKUP_RETENTION_DAYS` | 206 | Encrypted-backup retention in days (consumed by `db.backup_db`). **Mixed concern** — a persistence/infra config constant parked in `utils.py`, not domain. | `infra/config.py` (or colocate with `db.py`) |

> **No genuinely dead code found.** Every symbol in `utils.py` has at least one live call site (`app.py`, `db.py`, `sync_core.py`, `mcp_server.py`, `notifications.py`, `gamification.py`, `insights.py`, `app_pages/*`, or `tests/*`). The only "unknown/mixed" item is `BACKUP_RETENTION_DAYS` (config living in the wrong layer). The underscore import aliases `_date`/`_td` (line 10) are internal conveniences, not exported symbols.

---

## 7. ⚠ Flagged — domain calculations currently living in `utils.py`

These **must move to `domain/`** per spec. They are pure domain logic (or domain data) that happens to be in the shared utility module today.

**Currency engine → `domain/money.py`**
- `SUPPORTED_CURRENCIES`, `DEFAULT_RATES`, `MAX_AMOUNT`
- `get_currency_symbol`, `_valid_rate`, `get_rates`, `to_eur`, `to_display`, `to_display_row`

**Taxonomy → `domain/taxonomy.py`**
- `CATEGORIES`, `INCOME_SOURCES`, `INCOME_TYPES`, `SAVINGS_GOALS`, `CAT_LIST`, `ALL_SUBCATS`
- `TAXONOMY_MIGRATION`, `_TAXONOMY_LOOKUP`, `CATEGORY_RENAMES`, `_TRAVEL_SUBCATS`
- `remap_category_subcategory`, `remap_fun_categories`, `remap_travel_categories`

**Period / date → `domain/date.py`**
- `compute_salary_cycle`, `filter_started_templates`

**Budget / planning → `domain/budget.py` + `domain/planning.py`**
- `effective_category_budgets`, `NEAR_LIMIT_THRESHOLD` → `domain/budget.py`
- `SAVINGS_TARGET_PCT`, `SAVINGS_GOAL_PCT`, `MAX_SAVINGS_TARGET`, `classify_quadrant` → `domain/planning.py`

**Fun / travel pools → `domain/pools.py`**
- `DEFAULT_FUN_CATEGORIES`, `DEFAULT_TRAVEL_CATEGORIES`, `_pool_members`, `fun_spent`, `travel_spent`

**Layering nuance (not a domain move):** `fmt`, `fmt_row`, `fmt_dual`, `_fmt_number` technically perform conversion, but their role is display. Keep them in `ui/formatting.py` and have them call `domain/money.py`; do not move them to `domain/`.

---

## 8. Summary Migration Map (destination → symbols)

| Destination module | Symbols |
|--------------------|---------|
| `domain/money.py` | `SUPPORTED_CURRENCIES`, `DEFAULT_RATES`, `MAX_AMOUNT`, `get_currency_symbol`, `_valid_rate`, `get_rates`, `to_eur`, `to_display`, `to_display_row` |
| `domain/taxonomy.py` | `CATEGORIES`, `INCOME_SOURCES`, `INCOME_TYPES`, `SAVINGS_GOALS`, `CAT_LIST`, `ALL_SUBCATS`, `TAXONOMY_MIGRATION`, `_TAXONOMY_LOOKUP`, `CATEGORY_RENAMES`, `_TRAVEL_SUBCATS`, `remap_category_subcategory`, `remap_fun_categories`, `remap_travel_categories` |
| `domain/date.py` | `compute_salary_cycle`, `filter_started_templates` |
| `domain/budget.py` | `effective_category_budgets`, `NEAR_LIMIT_THRESHOLD` |
| `domain/planning.py` | `SAVINGS_TARGET_PCT`, `SAVINGS_GOAL_PCT`, `MAX_SAVINGS_TARGET`, `classify_quadrant` |
| `domain/pools.py` | `DEFAULT_FUN_CATEGORIES`, `DEFAULT_TRAVEL_CATEGORIES`, `_pool_members`, `fun_spent`, `travel_spent` |
| `ui/styles.py` | `CHART_COLORS`, `QUADRANT_COLORS`, `inject_mobile_css` |
| `ui/formatting.py` | `_fmt_number`, `fmt`, `fmt_row`, `fmt_dual` |
| `ui/board.py` | `validate_grouped_order`, `_CARD_BOARD`, `draggable_card_board` |
| `ui/panels.py` | `safe_error`, `safe_warning`, `help_expander` |
| `infra/exporting.py` | `_XL_UNSAFE_PREFIXES`, `_xl_safe`, `to_excel` |
| `infra/networking.py` | `APP_PORT`, `TLS_ENABLED`, `get_server_port`, `get_lan_urls`, `qr_png` |
| `infra/config.py` | `BACKUP_RETENTION_DAYS` |

**Counts:** 35 `domain` · 9 `UI` · 4 `formatting` · 3 `exporting` · 5 `networking` · 1 `legacy/unknown` = **57 total module-level symbols** inventoried (including 10 `(private)` helpers that move with their public wrapper).

---

## 9. Cross-cutting notes for the R6 executor

1. **`utils` is the most-imported module** (14+ files). Any split must preserve the public import surface during the transition — a re-export shim in `utils.py` (or `utils/__init__.py`) is advisable until call sites are repointed, since `db.py`, `sync_core.py`, `mcp_server.py`, and every page import from `utils` directly.
2. **`db.py` imports taxonomy symbols inside functions** (`TAXONOMY_MIGRATION`, `CATEGORY_RENAMES`, `remap_category_subcategory`, `remap_fun_categories`, `remap_travel_categories`, `BACKUP_RETENTION_DAYS`) — these are the only domain symbols imported by the persistence layer; keep those import paths stable.
3. **`sync_core.py`** imports `CATEGORIES`, `ALL_SUBCATS`, `remap_category_subcategory`, `MAX_AMOUNT`, `MAX_SAVINGS_TARGET`, `SUPPORTED_CURRENCIES` — sync validation must keep consuming the moved `domain/` modules.
4. **`get_lan_urls`** carries `@st.cache_data`; moving it to `infra/networking.py` is safe but keep the decorator attached. `TLS_ENABLED`/`APP_PORT` are also duplicated/consumed in `launcher.py` and `app.py` — centralizing in `infra/networking.py` would remove the `launcher.py:17` duplication.
5. **`QUADRANT_COLORS`** is display styling but is keyed by `classify_quadrant`'s return values — the label vocabulary must stay in `domain/planning.py` while the color map can live in `ui/styles.py`.
6. **`validate_grouped_order` + `draggable_card_board`** are UI-board concerns (persisted `sort_order` columns), not domain validation — do not route them to `domain/`.
