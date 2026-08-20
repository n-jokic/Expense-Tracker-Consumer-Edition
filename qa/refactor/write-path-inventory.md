# Write-path inventory (part B) — data-mutating user actions

> Scope: every UI-triggered action that changes data, its DB call chain, commit
> count, audit coverage, cache-revision bump, and atomicity. Read-only
> inspection; no code changed.
>
> Covered files: `app_pages/*.py`, `db.py`, `queries.py`, `sync_core.py`,
> `bank_import.py`, `pdf_import.py` (plus the write helpers they delegate to in
> `market_data.py`, `rates.py`, `notifications.py`, `auth.py`, `mcp_server.py`).

## 0. Shared mechanics (read this first)

- **One commit per `get_session()` block.** `db.py:300-311` is a context
  manager: it yields a session, and on clean exit runs `session.commit()`, on
  exception `session.rollback()`, always `session.close()`. Every `db.add_*` /
  `db.update_*` / `db.soft_delete_*` / `db.restore_*` function opens exactly one
  such block, so **each function = 1 commit** (its own `log_audit` row rides in
  that same commit).
- **`add_budget` uses `s.flush()` internally** (`db.py:1566,1576,1588`) but is
  still one commit (the flush is needed to read back `obj.id`; the commit still
  happens once when the `get_session()` block exits).
- **Audit.** `log_audit` (`db.py:998`) just `session.add`s an `AuditLog` row; it
  commits with the surrounding session. So *any* db write that calls `log_audit`
  has its audit trail in the **same** commit as the data change. Writes that do
  **not** audit: `bump_data_revision`, `atomic_update_setting_json`,
  `touch_device_sync`, `record_milestones`/`mark_custom_milestone_achieved`
  (they audit in a *second, separate* commit), `backup_db` (filesystem, not a
  row).
- **Revision bump.** `q.bump_db_version()` (`queries.py:77`) wraps
  `bump_data_revision` (`db.py:925`), which is itself an **extra `engine.begin()`
  commit** (UPDATE `users.data_revision`, household-wide by default). It is a
  cache invalidation, not user data. The db write functions themselves **never**
  bump; page handlers call `q.bump_db_version()` *after* the write. A few paths
  bump but forget it or bump twice — noted per-path.
- **`q.save_settings`** (`queries.py:234`) = `db.save_settings` (1 commit) +
  refresh session snapshot + `bump_db_version()` (1 more commit). So every
  settings save is **2 commits** and **always bumps**.

---

## 1. Expenses

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Save OCR receipt expense | `app_pages/log_expense.py:92-138` | "Save expense" in `receipt_form` | `db.add_expense` (`db.py:1044`) | 1 | ✅ CREATE | ✅ (L134) | No (single row) |
| Save manual expense (+optional recurring template) | `app_pages/log_expense.py:165-216` | "Save expense" in `exp_form` | `db.add_recurring` (`db.py:1618`) **then** `db.add_expense` | 2 (one per fn) | ✅ both | ✅ once (L214) | **Yes** — if `add_expense` fails after `add_recurring` succeeded, handler compensates by marking template inactive (`update_recurring` L205) + a second bump (L209). Compensated, but not atomic. |
| Inline edit history rows | `app_pages/log_expense.py:321-395` | "Save changes" (data_editor) | `db.update_expense` (`db.py:1073`) **per row in loop** | **N** (1 per row) | ✅ per row | ✅ once (L389) | **Yes** — `for row: update_expense()`; on exception it `break`s (L384), leaving earlier rows committed and later rows untouched. |
| Trash ticked rows | `app_pages/log_expense.py:397-415` | "Move ticked rows to trash" | `db.soft_delete_expense` (`db.py:1133`) **per row** | **N** | ✅ per row | ✅ once (L411) | **Yes** — same loop-of-commits; `break` on first error (L406). |
| Restore one expense | `app_pages/log_expense.py:427-436` | "Restore" button | `db.restore_expense` (`db.py:1144`) | 1 | ✅ RESTORE | ✅ (L434) | No |
| Dashboard one-tap quick-add | `app_pages/dashboard.py:114-144` | preset buttons (Coffee/Lunch/Transit) | `db.add_expense` | 1 | ✅ | ✅ (L141) | No |
| Recurring "Log now" | `app_pages/recurring.py:103-148` | "Log it" in dialog | `db.add_expense` | 1 | ✅ | ✅ (L147) | No |
| Bank-import bulk (CSV/PDF) | `bank_import.py:638-676` | "Import N expenses" | `db.add_expense` **per row** via `_save_edited_row` (`bank_import.py:302`) | **N** (1 per imported row) | ✅ per row | ✅ once (L665) | **Yes, by design** — each row is an independent commit; `imported`/`skipped`/`failed` counted separately (L650-662). Partial import is the expected outcome. |
| Loan payment (expense) | `app_pages/loans.py:391-435` | "Log" in payment popover | `db.add_expense` **then** conditional `db.update_loan` | 1 or 2 | ✅ both | ✅ (L433) | **Yes** — `add_expense` + conditional `update_loan(status="paid_off")` are separate commits; on failure handler compensates by `soft_delete_expense(exp_id2)` (L426-430). |
| Loan early repayment (expense) | `app_pages/loans.py:260-304` | "Log early repayment" | `db.add_expense` **then** conditional `db.update_loan` | 1 or 2 | ✅ both | ✅ (L302) | **Yes** — same two-commit pattern; compensates via `soft_delete_expense(exp_id)` (L295-299). |
| Big purchase → expense handoff | `app_pages/big_purchases.py:177-203` | "Confirm & log expense" | `db.add_expense` **then** `db.update_big_purchase` | 2 | ✅ both | ✅ (L201) | **Yes** — and **uncompensated**: on exception it only `st.error` (L198-200), so the expense may be logged while the item is left unmarked. |

