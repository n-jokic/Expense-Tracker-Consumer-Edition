# Transactions & Recurring — Ledger Reference

> **Sources:** `app_pages/log_expense.py` (404), `app_pages/log_income.py` (332), `app_pages/recurring.py` (271), `app_pages/audit_log.py` (47), `db.py` (Expense/Income/Recurring/AuditLog + `add/update/soft_delete/restore`), `finance.py:derive_hourly_rate`, `utils.py:filter_started_templates, CATEGORIES, MAX_AMOUNT`, `queries.py:recurring/audit`, `notifications.py:_unlogged_templates`

---

## 1. Purpose

This subsystem owns the **core transaction ledger**: every expense and income row, the monthly recurring-template system that spawns expenses, and the immutable audit trail of all mutations. It is the highest-churn surface of the app — every import, loan payment, receipt scan, and sync write ends here — and the source of truth for forecasting, insights, budgets, gamification, and notifications.

---

## 2. Source Scope

| File | Lines | Role |
|------|-------|------|
| `app_pages/log_expense.py` | 404 | Expense entry form, OCR receipt hook, searchable history (`st.data_editor` + Trash checkbox), pagination, trash/restore, Excel export |
| `app_pages/log_income.py` | 332 | Fixed-salary setup, one-tap salary, hourly (`hours × rate`), raise detection, income history + edit dialog |
| `app_pages/recurring.py` | 271 | Template CRUD, draggable board (`draggable_card_board`), due-day logic, "Log now" actual-amount dialog, grouped-order persistence |
| `app_pages/audit_log.py` | 47 | Latest 200 audit rows, JSON pretty-print, action filter |
| `db.py:Expense` | 338-371 | Table definition (34 columns) |
| `db.py:Income` | 374-392 | Table definition (16 columns) |
| `db.py:Recurring` | 453-467 | Template table (10 columns) |
| `db.py:AuditLog` | 470-479 | Audit table (8 columns) |
| `db.py` | 1000-1175 | `get_expenses, add_expense, update_expense, soft_delete_expense, restore_expense, get_income, add_income, update_income, soft_delete_income, restore_income, get_recurring, add_recurring, update_recurring, get_audit_log, log_audit` |
| `queries.py` | 113-186 | Cached wrappers `expenses/income/recurring/audit` + `save_settings` for salary |
| `finance.py:derive_hourly_rate` | 21-59 | Hourly income → work-hours conversion (used by big-purchases quadrant) |
| `utils:filter_started_templates` | 319 | Start-month gate for recurring checklist |

---

## 3. Internal Architecture

```text
log_expense.py  ──form──► add_expense ─┐
log_income.py   ──form──► add_income ──┤
recurring.py    ─"Log now"─► add_expense ├─► log_audit ─► bump_data_revision ─► queries cache invalidate
               edit Trash ─► update_*  ─┤       (inside same get_session() context for add_*)
                          soft_delete ─┘
history ─► st.data_editor (editable subset) ─► diff ─► update_expense per changed row ─► bump
trash section ─► restore_expense
audit_log.py ◄── q.audit(user_id, 200) ◄── get_audit_log (ORDER BY timestamp DESC LIMIT 200)
```

Recurring templates are **immutable history carriers**: editing a template (`update_recurring`) never rewrites already-logged expenses — they keep their own copied `category/amount/description` and only link back via `rec_template_id`. Deactivating a template sets `active=False` rather than deleting it.

---

## 4. Important Symbols

