# Reactive State Audit — Form Dependency Inventory

**Scope:** every `st.form(...)` in the repository.
**Invariant:** a widget inside a Streamlit form must not dynamically determine another widget in that same form (existence, choices, label, bounds, default, validation, or semantics).
**Rationale:** inside a Streamlit form, widgets only re-render on submit. A widget whose presence/choices/label/bounds depend on a *sibling widget in the same form* is rendered against a stale value: the user types/reads against one state and submits another, producing silently wrong data or uninstantiated variables.

This document is read-only inventory + fix plan for `R1`. No behavior changes are made here.

---

## 1. Summary table

| # | File | Form key | Line | Widgets inside form | Violation |
|---|------|----------|------|---------------------|-----------|
| 1 | `auth.py` | `login_form` | 208 | username, password (text_input) | N |
| 2 | `auth.py` | `register_form` | 233 | r_display, r_username, r_email, r_pass, r_confirm | N |
| 3 | `notifications.py` | `notif_form` | 571 | alert_email, smtp_host, smtp_port, smtp_user, smtp_pass, days_before, weekly | N |
| 4 | `onboarding.py` | `onboard_step1` | 54 | dc, rate_val, budget | **Y** |
| 5 | `onboarding.py` | `onboard_exp` | 92 | exp_date, cat, amount, desc | N |
| 6 | `app_pages/big_purchases.py` | `bp_form` | 73 | bp_name, bp_cat, bp_price, bp_cur, bp_use, bp_imp, bp_notes | N |
| 7 | `app_pages/budgets.py` | `overall_bud_form` | 64 | ob_amt | N |
| 8 | `app_pages/budgets.py` | `cat_bud_form` | 92 | by, bm, bsub, ba | N (bcat/bcur outside) |
| 9 | `app.py` | `rate_form` | 120 | rsd_val | N |
| 10 | `app_pages/log_expense.py` | `receipt_form` | 60 | r_date, r_cat, r_sub, r_amt, r_desc, r_notes | **Y** |
| 11 | `app_pages/log_expense.py` | `exp_form` | 150 | exp_date, subcat, amount, is_rec, desc, notes | N (cat/cur outside) |
| 12 | `app_pages/household.py` | `hh_create` | 64 | hh_name | N |
| 13 | `app_pages/household.py` | `hh_join` | 85 | code_in | N |
| 14 | `app_pages/loans.py` | `loan_form` | 44 | l_name, l_cur, l_principal, l_rate, l_start, l_term, l_day, l_notes, l_surcharge_type, l_surcharge_value | **Y** |
| 15 | `app_pages/log_income.py` | `salary_setup` | 34 | s_amt, s_cur, s_day, s_active | N |
| 16 | `app_pages/log_income.py` | `inc_form` | 121 | inc_date, hours, hr_rate, budgeted, actual, use_fixed, raise_cb, notes | **Y** |
| 17 | `app_pages/portfolio.py` | `hold_form` | 60 | h_symbol, h_name, h_qty, h_cur, h_cost | **Y** |
| 18 | `app_pages/settings_ai.py` | `ai_form` | 33 | ai_provider, ai_model_path, ai_gpu, ai_base, ai_model, ai_key | **Y** |
| 19 | `app_pages/settings.py` | `cur_form` | 77 | dc2, per-currency rate inputs | N |
| 20 | `app_pages/settings.py` | `display_name_form` | 139 | new_name | N |
| 21 | `app_pages/settings.py` | `pw_form` | 155 | old_pw, new_pw, conf_pw | N |
| 22 | `app_pages/settings.py` | `gh_backup_form` | 288 | gh_on, gh_repo, gh_token, gh_ret | N |
| 23 | `app_pages/recurring.py` | `rec_form` | 157 | rsub, rdesc, ramt, rdue, rnotes, rstart | N (rc/rcat outside) |
| 24 | `app_pages/rewards.py` | `fun_form` | 74 | f_amt, f_cats | N |
| 25 | `app_pages/rewards.py` | `custom_ms_form` | 128 | cm_title, cm_metric, cm_target, cm_reward | N |
| 26 | `app_pages/savings.py` | `sav_form` | 420 | sd, gn_sel, new_goal, tgt, ir, cur, dep, notes | **Y** |
| 27 | `app_pages/savings.py` | `savacc_form` | 617 | a_goal, a_name, a_cur, a_amt, a_rate, a_start, a_mat, a_notes | **Y** |
| 28 | `app_pages/travel.py` | `travel_setup` | 40 | t_amt, t_cats | N |

**Result:** 28 forms total. 8 flagged (rows 4, 10, 14, 16, 17, 18, 26, 27).

---

## 2. Detailed per-form analysis (flagged forms)

### 2.1 `ai_form` — `app_pages/settings_ai.py:33` — P0