---

## 2. Income

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Save fixed salary | `app_pages/log_income.py:53-64` | "Save salary" | `q.save_settings` → `db.save_settings` (`db.py:1973`) | 2 (save + bump) | ✅ UPDATE user_settings | ✅ (inside `q.save_settings`) | No |
| One-tap log salary | `app_pages/log_income.py:87-111` | "Log my salary…" | `db.add_income` (`db.py:1189`) | 1 | ✅ CREATE | ✅ (L109) | No |
| Save income entry (+optional raise) | `app_pages/log_income.py:188-205` | "Save income" | `db.add_income` **then**, if `raise_cb`, `q.save_settings` | 1 or 3 (add + save-settings[2]) | ✅ add; ✅ settings | ✅ twice (save_settings bumps, then L204 bumps again) | **Yes** — income row commits first; a failing `save_settings` (raise update) leaves the income logged but the fixed-salary raise unsaved. No compensation. |
| Edit income entry | `app_pages/log_income.py:244-259` | "Save" in edit dialog | `db.update_income` (`db.py:1208`) | 1 | ✅ UPDATE | ✅ (L257) | No |
| Trash income entry | `app_pages/log_income.py:286-295` | "Move to trash" | `db.soft_delete_income` (`db.py:1223`) | 1 | ✅ DELETE | ✅ (L293) | No |
| Restore income entry | `app_pages/log_income.py:315-324` | "Restore" | `db.restore_income` (`db.py:1234`) | 1 | ✅ RESTORE | ✅ (L322) | No |

---

## 3. Recurring expenses

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Add template | `app_pages/recurring.py:175-205` | "Save template" | `db.add_recurring` (`db.py:1618`) | 1 | ✅ CREATE | ✅ (L203) | No |
| Edit template | `app_pages/recurring.py:83-100` | "Save changes" in dialog | `db.update_recurring` (`db.py:1637`) | 1 | ✅ UPDATE | ✅ (L98) | No |
| Remove template (deactivate) | `app_pages/recurring.py:311-314` | "Remove" card action | `db.update_recurring(… {"active": False})` | 1 | ✅ UPDATE | ✅ (L313) | No |
| Log now | `app_pages/recurring.py:103-148` | "Log it" | `db.add_expense` | 1 | ✅ | ✅ (L147) | No |
| Reorder templates | `app_pages/recurring.py:217-267` (`_persist_grouped_order`) | drag-and-drop card board | `Recurring` ORM updates + `log_audit` in **one** custom session | **1** (`s.commit()` L257) | ✅ per row (same txn) | ✅ once (L266) | **No** — this was the formerly N+1 loop; now a single transaction/single bump (comment L240 "T4-005+A-002"). |

> **Delete / reorder note:** there is no `db.delete_recurring` — "Remove" is a soft
> deactivation (`active=False`). Reorder no longer loops commits.

---