| Symbol | File:Line | Role |
|--------|-----------|------|
| `Expense` | `db.py:338` | SQLAlchemy model — 34 cols incl. `loan_id, rec_template_id, suggest_* (ML telemetry), is_deleted/deleted_at` |
| `Income` | `db.py:374` | Model — `source, income_type Salary/Hourly/Bonus/Freelance/Investment/Rental/Other, hours/rate, budgeted/actual + _eur, notes` |
| `Recurring` | `db.py:453` | Template model — `category, subcategory, description, amount/currency/amount_eur, due_day 1-31/None, start_month YYYY-MM/None, active, sort_order` |
| `AuditLog` | `db.py:470` | `user_id, action CREATE/UPDATE/DELETE/RESTORE, table_name, record_id, details JSON, timestamp, ip_address` |
| `add_expense(user_id, row)` | `db.py:1021` | UUID id, sets all telemetry cols, `log_audit(CREATE, expenses)`, no revision bump inside (caller calls `q.bump_db_version`) |
| `update_expense(user_id, id, updates)` | `db.py:1050` | Scoped `user_id`, hasattr guard, `log_audit(UPDATE)` |
| `soft_delete_expense / restore_expense` | `db.py:1062/1073` | `is_deleted=True + deleted_at=_utcnow()` / inverse, audited |
| `add_income / update_income` | `db.py:1118/1137` | Mirrors expense pattern; income stores both `budgeted/actual` original + `_eur` |
| `soft_delete_income / restore_income` | `db.py:1152/1162` | Soft-delete pair for income |
| `get_expenses(user_id, include_deleted=False)` | `db.py:1011` | `SELECT where user_id && is_deleted flag` → DataFrame via `_to_df` + `_parse_dates` |
| `get_income` | `db.py:1107` | Includes `_fill_income_types` legacy map (`Primary Salary→Salary` etc.) |
| `get_recurring(user_id)` | `db.py:1539` | Returns all templates (active filter lives in UI via `active` col) |
| `add_recurring / update_recurring` | `db.py:1547/1566` | Template CRUD; drag-board also mutates `sort_order + category` |
| `get_audit_log(user_id, limit=200)` | `db.py:1924` | Latest N rows desc |
| `log_audit(session, user_id, action, table, id, details)` | `db.py:975` | Core audit writer — called inside every mutation session before commit |
| `filter_started_templates(df, year, month)` | `utils.py:319` | Drops templates whose `start_month > YYYY-MM` of view month |
| `derive_hourly_rate` | `finance.py:21` | Weighted `actual_eur/hours` or `salary_eur/160` fallback; feeds big-purchases |
| `_LEGACY_INCOME_TYPES` | `db.py:1091` | On-read remap for old `source` labels |
| `MAX_AMOUNT` | `utils.py` | Cap 1e9 — enforced on every amount input |

---

## 5. Inputs / Outputs

| Direction | Shape | Notes |
|-----------|-------|-------|
| **In (forms)** | `{date, category, subcategory ("—"→""), description, amount, currency, notes}` | Currency from dropdown, subcategory list depends on `CATEGORIES[cat]` |
| **In (enrichment)** | `amount_eur = to_eur(amount, currency, rates)` | Rates from `st.session_state.rates` (see `domain/currency-and-taxonomy.md`) |
| **In (recurring opt)** | `is_rec checkbox → add_recurring row + rec_template_id link` | Orphan recycle: if expense save fails after template creation, template is deactivated |
| **In (loan payment)** | `loan_id, loan_payment_type, loan_surcharge_eur` | Expense row doubles as loan payment (see `domain/planning-and-wealth.md`) |
| **In (OCR telemetry)** | `suggest_source/confidence/model_version/merchant/accepted` etc. | 8 nullable cols — measurement-first, never required |
| **Out (DB)** | Single row per mutation, scoped by `user_id` | String UUID PK for Expense/Income/Recurring |
| **Out (history)** | DataFrames sorted desc by date, paginated (25/50/100), searchable/filterable | Expense history uses `st.data_editor` diff + Trash checkbox column |

---

## 6. State & Ownership

