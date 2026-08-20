# OCR & Categorization — Receipt Scanning and Learned Category Suggestion

> **Owner:** Agent 6 — Ingestion Pipeline
> **Sources of truth:** `ocr.py` (217), `forecasting.py` §§ 130-333 (categorizer), `bank_import.py` `KEYWORD_MAP` + `categorize_expense`, `pdf_import.py` `_parse_amount_core`

## 1. Purpose & Scope

How a receipt photo becomes a single described expense (amount, merchant, category/subcategory) before the user ever confirms it. Two subsystems:

* **OCR** — Tesseract binary resolution, OCR execution with timeout, amount/merchant heuristics.
* **Categorization** — keyword-map fallback, learned TF-IDF + LogisticRegression classifier + per-category subcategorizers, confidence-gated fallback chain, dataset-fingerprint cache, and invalidation.

Bank CSV/PDF ingestion is in `import-pipeline.md`.

## 2. System Diagram

```
phone ──POST image_bytes──► server: ocr.analyze_receipt
                               ├─► ocr_image (Tesseract, 30 s timeout)
                               ├─► guess_total_amount (total-key proximity)
                               ├─► guess_merchant (first meaningful line)
                               └─► forecasting.suggest_category_and_subcategory
                                    ├─► get_categorizer(user_id, VERSION, fingerprint)
                                    │     cache_resource(max_entries=8, keyed on user+version+hash)
                                    └─► categorize_expense (KEYWORD_MAP fallback)
                               ──► {ok, text, amount, merchant, category, subcategory,
                                    confidence, source, model_version,
                                    subcategory_confidence, subcategory_source, reason}
                               ──► UI prefill (user accepts / edits / rejects; never auto-saved)
```

OCR runs **on the server** (phone only uploads bytes); a missing Tesseract binary never crashes the app.

## 3. Tesseract Binary Resolution — `_find_tesseract`

`winget install UB-Mannheim.TesseractOCR` writes the Registry but does **not** add `PATH`, so a plain `shutil.which` lookup keeps failing after install. Resolution order:

1. **PATH** — `shutil.which("tesseract")` → return immediately if found.
2. **Well-known paths** (checked in order):
   * `C:\Program Files\Tesseract-OCR\tesseract.exe`
   * `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`
   * `%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe`
   * `%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe`
3. **Windows Registry** — `winreg` over both hives × both key paths × both value names:
   * Hives: `HKEY_LOCAL_MACHINE`, `HKEY_CURRENT_USER`
   * Key paths: `SOFTWARE\Tesseract-OCR`, `SOFTWARE\WOW6432Node\Tesseract-OCR` (32-bit install on 64-bit OS)
   * Value names: `InstallDir`, `Path` (alternative name some installers use)
   * Each `InstallDir` → `os.path.join(install_dir, "tesseract.exe")` appended to candidates.
   * Registry access wrapped in `try/except OSError` per key/value; whole block in outer `try/except Exception` so non-Windows (no `winreg`) never raises.
4. **Filesystem probe** — first `candidate` where `os.path.isfile(c)` → return that path; else `None`.

Result is assigned to `pytesseract.pytesseract.tesseract_cmd` before `image_to_string`.

## 4. OCR Execution — `ocr_image` (30 s thread timeout)

```python
_OCR_TIMEOUT_S = 30

def ocr_image(image_bytes: bytes) -> tuple[str|None, str|None]:
    # (text, reason) — reason is None on success
```

* Spawns a **daemon** `threading.Thread(target=_run)` so a hung Tesseract call can never freeze the Streamlit script thread.
* `_run`:
  1. `import pytesseract`; call `_find_tesseract()` → if `None`: `result = (None, "ocr_not_installed")` and return.
  2. Set `pytesseract.pytesseract.tesseract_cmd`.
  3. `PIL.Image.open(io.BytesIO(image_bytes))` → `pytesseract.image_to_string(img)` → `strip()` → `result["text"]` (or `None` if empty), `reason=None`.
  4. Any exception → `(None, "ocr_failed")`.
* Join with timeout: `worker.join(_OCR_TIMEOUT_S)`. If still alive → `(None, "ocr_failed")` (daemon dies with process; no orphan).
* Never raises to the caller — UI maps `(None, reason)` to a hint.

Reason strings surfaced: `"ocr_not_installed"` (binary not found), `"ocr_failed"` (exception or 30 s timeout), `None` on success. `analyze_receipt` normalises a falsy reason to `"ocr_unavailable"`.