## 4. Savings & term deposits

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Quick deposit | `app_pages/savings.py:90-117` | "Save deposit" | `db.add_savings` (`db.py:1319`) | 1 | ✅ CREATE | ✅ (L114) | No |
| Quick withdraw | `app_pages/savings.py:135-162` | "Save withdrawal" | `db.add_savings` | 1 | ✅ CREATE | ✅ (L159) | No |
| New-goal / entry form | `app_pages/savings.py:454-501` | "Save entry" | `db.add_savings` | 1 | ✅ CREATE | ✅ (L493) | No |
| Edit entry | `app_pages/savings.py:399-414` | "Save" in `edit_savings_dialog` | `db.update_savings` (`db.py:1338`) | 1 | ✅ UPDATE | ✅ (L412) | No |
| Trash entry | `app_pages/savings.py:789-798` | "Move to trash" | `db.soft_delete_savings` (`db.py:1356`) | 1 | ✅ DELETE | ✅ (L796) | No |
| Restore entry | `app_pages/savings.py:812-820` | "Restore" | `db.restore_savings` (`db.py:1367`) | 1 | ✅ RESTORE | ✅ (L818) | No |
| Edit goal (rename + target/rate) | `app_pages/savings.py:188-209` | "Save goal" | `db.rename_savings_goal` (`db.py:1380`) **then** `db.update_savings_goal` (`db.py:1421`) | 2 | ✅ RENAME then ✅ UPDATE | ✅ once (L207) | **Yes** — rename commits first; if `update_savings_goal` then fails, the goal is renamed but target/rate not applied. Each function internally loops over rows *inside one session* (atomic per function). |
| Delete goal | `app_pages/savings.py:223-231` | "Delete goal" | `db.soft_delete_savings_goal` (`db.py:1439`) | 1 | ✅ DELETE (one row) | ✅ (L229) | No — trash entries + remove term accounts in one session/commit. |
| Open term deposit | `app_pages/savings.py:639-674` | "Open term deposit" | `db.add_savings_account` (`db.py:1481`) | 1 | ✅ CREATE | ✅ (L667) | No |
| Edit term deposit | `app_pages/savings.py:318-335` | "Save" in `edit_account_dialog` | `db.update_savings_account` (`db.py:1498`) | 1 | ✅ UPDATE | ✅ (L333) | No |
| Delete term deposit | `app_pages/savings.py:347-356` | "Delete" | `db.soft_delete_savings_account` (`db.py:1512`) | 1 | ✅ DELETE | ✅ (L354) | No |
| Restore term deposit | `app_pages/savings.py:826-834` | "Restore" | `db.restore_savings_account` (`db.py:1525`) | 1 | ✅ RESTORE | ✅ (L832) | No |
| Withdraw & close term deposit | `app_pages/savings.py:255-280` | "Withdraw and close" | `db.add_savings` **then** `db.update_savings_account(status="closed")` | 2 | ✅ both | ✅ (L277) | **Yes** — payout `add_savings` commits, then close `update_savings_account` commits separately; if the second fails the payout is logged but the account stays "active". No compensation (L273-276 only `st.error`). A re-read status guard (L259-263) mitigates double-click, not atomicity. |

---

## 5. Loans

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Add loan | `app_pages/loans.py:73-105` | "Save loan" | `db.add_loan` (`db.py:1728`) | 1 | ✅ CREATE | ✅ (L99) | No |
| Edit loan | `app_pages/loans.py:193-213` | "Save" in `edit_loan_dialog` | `db.update_loan` (`db.py:1749`) | 1 | ✅ UPDATE | ✅ (L211) | No |
| Delete loan | `app_pages/loans.py:119-128` | "Delete loan" | `db.delete_loan` (`db.py:1761`) | 1 | ✅ DELETE | ✅ (L126) | No (payments stay as expenses) |
| Log payment | `app_pages/loans.py:391-435` | "Log" in popover | `db.add_expense` + conditional `db.update_loan(status="paid_off")` | 1 or 2 | ✅ both | ✅ (L433) | **Yes** — compensated via `soft_delete_expense(exp_id2)` on failure. |
| Early repayment | `app_pages/loans.py:260-304` | "Log early repayment" | `db.add_expense` + conditional `db.update_loan` | 1 or 2 | ✅ both | ✅ (L302) | **Yes** — compensated via `soft_delete_expense(exp_id)`. |
| Mark paid off / reopen | `app_pages/loans.py:462-469` | "Mark paid off"/"Reopen" | `db.update_loan` | 1 | ✅ UPDATE | ✅ (L468) | No |

