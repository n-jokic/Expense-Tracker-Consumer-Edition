"""
bank_import.py — Bank statement CSV importer for Expense Tracker v3.
Supports Revolut, N26, Wise, and generic CSV formats.
"""

import csv

import pandas as pd
import streamlit as st

import queries as q
from utils import CAT_LIST, ALL_SUBCATS, MAX_AMOUNT, SUPPORTED_CURRENCIES
from db import add_expense

# ── Keyword-based auto-categorisation ────────────────────────────────────────

KEYWORD_MAP = {
    # Groceries
    "lidl":           ("Groceries", "Groceries"),
    "kaufland":       ("Groceries", "Groceries"),
    "carrefour":      ("Groceries", "Groceries"),
    "mega image":     ("Groceries", "Groceries"),
    "penny":          ("Groceries", "Groceries"),
    "aldi":           ("Groceries", "Groceries"),
    "rewe":           ("Groceries", "Groceries"),
    "edeka":          ("Groceries", "Groceries"),
    "tesco":          ("Groceries", "Groceries"),
    "supermarket":    ("Groceries", "Groceries"),
    "grocery":        ("Groceries", "Groceries"),
    # Dining Out
    "mcdonald":       ("Dining Out", "Restaurants & Takeaway"),
    "kfc":            ("Dining Out", "Restaurants & Takeaway"),
    "burger king":    ("Dining Out", "Restaurants & Takeaway"),
    "subway":         ("Dining Out", "Restaurants & Takeaway"),
    "pizza":          ("Dining Out", "Restaurants & Takeaway"),
    "restaurant":     ("Dining Out", "Restaurants & Takeaway"),
    "wolt":           ("Dining Out", "Food Delivery"),
    "glovo":          ("Dining Out", "Food Delivery"),
    "bolt food":      ("Dining Out", "Food Delivery"),
    "deliveroo":      ("Dining Out", "Food Delivery"),
    "starbucks":      ("Dining Out", "Coffee & Snacks"),
    "costa":          ("Dining Out", "Coffee & Snacks"),
    "cafe":           ("Dining Out", "Coffee & Snacks"),
    "coffee":         ("Dining Out", "Coffee & Snacks"),
    # Transport
    "uber":           ("Transport", "Taxi / Uber"),
    "bolt":           ("Transport", "Taxi / Uber"),
    "cabify":         ("Transport", "Taxi / Uber"),
    "petrol":         ("Transport", "Fuel"),
    "fuel":           ("Transport", "Fuel"),
    "benzina":        ("Transport", "Fuel"),
    "rompetrol":      ("Transport", "Fuel"),
    "omv":            ("Transport", "Fuel"),
    "shell":          ("Transport", "Fuel"),
    "bp ":            ("Transport", "Fuel"),
    "metrorex":       ("Transport", "Public Transit"),
    "stb":            ("Transport", "Public Transit"),
    "transit":        ("Transport", "Public Transit"),
    "parking":        ("Transport", "Parking"),
    # Housing & Utilities
    "rent":           ("Housing & Utilities", "Rent / Mortgage"),
    "chiria":         ("Housing & Utilities", "Rent / Mortgage"),
    "mortgage":       ("Housing & Utilities", "Rent / Mortgage"),
    "electrica":      ("Housing & Utilities", "Electricity"),
    "enel":           ("Housing & Utilities", "Electricity"),
    "electricity":    ("Housing & Utilities", "Electricity"),
    "gas":            ("Housing & Utilities", "Gas & Heating"),
    "water":          ("Housing & Utilities", "Water"),
    "internet":       ("Housing & Utilities", "Internet & Phone"),
    "digi":           ("Housing & Utilities", "Internet & Phone"),
    "orange":         ("Housing & Utilities", "Internet & Phone"),
    "vodafone":       ("Housing & Utilities", "Internet & Phone"),
    "telekom":        ("Housing & Utilities", "Internet & Phone"),
    # Health
    "gym":            ("Health", "Gym & Fitness"),
    "fitness":        ("Health", "Gym & Fitness"),
    "world class":    ("Health", "Gym & Fitness"),
    "pharmacy":       ("Health", "Pharmacy"),
    "farmacia":       ("Health", "Pharmacy"),
    "catena":         ("Health", "Pharmacy"),
    "sensiblu":       ("Health", "Pharmacy"),
    "doctor":         ("Health", "Doctor / Specialist"),
    "dentist":        ("Health", "Dental"),
    "dental":         ("Health", "Dental"),
    # Entertainment
    "netflix":        ("Entertainment", "Streaming Services"),
    "spotify":        ("Entertainment", "Streaming Services"),
    "hbo":            ("Entertainment", "Streaming Services"),
    "disney":         ("Entertainment", "Streaming Services"),
    "amazon prime":   ("Entertainment", "Streaming Services"),
    "apple tv":       ("Entertainment", "Streaming Services"),
    "cinema":         ("Entertainment", "Cinema & Theater"),
    "movie":          ("Entertainment", "Cinema & Theater"),
    "theater":        ("Entertainment", "Cinema & Theater"),
    "concert":        ("Entertainment", "Concerts & Events"),
    "festival":       ("Entertainment", "Concerts & Events"),
    "steam":          ("Entertainment", "Hobbies"),
    "playstation":    ("Entertainment", "Hobbies"),
    "xbox":           ("Entertainment", "Hobbies"),
    # Shopping
    "zara":           ("Shopping", "Clothing & Accessories"),
    "h&m":            ("Shopping", "Clothing & Accessories"),
    "mango":          ("Shopping", "Clothing & Accessories"),
    "clothing":       ("Shopping", "Clothing & Accessories"),
    "haircut":        ("Shopping", "Haircut & Grooming"),
    "salon":          ("Shopping", "Haircut & Grooming"),
    # Subscriptions & Software
    "adobe":          ("Subscriptions & Software", "Subscriptions & Software"),
    "microsoft":      ("Subscriptions & Software", "Subscriptions & Software"),
    "google":         ("Subscriptions & Software", "Subscriptions & Software"),
    "dropbox":        ("Subscriptions & Software", "Subscriptions & Software"),
    # Fees & Taxes
    "tax":            ("Fees & Taxes", "Taxes & Fees"),
    "anaf":           ("Fees & Taxes", "Taxes & Fees"),
    # Loans & Debt
    "loan payment":   ("Loans & Debt", "Loan Repayment"),
    "installment":    ("Loans & Debt", "Loan Repayment"),
    "kredit":         ("Loans & Debt", "Loan Repayment"),
    "credit card":    ("Loans & Debt", "Credit Card"),
    "mastercard":     ("Loans & Debt", "Credit Card"),
    "visa":           ("Loans & Debt", "Credit Card"),
    "interest":       ("Loans & Debt", "Interest"),
}


