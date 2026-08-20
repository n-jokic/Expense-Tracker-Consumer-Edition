# Insights & LLM — Agent Intelligence Contract

> **Source of truth:** `insights.py` (510 LOC), `llm.py` (461 LOC),
> `app_pages/insights_view.py`, `app_pages/ask.py`, `app_pages/settings_ai.py`
> **Tests:** `tests/test_insights.py`, `tests/test_llm.py`
> **Owner:** Agent 7 — Intelligence (Forecasting / Insights / LLM)

---

## 1. Purpose & Scope

| Layer | File | Role |
|-------|------|------|
| **Insight engine** | `insights.py` | Pure analysis helpers + `render_insights()` (14 rule-based cards + ML scans). Receives DataFrames/DB dicts from the caller; **never writes to the DB** and **never fetches data itself** (except the decorative LLM narrative, which is explicitly optional). |
| **LLM engine** | `llm.py` | Optional Gemma helpers (local GGUF via `llama-cpp-python` **or** OpenAI-compatible API via `requests`). Every public call returns `str | None` — `None` always means “no LLM” and the caller falls back to rule-based text. No caller is ever blocked by the LLM. |
| **Pages** | `insights_view.py` (thin delegate), `ask.py` (chat), `settings_ai.py` (provider UI) | Wire the two layers into Streamlit; enforce the **privacy contract** for `Ask`. |
| **Market data** | `market_data.py` | Optional portfolio-price input — not part of insights/LLM but documented here because holdings can appear in insight contexts (e.g., portfolio pages). |

---

## 2. Insights — Pure Functions

### 2.1 Caller-feeds-DataFrames contract

Insight helpers are **stateless**: the page layer (`insights_view.py`) loads data via `queries as q` and passes DataFrames in. Insights code does not call `q.*` except:
- `q` is used in `insights_view.py`, not inside `insights.py` analysis helpers.
- `insights.render_insights` reads `detect_anomalies` / `detect_subscriptions` from `forecasting.py` internally (ML scans at render time).
- `gamification.detect_raise` and `utils` helpers (`fmt`, `get_currency_symbol`, `fun_spent`, …) are formatting/domain helpers only.

Return types are always **strings/dicts/DataFrames for rendering** — no budget or expense rows are created or mutated.

### 2.2 Analysis helpers

#### `month_over_month(df, col, current_year, current_month) -> dict`
```python
{ "current": float, "previous": float, "change_pct": float, "trend": "up"|"down"|"same" }
```
- Filters `df` by `(date.dt.year==year & date.dt.month==month)` vs previous calendar month (wraps year: Jan -> Dec of prior year).
- `change_pct = round(((cur - prev)/prev)*100, 1)`; when `prev==0`: `100.0` if `cur>0` else `0.0`. Trend follows the sign of `cur - prev`.
- Guards: `df.empty or col not in df.columns` -> zeros with `"same"`.

#### `top_category_this_month(expenses_df, year, month) -> (category, amount) | None`
- Slices by year+month, groups by `category`, `idxmax`. Returns `None` when input or month slice is empty.

#### `unusual_expenses(expenses_df, multiplier=2.0) -> DataFrame`
- `multiplier` default **2.0** at the helper; callers (`build_narrative_stats`, `render_insights`) pass **2.5**.
- Returns rows where `amount_eur > multiplier * category_mean`. Empty input returns a schema-preserving empty frame `df.iloc[0:0]` (keeps `amount_eur` dtype).

#### `days_until_budget_depleted(expenses_df, total_budget_eur, period_start: date) -> int | None`
- `days_elapsed = max((today - period_start).days + 1, 1)`. Period slice: `date >= period_start and date <= today` (future-dated rows excluded).
- Guards: `total_budget <= 0 or df.empty -> None`; empty period slice -> `None`; `daily_avg <= 0 -> None`; `remaining <= 0 -> 0` (already over budget).
- Otherwise `int(remaining / daily_avg)`.
- Edge: `math.isfinite` check on the raw budget before calling (see §2.4).