## 5. Amount Extraction

### 5.1 Regex & core parser

* `_AMOUNT_RE` (in `ocr.py`) — matches only amounts with a decimal part (bare integers like quantities/times do **not** match):
  ```python
  _AMOUNT_RE = re.compile(
    r"(?<![\d.,])(?:"
    r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?"  # 1.234 or 1.234,56
    r"|\d{1,3}(?:[.,]\d{3})*[.,]\d{2}"     # 12,50 or 1.234,56
    r"|\d+[.,]\d{2}"                       # 1234,56
    r")(?![\d.,])"
  )
  ```
* `_DATE_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")` — stripped **before** amount scanning so `15.05.2024` never becomes an amount.
* `_TOTAL_KEYS = ("total","ukupno","suma","svega","amount due","to pay","grand total","плати","укупно")` — Serbian/Macedonian terms included.
* Shared numeric conversion via `pdf_import._parse_amount_core`: both separators → last is decimal; single-dot/commas with all trailing groups len 3 → thousands; otherwise decimal. Filtered to `0.01 <= val <= 1_000_000`.

### 5.2 `extract_amounts(text) -> list[float]`

1. `cleaned = _DATE_RE.sub(" ", text)`.
2. For each `_AMOUNT_RE.finditer(cleaned)`, `_parse_amount_core(m.group())` → keep if in range.

Tests: `1.234,56` → 1234.56 (EU), `1,234.56` → 1234.56 (US), `1.234` → 1234 (Serbian thousands), `1234,56` → 1234.56 (comma decimal), dates ignored.

### 5.3 `guess_total_amount(text) -> float|None`

Best guess for the receipt total:

1. `amounts = extract_amounts(text)`; if empty → `None`.
2. Scan lines top-to-bottom: first line where `any(k in line.lower() for k in _TOTAL_KEYS)` and `extract_amounts(line)` non-empty → `return max(line_amounts)` (handles lines like `UKUPNO 1.200 din`).
3. Else fallback → `max(amounts)` (largest plausible amount — usually the total).

### 5.4 `guess_merchant(text) -> str|None`

First line that survives:

* `3 <= len(s) <= 60`, non-empty, not pure amounts (`extract_amounts(s)` empty), not containing any `_TOTAL_KEYS`, not matching `^[\d./\-: \s]+$` (dates/phones/times).

Returns the raw line (preserving case).

## 6. Keyword Map — `bank_import.KEYWORD_MAP`

```python
KEYWORD_MAP: dict[str, tuple[str,str]] = {
  "lidl": ("Groceries","Groceries"),
  # ... 100+ entries ...
  "interest": ("Loans & Debt","Interest"),
}
def categorize_expense(description: str) -> tuple[str,str]:
  desc_lower = description.lower()
  for keyword, (cat,subcat) in KEYWORD_MAP.items():  # insertion order
    if keyword in desc_lower:   # lowercase substring containment
      return cat, subcat
  return "Other","Miscellaneous"
```

* **Key** is a lowercase substring (single or multi-word like `"burger king"`, `"bolt food"`, `"world class"`, `"amazon prime"`), matched via `in` — not word-boundary.
* **Order matters**: first matching keyword wins (dict insertion order). Shadowing example: `"bolt"` before `"bolt food"` would shadow — current map puts `"bolt food"` first in its block, so Food Delivery wins over Taxi/Uber when the description is `bolt food`. Be careful when adding.
* **Snippet**: `"bp "` has a trailing space to avoid matching words containing "bp".
* **Coverage** (categories): Groceries, Dining Out (Restaurants & Takeaway / Food Delivery / Coffee & Snacks), Transport (Taxi/Uber, Fuel, Public Transit, Parking), Housing & Utilities (Rent, Electricity, Gas, Water, Internet, Phone), Health (Gym, Pharmacy, Doctor, Dental), Entertainment (Streaming, Cinema, Concerts, Hobbies), Shopping (Clothing, Haircut), Subscriptions & Software, Fees & Taxes, Loans & Debt.
* **Default fallback**: `("Other","Miscellaneous")` — not a separate "Groceries default"; every unknown merchant maps to Other. The forecasting fallback chain's final fallback is therefore Other, verified in `categorize_expense`.
* Pure function — safe to call from any thread, no I/O.

## 7. Learned Categorizer — `forecasting.py` §§ 130-333

### 7.1 Model classes

