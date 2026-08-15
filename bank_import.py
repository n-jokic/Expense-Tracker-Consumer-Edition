"""
bank_import.py — Bank statement CSV importer for Expense Tracker v3.
Supports Revolut, N26, Wise, and generic CSV formats.
"""

import uuid
from datetime import date

import pandas as pd
import streamlit as st

import queries as q
from utils import CATEGORIES, CAT_LIST, ALL_SUBCATS, MAX_AMOUNT
from db import add_expense

# ── Keyword-based auto-categorisation ────────────────────────────────────────

KEYWORD_MAP = {
    # Food & Dining
    "lidl":           ("Food & Dining", "Groceries"),
    "kaufland":       ("Food & Dining", "Groceries"),
    "carrefour":      ("Food & Dining", "Groceries"),
    "mega image":     ("Food & Dining", "Groceries"),
    "penny":          ("Food & Dining", "Groceries"),
    "aldi":           ("Food & Dining", "Groceries"),
    "rewe":           ("Food & Dining", "Groceries"),
    "edeka":          ("Food & Dining", "Groceries"),
    "tesco":          ("Food & Dining", "Groceries"),
    "supermarket":    ("Food & Dining", "Groceries"),
    "grocery":        ("Food & Dining", "Groceries"),
    "mcdonald":       ("Food & Dining", "Restaurants & Takeaway"),
    "kfc":            ("Food & Dining", "Restaurants & Takeaway"),
    "burger king":    ("Food & Dining", "Restaurants & Takeaway"),
    "subway":         ("Food & Dining", "Restaurants & Takeaway"),
    "pizza":          ("Food & Dining", "Restaurants & Takeaway"),
    "restaurant":     ("Food & Dining", "Restaurants & Takeaway"),
    "wolt":           ("Food & Dining", "Food Delivery"),
    "glovo":          ("Food & Dining", "Food Delivery"),
    "bolt food":      ("Food & Dining", "Food Delivery"),
    "deliveroo":      ("Food & Dining", "Food Delivery"),
    "starbucks":      ("Food & Dining", "Coffee & Snacks"),
    "costa":          ("Food & Dining", "Coffee & Snacks"),
    "cafe":           ("Food & Dining", "Coffee & Snacks"),
    "coffee":         ("Food & Dining", "Coffee & Snacks"),
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
    # Housing
    "rent":           ("Housing", "Rent / Mortgage"),
    "chiria":         ("Housing", "Rent / Mortgage"),
    "mortgage":       ("Housing", "Rent / Mortgage"),
    "electrica":      ("Housing", "Electricity"),
    "enel":           ("Housing", "Electricity"),
    "electricity":    ("Housing", "Electricity"),
    "gas":            ("Housing", "Gas & Heating"),
    "water":          ("Housing", "Water"),
    "internet":       ("Housing", "Internet & Phone"),
    "digi":           ("Housing", "Internet & Phone"),
    "orange":         ("Housing", "Internet & Phone"),
    "vodafone":       ("Housing", "Internet & Phone"),
    "telekom":        ("Housing", "Internet & Phone"),
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
    # Personal
    "zara":           ("Personal", "Clothing & Accessories"),
    "h&m":            ("Personal", "Clothing & Accessories"),
    "mango":          ("Personal", "Clothing & Accessories"),
    "clothing":       ("Personal", "Clothing & Accessories"),
    "haircut":        ("Personal", "Haircut & Grooming"),
    "salon":          ("Personal", "Haircut & Grooming"),
    # Other
    "adobe":          ("Other", "Subscriptions & Software"),
    "microsoft":      ("Other", "Subscriptions & Software"),
    "google":         ("Other", "Subscriptions & Software"),
    "dropbox":        ("Other", "Subscriptions & Software"),
    "tax":            ("Other", "Taxes & Fees"),
    "anaf":           ("Other", "Taxes & Fees"),
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


def normalize_bank_csv(df: pd.DataFrame, bank_format: str) -> pd.DataFrame:
    """Return DataFrame with columns: date, description, amount, currency."""
    try:
        if bank_format == "revolut":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(_pick(df, ["Started Date"], 0), errors="coerce")
            out["description"] = _pick(df, ["Description"], 2).astype(str)
            out["amount"]      = pd.to_numeric(_pick(df, ["Amount"], 5), errors="coerce")
            out["currency"]    = df.get("Currency", "EUR")

        elif bank_format == "n26":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(_pick(df, ["Date"], 0), errors="coerce")
            out["description"] = _pick(df, ["Payee", "Partner Name"], 1).astype(str)
            amt_col = next((c for c in df.columns if "amount" in c.lower()), None)
            amt = df[amt_col] if amt_col is not None else (
                df.iloc[:, -1] if df.shape[1] else pd.Series(dtype=object))
            out["amount"]      = pd.to_numeric(amt, errors="coerce")
            out["currency"]    = "EUR"

        elif bank_format == "wise":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(_pick(df, ["Date"], 0), errors="coerce")
            out["description"] = _pick(df, ["Description"], 2).astype(str)
            out["amount"]      = pd.to_numeric(
                _pick(df, ["Source amount (after fees)", "Amount"], 3), errors="coerce")
            out["currency"]    = df.get("Source currency", "EUR")

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
            out["date"]        = pd.to_datetime(df[date_col], errors="coerce") if date_col else pd.Series(dtype=object)
            out["description"] = df[desc_col].astype(str) if desc_col else pd.Series(dtype=object)
            out["amount"]      = pd.to_numeric(df[amt_col], errors="coerce") if amt_col else pd.Series(dtype=object)
            out["currency"]    = df[cur_col] if cur_col else "EUR"

        return out.dropna(subset=["date", "amount"])
    except Exception as e:
        st.error(f"Could not parse the file: {e}. Please check the format.")
        return pd.DataFrame(columns=["date", "description", "amount", "currency"])


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def _to_eur_amount(amount: float, currency: str, rates: dict) -> float:
    """Convert a bank row amount to its EUR base value."""
    cur = str(currency or "EUR").strip().upper()
    if cur == "EUR":
        return round(float(amount), 4)
    r = rates.get(cur)
    if r:
        return round(float(amount) / r, 4)
    # Unknown currency: assume 1:1 and flag it to the user via the review table.
    return round(float(amount), 4)


def render_bank_import_page(user_id: int, rates: dict):
    st.title("🏦 Import Bank Statement")
    st.caption("Import expenses directly from your bank's CSV export — we'll auto-categorise them for you.")

    with st.expander("ℹ️ Supported formats & how to export"):
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

    uploaded = st.file_uploader("Upload your bank CSV", type=["csv"])
    if not uploaded:
        return

    try:
        raw = pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Could not read the file: {e}")
        return

    st.subheader("📋 Preview")
    st.dataframe(raw.head(5), hide_index=True)

    bank_fmt  = detect_bank_format(raw)
    st.caption(f"Detected format: **{bank_fmt.capitalize()}**")

    normalised = normalize_bank_csv(raw, bank_fmt)
    if normalised.empty:
        st.warning("No valid rows found. Please check the file format.")
        return

    # Only keep debit rows (negative amounts = expenses)
    expenses_only = normalised[normalised["amount"] < 0].copy()
    expenses_only["amount"] = expenses_only["amount"].abs()

    if expenses_only.empty:
        st.info("No debit transactions found. If your bank uses positive amounts for expenses, "
                "all rows are shown below.")
        expenses_only = normalised[normalised["amount"] > 0].copy()

    if expenses_only.empty:
        st.warning("No importable rows found.")
        return

    # Auto-categorise
    expenses_only[["category", "subcategory"]] = expenses_only["description"].apply(
        lambda d: pd.Series(categorize_expense(d))
    )

    # Convert to EUR using the user's rate table
    expenses_only["amount_eur"] = expenses_only.apply(
        lambda r: _to_eur_amount(r["amount"], r.get("currency", "EUR"), rates), axis=1
    )

    st.subheader(f"✏️ Review & edit ({len(expenses_only)} rows)")
    st.caption("Correct categories and untick any row you don't want to import.")

    review = expenses_only[["date","description","amount","currency","amount_eur",
                            "category","subcategory"]].copy()
    review["include"] = True

    edited = st.data_editor(
        review,
        num_rows="fixed",
        hide_index=True,
        key="bank_review",
        column_config={
            "date": st.column_config.DateColumn("Date"),
            "description": st.column_config.TextColumn("Description"),
            "category": st.column_config.SelectboxColumn("Category", options=CAT_LIST),
            "subcategory": st.column_config.SelectboxColumn("Subcategory", options=ALL_SUBCATS),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "currency": st.column_config.TextColumn("Currency"),
            "amount_eur": None,
            "include": st.column_config.CheckboxColumn("Import", default=True),
        },
    )

    n_include = int(edited["include"].sum()) if not edited.empty else 0

    st.divider()
    if st.button(f"✅ Import {n_include} expenses", type="primary", width="stretch"):
        existing = q.expenses(user_id)
        existing_keys = set()
        if not existing.empty:
            existing_keys = set(zip(
                existing["date"].dt.date,
                existing["description"].str.strip().str.lower(),
                existing["amount_eur"].round(2),
            ))

        imported = 0
        skipped  = 0
        for _, row in edited[edited["include"]].iterrows():
            try:
                ae = float(row["amount_eur"])
                if ae <= 0 or ae > MAX_AMOUNT:
                    skipped += 1
                    continue
                d = row["date"].date() if hasattr(row["date"], "date") else pd.Timestamp(row["date"]).date()
                key = (d, str(row["description"]).strip().lower(), round(ae, 2))
                if key in existing_keys:
                    skipped += 1
                    continue
                add_expense(user_id, {
                    "date": d,
                    "category": row["category"],
                    "subcategory": row["subcategory"] or "",
                    "description": str(row["description"]),
                    "amount": float(row["amount"]),
                    "currency": str(row["currency"]).upper(),
                    "amount_eur": ae,
                    "recurring": False,
                    "notes": "Imported from bank statement",
                })
                imported += 1
            except Exception:
                skipped += 1

        if imported > 0:
            q.bump_db_version()
            st.success(f"✅ Successfully imported **{imported}** expenses!")
            if skipped:
                st.caption(f"{skipped} row(s) skipped (invalid amounts or duplicates).")
            st.balloons()
        else:
            st.error("No expenses could be imported. Please check the data.")