#### `savings_projection(savings_df, goal_name) -> dict`
```python
{ "current_balance": float, "target": float, "months_to_goal": int|None, "projected_date": date|None }
```
- Filters `goal_name`, sorts by `date`, takes `latest` as last row.
- `balance = latest.balance_eur` (0.0 on NaN), `target = max(target_eur)` (0.0 on NaN/empty), `interest_rate = latest.interest_rate` (0.0 on NaN).
- Guards: `target <= 0 or balance >= target -> months=0, projected=today`.
- **Monthly deposit:** when `len(rows) >= 2`, resamples `deposited_eur` by `MS` (`dropna()`), takes `mean(tail(3))`; fallback to `rows["deposited_eur"].mean()` when resampled frame is empty or only one row.
- Critical guard: `pd.isna(monthly_dep) or monthly_dep <= 0 -> None` (withdrawals or NaN cannot reach goal — no bogus projection). Capped loop: iterates `cur_bal = cur_bal*(1+monthly_rate)+monthly_dep` up to **600 months**; `>=600 -> None`.
- `projected_date = date(year + (month-1+months)//12, (month-1+months)%12+1, 1)`.

#### `build_narrative_stats(expenses_df, settings, year, month) -> dict`
Sanitized snapshot shared by the page and the LLM. Built from the same helpers above:
```python
{
  "spent_eur": float, "prev_spent_eur": float, "change_pct": float,
  "top_category"?: "CAT (AMT EUR)",
  "unusual"?:       ["desc (amount EUR)", ... up to 3, this month only],
  "budget_remaining"?: float,  # only when monthly_budget > 0
}
```
- Uses `month_over_month` and `top_category_this_month`; `unusual` is filtered to the current month and capped at `head(3)`.

### 2.3 Insight strings generation

`render_insights(expenses_df, income_df, savings_df, settings, DC, rates, recurring_df, loans_df, user_id)` builds a `cards: list[(type, markdown_text)]` and renders each as `st.success / warning / error / info` with Material icons. **14 card families** (see §4). If `cards` stays empty, renders a single `st.info("Log some…")`.

- **Budget inputs:** `total_budget` comes from `settings["monthly_budget"]` first; only when that is `0`/`NaN`/`inf` does the forecast-page path look at `budgets` table (`effective_category_budgets`). Insights itself only uses `settings` budgets (+ `fun_money`, `travel_budget`). `math.isfinite` is applied before using the raw setting.
- **Budget guards:** `days_until_budget_depleted` is only called when `total_budget > 0` and `not expenses_df.empty`; `fun_money`/`travel_budget` cards only when `> 0`.
- **No DB writes inside insights.** The subscription section shows “add as recurring” via `db.add_recurring` — but the row is created only on user button click inside the Streamlit fragment, not by the analysis helpers themselves.

### 2.4 Pure-function guarantees

| Property | Enforcement |
|----------|-------------|
| No DB writes | Helpers return `dict/list/str/DataFrame`; `render_insights` only reads data passed in + calls `detect_anomalies`/`detect_subscriptions` (read-only ML) |
| No side effects | No mutations of input DataFrames (copies where needed); no global state |
| Budget source | Explicitly from `settings` (+ `recurring_df`/`loans_df` DFs) — not queried from inside |
| Narrative is optional | Wrapped in `try: generate_narrative(...) except Exception: pass` — a failing model never breaks the page |
| Future dates ignored | `days_until_budget_depleted` and no-spend-days filter `date <= today` |

---

## 3. LLM — Providers & Contracts

### 3.1 Provider resolution

```python
DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_API_MODEL = "google/gemma-3-12b-it"
DEFAULT_LOCAL_MODEL_FILENAME = "google_gemma-3-1b-it-Q4_K_M.gguf"
LOCAL_RUNTIME_INSTALL_HINT = ".venv-clean\\Scripts\\python.exe -m pip install --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/vulkan llama-cpp-python==0.3.34"
```

```python
def resolve_provider(settings: dict) -> "none" | "local" | "api":
```
| Input | Result |
|-------|--------|
| `ai_provider` missing/unknown | `"none"` |
| `ai_provider == "local"` but `ai_local_model.strip()==""` **and** `find_bundled_model()==None` | `"none"` |
| `ai_provider == "api"` but `decrypt_str(ai_api_key_enc or "")==""` | `"none"` |
| Otherwise | the normalized `ai_provider` when in `("local","api")` |

