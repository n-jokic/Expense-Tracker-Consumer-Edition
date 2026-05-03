"""
bank_import.py — Bank statement CSV importer for Expense Tracker v3.
Supports Revolut, N26, Wise, and generic CSV formats.
"""

import uuid
from datetime import date

import pandas as pd
import streamlit as st

from utils import CATEGORIES, CAT_LIST, MAX_AMOUNT
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


def normalize_bank_csv(df: pd.DataFrame, bank_format: str) -> pd.DataFrame:
    """Return DataFrame with columns: date, description, amount, currency."""
    try:
        if bank_format == "revolut":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(df.get("Started Date", df.iloc[:, 0]), errors="coerce")
            out["description"] = df.get("Description", df.iloc[:, 2]).astype(str)
            out["amount"]      = pd.to_numeric(df.get("Amount", df.iloc[:, 5]), errors="coerce")
            out["currency"]    = df.get("Currency", "EUR")

        elif bank_format == "n26":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(df.get("Date", df.iloc[:, 0]), errors="coerce")
            out["description"] = df.get("Payee", df.get("Partner Name", df.iloc[:, 1])).astype(str)
            amt_col = next((c for c in df.columns if "amount" in c.lower()), df.columns[-1])
            out["amount"]      = pd.to_numeric(df[amt_col], errors="coerce")
            out["currency"]    = "EUR"

        elif bank_format == "wise":
            out = pd.DataFrame()
            out["date"]        = pd.to_datetime(df.get("Date", df.iloc[:, 0]), errors="coerce")
            out["description"] = df.get("Description", df.iloc[:, 2]).astype(str)
            out["amount"]      = pd.to_numeric(df.get("Source amount (after fees)",
                                                df.get("Amount", df.iloc[:, 3])), errors="coerce")
            out["currency"]    = df.get("Source currency", "EUR")

        else:  # generic
            out = pd.DataFrame()
            # Try to find date, description, amount columns by name pattern
            date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
            desc_col = next((c for c in df.columns
                             if any(x in c.lower() for x in ["desc","payee","merchant","name","detail"])),
                            df.columns[1])
            amt_col  = next((c for c in df.columns if "amount" in c.lower()), df.columns[-1])
            cur_col  = next((c for c in df.columns if "currency" in c.lower()), None)
            out["date"]        = pd.to_datetime(df[date_col], errors="coerce")
            out["description"] = df[desc_col].astype(str)
            out["amount"]      = pd.to_numeric(df[amt_col], errors="coerce")
            out["currency"]    = df[cur_col] if cur_col else "EUR"

        return out.dropna(subset=["date", "amount"])
    except Exception as e:
        st.error(f"Could not parse the file: {e}. Please check the format.")
        return pd.DataFrame(columns=["date", "description", "amount", "currency"])


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_bank_import_page(user_id: int, rate: float):
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
    st.dataframe(raw.head(5), use_container_width=True)

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

    # Convert to EUR if needed
    expenses_only["amount_eur"] = expenses_only.apply(
        lambda r: r["amount"] / rate if str(r.get("currency", "EUR")).upper() == "RSD"
        else r["amount"], axis=1
    )

    st.subheader(f"✏️ Review & edit ({len(expenses_only)} rows)")
    st.caption("Correct the category for any row before importing.")

    # Editable category selection per row
    display_rows = []
    for i, (_, row) in enumerate(expenses_only.iterrows()):
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])
        with c1:
            st.write(row["date"].strftime("%d %b %Y") if pd.notna(row["date"]) else "—")
        with c2:
            st.write(str(row["description"])[:35])
        with c3:
            cat = st.selectbox("Category", CAT_LIST,
                               index=CAT_LIST.index(row["category"]) if row["category"] in CAT_LIST else 0,
                               key=f"imp_cat_{i}", label_visibility="collapsed")
        with c4:
            sub_list = [""] + CATEGORIES.get(cat, [])
            default_sub = row["subcategory"] if row["subcategory"] in sub_list else ""
            sub = st.selectbox("Subcategory", sub_list,
                               index=sub_list.index(default_sub),
                               key=f"imp_sub_{i}", label_visibility="collapsed")
        with c5:
            st.write(f"€{row['amount_eur']:,.2f}")

        display_rows.append({
            "date": row["date"].date() if pd.notna(row["date"]) else date.today(),
            "description": str(row["description"]),
            "amount": float(row["amount"]),
            "currency": str(row.get("currency", "EUR")).upper(),
            "amount_eur": float(row["amount_eur"]),
            "category": cat,
            "subcategory": sub,
        })

    st.divider()
    if st.button(f"✅ Import {len(display_rows)} expenses", type="primary", use_container_width=True):
        imported = 0
        skipped  = 0
        for row in display_rows:
            try:
                if row["amount_eur"] <= 0 or row["amount_eur"] > MAX_AMOUNT:
                    skipped += 1
                    continue
                add_expense(user_id, {
                    "date": row["date"],
                    "category": row["category"],
                    "subcategory": row["subcategory"],
                    "description": row["description"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "amount_eur": row["amount_eur"],
                    "recurring": False,
                    "notes": "Imported from bank statement",
                })
                imported += 1
            except Exception:
                skipped += 1

        if imported > 0:
            st.success(f"✅ Successfully imported **{imported}** expenses!")
            if skipped:
                st.caption(f"{skipped} row(s) skipped due to invalid amounts.")
            st.balloons()
        else:
            st.error("No expenses could be imported. Please check the data.")