The provider selector gates the existence of all provider-specific controls:

```python
33:     with st.form("ai_form"):
34:         ai_provider = st.selectbox(
35:             "Provider",
36:             ["none", "local", "api"],
...
42:             key="ai_provider_select")
43:         ai_model_path = ai_gpu = ai_base = ai_model = ai_key = None
44:         if ai_provider == "local":
45:             ai_model_path = st.text_input(
46:                 "GGUF model file path", ...)
...
66:             ai_gpu = st.number_input(
67:                 "GPU layers (-1 = all to GPU, 0 = CPU)", ...)
71:         elif ai_provider == "api":
72:             ai_base = st.text_input("API base URL", ...)
75:             ai_model = st.text_input("Model name", ...)
78:             ai_key = st.text_input("API key", type="password", ...)
```

**Violation:** `ai_provider` (selectbox, inside form) determines whether `ai_model_path`/`ai_gpu` (local branch) or `ai_base`/`ai_model`/`ai_key` (api branch) are even instantiated. Switching provider and saving in a single submit leaves the *other* provider's fields `None`; the code already carries compensating comments and guards (lines 88–105) precisely because of this defect:

```python
89:         # NB: the provider-specific fields only exist when their provider is
90:         # SELECTED at render time — switching the selectbox and saving in one
91:         # submit leaves them None. In that case keep the stored values ...
```

**Impact:** switching "Off → local" (or "local → api") and clicking Save in one submission cannot capture the newly visible fields; the save silently keeps stored values or uses defaults. This is the canonical failing case for the invariant.

---

### 2.2 `receipt_form` — `app_pages/log_expense.py:60` — P0

The category selectbox drives the subcategory selectbox's choices and default inside the same form:

```python
64:                     r_cat  = st.selectbox(
65:                         "Category", CAT_LIST,
66:                         index=CAT_LIST.index(result["category"])
67:                         if result["category"] in CAT_LIST else 0,
68:                         key="rcpt_cat")
69:                     r_sub  = st.selectbox(
70:                         "Subcategory", ["—"] + CATEGORIES[r_cat],
71:                         index=(list(["—"] + CATEGORIES[r_cat]).index(result["subcategory"])
72:                                if result["subcategory"] in CATEGORIES[r_cat] else 0),
73:                         key="rcpt_sub")
```

**Violation:** `r_cat` (inside form) determines `r_sub`'s option list `["—"] + CATEGORIES[r_cat]` and its default index. If the OCR guessed a category the user then changes before submitting, the subcategory list the user saw was for the wrong category; the submitted `r_sub` index maps into a different option set, or the OCR subcategory is dropped to `"—"` when it is not in the new category's list.

---

### 2.3 `sav_form` — `app_pages/savings.py:420` — P1

Two separate same-form dependencies:

**(a) goal selector → new-goal fields (existence):**

```python
425:         gn_sel = st.selectbox("Goal", goal_options)
426:         new_goal = ""
427:         tgt = 0.0
428:         ir = 0.0
429:         if gn_sel == "➕ New goal...":
430:             new_goal = st.text_input("New goal name", placeholder="e.g. New laptop")
431:             tgt = st.number_input(f"Target ({SYM})", ...)
434:             ir = st.number_input("Annual interest rate (%)", ...)
438:         else:
439:             _grows = goal_rows(dfs_all, gn_sel)
440:             _gt, _gr, _gc = goal_attrs(_grows)
441:             st.caption(f"Target: {fmt(_gt, DC, rates) ...}")
```

**Violation:** `gn_sel` (selectbox, inside form) controls whether `new_goal`/`tgt`/`ir` are instantiated. The submit handler mirrors the branch (`goal_name = new_goal.strip() if gn_sel == "➕ New goal..." else gn_sel`), so a user who flips to "New goal" and submits in the same action gets `new_goal = ""` (never rendered) → "Please name your new goal." error, and `tgt`/`ir` fall back to `0.0`.

**(b) currency → amount label:**

```python
445:         cur = st.selectbox("Save in", list(SUPPORTED_CURRENCIES.keys()), key="sav_cur")
446:         sym = get_currency_symbol(cur)
447:         dep = st.number_input(f"Amount ({sym}) — negative = withdrawal", ...)
```

**Violation:** `cur` (inside form) sets `dep`'s label. The amount label the user sees is for the previously submitted currency, not the currency they just selected.

---

### 2.4 `loan_form` — `app_pages/loans.py:44` — P1

Two same-form dependencies:

**(a) currency → amount label:**

```python
49:         l_cur     = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="loan_cur")
50:         l_principal = st.number_input(f"Principal ({get_currency_symbol(l_cur)})",
51:                                       min_value=0.0, max_value=MAX_SAVINGS_TARGET, ...)
```

