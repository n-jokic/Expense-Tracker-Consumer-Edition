# Import Pipeline — Bank CSV & PDF Statement Ingestion

> **Owner:** Agent 6 — Ingestion Pipeline
> **Sources of truth:** `bank_import.py` (640), `pdf_import.py` (501), `app_pages/bank_import_view.py` (12), `queries.py`, `db.py`, `utils.py`

## 1. Purpose & Scope

Covers every bank-statement import path that turns an uploaded file into
persisted expenses: CSV (Revolut / N26 / Wise / generic), PDF (bordered &
borderless tables plus plain-text fallback), shared normalisation, human
review, dedup, FX conversion, and cache invalidation. Receipt OCR is
documented separately in `ocr-and-categorization.md`.

Out of scope: receipt photo OCR (see companion doc), recurring / income /
savings flows, market-price fetching.

## 2. Supported Banks & File Types

| `bank_format` | Trigger columns (`detect_bank_format`) | Amount column | Currency column | Date column |
|---|---|---|---|---|
| **revolut** | `"Started Date"` in lower-cased headers | `"Amount"` (pos 5) | `"Currency"` | `"Started Date"` |
| **n26** | `"Amount (EUR)"` anywhere | first column with `"amount"` | hardcoded `"EUR"` | `"Date"` |
| **wise** | `"Source amount"` / `"Source currency"` | `"Source amount (after fees)"` → `"Amount"` | `"Source currency"` | `"Date"` |
| **generic** | fallback | first `"amount"` else last column | first `"currency"` else blank | first `"date"` else col 0 |
| **pdf** | file extension `.pdf` | pdfplumber table / text parse | hardcoded EUR (see §5) | pdfplumber parse |

* Upload widget (`render_bank_import_page`): `st.file_uploader(..., type=["csv","pdf"])`, 20 MB limit (`MAX_UPLOAD_MB`).
* PDF branch: `extract_transactions_from_pdf(uploaded.getvalue())` → same normalized frame as CSV; empty result → warning and early return.
* CSV branch: delimiter sniffed via `csv.Sniffer` (`,`, `;`, `\t`) with `sep=None, engine='python'` fallback — EU `;` + `,` decimal files parse without re-export. Header detection is case-insensitive (`cols = [c.lower() for c in df.columns]`).

### Header normalisation

* `_pick(df, names, fallback_idx)` — first matching name wins, else positional fallback; returns empty Series if neither exists.
* Generic fallback searches by substring: date ← `"date"`, description ← `"desc" | "payee" | "merchant" | "name" | "detail"`, amount ← `"amount"`, currency ← `"currency"`.
* All currencies go through `_clean_currency`: `fillna("") → str → strip() → upper()` — never NaN. Empty/blank is left as `""` so the downstream "Statement currency" selector can fill it (§5).

## 3. CSV Dialect, Locale & Date Handling

### Numeric locale (`_to_numeric_locale`)

Handles four notations in one path:

1. **Per-value pure dot-thousands** (Serbian `1.234`): each token examined independently — pure 3-digit dot groups → dots stripped (no column-wide all-or-nothing check that misparsed mixed `1.200`/`1.50`).
2. **Both separators**: last separator is the decimal (`1.234,56` → EU, `1,234.56` → US) per token.
3. **Single comma**: `12,50` → `12.50` per token.
4. **Fallback**: `pd.to_numeric` on raw, filled by per-value `conv`.

### Date parsing (`_parse_date_series` → `pdf_import._parse_date_token`)

* **ISO first** — `_DATE_ISO_RE = \b(\d{4})[-./](\d{1,2})[-./](\d{1,2})\b` → `datetime(y,m,d)`. Short-circuits before ambiguous handling.
* **Ambiguous** — `_DATE_AMBIG_RE = \b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b` with **day-first heuristic**:
  * `a > 12 and b <= 12` → `d=a, mo=b` (dd/mm)
  * `b > 12 and a <= 12` → `mo=a, d=b` (mm/dd — only when first token could be a month)
  * both ≤ 12 → default **day-first** (`d=a`).
* **2-digit year pivot**: `y <= 50 → 2000+y` else `1900+y`.
* Invalid dates → `None`, then `dropna(subset=["date","amount"])` discards the row. Regression tests: `05/02/2025` → Feb 5, not May 2; `13/02/2025` → Feb 13; Wise `05-02-2025` → Feb 5.