```python
def find_bundled_model() -> str | None:
    path = model_dir() / DEFAULT_LOCAL_MODEL_FILENAME
    return str(path) if path.is_file() else None
# model_dir() = state_dir()/"models"  (state_dir = EXPENSE_TRACKER_DATA_DIR or %LOCALAPPDATA%/ExpenseTracker or data/)
```

Every public generator (`generate_summary`, `generate_narrative`, `answer_query`) calls `resolve_provider` first and returns `None` immediately when it is `"none"` — `requests` / `llama_cpp` are **never touched** in `"none"`.

### 3.2 Local GGUF path

```python
@dataclass
class LocalResult:
    text: str | None
    diagnostic: str = ""
```

Model location (first match wins):
1. `settings["ai_local_model"]` (user-typed GGUF path), `strip()`’d; when `== ""` fall through.
2. `find_bundled_model()` (`model_dir()/gemma-3-1b` auto-detected for source + installed layouts).
3. No file -> `LocalResult(None, "Choose a GGUF model file before testing Local AI.")`.
4. Path does not exist (`Path(path).is_file()==False`) -> `LocalResult(None, "GGUF model file does not exist: {path}")`.

Settings keys:
- `ai_local_model: str` — absolute GGUF path.
- `ai_local_gpu_layers: int` — `-1` = all on GPU, `0` = CPU, `N>0` = N layers on GPU. Non-numeric garbage -> `-1`. Stored as `int`.
- `ai_api_key_enc: str` — **Fernet-encrypted** (see §3.5), `decrypt_str`’d only inside `llm.py`.
- `ai_api_base: str` — defaults to `DEFAULT_API_BASE`, `rstrip("/")`’d for `/chat/completions`.
- `ai_api_model: str` — defaults to `DEFAULT_API_MODEL`.

### 3.3 `_get_local_model(settings)` — load-once, lock, fallbacks

```python
_local_lock = threading.Lock()          # one generation at a time
_local_cache: tuple = ()                # (path, gpu_layers, llama_instance) | ()
_last_result: LocalResult | None = None # backing store for local_diagnostic()
```

- Clears `_last_result = LocalResult(None, "")` on entry (no stale diagnostics, A2.3).
- **GPU layers** parsing is inside `try: int(...); except (TypeError, ValueError): gpu_layers=-1`.
- **Cache key is `(path, gpu_layers)`** — changing `gpu_layers` without an app restart forces a fresh `Llama(...)` construction; same pair returns the cached instance.
- **`llama_cpp` import is lazy inside the function** with `except Exception` (not just `ImportError`): missing Vulkan/MSVC DLLs raise `OSError` on attribute access — caught with `_runtime_missing_diagnostic()`.
- **`_runtime_missing_diagnostic() -> str`:**
  - `getattr(sys, "frozen", False) == True` (PyInstaller installed build) -> `"The bundled llama.cpp runtime is unavailable. Reinstall Expense Tracker."`
  - Otherwise (source run) -> `LOCAL_RUNTIME_INSTALL_HINT` (exact `pip install … --extra-index-url … llama-cpp-python==0.3.34` command). Verified by `test_local_import_importerror_message_source_vs_frozen`.
- **Constructor:** `Llama(model_path=path, n_ctx=2048, n_gpu_layers=gpu_layers, verbose=False)`.
- **Vulkan failure fallback:** If construction raises and `gpu_layers != 0`, retries once with `n_gpu_layers=0` (CPU), stores `_last_result = LocalResult(None, "Vulkan initialization failed; using CPU fallback. Original error: {e}")` and caches `(path, 0, model)`. If CPU also fails (or `gpu_layers==0` originally), returns `LocalResult(None, "Could not load this GGUF model: {e}")`.
- Thread-safe via `with _local_lock:` in `_local_chat`.

### 3.4 Diagnostics

```python
def local_runtime_status(settings) -> tuple[bool, str]:
    """WITHOUT loading the model. Exists + importable -> (True, ""). Else (False, one-liner)."""
```
- Checks resolved path exists; then `from llama_cpp import Llama` as existence probe. On probe failure, sets `_last_result` and returns `(False, diag)`.
- Used by **Ask page badge** and **Settings “Runtime ready / missing” captions** (reflects the path as typed before saving).