**Violation:** `l_cur` (inside form) sets `l_principal`'s label symbol.

**(b) surcharge type → value bounds + step:**

```python
62:         l_surcharge_type = st.selectbox(
63:             "Early repayment surcharge",
64:             ["fixed", "percent"],
65:             format_func=lambda v: "Fixed amount" if v == "fixed" else "Percentage",
66:         )
67:         l_surcharge_value = st.number_input(
68:             "Surcharge value (% or loan currency)", min_value=0.0,
69:             max_value=100.0 if l_surcharge_type == "percent" else MAX_SAVINGS_TARGET,
70:             step=0.1 if l_surcharge_type == "percent" else 10.0,
71:             format="%.2f", value=0.0,
72:         )
```

**Violation:** `l_surcharge_type` (inside form) determines `l_surcharge_value`'s `max_value` and `step`. Changing the type and submitting in one action submits a value bounded/stepped under the *old* type.

---

### 2.5 `savacc_form` — `app_pages/savings.py:617` — P1/P2

```python
623:                 a_cur  = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()),
624:                                       key="savacc_cur")
625:                 a_amt  = st.number_input(f"Amount ({get_currency_symbol(a_cur)})",
626:                                          min_value=0.0, max_value=MAX_AMOUNT, ...)
```

**Violation:** `a_cur` (inside form) sets `a_amt`'s label. `a_goal` (line 621) does **not** drive any other widget — the goal selector here is safe; only the currency→label dependency is flagged.

---

### 2.6 `hold_form` — `app_pages/portfolio.py:60` — P2

```python
68:         h_cur    = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="hold_cur")
69:         h_cost   = st.number_input(f"Total invested ({get_currency_symbol(h_cur)})",
70:                                    min_value=0.0, max_value=MAX_SAVINGS_TARGET, ...)
```

**Violation:** `h_cur` (inside form) sets `h_cost`'s label. Same currency→label pattern as loans/savings.

---

### 2.7 `inc_form` — `app_pages/log_income.py:121` — P2

`inc_type` (line 116) and `cur` (line 118) are correctly placed **outside** the form, so the type-driven and currency-driven branches against them are safe. The one same-form dependency is the fixed-salary "raise" checkbox:

```python
142:     use_fixed = False
143:     raise_cb  = False
144:     if inc_type == "Salary" and salary_active and salary_amount > 0:
145:         use_fixed = st.checkbox(
146:             f"Use my fixed salary (...)",
147:             value=True)
148:         if not use_fixed and float(actual) > salary_amount + 0.005:
149:             raise_cb = st.checkbox("Update my fixed salary — this is a raise", value=True)
```

**Violation:** `use_fixed` (checkbox, inside form) — and `actual` (number_input, inside form) — determine whether `raise_cb` is instantiated. Unticking "Use my fixed salary" and submitting in one action means `raise_cb` was never rendered, so the raise is not recorded even when the typed `actual` exceeds the stored salary.

*(Note: the hourly branch at lines 125–140 keys off the **outside** `inc_type`, so `hours`/`hr_rate`/`budgeted`/`actual` existence is **not** a same-form violation.)*

---

### 2.8 `onboard_step1` — `onboarding.py:54` — P2

```python
54:         with st.form("onboard_step1"):
55:             dc = st.selectbox("Display currency", list(SUPPORTED_CURRENCIES.keys()),
56:                               index=dc_idx, help="...")
57:             rate_val = None
58:             if dc != "EUR":
59:                 rate_val = st.number_input(
60:                     f"Exchange rate (1 EUR = ? {get_currency_symbol(dc)})", ...)
64:             budget   = st.number_input("Monthly budget (EUR)", ...)
```

**Violation:** `dc` (selectbox, inside form) determines whether the `rate_val` number_input exists. Switching from EUR to a non-EUR currency and continuing in one submit submits the never-rendered `rate_val` (default), and the submit guard `if dc != "EUR" and not (rate_val > 0 ...)` operates on a value the user never saw/typed.

---

## 3. Explicit violations to fix in R1 (priority order)

| Priority | Form / file | Dependency to eliminate |
|----------|-------------|-------------------------|
| **P0** | `ai_form` — `app_pages/settings_ai.py:33` | `ai_provider` → existence of local/API field sets |
| **P0** | `receipt_form` — `app_pages/log_expense.py:60` | `r_cat` → `r_sub` choices + default |
| **P1** | `sav_form` — `app_pages/savings.py:420` | `gn_sel` → existence of `new_goal`/`tgt`/`ir` |
| **P1** | `loan_form` — `app_pages/loans.py:44` | `l_cur` → `l_principal` label; `l_surcharge_type` → `l_surcharge_value` bounds/step |
| **P1** | `sav_form` — `app_pages/savings.py:420` | `cur` → `dep` label |
| **P1** | `savacc_form` — `app_pages/savings.py:617` | `a_cur` → `a_amt` label |
| **P2** | `hold_form` — `app_pages/portfolio.py:60` | `h_cur` → `h_cost` label |
| **P2** | `onboard_step1` — `onboarding.py:54` | `dc` → existence of `rate_val` |
| **P2** | `inc_form` — `app_pages/log_income.py:121` | `use_fixed`/`actual` → existence of `raise_cb` |