## 4. PDF Pipeline — Tables First, Text Fallback

### 4.1 Page loop (`extract_transactions_from_pdf`)

```
for each page:
  for settings in _TABLE_SETTINGS:
    tables = page.extract_tables(settings)
    parsed = parse_table_rows(t) for t in tables
    if any parsed: extend all_rows, break  # stop trying text on this page
  if nothing parsed:
    text = page.extract_text() or ""
    extend parse_text_lines(text)
return DataFrame(all_rows, columns=[date,description,amount,currency]).dropna(...)
```

* `_TABLE_SETTINGS` (in order):
  1. **lines** — `{vertical_strategy:"lines", horizontal_strategy:"lines"}` for ruled PDFs.
  2. **text** — `{vertical_strategy:"text", horizontal_strategy:"text", snap/join/text_*_tolerance: 3-4}` for borderless PDFs (second call only if the first yielded no transactions).
* `_extract_tables` wraps `TypeError` (mocks / old pdfplumber without settings arg) and generic exceptions → `[]`.
* `pdfplumber.open` failure → warning log + empty frame with correct columns.

### 4.2 Column-role detection (`_classify_columns`)

Scans **header rows only** (rows before the first dated row):

| Role | Vocabulary (`re.IGNORECASE`) |
|---|---|
| `balance` | `balan|saldo|stanje|sold|solda|available|verfügbar|bilan|solde|kontostand|салдо` |
| `debit` | `debit|soll|belast|zadužen|zaduzen|terećen|terecen|duguje|должи|\bout\b|withdrawal` |
| `credit` | `credit|haben|gutschrift|prihod|potraž|potraz|potražuje|...|побарува|\bin\b|deposit` |
| `date` | `\bdate\b|datum|датум|dátum|booked|posting` |
| `amount` | `\bamount\b|iznos|износ|betrag|montant|importe|summe|promet|промет` |
| `description` | `description|opis|опис|details|narrative|transaction|svrha|namena|намена` |

Covers English, Serbian (Latin), Macedonian (Cyrillic), German. Roles assigned per column from the joined header text.

### 4.3 Noise & balance filtering

* `_HEADER_WORDS` set (date/description/debit/... plus Serbian/Macedonian/German terms) — excluded from description candidates.
* `_NOISE_RE` — opening/closing/carried forward/bf/cf/balance/saldo/.../page N/statement period/IBAN/SWIFT/www./tel/VAT/tax id/total/subtotal/suma/ukupno/totaal — matched case-insensitively; headerless-path also checks `_is_noise(cell)` before accepting a description token. Noise lines without a date are never continuations.
* `_BALANCE_WORDS` columns are **skipped entirely** during amount extraction.
* Headerless balance heuristic (`_detect_balance_column`): rightmost numeric column present on ≥ 70 % of dated rows whose values are ≥ 80 % monotonic (inc or dec) → treated as running balance and skipped. Requires ≥ 3 dated rows. Used only when no header was classified as `balance`.

### 4.4 Debit vs credit vs amount (§ parse_table_rows)

* Each amount cell is signed by its column role: `debit → -abs(a)`, `credit → abs(a)`, `amount → as-parsed`.
* Cells in `description` columns are **never amount-parsed** (prevents `PAYMENT REF 1234` → 1234).
* Headerless amount cells count only when `_is_pure_amount_cell(cell)` — whole cell, after iteratively stripping parentheses / trailing minus / currency symbols **and codes** (EUR/RSD/…), and rejecting dates, matches `_AMOUNT_RE.fullmatch`.
* Filters: `amount == 0` or `abs(amount) > 1_000_000` → dropped (same threshold as `MAX_AMOUNT`). Amount core detection is per-value/per-cell, never column-wide, so mixed `1.200` (1200) and `1.50` (1.50) both parse correctly (INTEGRATION-B-001 fix).

### 4.5 Amount parsing (`_parse_amount_token` + `_parse_amount_core`)