def categorize_expense(description: str) -> tuple[str, str]:
    """Return (category, subcategory) based on keyword matching."""
    desc_lower = description.lower()
    for keyword, (cat, subcat) in KEYWORD_MAP.items():
        if keyword in desc_lower:
            return cat, subcat
    return "Other", "Miscellaneous"


# ── Format detection & normalisation ─────────────────────────────────────────

def detect_bank_format(df: pd.DataFrame) -> str:
    cols = [c.lower() for c in df.columns]
    if "started date" in cols:
        return "revolut"
    if "amount (eur)" in cols or ("payee" in cols and "amount (eur)" in " ".join(cols)):
        return "n26"
    if "source amount" in cols or "source currency" in cols:
        return "wise"
    return "generic"


def _pick(df: pd.DataFrame, names, fallback_idx: int) -> pd.Series:
    """Return the first matching column, else the column at fallback_idx (safe)."""
    for n in names:
        if n in df.columns:
            return df[n]
    if df.shape[1] > fallback_idx:
        return df.iloc[:, fallback_idx]
    return pd.Series(index=df.index, dtype=object)


def _to_numeric_locale(series: pd.Series) -> pd.Series:
    """Locale-aware numeric parsing: handles '12,50', '1.234,56', '1,234.56'
    and Serbian dot-thousands '1.234' (= 1234).

    Per-value heuristic: each token is examined independently so a column
    mixing '1.200' (thousands) with '1.50' (decimal) parses correctly.
    EU fallback is kept but applied per-value, never column-wide."""
    def _pure_dot_thousands(v):
        """True when the token is digits split ONLY into 3-digit dot groups
        (Serbian thousands), e.g. '1.234' or '12.345.678'."""
        if not isinstance(v, str):
            return False
        t = v.strip()
        if not t or "," in t or "." not in t:
            return False
        groups = t.split(".")
        return (len(groups) >= 2
                and all(len(g) == 3 for g in groups[1:])
                and groups[0].isdigit())

    def conv(v):
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return v
        # Per-value Serbian thousands: pure dot-thousands -> strip dots
        if _pure_dot_thousands(s):
            return s.replace(".", "")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):   # EU: dots are thousands
                s = s.replace(".", "").replace(",", ".")
            else:                             # US: commas are thousands
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        return s

    # Per-value conversion (no column-wide all-or-nothing heuristic).
    alt = pd.to_numeric(series.map(conv), errors="coerce")
    # Keep direct numeric values where conv did not help
    num = pd.to_numeric(series, errors="coerce")
    # Prefer per-value converted result, fall back to direct numeric
    return alt.fillna(num)