| State | Owner | Mutator | Consumer | Persistence |
|-------|-------|---------|----------|-------------|
| Expense rows | `db.Expense` | `add/update/soft_delete/restore_expense` | `q.expenses`, insights, forecasting, budgets, notifications, portfolio loan recompute, household aggregate | `expenses` table, soft-delete via `is_deleted + deleted_at` |
| Income rows | `db.Income` | `add/update/soft_delete/restore_income` | `q.income`, hourly rate, forecast, insights | Same soft-delete semantics |
| Recurring templates | `db.Recurring` | `add_recurring, update_recurring` (also drag-board sort_order/category) | `q.recurring`, monthly checklist, `_unlogged_templates` (notifications) | `recurring.active` flag; no hard delete in UI |
| Salary settings | `UserSettings` | `q.save_settings` | `log_income` fixed-salary UX | JSON cols: `salary_amount/currency/day/active` |
| Audit history | `db.AuditLog` | `log_audit` (inside every session) | `q.audit` audit_log page | Append-only; no delete path; `details` JSON string |

**Versioning:** Every write path calls `q.bump_db_version()` (= `db.bump_data_revision`) after success — invalidates all `queries.py` caches sharing the same `data_revision`. Settings are read fresh (uncached) via `queries.get_settings`.

---

## 7. Execution Flows

### Flow A — Log normal expense (with optional recurring)

```text
User fills category → subcategory (— → "") + amount/currency + description + notes
  ├─ tick is_rec?
  │   └─ add_recurring(user_id, {category, subcategory, description, amount, currency, amount_eur, notes, active:true}) → rec_id
  ├─ duplicate guard: (date, description, amount_eur±0.005) in q.expenses → toast "Already saved"
  ├─ add_expense(user_id, {date, category, subcategory, description, amount, currency, amount_eur, recurring=is_rec, rec_template_id=rec_id, notes})
  ├─ on exception && rec_id → update_recurring(rec_id, {active:false}) // recycle orphan
  └─ q.bump_db_version() → balloons → rerun → history appears (q.expenses refreshed)
```

### Flow B — Edit history inline (expense) + Trash

```text
v = q.expenses sorted desc, filtered, paginated
st.data_editor(v[editable cols] + Trash checkbox) → edited DataFrame
  diff each row: _same(a,b) handles NaN/"" equivalence
  changed rows → update_expense(user_id, rid, {changed keys}) per row
  Trash ticked → soft_delete_expense(user_id, id) (sets is_deleted, deleted_at, audit DELETE)
on Save → q.bump_db_version()
Trash expander (below history) → restore_expense → toast → rerun
Excel export → to_excel(df_exp) (with _xl_safe guard)
```

### Flow C — Recurring "Log now" + drag board + due-day badge

```text
active = q.recurring filtered: active==true && filter_started_templates(active, today.year, today.month)
unlogged = notifications._unlogged_templates(active, q.expenses, today) // id set
for each template:
  due badge: due_day ? date(year,month, min(due_day, month_len)) vs today → overdue/due today/due in Nd
  card = {id, title "✅/⏳ desc", details "subcat · badge", amount fmt(amount_eur, DC, rates)}
groups = per-category cards in CAT_LIST order then alphabetical tail
draggable_card_board(groups, "recurring_order_{user_id}") → ordered groups + action
_persist_grouped_order: for each item_id → updates {sort_order=pos, category?, subcategory?="" if invalid} → update_recurring per changed row
action log → log_template_dialog(row): amount as "Log now" actual (may differ), currency picker, description file
action edit → edit_template_dialog; remove → update_recurring(active:false)
```

### Flow D — Log income (salary / hourly) + raise detection

```text
Fixed salary form: number amount+currency+payday+active → q.save_settings
One-tap salary: inc_type=Salary → checkbox use_fixed (default true) when salary_active
  use_fixed → actual=salary_amount, cur=salary_currency, date=YYYY-MM-min(salary_day, month_len)
  !use_fixed && actual > salary_amount+0.005 → raise_cb checkbox
  hourly: hours×rate computed → actual=budgeted=computed, hours/rate stored
  dedup: (date, income_type, actual_eur) guard
  add_income({date, source=inc_type, income_type, hours, rate, budgeted, actual, currency, budgeted_eur, actual_eur})
  raise_cb → q.save_settings(salary_amount=actual, salary_currency=cur, salary_active:true) + toast
  q.bump_db_version()
```