---

## 6. Wishlist / big purchases

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Add item | `app_pages/big_purchases.py:89-114` | "Add to wishlist" | `db.add_big_purchase` (`db.py:1671`) | 1 | ✅ CREATE | ✅ (L112) | No |
| Change status (selectbox) | `app_pages/big_purchases.py:31-37` (`_bp_update_status`) + call sites L326-332 / L387-394 | status selectbox / card status action | `db.update_big_purchase` | 1 | ✅ UPDATE | ✅ (L37) | No |
| Confirm purchase → expense | `app_pages/big_purchases.py:177-203` | "Confirm & log expense" | `db.add_expense` **then** `db.update_big_purchase(status="bought")` | 2 | ✅ both | ✅ (L201) | **Yes, uncompensated** — expense commits first; if `update_big_purchase` fails, expense exists but item not marked bought (only `st.error`, no rollback/compensation). |
| Edit item | `app_pages/big_purchases.py:242-259` | "Save" in `edit_purchase_dialog` | `db.update_big_purchase` (`db.py:1690`) | 1 | ✅ UPDATE | ✅ (L257) | No |
| Delete item | `app_pages/big_purchases.py:347-355` and `402-408` | "Delete" (popover / card action) | `db.delete_big_purchase` (`db.py:1702`) | 1 | ✅ DELETE | ✅ (L354 / L407) | No |
| Restore archived item | `app_pages/big_purchases.py:315-324` | "Restore" | `db.update_big_purchase(status="wishlist")` | 1 | ✅ UPDATE | ✅ (L323) | No |
| Reorder items | `app_pages/big_purchases.py:267-291` (`_persist_grouped_order`) | drag-and-drop card board | `db.update_big_purchase` **per item in loop** | **N** (1 per changed item) | ✅ per row | ✅ once (L290) | **Yes — loop-of-commits** (`for item: update_big_purchase()`). Unlike recurring, this was **not** consolidated: each row is its own commit, and per-item failures are caught and reported but the loop *continues* (no `break`), so a partial reorder can succeed silently for some items and fail for others. |

---

## 7. Budgets

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Overall monthly budget | `app_pages/budgets.py:77-84` | "Save budget" (overall form) | `q.save_settings` → `db.save_settings` | 2 (save + bump) | ✅ UPDATE user_settings | ✅ (inside `q.save_settings`) | No |
| Category budget (upsert) | `app_pages/budgets.py:105-118` | "Save" (category form) | `db.add_budget` (`db.py:1548`, upsert with race-retry) | 1 | ✅ CREATE or UPDATE | ✅ (L116) | No (single-row upsert) |
| Delete budget row | `app_pages/budgets.py:35-43` | "Delete row" in dialog | `db.delete_budget` (`db.py:1594`) | 1 | ✅ DELETE | ✅ (L41) | No |

> There is no dedicated `update_budget`/`set_budget` in `db.py` — "set" is the
> `add_budget` upsert, and "set overall monthly budget" is a settings key.

---

## 8. Portfolio holdings

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Add holding | `app_pages/portfolio.py:73-103` | "Save holding" | `db.add_holding` (`db.py:1798`) | 1 | ✅ CREATE | ✅ (L98) | No |
| Edit quantity | `app_pages/portfolio.py:260-267` | "Save" (manage row) | `db.update_holding` (`db.py:1817`) | 1 | ✅ UPDATE | ✅ (L266) | No |
| Remove holding | `app_pages/portfolio.py:238-245` | "Delete holding" in dialog | `db.delete_holding` (`db.py:1829`, deletes `HoldingPrice` rows + holding in one session) | 1 | ✅ DELETE | ✅ (L243) | No |
| Refresh prices (manual) | `app_pages/portfolio.py:49-57` → `market_data.refresh_prices_if_due` (`market_data.py:111`) | "Refresh prices" | `db.update_holding` **then** `db.add_holding_price` **per holding** (loop `market_data.py:132-147`) | **2N** (2 per holding) | ✅ update_holding + ✅ add_holding_price | ✅ once (portfolio L53) | **Yes** — per-holding loop; a network `None` skips that holding (L135-136) and a mid-loop exception leaves earlier holdings refreshed and later ones stale. Snapshot append (`add_holding_price`) commits separately from `update_holding`, so a holding can get a new `last_price` without its snapshot (or vice-versa). |
| Refresh prices (background) | `market_data.maybe_refresh_in_background` (`market_data.py:151`) | login-time auto | same as above | 2N | ✅ per row | ✅ via `bump_data_revision(include_household=False)` (L172) | **Yes** — same per-holding granularity. |