```python
def local_diagnostic() -> str:
    return _last_result.diagnostic if _last_result else ""
```
- Backing-store pattern: every `_get_local_model` / `_local_chat` / `_api_chat` path writes `_last_result` so the Settings “Test summary” warning can surface the actionable reason without exposing a stack trace.

### 3.5 API path — Fernet, `requests` vs `llama_cpp` lazy import, provider guards

- **Key storage:** `settings["ai_api_key_enc"] = encrypt_str(plain)` (Fernet). Read path: `key = decrypt_str(settings.get("ai_api_key_enc") or "")`. Plain key is held only in a local var and in `headers={"Authorization": f"Bearer {key}"}` — never logged, never echoed in error strings.
- **Call:** `requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}", "Content-Type":"application/json"}, json={"model": model_name, "messages": [...], "max_tokens": int(max_tokens), "temperature": 0.7}, timeout=15)` -> `raise_for_status()` -> `resp.json()["choices"][0]["message"]["content"]`. A `try/except Exception` wrapper logs `LLM API request failed (type: e)` without the key and returns `LocalResult(None, "The API request failed — check the API key and base URL…")`.
- **`requests` is imported at top level** (always available); **`llama_cpp` is imported lazily** inside `_get_local_model` / `local_runtime_status` only when the local provider is active — the rest of the app never needs it (README “OPTIONAL” install). Verified by `test_none_provider_never_touches_requests` (boom stub for `requests.post` must not be called when provider is `none`).
- **Provider guards:** `resolve_provider == "none"` returns `None` without touching either runtime. `_local_chat` is only called when `provider=="local"`; `_api_chat` only when `provider=="api"`. A `generate_*` / `answer_query` call with the wrong provider is a no-op.

### 3.6 Prompt sanitization & history

```python
def _sanitize_stat(value) -> str:
    s = str(value).replace("\r"," ").replace("\n"," ")
    return s[:100]  # all statutes capped
```

- Applied to **every** stat, category name, description, question, and history turn before embedding in the prompt. Newlines collapse so a synced row cannot inject *“Ignore previous instructions…”*.
- `answer_query` caps each history turn content at `[:200]` after `_sanitize_stat(... )[:100]` style truncation; only the last **4 turns** are sent as `CHAT SO FAR:\n{role}: {content}\n…`.
- Question itself sanitized as `_sanitize_stat(question or "")` and capped at implicit 100 (plus outer 200 in history). Empty after strip -> `None`.
- System prompts are frozen constants:
  - `_SUMMARY_SYSTEM`: weekly email paragraph, 2–4 sentences, second person, plain text, no markdown/emoji, no advice, use only numbers provided.
  - `_NARRATIVE_SYSTEM`: Insights-page narrative — same constraints, neutral tone.
  - `_ASK_SYSTEM`: chat — 1–4 plain sentences, may do sums/averages on provided numbers only, no advice/predictions, must admit when DATA cannot answer.

### 3.7 Public LLM surface

| Function | Prompt keys | `max_tokens` | Returns |
|----------|-------------|--------------|---------|
| `generate_summary(stats, settings)` | `total_eur, prev_week_eur, top_categories[], fun_remaining?` | 256 | `str \| None` |
| `generate_narrative(stats, settings)` | `spent_eur, prev_spent_eur, change_pct, top_category?, unusual[]?, budget_remaining?` | 256 | `str \| None` |
| `answer_query(user_id, question, settings, history?)` | `build_data_context(user_id, settings)` + `QUESTION` | 300 | `str \| None` |

`build_data_context` (called inside `answer_query`, never directly by pages):
- Today’s date, this/previous month counts+EU totals, top 5 expense categories (sanitized), monthly budget remaining, fun-money allowance, per-goal balances+targets, active loans (principal+rate), up to 8 recurring bills, previous-month totals, all-time counts, logging streak, last 10 expenses (desc+cat+EUR+date). All numbers `round(...,2)`, NaN -> `0.0`, dates `isoformat` or `?`, strings `_sanitize_stat`’d. Any exception during context building returns `None` from `answer_query` (never surfaces).
- Internally calls `get_expenses / get_income / get_savings / get_loans / get_recurring / get_logging_streak` with `user_id`.

All generators unwrap `_generate(...).text` and return `text.strip() or None`. Any `Exception` (including inside `_generate`) is caught and yields `None` so **email sending is never blocked** by the LLM.