* `_CategorizerModel` — global category classifier; trains per-category `_SubcategorizerModel` instances.
* `_SubcategorizerModel` — per-category subcategory classifier; trained only on rows of that single category with a non-empty `subcategory`.

### 7.2 Training

| Param | Value |
|---|---|
| Vectorizer | `TfidfVectorizer(ngram_range=(1,2), min_df=1)` |
| Classifier | `LogisticRegression(max_iter=500)` (defaults: lbfgs, L2) |
| Min rows (global) | `len(expenses_df) >= 10` **and** `category.nunique() >= 2` **and** `len(df) >= 10` after dropping NA descriptions/categories |
| Min rows (sub) | `len(df) >= 8`, `subcategory.nunique() >= 2`, and ≥ 8 non-empty subcategories after filtering |
| Version | `CATEGORIZER_MODEL_VERSION = 3` — bump when training pipeline changes to invalidate all cached models |
| Thresholds | `CATEGORY_CONFIDENCE = 0.5`, `SUBCATEGORY_CONFIDENCE = 0.4` |

`_CategorizerModel.train(df)`:
* Ensures `subcategory` column exists (`assign(subcategory="")` if missing).
* Drops rows with NA description/category.
* Fits vec on `description.astype(str)` → `LogisticRegression.fit`.
* Records `self.categories = list(clf.classes_)`, `trained_rows = len(df)`.
* Then for each `cat, grp in df.groupby("category")`: `sm = _SubcategorizerModel(); if sm.train(grp): self.sub_models[cat]=sm`.

`_SubcategorizerModel.train(df)` filters `df[df["subcategory"].fillna("").str.strip() != ""]` then fits its own TF-IDF + LR.

Both return `False` (no exception) when requirements aren't met or sklearn import fails.

### 7.3 Prediction

* `_CategorizerModel.predict(text)`: `vec.transform([text]) → clf.predict_proba → argmax` → `(category, prob)`; returns `(None,0.0)` if untrained.
* `_SubcategorizerModel.predict(text)`: same for subcategories.

### 7.4 Dataset fingerprint & caching

```python
def _dataset_fingerprint(expenses_df) -> str:
  # "empty" if empty
  df = expenses_df[["description","category","subcategory"]].dropna(subset=[...])
  # lower+strip each triple, sort, join as "d|c|s"
  digest = md5("\n".join(sorted(f"{d}|{c}|{s}" ...)).encode()).hexdigest()
  return f"{len(df)}|{digest}"

@st.cache_resource(max_entries=8)
def get_categorizer(user_id=None, model_version=CATEGORIZER_MODEL_VERSION, fingerprint="") -> _CategorizerModel:
  return _CategorizerModel()  # train-on-demand; caller checks trained_fingerprint
```

* Fingerprint is over **(description, category, subcategory)** triples, lowercased+stripped, sorted, MD5-hashed, prefixed with row count. Any addition, deletion, or correction (category **or** subcategory) changes the hash.
* `@st.cache_resource` keys on **all three args** (`user_id`, `model_version`, `fingerprint`): different users never share training data; bumping `CATEGORIZER_MODEL_VERSION` discards all cached models.
* Train-on-demand: `suggest_category*` fetches the cached model, and if `model.clf is None or model.trained_fingerprint != fp`, calls `model.train` and stamps `model.trained_fingerprint = fp`.
* `clear_categorizers()` → `get_categorizer.clear()` (drops all 8 entries). Called on account deletion.

### 7.5 Fallback chain — `suggest_category_and_subcategory`

```
kw_cat, kw_sub = categorize_expense(text)

if model.clf is None:
  return kw_cat, kw_sub, 0.0, 0.0

cat, cat_conf = model.predict(text)
if cat_conf < CATEGORY_CONFIDENCE (0.5):
  return kw_cat, kw_sub, 0.0, 0.0   # classifier not confident → keyword decides BOTH

# classifier wins the category
sub, sub_conf = "", 0.0
sm = model.sub_models.get(cat)
if sm is not None:
  s, sc = sm.predict(text)
  if sc >= SUBCATEGORY_CONFIDENCE (0.4): sub, sub_conf = s, sc

if not sub and kw_cat == cat and kw_sub:
  sub = kw_sub; sub_conf = 0.0      # keyword refinement when categories agree

return cat, sub, cat_conf, sub_conf
```

* `cat_conf` is the classifier probability when it decided the category, else `0.0`.
* `sub_conf` is the submodel probability when it decided the subcategory, else `0.0`.
* `suggest_category` is a thin wrapper around the same fingerprint logic with a single `min_confidence` threshold.