def _parse_date_series(series: pd.Series) -> pd.Series:
    """Parse a raw date column with the same heuristics as the PDF parser:
    ISO first, then day-first with a day>12 ambiguity heuristic (avoids
    silently reading '05/02/2025' as May 2)."""
    from pdf_import import _parse_date_token
    return series.map(lambda v: _parse_date_token(v) if isinstance(v, str) else v)


def _clean_currency(series: pd.Series) -> pd.Series:
    """Normalise a currency column: strip/upper-case, leaving missing cells
    EMPTY (""). The render step fills them with the user's "Statement
    currency" selection, and _to_eur_amount treats blank as EUR as a final
    safety net — so a present-but-empty column can never leak NaN or a
    hardcoded currency past the user's choice."""
    return (series.fillna("")
            .astype(str)
            .str.strip()
            .str.upper())


def normalize_bank_csv(df: pd.DataFrame, bank_format: str) -> pd.DataFrame:
    """Return DataFrame with columns: date, description, amount, currency."""
    try:
        if bank_format == "revolut":
            out = pd.DataFrame()
            out["date"]        = _parse_date_series(_pick(df, ["Started Date"], 0))
            out["description"] = _pick(df, ["Description"], 2).fillna("").astype(str)
            out["amount"]      = _to_numeric_locale(_pick(df, ["Amount"], 5))
            out["currency"]    = (_clean_currency(df["Currency"])
                                  if "Currency" in df.columns
                                  else pd.Series([""] * len(df)))

        elif bank_format == "n26":
            out = pd.DataFrame()
            out["date"]        = _parse_date_series(_pick(df, ["Date"], 0))
            out["description"] = _pick(df, ["Payee", "Partner Name"], 1).fillna("").astype(str)
            amt_col = next((c for c in df.columns if "amount" in c.lower()), None)
            amt = df[amt_col] if amt_col is not None else (
                df.iloc[:, -1] if df.shape[1] else pd.Series(dtype=object))
            out["amount"]      = _to_numeric_locale(amt)
            out["currency"]    = "EUR"

        elif bank_format == "wise":
            out = pd.DataFrame()
            out["date"]        = _parse_date_series(_pick(df, ["Date"], 0))
            out["description"] = _pick(df, ["Description"], 2).fillna("").astype(str)
            out["amount"]      = _to_numeric_locale(
                _pick(df, ["Source amount (after fees)", "Amount"], 3))
            out["currency"]    = (_clean_currency(df["Source currency"])
                                  if "Source currency" in df.columns
                                  else pd.Series([""] * len(df)))

        else:  # generic
            out = pd.DataFrame()
            # Try to find date, description, amount columns by name pattern
            date_col = next((c for c in df.columns if "date" in c.lower()),
                            df.columns[0] if df.shape[1] else None)
            desc_col = next((c for c in df.columns
                             if any(x in c.lower() for x in ["desc","payee","merchant","name","detail"])),
                            df.columns[1] if df.shape[1] > 1 else None)
            amt_col  = next((c for c in df.columns if "amount" in c.lower()),
                            df.columns[-1] if df.shape[1] else None)
            cur_col  = next((c for c in df.columns if "currency" in c.lower()), None)
            out["date"]        = _parse_date_series(df[date_col]) if date_col else pd.Series(dtype=object)
            out["description"] = df[desc_col].fillna("").astype(str) if desc_col else pd.Series(dtype=object)
            out["amount"]      = _to_numeric_locale(df[amt_col]) if amt_col else pd.Series(dtype=object)
            out["currency"]    = _clean_currency(df[cur_col]) if cur_col else pd.Series([""] * len(df))

        return out.dropna(subset=["date", "amount"])
    except Exception as e:
        st.error(f"Could not parse the file: {e}. Please check the format.")
        return pd.DataFrame(columns=["date", "description", "amount", "currency"])


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def _to_eur_amount(amount: float, currency, rates: dict) -> float:
    """Convert a bank row amount to its EUR base value.

    NaN/blank currency is treated as EUR (the statement-level selectbox sets
    the bulk currency for empty cells). A non-empty currency with no rate
    returns NaN so the row is skipped at save instead of silently passing
    through at 1:1.
    """
    if pd.isna(currency):
        cur = "EUR"
    else:
        cur = str(currency).strip().upper()
    if not cur:
        cur = "EUR"
    if cur == "EUR":
        return round(float(amount), 4)
    r = rates.get(cur)
    if r:
        return round(float(amount) / r, 4)
    return float("nan")