---

## 4. Insights Page — 14 Cards & Wiring

`app_pages/insights_view.py` is a one-line delegate:
```python
render_insights(q.expenses(user_id), q.income(user_id), q.savings(user_id),
                settings, DC, rates, q.recurring(user_id), q.loans(user_id), user_id)
```

Inside `render_insights` (each card shown only when its guard passes):

| # | Card | Guard | Logic |
|---|------|-------|-------|
| 0 | **AI narrative** (decorative) | `generate_narrative(build_narrative_stats(...), settings)` is truthy | `st.container(border=True)` with `**In short**` + narrative; `try/except: pass` so it never breaks the page |
| 1 | MoM spending | `not expenses.empty` | `month_over_month` -> warning(up)/success(down)/info(same) |
| 2 | Top category + MoM | `top_category_this_month is not None` | `month_over_month` filtered to that `category` to add “up/down/steady” |
| 3 | Unusual expense | `unusual_expenses(multiplier=2.5)` filtered to current month non-empty | `nlargest(1, amount_eur)` + `mult = amount/cat_avg` |
| 4 | Savings projections | `not savings.empty` -> per `goal_name` | `savings_projection`; `months>0` -> success, `months==0` -> goal-reached |
| 5 | Budget burn rate | `total_budget>0 and not expenses.empty` | `days_until_budget_depleted`; `0`->error, `<7`->warning (days left), `>=remaining_days_in_month`->success |
| 6 | Income vs expense ratio | `not income.empty and not expenses.empty` + `inc>0 and exp>0` | `saved_pct=100-ratio`; `>=20%` success, `>=10%` info, `<0` error |
| 7 | Top merchants | `not expenses.empty` -> month slice non-empty | `groupby(description).nlargest(3)` |
| 8 | No-spend days | `not expenses.empty` | `today.day - len(spent_days)` where `spent_days` from `date.dt.day` of month slice `<=today`; `>=3` -> success |
| 9 | Fixed costs | `recurring_df is not None and not empty` | `active==True` sum |
| 10 | Income highlights | `not income.empty` | `detect_raise` + bonus (`Bonus/Raise`) + by-type leader |
| 11 | 30d vs prev 30d | `not expenses.empty and p_sum>0` | `recent=[today-30..today]` vs `prev=[today-60..today-30)`; `abs(pct)>=10` -> error(success) with % |
| 12 | Loans | `loans_df is not None and not empty` | `status==active` + `finance.annuity_payment` sum -> `/month` |
| 13 | Fun money | `fun_money>0 and not expenses.empty` | `fun_spent(df, fun_categories, y, m)` vs `NEAR_LIMIT_THRESHOLD` (from `utils`) |
| 14 | Travel budget | `travel_budget>0 and not expenses.empty` | `travel_spent(df, travel_categories, year)` vs `budget_pct` vs `year_pct` (`leap` aware) |
| ML | **Anomaly scan** | `detect_anomalies(expenses_df)` non-empty | Table head(10) sorted by `date desc`: `date`, `description`, `category`, `Amount` (`to_display`), `Vs median` (`multiplier>x else —`) |
| ML | **Subscription scan** | `detect_subscriptions(expenses_df)` non-empty | Head(6) with “add as recurring” `add_recurring` button per row |

Formatting: `fmt(amount, DC, rates)`, `get_currency_symbol(DC)`, `to_display`. All amounts are EUR-native (`amount_eur`) and converted only for display via `rates`.

---

## 5. Ask — Privacy Contract

> `app_pages/ask.py` header: *“the model only ever receives a sanitized snapshot of NUMERIC aggregates (plus your recent transaction descriptions, stripped and capped), never credentials, and — with the local provider — nothing leaves this machine at all.”*

| Rule | Enforcement |
|------|-------------|
| Only aggregates + capped descriptions | `build_data_context` + `_sanitize_stat` on every field; provider badge uses only `os.path.basename(path)` and `settings[ai_api_model]` |
| Never credentials | `ai_api_key_enc` is `decrypt_str`’d only into a local var/header; history/context strings never contain keys; logs explicitly note “Never echo the key” |
| Local leaves nothing | Local path never calls `requests`; no network egress. Verified by provider-guard tests |
| Failures stay out of transcript | `ask.py`: failed `answer_query` shows `st.error(local_diagnostic() or fallback)` and **does not append** an assistant message; next prompt’s `history[:-1]` can’t replay the failure |
| Chat input locked during generation | `st.chat_input(..., submit_mode="disable")` — long local generation can’t be interrupted mid-run |
| Suggested pills only on empty chat | `SUGGESTIONS` shown when `ask_history` is empty; `st.pills` + `st.rerun` pattern |
| Clear chat | Button resets `st.session_state.ask_history = []` |

