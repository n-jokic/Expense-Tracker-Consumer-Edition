# UI migration — Phase 2 progress

> Operational artifact for Phase 2. Update as Panel / layout_state /
> GroupedBoard and the 8 page migrations land.

## Primitives

- **U1 Panel** — `ui/panel.py` (`PanelSpec(id, title, icon, collapsible, ...)`);
  shell only (header, collapse/expand, summary/badge, actions slot, drag handle
  when enabled, stable ID, layout-state integration).
- **U2 Persistent layout state** — `ui/layout_state.py` (`{version, dashboard:
  {order, collapsed}}` stored in user-settings JSON, not on domain rows).
- **U3 GroupedBoard v2** — extract `draggable_card_board` out of `utils.py`
  into `ui/board.py` (or `ui/grouped_board.py`). Returns `BoardResult(group_order,
  collapsed_groups, item_order, moved_items, action)` with `ItemMove(id, group,
  position)`. **Board never writes to DB.**

## Interaction capability matrix

| Area | Collapse | Reorder group | Reorder items | Cross-group semantic move |
|---|---|---|---|---|
| Dashboard | Yes | Yes | — | No |
| Wishlist (big_purchases) | Yes | Yes | Yes | Yes |
| Recurring | Yes | Yes | Yes | Yes |
| Savings goals | Yes | Yes | Maybe | No |
| Loans | Yes | Yes | No | No |
| Budgets | Yes | Optional | Optional | No |
| Portfolio | Yes | Optional | No | No |
| Settings | Yes | No | No | No |

## Migration order (Phase 2 — U5)

1. Recurring
2. Big Purchases
3. Dashboard
4. Savings
5. Loans
6. Budgets
7. Portfolio
8. Settings

## `st.fragment` usage (U6)

Selective: AI settings, boards, expandable dashboard sections with independent
refresh, OCR review. Not "every 5 lines".

## Status

| Step | File(s) | Owner | Status |
|---|---|---|---|
| U1 Panel | `ui/panel.py` | — | ☑ done (PanelSpec + header/collapse/actions/drag slot, no DB writes) |
| U2 layout_state | `ui/layout_state.py` | — | ☑ done (JSON in user_settings `ui_layout`, normalize + load/save/toggle) |
| U3 GroupedBoard v2 | `ui/board.py` | — | ☑ done (grouped_board w/ BoardResult+moved_items+action, caps enforced, utils compat) |
| U4 semantics matrix | (docs + component props) | — | ☑ enforced via grouped_board allow_* flags |
| Migration 1 | `app_pages/recurring.py` | — | ☑ done (grouped_board w/ allow_cross_group_move) |
| Migration 2 | `app_pages/big_purchases.py` | — | ☑ done (grouped_board w/ allow_cross_group_move) |
| Migration 3 | `app_pages/dashboard.py` | — | ☑ done (4 Panels: quick_actions, upcoming_bills, one_tap, recent) |
| Migration 4 | `app_pages/savings.py` | — | ☑ done (per-goal Panels in b761353) |
| Migration 5 | `app_pages/loans.py` | — | ☑ done (per-loan Panels in b761353) |
| Migration 6 | `app_pages/budgets.py` | — | ☑ done (4 Panels: overall, category, progress, all_rows) |
| Migration 7 | `app_pages/portfolio.py` | — | ☑ done (4 Panels: metrics, allocation, value_over_time, holdings) |
| Migration 8 | `app_pages/settings.py` + `settings_ai.py` | — | ☑ done (Settings tabs are native tab layout per spec; AI settings is fragmented via `st.fragment` + provider-keyed form — no per-tab Panel needed; optional Panel chrome deferred as spec allows) |