---

## 9. Settings / household / travel (+ related settings writers)

| Action | UI handler (file:line) | Trigger widget | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| Currency & rates | `app_pages/settings.py:91-107` | "Save" (currency form) | `q.save_settings` → `db.save_settings` | 2 | ✅ UPDATE user_settings | ✅ | No |
| Refresh rates now | `app_pages/settings.py:119-128` → `rates.refresh_rates_if_due` (`rates.py:93`) | "Refresh rates now" | `q.save_settings` | 2 | ✅ | ✅ | No (single settings row) |
| Update display name | `app_pages/settings.py:141-152` | "Update name" | `db.update_user_display_name` (`db.py:2202`) | 1 | ✅ UPDATE users | ✅ (L150) | No |
| Change password | `app_pages/settings.py:160-168` → `auth.change_password` → `db.update_user_password` (`db.py:2192`) | "Change password" | `db.update_user_password` | 1 | ✅ UPDATE users | ❌ (no bump; password not cache-keyed) | No |
| Delete account | `app_pages/settings.py:174-186` | "Delete account permanently" | `db.delete_user_account` (`db.py:2213`, multi-table hard delete) | 1 (all deletes in one session) | ✅ DELETE (via audit? see note) | ❌ (account gone; `logout()` instead) | No — single session covers every table delete. |
| GitHub backup config | `app_pages/settings.py:308-321` | "Save configuration" | `q.save_settings` | 2 | ✅ | ✅ | No |
| GitHub backup run | `app_pages/settings.py:323-330` | "Back up to GitHub now" | background `run_github_backup` (writes `gh_*` settings) | n/a (background) | ✅ (settings writes) | n/a | n/a |
| AI settings | `app_pages/settings_ai.py:88-113` | "Save AI settings" | `q.save_settings` | 2 | ✅ | ✅ | No |
| Notification settings | `notifications.py:606-620` | "Save" (notif form) | `q.save_settings` | 2 | ✅ | ✅ | No |
| Disable email alerts | `notifications.py:640-643` | "Save (disabled)" | `q.save_settings` | 2 | ✅ | ✅ | No |
| Weekly-summary sent stamp | `notifications.py:545` | (background `_on_delivered` callback) | `db.save_settings` | 1 | ✅ | ❌ (background; no Streamlit session) | No |
| Email "sent" markers | `notifications.py:200-215` → `db.atomic_update_setting_json` (`db.py:1937`) | (background delivery callback) | `atomic_update_setting_json` (`engine.begin()` read-modify-write) | 1 | ❌ (no audit) | ❌ | No — single-statement JSON merge. |
| Fun money | `app_pages/rewards.py:84-92` | "Save fun money" | `q.save_settings` | 2 | ✅ | ✅ | No |
| Travel budget + categories | `app_pages/travel.py:63-73` | "Save" (travel setup) | `q.save_settings` | 2 | ✅ | ✅ | No |
| Create household | `app_pages/household.py:66-81` | "Create household" | `db.create_household` (`db.py:2014`) | 1 | ✅ CREATE households | ✅ (L77) | No |
| Join household | `app_pages/household.py:85-103` | "Join household" | `db.join_household` (`db.py:2072`) | 1 | ✅ UPDATE users | ✅ (L99) | No |
| Regenerate invite code | `app_pages/household.py:117-129` | "Regenerate invite code" | `db.regenerate_invite_code` (`db.py:2041`) | 1 | ✅ UPDATE households | ✅ (L126) | No |
| Leave household | `app_pages/household.py:44-57` | "Leave household" | `q.bump_db_version()` **first** then `db.leave_household` (`db.py:2103`) | 2 (bump + leave) | ✅ UPDATE users | ✅ (intentional bump-before-leave) | **Yes (by design)** — bump commits before the leave so other members' caches invalidate while the user is still a member; if `leave_household` then fails, caches are bumped but membership unchanged (benign). |