> **Fallback summary:** **learned ML (TF-IDF+LR) → keyword map (`KEYWORD_MAP`) → `Other/Miscellaneous`**. The "Groceries default" mentioned in some briefs does **not** exist — the verified default is `Other/Miscellaneous` from `categorize_expense`.

## 8. Receipt Pipeline — `analyze_receipt`

```python
def analyze_receipt(image_bytes, expenses_df=None, user_id=None) -> dict:
  text, ocr_reason = ocr_image(image_bytes)
  if text is None:
    return {"ok":False, "reason": ocr_reason or "ocr_unavailable", "text":None,
            "amount":None, "merchant":None, "category":None, "subcategory":"",
            "confidence":0.0, "subcategory_confidence":None, "subcategory_source":None}

  amount   = guess_total_amount(text)
  merchant = guess_merchant(text)
  # category block only if merchant is truthy
  if merchant:
    try:
      cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(expenses_df, merchant, user_id=user_id)
      if cat_conf >= CATEGORY_CONFIDENCE:
        category, subcategory, confidence = cat, sub, round(cat_conf,2)
        source="classifier"; model_version=CATEGORIZER_MODEL_VERSION
        if sub and sub_conf >= SUBCATEGORY_CONFIDENCE:  subcategory_confidence, subcategory_source = round(sub_conf,2), "classifier"
        elif sub: subcategory_source="keywords"
    except Exception: pass
    if category is None:  # fallback
      category, subcategory = categorize_expense(merchant); source="keywords"; subcategory_source="keywords"
  return {"ok":True, "text":text, "amount":amount, "merchant":merchant,
          "category":category, "subcategory":subcategory, "confidence":confidence, ...}
```

* Never raises — always returns a dict with `ok`, `reason`.
* Telemetry fields: `source` (`"classifier"`|`"keywords"`|`None`), `model_version`, `subcategory_source`/`subcategory_confidence`.
* UI contract: result is an **editable prefill** — user accepts, edits, or rejects before any `db.add_expense`.

Bank import carries its own telemetry per row (`_suggest_source/conf/cat/sub...`) for the same acceptance measurement.

## 9. Error & Timeout Handling

* Missing Tesseract → `_find_tesseract` returns `None` → `ocr_image` → `(None,"ocr_not_installed")` → `analyze_receipt` → `{ok:False, reason:"ocr_not_installed"}` → UI shows setup hint (`winget install UB-Mannheim.TesseractOCR`).
* Tesseract exception / 30 s timeout → `(None,"ocr_failed")` → same path, different hint.
* No amount found → `amount=None` (field left blank in prefill).
* No merchant found → `merchant=None`, category block skipped (category stays `None`).
* Categorizer untrained / low confidence → keyword fallback; unknown keyword → `Other/Miscellaneous`.
* Images kept **in memory only** (`io.BytesIO`), never written to disk.

## 10. Key Tests

| File | What it proves |
|---|---|
| `tests/test_ocr.py` | EU/US/plain/comma-decimal/Serbian-thousands amounts, date stripping, total-key proximity vs largest-amount fallback, merchant noise skipping, Tesseract-mocked `analyze_receipt` without binary, keyword fallback path (`LIDL → Groceries`) |
| `tests/test_categorizer_cache.py` | fingerprint changes on category edit / subcategory edit / row delete, edited labels retrain immediately (no stale cache), cache keyed by `(user_id, version, fingerprint)` with isolation, `clear_categorizers` drops all models |

## 11. When Changing Categorization or OCR

* **New keyword**: append to `KEYWORD_MAP` — order matters for overlapping substrings; put the more specific multi-word key first; re-run `test_bank_import` keyword tests.
* **Training change** (vectorizer params, thresholds, model class): **bump** `CATEGORIZER_MODEL_VERSION` so old cached models are discarded; update the thresholds table above; add a version-migration test if needed.
* **Tesseract path change**: add to `_find_tesseract` candidates or registry enumeration; never remove PATH-first check — it covers Docker/Linux where Registry doesn't exist.
* **OCR timeout**: `_OCR_TIMEOUT_S` is 30; raising it increases worst-case Streamlit script-thread block time (only the worker is daemonised, the main thread still joins).
* **New total keyword language**: extend `_TOTAL_KEYS` and add an `extract_amounts` + `guess_total_amount` test.