* Normalises typographic minus (`\u2212/\u2013/\u2014` → `-`), NBSP → space.
* **Parenthesised negatives** `(45.00)` → `-45.00`.
* **Trailing minus** `45.00-` → `-45.00` (accounting style).
* Strips currency symbols **and codes** (`EUR`, `RSD`, `USD`, …) from either end (`_CURRENCY_SYMBOLS` + `_CURRENCY_CODE_STRIP_RE`) so headerless cells like `"1.234,56 EUR"` are recognised; iterative strip handles both orders.
* **Date guard**: full ISO match or `_parse_date_token(m.group(0)) is not None` → not an amount (prevents `2025-01-02` → 2025).
* Core numeric: same locale logic as CSV — both separators → last is decimal; single-dot with all trailing groups len 3 → thousands; single-comma likewise.

### 4.6 Text-line fallback (`parse_text_lines`)

* Strips dates before scanning amounts (dates must not parse as amounts).
* Noise lines (`_is_noise`) clear any pending wrapped transaction.
* Bare amount fragment on its own line completes the previous `pending_tx` (date+description without amount → joined to amount on next line). Filtered if zero/huge.
* Wrapped description continuation appended to `out[-1]["description"]`.
* Trailing-balance heuristic: 2+ amounts on a line → first is transaction amount, last is running balance (discarded).

## 5. Normalised Frame Schema

Every path converges to:

| Column | Type | Notes |
|---|---|---|
| `date` | `datetime.date` (via `_parse_date_token`) | ISO → ambiguous day-first |
| `description` | `str` ("" if missing) | never amount-parsed when role=description |
| `amount` | `float` (signed in PDF, negative=debit in CSV) | locale-aware, zero/huge filtered |
| `currency` | `str` (upper, `""` when unknown) | PDF hardcoded EUR; CSV via `_clean_currency` |
| `category` | `str` (filled post-normalisation) | classifier → keyword fallback |
| `subcategory` | `str` | same chain |
| `_suggest_*` | hidden telemetry columns | see §7 |

`normalize_bank_csv` returns `out.dropna(subset=["date","amount"])`; PDF path does the same.

## 6. Human Review → Persist → Invalidate

### 6.1 Debit filtering

* Detect `has_both_signs` (any negative **and** any positive). If true, show checkbox *"My bank exports debits as POSITIVE"* (`key="bank_invert_sign"`) — toggling flips which sign is treated as expenses. Expenses are then `abs()`.
* Single-sign statements: the present sign is treated as expenses (no toggle).

### 6.2 Auto-categorisation before review

`suggest_category_and_subcategory(user_exp, desc, user_id)` per row (see companion doc). Results stored as `category`, `subcategory` plus hidden `_suggest_source`, `_suggest_conf`, `_suggest_cat/sub`, `_suggest_sub_source/conf` for telemetry.

### 6.3 Statement currency & FX

* PDF: `inferred = "EUR"` (parser hardcodes EUR).
* CSV: collect distinct non-blank `currency` values; single distinct code → inferred, else user's `settings.default_currency` (fallback EUR if not in `SUPPORTED_CURRENCIES`).
* User picks `stmt_cur` via `st.selectbox(key=f"stmt_cur_{name}_{size}")`:
  * PDF: `expenses_only["currency"] = stmt_cur`.
  * CSV: `fillna(stmt_cur).replace("", stmt_cur)` — empty/NaN cells inherit the statement currency.
* EUR conversion: `expenses_only["amount_eur"] = _to_eur_amount(amount, currency, rates)` row-wise:
  * `_to_eur_amount`: NaN/blank → EUR; known code → `amount / rates[code]` rounded to 4 dp; unknown non-empty → `NaN` so the row is skipped at save (never silently 1:1). `rates` is `utils.get_rates(settings)` (1 EUR = X).
* Unknown codes warned; NaN `amount_eur` rows show as NaN in the editor and are rejected at save.

### 6.4 Editable review

`st.data_editor(review, num_rows="fixed", hide_index=True)` with:

* `DateColumn` (date), `TextColumn` (description), `SelectboxColumn` (category ∈ `CAT_LIST`, subcategory ∈ `ALL_SUBCATS`, currency ∈ `SUPPORTED_CURRENCIES`), `NumberColumn` (amount, %.2f), `CheckboxColumn` (include, default True).
* Hidden telemetry columns rendered as `None` (not shown).
* `amount_eur` hidden (recalculated at save from edited amount/currency, never carried forward).

### 6.5 Save & dedup

On **Import N expenses** click:

1. Build `existing_keys` from `q.expenses(user_id)`: `{(date, norm_desc, round(amount_eur,2))}` where `norm_desc = re.sub(r"\s+", " ", str(desc)).strip().lower()`.
2. For each included row: `_save_edited_row(user_id, row, rates, existing_keys)`:
   * Recalculates `ae = _to_eur_amount(float(row["amount"]), row["currency"], rates)` from **edited** values; rejects if not `0 < ae <= MAX_AMOUNT` (covers NaN).
   * Key ` (d, norm_desc, round(ae,2))` against `existing_keys` → `"skipped"` if present (covers both pre-existing DB rows **and** duplicate rows within this upload via `existing_keys.add(key)` after each successful insert).
   * Records ML telemetry (suggest source/conf/model version/merchant/accepted) and calls `db.add_expense(user_id, {..., suggest_*, notes="Imported from bank statement"})`.
   * Returns `"imported"` / `"skipped"`; raises on DB errors (counted as `failed`).
3. If any imported: `q.bump_db_version()` → `db.bump_data_revision(user_id)` (shared DB revision + household cascade), `st.success` + balloons; else error summary. Counts: imported / skipped / failed.

> **Dedup signals (three layers):**
> 1. **Cross-DB**: date + normalised description + rounded EUR amount against all stored expenses.
> 2. **Within-upload**: same key added to the set after each insert.
> 3. **Pre-filter**: `normalize_*.dropna` + zero/huge amount filters remove malformed rows before review.

> **No STOP sentinel:** bank import uses dedup keys, not a `STOP` row marker. Any "STOP" string in a file is treated as an ordinary description (and likely dropped for lacking a parseable amount).

## 7. Wrapper Page

`app_pages/bank_import_view.py` (12 lines) is a thin delegator:

```python
import streamlit as st
from bank_import import render_bank_import_page
user_id = st.session_state.user_id
render_bank_import_page(user_id, st.session_state.rates)
```

Named `bank_import_view.py` so it doesn't shadow the top-level `bank_import` module. All logic lives in `bank_import.render_bank_import_page`.

## 8. Error & Empty Handling

* `MAX_UPLOAD_MB` exceeded → `st.error`.
* `pd.read_csv` exception → `st.error`.
* `normalize_bank_csv` exception → `st.error` + empty frame with correct columns.
* PDF open / page `extract_tables` / `extract_text` exceptions → warning / empty list, never crash.
* Empty `normalised` → `"No valid rows found"` warning.
* No debit rows → info/warning paths (single-sign handled gracefully).
* Unknown currency → NaN EUR, row skipped unless user edits currency before save.

## 9. Key Tests

| File | What it proves |
|---|---|
| `tests/test_bank_import.py` | format detection, Revolut/N26/Wise/generic normalisation, day-first dates, Serbian dot-thousands (1.234→1234), empty-but-present currency → `""` not NaN, `_clean_currency` never NaN, EUR recalculation from edited values, within-upload dedup, NaN amount rejection, unknown currency → NaN→skip, suggestion telemetry |
| `tests/test_pdf_import.py` | EU/US/ISO dates & amounts, comma decimals, Serbian thousands, parenthesised/trailing-minus/currency-symbol amounts, date≠amount guards, day-first heuristic, 2-digit year pivot, column classification (Serbian headers), balance-column ignore + headerless monotonic heuristic, debit=- / credit=+, zero/huge filters, `PAYMENT REF 1234` not misread as amount, trailing-balance uses first amount, wrapped description continuation, noise skip, borderless text-strategy fallback, mocked `extract_transactions_from_pdf` tables & text paths |

## 10. When Changing This Pipeline

* **New bank**: add a branch in `detect_bank_format` + `normalize_bank_csv`; add header-name aliases to the generic fallbacks; keep `_clean_currency` empty→blank invariant.
* **New PDF language**: extend `*_WORDS` regexes + `_HEADER_WORDS` set; cover both Latin and Cyrillic where applicable; add a `_classify_columns` test.
* **Amount/date heuristics**: keep `_parse_amount_core` and `_parse_date_token` shared — CSV and PDF both call them. Any change needs both `test_bank_import` and `test_pdf_import` updates.
* Never persist `amount_eur` from the editor — always recalculate via `_to_eur_amount`.