`resolve_provider` badge logic in `ask.py`:
- `local`: `local_runtime_status(settings)` → `Local Gemma ready` or `model file missing` (`"does not exist" in diag`) or `runtime missing` (else).
- `api`: `API ready` + `settings[ai_api_model]`.
- `none`: `st.info("not configured yet…")` + `st.stop()`.

---

## 6. Settings — AI Assistant UI

`app_pages/settings_ai.py::render_ai_settings(user_id, settings)` — split out of `notifications.py` so the email module never imports the LLM stack. Rendered below the email settings on the Settings page.

- Header + caption note “falls back to built-in templates — see README for downloads”.
- `selectbox [none, local, api]` (formatted as Off / Local Gemma (…) / External API (…)).
- `local`: `text_input GGUF path` (placeholder `C:\models\…`, auto-fills from `find_bundled_model()`), live `local_runtime_status(merged)` indicator (green/red badge), `number_input gpu layers min -1 max 999`.
- `api`: `text_input base URL` (default `DEFAULT_API_BASE`), `text_input model name` (default `DEFAULT_API_MODEL`), `text_input API key type=password` (placeholder “Leave blank to keep…” — only overwritten when non-empty, encrypted via `crypto.encrypt_str`).
- `Save AI settings` + `Test summary` as two `st.columns` submit buttons in the same `st.form("ai_form")` (form name + field keys stable so session state survives the move out of `notifications.py`). Save: `q.save_settings(user_id, updates)`; `api` path only overwrites `ai_api_key_enc` when `ai_key` is truthy. Test: `generate_summary({total_eur:123.45, prev_week:98.20, top:[Groceries 52.10, Transport 18.00]}, merged)` under `st.spinner`, `try/except Exception` so a bad runtime surfaces as `st.error(type(e).__name__: e)` not a page crash; on success `st.success(out)` else `st.warning(local_diagnostic() or "No summary generated…")`.
- Switching providers and saving in one submit: provider-specific fields are `None` when the other provider was selected at render — keep stored values instead of overwriting with defaults.

---

## 7. Degradation Table

| Condition | Result |
|-----------|--------|
| `elapsed_months < 6` | `forecasting._ets_forecast -> (None,None,None)`; `forecast_next_month -> fallback=True`; page uses period-average; insights never calls the forecast path |
| `len(expenses) < 20` | `detect_anomalies -> empty DataFrame` (no flags, scan hidden) |
| `len(expenses) < 10` or `< 2 categories` | Categorizer `train() -> False`; `suggest_category -> None,0.0`; keyword map fallback |
| `len(pivot) < 6` | `cluster_month_patterns -> {ok:False, reason:"short_history"}` |
| Missing `statsmodels` | `forecasting.* -> fallback/None` via broad `except` |
| Missing `sklearn` | `detect_anomalies` empty; categorizer `False`; `cluster_month_patterns -> {ok:False, reason:"no_sklearn"}` |
| Missing `llama_cpp` | `llm._get_local_model -> None`; `local_runtime_status -> (False, diag)`; `resolve_provider` -> `"none"` when no bundled file; all LLM generators return `None`; page shows “Runtime missing” badge/hint; email uses rule-based text |
| Missing `requests` / API down | `llm._api_chat -> LocalResult(None, friendly diag)`; never crashes; public generators return `None` |
| Any exception inside `generate_*` / `answer_query` | `None` (caller renders fallback); weekly email explicitly guarantees “can never be blocked or broken by the LLM” |
| `math.isfinite` / `NaT` / `NaN` in budgets/rates | Guards coerce to `0.0`; `savings_projection` returns “no projection” instead of a fake 1-month goal |

---

## 8. Optional Intelligence Input — `market_data.py`