### Fix strategy per class

- **Provider/type/selector-driven existence (P0/P1 — ai_form, receipt_form, sav_form gn_sel, onboard_step1, inc_form raise_cb):** move the "driving" widget **outside** the form so its value re-renders the form contents before submit (the pattern already used correctly by `exp_form` and `rec_form`), *or* render all variants unconditionally with stable keys and gate only the save logic. Rendering every variant with stable keys is preferred for `ai_form` because it preserves one-submit provider switching.
- **Currency → amount label (P1/P2 — loan_form, sav_form, savacc_form, hold_form):** move the currency selectbox outside the form (matching `exp_form`/`rec_form`/`inc_form`), *or* fix the label to the display currency `DC`/a currency-agnostic unit. Moving the selector outside is the consistent, low-risk fix.
- **Bounds/step driven by type (loan_form surcharge):** move `l_surcharge_type` outside the form, *or* use a fixed generous `max_value` and validate the percent range in the submit handler.

### Currently correct — do not change

- `exp_form` (`log_expense.py:150`): `cat` (line 145) and `cur` (line 147) are **outside** the form; `subcat` uses the outside `cat` (line 154) and `amount` uses the outside `cur` symbol. Correct.
- `cat_bud_form` (`budgets.py:92`): `bcat` (line 90) and `bcur` (line 91) are **outside** the form; `bsub` choices and `ba` label reference them. Correct.
- `rec_form` (`recurring.py:157`): `rc` (line 154) and `rcat` (line 155) are **outside**; `rsub`/`ramt` reference them. Correct.
- `inc_form` (`log_income.py:121`): `inc_type`/`cur` outside (only the `raise_cb` sub-dependency is flagged above).
- `salary_setup` (`log_income.py:34`): `s_cur` is inside but does not affect any other widget's label/bounds (amount label is static). Not a violation.
- `bp_form` (`big_purchases.py:73`): `bp_cur` inside but `bp_price` label is static (`"Price"`); no sibling dependency.

---

## 4. Acceptance criteria checklist for R1

- [ ] **AI settings (P0):** every provider-specific control in `settings_ai.py` is instantiated on every render of `ai_form` (or the provider selector is moved outside the form). Switching provider and saving in a single submit persists the newly selected provider's fields without relying on the "keep stored values" guard.
- [ ] **Receipt category (P0):** in `log_expense.py` `receipt_form`, the subcategory list and its default reflect the category the user actually submits in a single action (e.g. category selector moved outside the form, or subcategory rendered with stable options + post-submit whitelist validation like `exp_form`).
- [ ] **Savings goal selector (P1):** in `savings.py` `sav_form`, choosing "➕ New goal..." and saving in one submit does not produce the empty-name error and does not silently zero the target/interest rate.
- [ ] **Loans (P1):** in `loans.py` `loan_form`, the principal label and the surcharge value's max/step match the currency/type the user submits in a single action.
- [ ] **Savings currency labels (P1):** in `sav_form` (`dep`) and `savacc_form` (`a_amt`), the amount label matches the submitted currency.
- [ ] **Portfolio (P2):** in `portfolio.py` `hold_form`, the "Total invested" label matches the submitted currency.
- [ ] **Onboarding (P2):** in `onboarding.py` `onboard_step1`, selecting a non-EUR currency and continuing in one submit surfaces a rate field the user can see/type before save.
- [ ] **Income raise checkbox (P2):** in `log_income.py` `inc_form`, unticking "Use my fixed salary" and submitting a higher `actual` in one action records the raise.
- [ ] No widget inside any form reads another same-form widget's value to set `options`, `index`/`value`, `min_value`/`max_value`, `step`, `label`, `disabled`, or its own existence (grep audit: no `st.<widget>(...)` argument references a sibling widget variable declared inside the same `with st.form` block).
- [ ] Each fixed form has a stable `key=` on any widget whose position/existence moved (session state must not collide across providers/types).
- [ ] Existing duplicate-prevention, validation, and currency-conversion semantics (`to_eur`, `fmt`, `MAX_*` bounds) are preserved; no behavior change beyond resolving the reactive dependencies.
- [ ] `pytest` suite (or at minimum the form-relevant smoke/`test_app_smoke.py` and page tests) passes; no new import cycles introduced.