> **Delete-account audit note:** `delete_user_account` (`db.py:2213-2261`) deletes
> the `AuditLog` rows **within** the same session as everything else, so the
> deletion of other tables is *not* captured in an audit trail (the audit rows
> for the account are destroyed by the same commit).

---

## 10. Bank import / PDF import

| Action | File:line | Trigger | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|---|
| PDF parse (no writes) | `pdf_import.extract_transactions_from_pdf` (`pdf_import.py:488`) | upload .pdf | — pure parse → DataFrame | 0 | n/a | n/a | n/a |
| CSV/PDF import save loop | `bank_import.py:638-676` | "Import N expenses" | `db.add_expense` **per row** (`_save_edited_row` `bank_import.py:302-387`) | **N** (1 per row) | ✅ per row | ✅ once (L665) | **Yes, by design** — rows commit independently; `imported`/`skipped`/`failed` tallied (L650-662). A row whose `amount_eur` is NaN (unknown currency) is skipped *before* any write (L318). |

> The bulk import is the clearest "multiple commits that can leave partial
> state" path: there is no outer transaction, so an interrupt/failure mid-import
> leaves the first *k* rows committed and the rest unimported (no duplicates,
> thanks to the `existing_keys` dedupe set, but the import is not all-or-nothing).

---

## 11. Sync paths (`sync_core.py`, `api.py`, `db.apply_record_fields`)

| Action | File:line | Called function(s) | Commits | Audit? | Bump? | Can partially succeed? |
|---|---|---|---|---|---|---|
| Apply a device change batch | `sync_core.apply_changes` (`sync_core.py:469-510`) | `_apply_update` / `create_record` / `add_sync_conflict` — **per change in loop** | **N** (1 per change) | ✅ each write (`via:"sync"`); conflicts audit as UPDATE/CREATE + `SyncConflict` row | ❌ (bumped later by API layer) | **Yes, by design** — result splits into `applied`/`conflicts`/`failed`; each change is its own commit. |
| `create_record` | `sync_core.py:387-430` | model insert + `log_audit` in one session | 1 | ✅ CREATE | ❌ | No (IntegrityError → return False) |
| `_apply_update` | `sync_core.py:433-466` | read-compare-write in one session | 1 | ✅ UPDATE (or conflict, no write) | ❌ | No (conflict path writes nothing) |
| Conflict resolution — keep device value | `app_pages/settings.py:398-412` | `db.apply_record_fields` (`db.py:2624`) **then** `db.resolve_sync_conflict` (`db.py:2609`) | 2 | ✅ UPDATE table, ✅ UPDATE sync_conflicts | ✅ (L408) | **Yes** — `apply_record_fields` commits first; if `resolve_sync_conflict` then fails, the record is updated but the conflict row stays unresolved (user can re-resolve; not data loss, but not atomic). |
| Conflict resolution — keep server value | `app_pages/settings.py:414-422` | `db.resolve_sync_conflict` | 1 | ✅ UPDATE sync_conflicts | ✅ (L420) | No |
| API `/api/sync` (v1) | `api.py:106-119` | `apply_changes` (N) → `_bump_after_sync` → `touch_device_sync` → `snapshot` | N + 1 (bump) + 1 (touch) | ✅ per change | ✅ (L126, only if applied/conflicts) | **Yes** — batch is not atomic; partial application is reported per-change. |
| API `/api/v2/sync` | `api.py:129-143` | same as v1 with server-issued cursor | N + 2 | ✅ per change | ✅ (L138) | **Yes** — same per-change granularity. |
| MCP write tools (`add_expense` / `add_income`) | `mcp_server.py:355-428` | `db.add_expense` / `db.add_income` | 1 each | ✅ | ❌ (no Streamlit session; separate service) | No (single row each). |

---

## 12. Summary of atomicity risks (which paths can partially succeed)

### A. Loop-of-commits handlers (`for row: write(); commit()` patterns)