Same freshness rule as forecasting: stale does not block the app.
- `holdings.last_price + last_price_date` persists; failures keep the previous value.
- `PRICES_MAX_AGE_DAYS=1`, `_fetch_cached` `@st.cache_data(ttl=1800)` (even `None`), `maybe_refresh_in_background` daemon thread + `_refresh_lock` + `bump_data_revision` on success.

---

## 9. Optional-Dependency Guards

```python
# forecasting / insights / llm — lazy + broad
try:
    from sklearn.ensemble import IsolationForest      # or ExponentialSmoothing / KMeans / TfidfVectorizer
    from llama_cpp import Llama                      # llm.py only; lazy
    import requests                                  # llm.py top-level (always installed) but API path still guarded
except Exception:                                     # OSError matters on Windows (missing DLLs)
    return safe_sentinel
```

- `llama_cpp` import is **lazy inside the function** and wrapped as `except Exception` so an `OSError` (“DLL load failed: %1 is not a valid Win32 application”) surfaces as a **diagnostic**, never a crash. Verified by `test_local_import_oserror_is_caught`.
- Frozen vs source diagnostic split is verified by `test_local_import_importerror_message_source_vs_frozen`.
- Vulkan → CPU fallback (`n_gpu_layers: -1 → 0`) is verified by `test_local_model_retries_cpu_after_vulkan_load_failure`.
- Stale-diagnostic clear on success (`_last_result` reset at top of `_get_local_model`) verified by `test_stale_diagnostic_cleared_on_successful_reload`.
- Cache key includes `gpu_layers` verified by `test_local_cache_key_includes_gpu_layers`.

---

## 10. Test Coverage Map

| Suite | Key invariants |
|-------|---------------|
| `tests/test_insights.py` | `month_over_month` year wrap + `no-previous` (100%), `unusual_expenses` flag, `days_until_budget_depleted` over-budget -> 0, `savings_projection` net-withdrawal -> None, NaN inputs -> None (regression for “fake 1 month”), resample `MS` tail(3) |
| `tests/test_llm.py` | `resolve_provider` all four gates (patched `find_bundled_model`); `find_bundled_model` on `EXPENSE_TRACKER_DATA_DIR` tmp + `test_local_provider_discovers_app_model` auto-enables local; API happy/fail/sanitize/never-touches-requests-when-none; local via `FakeLlama` (generate / error returns None); `gpu_layers==0` preserved, missing GGUF -> diagnostic; OSError/ImportError caught with correct hint (frozen vs source); Vulkan retry; stale-diagnostic clear; `local_runtime_status` four branches; email HTML-escape of `<script>/&/<b>`; settings encrypt (`ai_api_key_enc != plain`); `answer_query` happy + sanitize + sanitized stored text |

Any change to: sanitization length/limits, provider resolution, diagnostic wording, `max_tokens`, `temperature`, `top_p`, per-month context shape, or system-prompt wording must update the corresponding test.

---

## 11. Agent Checklist

- [ ] New analytics helper: does it receive DataFrames/dicts from the caller, return `str/dict/DataFrame`, and never call `q.*` or write DB rows?
- [ ] Budget input came from `settings` (or an explicit DF) and passed `math.isfinite` / `pd.isna` guards?
- [ ] LLM prompt used `_sanitize_stat` on **every** free-text and numeric field, and capped history to last 4 turns × 200 chars?
- [ ] `resolve_provider` is checked before any `requests`/`llama_cpp` import or call?
- [ ] `_get_local_model` cache key still includes `gpu_layers` and clears stale `_last_result` on entry?
- [ ] `sys.frozen` branch covered — diagnostic wording has both “pip install … --extra-index-url …” and “Reinstall Expense Tracker”?
- [ ] Failures leave the Ask transcript clean (no failed assistant message appended)?
- [ ] `generate_*` / `answer_query` catch `Exception` and return `None` so the caller’s fallback renders?
- [ ] `build_data_context` caps every list (`.nlargest(5)`, `.head(8)`, `.head(10)`) and sanitizes names?

---

*Last verified against `insights.py`, `llm.py`, `app_paths.py`, `crypto.py`, `app_pages/ask.py`, `app_pages/settings_ai.py`, `app_pages/insights_view.py`, and `tests/test_insights.py` + `tests/test_llm.py` at head.*