---

## 8. Dependencies

| Kind | Depends on | Why |
|------|-----------|-----|
| **Internal** | `utils.CATEGORIES/CAT_LIST, filter_started_templates, to_eur, fmt` | Category lists, start-month gate, currency conversion |
| **Other domains** | `persistence/data-model.md` (Expense/Income/Recurring tables) | Source of truth lives there |
| | `persistence/caching-and-revision.md` (queries cache + bump) | Every mutation must invalidate |
| | `persistence/encryption-and-crypto.md` | SQLCipher pragma on every Expense/Income connection |
| | `domain/currency-and-taxonomy.md` (rates, TAXONOMY_MIGRATION remap, MAX_AMOUNT) | Amounts stored dual original+Eur; legacy cats remapped on read |
| | `domain/planning-and-wealth.md` (loan_id on Expense, salary_day cycle) | Loan payments are expenses; salary cycle uses salary_day |
| | `intelligence/forecasting-and-anomalies.md` (categorizer retrain on expenses) | New expenses invalidate learned model |
| | `connectivity/notifications-and-market-data.md` (_unlogged_templates) | Bill-reminder logic |
| **External libs** | `streamlit (st.data_editor, st.dialog, st.toast), pandas, calendar` | Editor/pagination/date math |

---

## 9. Cross-Subsystem Interfaces

```text
Log Expense form ─category list─► domain/currency-and-taxonomy (CATEGORIES, to_eur, MAX_AMOUNT)
         │
         ├─to_eur────► domain/currency-and-taxonomy (rates from app-shell)
         └─add_expense─► persistence/data-model (Expense table, log_audit) ─► persistence/caching-and-revision (bump)
                                          │
                                          ├─e ─► intelligence/forecasting → categorizer cache invalidate
                                          └─e ─► connectivity/notifications (_unlogged_templates) + insights

Recurring template ─filter_started_templates─► domain/currency-and-taxonomy
                 ─draggable_card_board─► persistence/data-model (sort_order, category)
                 ─"Log now"──► add_expense (rec_template_id link, actual≠expected allowed)

Income form ─salary_* settings──► persistence/data-model (UserSettings) via queries.save_settings
          ─derive_hourly_rate──► domain/planning-and-wealth (big-purchases quadrant)
```

Links: [currency-and-taxonomy](../domain/currency-and-taxonomy.md), [caching-and-revision](../persistence/caching-and-revision.md), [data-model](../persistence/data-model.md), [planning-and-wealth](../domain/planning-and-wealth.md), [forecasting-and-anomalies](../intelligence/forecasting-and-anomalies.md), [notifications](../connectivity/notifications-and-market-data.md)

---

## 10. Architectural Invariants

- **User-scoped every call:** all `add/update/delete/get` filter `user_id` — no cross-user access even via sync/MCP. Violated only if caller forgets the predicate (tests cover).
- **Soft-delete only:** history rows are never hard-deleted; `is_deleted + deleted_at` + `include_deleted` flag on reads. Trash section restores via `is_deleted=False, deleted_at=None`.
- **Audit inside transaction:** `log_audit(session, ...)` shares the same `get_session()` context as the mutation — atomic commit or both rolled back.
- **Bump after every success:** `q.bump_db_version()` after each `add/update/delete/restore` and after salary settings save. Forgetting it leaves `st.cache_data` stale across sessions/household.
- **Template immutability:** editing a `Recurring` row never mutates already-logged `expenses` — they hold their own snapshot (diagnostic in edit dialog caption guarantees this expectation).
- **Orphan recycle:** recurring creation is not transactional with expense creation; on expense failure the orphan template is deactivated (`active:false`) rather than left dangling.
- **Duplicate guard exact:** `date+description+amount_eur rounded 2dp` — prevents double-submit on rerun; income uses `date+income_type+actual_eur`.
- **Subcategory sentinel:** UI "—" always stored as `""`; required for budget unique constraint (bare category = `subcategory=""`) and category remap code.
- **Start-month gate:** `start_month` is `"YYYY-MM"` string or `None`; `filter_started_templates` hides not-yet-started templates in checklist but `get_recurring` still returns them.
- **EUR dual storage:** original `amount/currency` kept alongside `amount_eur` for display/history; conversion via `to_eur` at write time — rates at read time only affect formatting (`fmt`), not stored Eur.