| Handler | File:line | Per-row fn | Commits | Failure behavior |
|---|---|---|---|---|
| Expense inline edit | `log_expense.py:321-395` | `update_expense` | N | `break` on error → earlier rows committed, later rows not saved. |
| Expense trash ticked rows | `log_expense.py:397-415` | `soft_delete_expense` | N | `break` on error → partial trash. |
| Bank import bulk | `bank_import.py:638-676` | `add_expense` | N | partial import is the intended outcome; counted, not rolled back. |
| Big-purchase reorder | `big_purchases.py:267-291` | `update_big_purchase` | N | per-item error caught but loop **continues** → silent partial reorder. |
| Portfolio price refresh | `market_data.py:132-147` | `update_holding` + `add_holding_price` | 2N | per-holding skip/failure → partially refreshed portfolio. |
| Sync batch | `sync_core.py:482-509` | `_apply_update` / `create_record` | N | partial application is the intended protocol; `applied/conflicts/failed` reported. |

> **Contrast:** recurring reorder was the same N+1 loop historically but is now
> a **single** transaction + single bump (`recurring.py:240-266`, `s.commit()` at
> L257). Big-purchases reorder (`big_purchases.py:267-291`) is the remaining
> in-page loop-of-commits reorder.

### B. Multi-step (2+ distinct commits) single-action handlers

| Handler | File:line | Steps | Compensation? |
|---|---|---|---|
| Manual expense + recurring template | `log_expense.py:165-216` | `add_recurring` → `add_expense` | ✅ marks template inactive on failure (L201-211). |
| Income entry + salary raise | `log_income.py:188-205` | `add_income` → `q.save_settings` | ❌ none (income stays if raise save fails). |
| Edit savings goal | `savings.py:188-209` | `rename_savings_goal` → `update_savings_goal` | ❌ none (rename can persist w/o target/rate). |
| Term-deposit withdraw & close | `savings.py:255-280` | `add_savings` → `update_savings_account(status=closed)` | ❌ none (payout can persist w/o close). |
| Loan log payment | `loans.py:391-435` | `add_expense` → `update_loan(paid_off)` | ✅ `soft_delete_expense` on failure. |
| Loan early repayment | `loans.py:260-304` | `add_expense` → `update_loan(paid_off)` | ✅ `soft_delete_expense` on failure. |
| Big purchase → expense | `big_purchases.py:177-203` | `add_expense` → `update_big_purchase(bought)` | ❌ **none** — highest-value uncompensated risk. |
| Sync "keep device value" | `settings.py:398-412` | `apply_record_fields` → `resolve_sync_conflict` | ❌ none (non-destructive; conflict row stays). |
| Leave household | `household.py:44-57` | `bump_db_version` → `leave_household` | n/a (benign bump-then-leave). |

### C. Notable commit-count facts

- `q.save_settings` is always **2 commits** (save + revision bump), so every
  settings-family save (currency, AI, notifications, fun money, travel, overall
  budget) is two commits with the bump folded in.
- `db.add_budget` is **1 commit** despite internal `flush()` + a rollback/retry
  branch (`db.py:1577-1590`); the retry still commits once.
- `db.record_milestones` (`db.py:2480`) and `db.mark_custom_milestone_achieved`
  (`db.py:2563`) each use an `engine.begin()` commit for the write **plus a
  second `get_session()` commit** for the audit trail — two commits, and the
  audit commit can fail independently of the milestone write.
- `db.bump_data_revision` is a separate `engine.begin()` commit on every bump;
  page handlers that bump after a write therefore always produce at least
  **write-commit + bump-commit**.

### D. Writes without audit trail

| Function | Why |
|---|---|
| `db.atomic_update_setting_json` (`db.py:1937`) | raw SQL JSON merge; no `log_audit`. |
| `db.touch_device_sync` (`db.py:2451`) | device `last_sync_at`/token-window refresh; not audited. |
| `db.bump_data_revision` (`db.py:925`) | cache counter only. |
| `db.record_milestones` / `db.mark_custom_milestone_achieved` | the write itself is audited in a **separate later commit**; if that second commit fails, the milestone write is un-audited. |
| `db.delete_user_account` (`db.py:2213`) | deletes the `AuditLog` rows in the same session, so no surviving trail of the deletion. |

### E. Writes without a revision bump (caches may not refresh)

- `db.update_user_password` (password change — not cache-keyed).
- Background callbacks that have no Streamlit session: `notifications._persist_marker`
  (`atomic_update_setting_json`) and `_db_save_settings` for
  `weekly_summary_last_sent` (`notifications.py:545`).
- `mcp_server` write tools (separate service; no `q.bump_db_version`).
- `db.record_milestones` / `db.mark_custom_milestone_achieved` (callers in
  `gamification.py` may or may not bump; not a page-level `q.bump_db_version`).