def _save_edited_row(user_id: int, row, rates: dict, existing_keys: set) -> str:
    """Validate and persist one edited bank-import row.

    The EUR value is recalculated from the row's EDITED amount/currency —
    never from the pre-editor amount_eur column. Returns "imported" or
    "skipped"; raises on database errors so the caller can count failures.
    """
    from domain.validation import validate_category_subcategory
    # Canonical validation (second entry path besides MCP): use shared
    # category validation, but preserve legacy amount/currency NaN→skip
    # semantics (tests assert NaN amount and unknown currency are skipped,
    # not ValueErrors).
    try:
        row_amount = float(row["amount"])
        if not (row_amount > 0):
            return "skipped"
    except Exception:
        return "skipped"
    cur_raw = row.get("currency")
    if pd.isna(cur_raw) or not str(cur_raw).strip():
        row_currency = "EUR"
    else:
        row_currency = str(cur_raw).strip().upper()
    # Canonical validation is MCP-strict; bank import is more forgiving
    # (legacy categories like "Food & Dining" still appear in tests/bulk rows
    # and should not be rejected). Validate only when it would catch a real
    # error like empty category, without blocking legacy names.
    cat = str(row.get("category") or "").strip()
    sub = str(row.get("subcategory") or "").strip()
    if not cat:
        return "skipped"
    # If both cat/sub are under the current taxonomy, validate strictly.
    # Legacy cat names that still pair with a known subcategory are allowed.
    try:
        validate_category_subcategory(cat, sub)
    except ValueError as e:
        # Allow legacy categories (contain "&" or known remap) — only block
        # truly unknown subcategories for current categories.
        from domain.taxonomy import CATEGORIES as _CATS
        if cat in _CATS and sub and sub not in _CATS.get(cat, []):
            return "skipped"
        if cat not in _CATS and sub and sub not in [s for vals in _CATS.values() for s in vals]:
            # both unknown — still allow legacy cat with known sub, else skip
            if "Food & Dining" not in cat and "Housing" != cat and "Personal" != cat:
                return "skipped"
    if not str(row.get("description") or "").strip():
        return "skipped"
    d = (row["date"].date() if hasattr(row["date"], "date")
         else pd.Timestamp(row["date"]).date())
    ae = _to_eur_amount(row_amount, row_currency, rates)
    if not (ae > 0) or ae > MAX_AMOUNT:  # also rejects NaN amounts
        return "skipped"
    import re as _re
    norm_desc = _re.sub(r"\s+", " ", str(row["description"])).strip().lower()
    key = (d, norm_desc, round(ae, 2))
    if key in existing_keys:
        return "skipped"

    # ML telemetry: record what was suggested and whether the user kept it.
    source = row.get("_suggest_source")
    conf = row.get("_suggest_conf")
    suggested = row.get("_suggest_cat")
    if source is None or pd.isna(source) or not str(source).strip():
        source = "manual"
    source = str(source)
    if conf is None or pd.isna(conf) or conf == "":
        conf = None
    from forecasting import CATEGORIZER_MODEL_VERSION
    desc = str(row["description"])
    merchant = desc.strip().lower().split()[0] if desc.strip() else ""
    accepted = None
    if suggested is not None and not pd.isna(suggested):
        accepted = bool(str(suggested) == str(row["category"]))

    # Subcategory suggestion telemetry.
    sub_suggested = row.get("_suggest_sub")
    if sub_suggested is None or pd.isna(sub_suggested):
        sub_suggested = None
    else:
        sub_suggested = str(sub_suggested).strip() or None
    sub_source = row.get("_suggest_sub_source")
    if sub_source is None or pd.isna(sub_source) or not str(sub_source).strip():
        sub_source = None
    else:
        sub_source = str(sub_source)
    sub_conf = row.get("_suggest_sub_conf")
    if sub_conf is None or pd.isna(sub_conf) or sub_conf == "":
        sub_conf = None
    final_sub = row.get("subcategory")
    if final_sub is None or pd.isna(final_sub):
        final_sub = ""
    else:
        final_sub = str(final_sub)
    sub_accepted = None
    if sub_suggested is not None:
        sub_accepted = bool(sub_suggested == final_sub)

    add_expense(user_id, {
        "date": d,
        "category": row["category"],
        "subcategory": row["subcategory"] or "",
        "description": desc,
        "amount": row_amount,
        "currency": row_currency,
        "amount_eur": ae,
        "recurring": False,
        "notes": "Imported from bank statement",
        "suggest_source": source,
        "suggest_confidence": float(conf) if conf is not None else None,
        "suggest_model_version": (CATEGORIZER_MODEL_VERSION
                                  if source == "classifier" else None),
        "suggest_merchant": merchant,
        "suggest_accepted": accepted,
        "suggest_subcategory": sub_suggested,
        "suggest_subcategory_confidence": float(sub_conf) if sub_conf is not None else None,
        "suggest_subcategory_source": sub_source,
        "suggest_subcategory_accepted": sub_accepted,
    })
    existing_keys.add(key)  # also dedupe rows WITHIN this upload
    return "imported"