---

## 11. Change-Impact Guidance

| Changing | Must check |
|----------|-----------|
| Adding a column to Expense/Income | `db.py` model + `_EXP_COLS/_INC_COLS` + `add_*/update_*` whitelist + `_to_df` column lists + `log_expense/log_income` forms + `queries.py` no extra handling (DataFrame passthrough) + `sync_core.FIELD_SCHEMAS` + `api.py` caps + `mcp_server.py` if writable via MCP |
| Amount representation | `utils.to_eur / fmt`, `MAX_AMOUNT`, duplicate guard rounding, `finance.annuity_payment` unrelated, `to_excel` formatting, every test touching amounts |
| Soft-delete semantics | `get_expenses/include_deleted`, history Trash checkbox, `test_entry_editing`, sync include_deleted snapshot, audit log DELETE vs TRASH distinction |
| Recurring scheduling | `due_day` 0/None semantics, `filter_started_templates`, ``draggable_card_board`` grouped-order validation, `notifications._unlogged_templates` (description+amount legacy fallback), loan payment templates |
| Income types | `INCOME_SOURCES/TYPES`, `_LEGACY_INCOME_TYPES`, `finance.derive_hourly_rate` hourly branch, salary raise checkbox logic, household income not aggregated here |

---

## 12. Agent Modification Rules

- **Preserve soft-delete contract:** never add hard-delete paths; always expose restore alongside delete; call `q.bump_db_version()` symmetrically on restore.
- **Keep duplicate guards:** if adding fields that affect identity (e.g., currency), update the dedup predicate in both `log_expense.py:170` and `log_income.py:181`.
- **Never bypass audit:** every mutation goes through `db.add/update/delete` helpers that call `log_audit` — don't write raw SQLAlchemy adds outside them.
- **Use existing telemetry cols** for any suggestion-rating feature: cols `suggest_*` already exist (nullable); don't add new tables without removing legacy telemetry first.
- **Respect sentinel:** subcategory "—" → `""` on save; round-trip must stay consistent or budgets/recurring queries break.
- **Pagination state:** new filters must call `_reset_hist_page` and respect `exp_hist_page` clamping after deletions.
- **Tests to run:** `pytest tests/test_entry_editing.py tests/test_recurring.py tests/test_income.py tests/test_db.py -q` and smoke `tests/test_app_smoke.py`.
- **Preload before editing:** this file + `../persistence/data-model.md` + `../persistence/caching-and-revision.md` + `../domain/currency-and-taxonomy.md`. For recurring checklist also load `../connectivity/notifications-and-market-data.md`.

---

## 13. Common Tasks Router

| Task | Load this + | Also load |
|------|-------------|-----------|
| Fix expense history pagination/edit/trash | this file | `persistence/caching-and-revision.md`, `domain/currency-and-taxonomy.md` |
| Change income types / hourly math / raise flow | this file | `domain/planning-and-wealth.md` (quadrant), `persistence/data-model.md` |
| Modify recurring checklist / drag board / due badges | this file | `connectivity/notifications-and-market-data.md`, `domain/currency-and-taxonomy.md` |
| Add new ledger column or OCR telemetry | this file | `persistence/data-model.md`, `connectivity/sync-and-household.md`, `persistence/caching-and-revision.md` |
| Change duplicate guard or amount validation | this file | `domain/currency-and-taxonomy.md` (MAX_AMOUNT, to_eur), `persistence/data-model.md` |
| Audit log retention / new action type | this file | `persistence/data-model.md`, `app-shell/shell-and-navigation.md` (audit viewer) |