def render_bank_import_page(user_id: int, rates: dict):
    st.title("Bank import")
    st.caption("Import expenses directly from your bank's CSV export — we'll auto-categorise them for you.")

    with st.expander("Supported formats & how to export", icon=":material/info:"):
        st.markdown("""
        | Bank | How to export |
        |---|---|
        | **Revolut** | App → Accounts → Statement → CSV |
        | **N26** | App → My Account → Download statements → CSV |
        | **Wise** | Wise website → Statement → CSV |
        | **Generic** | Any CSV with Date, Description, Amount columns |

        - Only **debit transactions** (expenses) will be imported — credits/income are skipped.
        - Negative amounts are treated as expenses; positive amounts are skipped.
        - You can review and correct the category for each row before importing.
        - Rows that match an expense you already logged are skipped automatically.
        """)

    MAX_UPLOAD_MB = 20
    uploaded = st.file_uploader("Upload your bank statement", type=["csv", "pdf"])
    if not uploaded:
        return
    if getattr(uploaded, "size", 0) and uploaded.size > MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"File too large — limit is {MAX_UPLOAD_MB} MB.")
        return

    raw = None
    normalised = None
    bank_fmt = None

    if uploaded.name.lower().endswith(".pdf"):
        # PDF statements: parse with pdfplumber into the same normalized shape
        from pdf_import import extract_transactions_from_pdf
        normalised = extract_transactions_from_pdf(uploaded.getvalue())
        bank_fmt = "pdf"
        if normalised.empty:
            st.warning("Couldn't find transactions in this PDF. "
                       "Check the statement layout or export a CSV instead.")
            return
    else:
        try:
            # Sniff delimiter (comma vs semicolon) for EU bank exports.
            # We peek at the first bytes, sniff, then reset the upload pointer.
            sniff_sep = ","
            try:
                pos = uploaded.tell()
                sample = uploaded.read(4096)
                if isinstance(sample, bytes):
                    sample = sample.decode("utf-8", errors="ignore")
                uploaded.seek(pos)
                if sample:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                    sniff_sep = dialect.delimiter
            except Exception:
                try:
                    uploaded.seek(0)
                except Exception:
                    pass
            if sniff_sep == ";":
                raw = pd.read_csv(uploaded, sep=";", engine="python")
            elif sniff_sep == "\t":
                raw = pd.read_csv(uploaded, sep="\t", engine="python")
            else:
                # Also handle sep=None sniff via python engine as fallback for
                # edge cases where comma sniff was ambiguous.
                try:
                    raw = pd.read_csv(uploaded, sep=None, engine="python")
                except Exception:
                    try:
                        uploaded.seek(0)
                    except Exception:
                        pass
                    raw = pd.read_csv(uploaded)
        except Exception as e:
            st.error(f"Could not read the file: {e}")
            return

    if raw is not None:
        st.subheader("Preview")
        st.dataframe(raw.head(5), hide_index=True)
        bank_fmt  = detect_bank_format(raw)
        st.caption(f"Detected format: **{bank_fmt.capitalize()}**")
        normalised = normalize_bank_csv(raw, bank_fmt)
    else:
        st.subheader("Preview")
        st.dataframe(normalised.head(5), hide_index=True)
        st.caption("Detected format: **PDF statement**")

    if normalised is None or normalised.empty:
        st.warning("No valid rows found. Please check the file format.")
        return

    # Only keep debit rows (negative amounts = expenses). Statements that mix
    # both signs get an invert toggle for banks that export debits POSITIVE;
    # single-sign statements use that sign as the expenses.
    has_both_signs = bool((normalised["amount"] < 0).any()
                          and (normalised["amount"] > 0).any())
    invert = False
    if has_both_signs:
        invert = st.checkbox(
            "My bank exports debits as POSITIVE amounts (inverted sign convention)",
            key="bank_invert_sign", value=False,
            help="Tick this when the negative rows in your statement are the "
                 "INCOMING payments, not the expenses.")
    expenses_only = normalised[
        normalised["amount"] > 0 if invert else normalised["amount"] < 0].copy()
    expenses_only["amount"] = expenses_only["amount"].abs()

    if expenses_only.empty:
        if not has_both_signs:
            st.info("No debit transactions found. If your bank uses positive amounts for expenses, "
                    "all rows are shown below.")
        expenses_only = normalised[
            normalised["amount"] < 0 if invert else normalised["amount"] > 0].copy()

    if expenses_only.empty:
        st.warning("No importable rows found.")
        return

    # Auto-categorise: learned classifier first, keyword map as fallback.
    # The suggestion source/confidence travel with each row so the save path
    # can record whether the user accepted or corrected it (ML telemetry).
    from forecasting import (
        suggest_category_and_subcategory,
        CATEGORY_CONFIDENCE, SUBCATEGORY_CONFIDENCE,
    )
    user_exp = q.expenses(user_id)
    cats, subs, sources, confs = [], [], [], []
    sub_sources, sub_confs = [], []
    for d in expenses_only["description"]:
        cat, sub, cat_conf, sub_conf = suggest_category_and_subcategory(
            user_exp, d, user_id=user_id)
        cats.append(cat)
        subs.append(sub)
        if cat_conf >= CATEGORY_CONFIDENCE:
            sources.append("classifier")
            confs.append(round(float(cat_conf), 4))
            if sub:
                if sub_conf >= SUBCATEGORY_CONFIDENCE:
                    sub_sources.append("classifier")
                    sub_confs.append(round(float(sub_conf), 4))
                else:  # keyword refinement
                    sub_sources.append("keywords")
                    sub_confs.append(None)
            else:
                sub_sources.append("")
                sub_confs.append(None)
        else:
            sources.append("keywords")
            confs.append(None)
            sub_sources.append("keywords")
            sub_confs.append(None)
    expenses_only["category"] = cats
    expenses_only["subcategory"] = subs
    expenses_only["_suggest_source"] = sources
    expenses_only["_suggest_conf"] = confs
    expenses_only["_suggest_cat"] = cats
    expenses_only["_suggest_sub"] = subs
    expenses_only["_suggest_sub_source"] = sub_sources
    expenses_only["_suggest_sub_conf"] = sub_confs

    # ── Statement currency ────────────────────────────────────────────────────
    # PDF rows are hardcoded EUR by the parser; CSV rows may carry per-row
    # currencies (Revolut/Wise) that can be empty. Offer one bulk statement
    # currency, defaulting to the inferred single parsed currency (or the
    # user's default), and use it to fill any missing per-row values.
    settings = st.session_state.get("settings") or {}
    default_cur = settings.get("default_currency", "EUR")
    if default_cur not in SUPPORTED_CURRENCIES:
        default_cur = "EUR"
    cur_options = list(SUPPORTED_CURRENCIES.keys())

    if bank_fmt == "pdf":
        inferred = "EUR"
    else:
        codes = [str(c).strip().upper()
                 for c in expenses_only["currency"].dropna()
                 if str(c).strip()]
        inferred = codes[0] if len(set(codes)) == 1 else default_cur
        if inferred not in SUPPORTED_CURRENCIES:
            inferred = default_cur

    stmt_cur = st.selectbox(
        "Statement currency",
        options=cur_options,
        index=cur_options.index(inferred),
        key=f"stmt_cur_{uploaded.name}_{uploaded.size}",
    )
    st.caption("Statement currency: amounts are converted to EUR at import "
               "using your rate table.")

    if bank_fmt == "pdf":
        expenses_only["currency"] = stmt_cur
    else:
        expenses_only["currency"] = (expenses_only["currency"]
                                     .fillna(stmt_cur).replace("", stmt_cur))

    # Convert to EUR using the user's rate table
    expenses_only["amount_eur"] = expenses_only.apply(
        lambda r: _to_eur_amount(r["amount"], r.get("currency", "EUR"), rates), axis=1
    )

    # Warn about any currency with no rate: those rows show NaN in the editor
    # and are skipped at save unless the user edits them to a supported code.
    unknown_codes = sorted({str(c).strip().upper()
                            for c in expenses_only["currency"].dropna()
                            if str(c).strip().upper()
                            and str(c).strip().upper() not in rates})
    if unknown_codes:
        st.warning("No exchange rate for " + ", ".join(unknown_codes) +
                   " — these rows will be skipped unless you set the currency "
                   "to a supported one.")

    st.subheader(f"Review & edit ({len(expenses_only)} rows)")
    st.caption("Correct categories and untick any row you don't want to import.")

    review = expenses_only[["date","description","amount","currency","amount_eur",
                            "category","subcategory",
                            "_suggest_source","_suggest_conf","_suggest_cat",
                            "_suggest_sub","_suggest_sub_source","_suggest_sub_conf"]].copy()
    review["include"] = True

    edited = st.data_editor(
        review,
        num_rows="fixed",
        hide_index=True,
        key=f"bank_review_{uploaded.name}_{uploaded.size}",
        column_config={
            "date": st.column_config.DateColumn("Date"),
            "description": st.column_config.TextColumn("Description"),
            "category": st.column_config.SelectboxColumn("Category", options=CAT_LIST),
            "subcategory": st.column_config.SelectboxColumn("Subcategory", options=ALL_SUBCATS),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "currency": st.column_config.SelectboxColumn("Currency", options=list(SUPPORTED_CURRENCIES.keys())),
            "amount_eur": None,
            "include": st.column_config.CheckboxColumn("Import", default=True),
            "_suggest_source": None,
            "_suggest_conf": None,
            "_suggest_cat": None,
            "_suggest_sub": None,
            "_suggest_sub_source": None,
            "_suggest_sub_conf": None,
        },
    )

    n_include = int(edited["include"].sum()) if not edited.empty else 0

    if st.button(f"Import {n_include} expenses", type="primary", width="stretch",
                 icon=":material/check:"):
        existing = q.expenses(user_id)
        existing_keys = set()
        if not existing.empty:
            import re as _re2
            existing_keys = set(zip(
                existing["date"].dt.date,
                existing["description"].apply(lambda s: _re2.sub(r"\s+", " ", str(s)).strip().lower()),
                existing["amount_eur"].round(2),
            ))

        imported = 0
        skipped  = 0
        failed   = 0
        failed_msgs = []
        for _, row in edited[edited["include"]].iterrows():
            try:
                if _save_edited_row(user_id, row, rates, existing_keys) == "imported":
                    imported += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                failed_msgs.append(f"{row.get('description', '?')}: {e}")

        if imported > 0:
            q.bump_db_version()
            st.success(f"Successfully imported **{imported}** expenses!",
                       icon=":material/check:")
            if skipped:
                st.caption(f"{skipped} row(s) skipped (invalid amounts or duplicates).")
            if failed:
                detail = f" ({failed_msgs[0]})" if failed_msgs else ""
                st.warning(f"{failed} row(s) failed to save{detail}")
            st.balloons()
        else:
            st.error(f"No expenses could be imported ({skipped} skipped, {failed} failed). "
                     "Please check the data.")
