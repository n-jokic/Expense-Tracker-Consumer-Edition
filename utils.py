"""
utils.py — Shared constants, currency engine, formatting helpers, CSS, and network utilities.
"""

import io
import os
import math
import socket
import calendar
from datetime import date as _date, timedelta as _td

import pandas as pd
import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORIES = {
    "Housing & Utilities": ["Rent / Mortgage","Electricity","Gas & Heating","Water",
                            "Internet & Phone","Home Insurance","Building Maintenance","Furniture & Appliances"],
    "Groceries":           ["Groceries"],
    "Dining Out":          ["Restaurants & Takeaway","Coffee & Snacks","Food Delivery","Work Lunch"],
    "Transport":           ["Fuel","Public Transit","Taxi / Uber","Car Insurance",
                            "Car Maintenance","Parking","Tolls"],
    "Travel":              ["Flights & Trains","Hotels & Lodging","Tours & Activities"],
    "Health":              ["Gym & Fitness","Pharmacy","Doctor / Specialist","Dental",
                            "Supplements","Mental Health"],
    "Entertainment":       ["Streaming Services","Cinema & Theater","Concerts & Events",
                            "Going Out","Hobbies","Books & Courses"],
    "Shopping":            ["Clothing & Accessories","Beauty & Skincare","Haircut & Grooming","Gifts"],
    "Subscriptions & Software": ["Subscriptions & Software"],
    "Fees & Taxes":        ["Taxes & Fees","Bank & ATM Fees"],
    "Loans & Debt":        ["Loan Repayment","Interest","Credit Card","Other Debt"],
    "Other":               ["Charity & Donations","Miscellaneous"],
}

INCOME_SOURCES  = ["Primary Salary","Freelance / Side Income","Investment Returns","Rental Income","Other"]
INCOME_TYPES    = ["Salary","Hourly","Bonus / Raise","Freelance","Investment","Rental","Other"]
SAVINGS_GOALS   = ["Emergency Fund","Vacation / Travel","Investment Account","Down Payment","Other"]
CHART_COLORS    = ["#0F3460","#E94560","#00B050","#F4A261","#457B9D","#A8DADC","#E9C46A","#2A9D8F"]
CAT_LIST        = list(CATEGORIES.keys())
ALL_SUBCATS     = sorted({s for subs in CATEGORIES.values() for s in subs})


# ── Taxonomy migration (old → new category/subcategory) ──────────────────────
#
# Rows are (old_cat, old_sub, new_cat, new_sub); an empty subcategory ("")
# means the whole category. Used by db._migrate (rewrite stored data) and by
# sync_core.validate_fields (accept legacy names from syncing devices).

TAXONOMY_MIGRATION = [
    # Housing -> Housing & Utilities (subcategory names unchanged)
    ("Housing", "Rent / Mortgage",        "Housing & Utilities", "Rent / Mortgage"),
    ("Housing", "Electricity",            "Housing & Utilities", "Electricity"),
    ("Housing", "Gas & Heating",          "Housing & Utilities", "Gas & Heating"),
    ("Housing", "Water",                  "Housing & Utilities", "Water"),
    ("Housing", "Internet & Phone",       "Housing & Utilities", "Internet & Phone"),
    ("Housing", "Home Insurance",         "Housing & Utilities", "Home Insurance"),
    ("Housing", "Building Maintenance",   "Housing & Utilities", "Building Maintenance"),
    ("Housing", "Furniture & Appliances", "Housing & Utilities", "Furniture & Appliances"),
    ("Housing", "",                       "Housing & Utilities", ""),
    # Food & Dining splits into Groceries / Dining Out
    ("Food & Dining", "Groceries",               "Groceries", "Groceries"),
    ("Food & Dining", "Restaurants & Takeaway",  "Dining Out", "Restaurants & Takeaway"),
    ("Food & Dining", "Coffee & Snacks",         "Dining Out", "Coffee & Snacks"),
    ("Food & Dining", "Food Delivery",           "Dining Out", "Food Delivery"),
    ("Food & Dining", "Work Lunch",              "Dining Out", "Work Lunch"),
    ("Food & Dining", "",                        "Groceries", "Groceries"),  # documented default
    # Transport: the travel subcategory moves to Travel, the rest stay
    ("Transport", "Fuel",           "Transport", "Fuel"),
    ("Transport", "Public Transit", "Transport", "Public Transit"),
    ("Transport", "Taxi / Uber",    "Transport", "Taxi / Uber"),
    ("Transport", "Car Insurance",  "Transport", "Car Insurance"),
    ("Transport", "Car Maintenance","Transport", "Car Maintenance"),
    ("Transport", "Parking",        "Transport", "Parking"),
    ("Transport", "Tolls",          "Transport", "Tolls"),
    ("Transport", "Flights & Trains", "Travel", "Flights & Trains"),
    ("Transport", "",               "Transport", ""),
    # Health unchanged
    ("Health", "Gym & Fitness",       "Health", "Gym & Fitness"),
    ("Health", "Pharmacy",            "Health", "Pharmacy"),
    ("Health", "Doctor / Specialist", "Health", "Doctor / Specialist"),
    ("Health", "Dental",              "Health", "Dental"),
    ("Health", "Supplements",         "Health", "Supplements"),
    ("Health", "Mental Health",       "Health", "Mental Health"),
    ("Health", "",                    "Health", ""),
    # Entertainment: travel subcategories move to Travel, the rest stay
    ("Entertainment", "Streaming Services",  "Entertainment", "Streaming Services"),
    ("Entertainment", "Cinema & Theater",    "Entertainment", "Cinema & Theater"),
    ("Entertainment", "Concerts & Events",   "Entertainment", "Concerts & Events"),
    ("Entertainment", "Going Out",           "Entertainment", "Going Out"),
    ("Entertainment", "Hobbies",             "Entertainment", "Hobbies"),
    ("Entertainment", "Books & Courses",     "Entertainment", "Books & Courses"),
    ("Entertainment", "Vacation / Travel",   "Travel", "Tours & Activities"),
    ("Entertainment", "Hotels & Lodging",    "Travel", "Hotels & Lodging"),
    ("Entertainment", "",                    "Entertainment", ""),
    # Personal -> Shopping
    ("Personal", "Clothing & Accessories", "Shopping", "Clothing & Accessories"),
    ("Personal", "Beauty & Skincare",      "Shopping", "Beauty & Skincare"),
    ("Personal", "Haircut & Grooming",     "Shopping", "Haircut & Grooming"),
    ("Personal", "Gifts",                  "Shopping", "Gifts"),
    ("Personal", "",                       "Shopping", ""),
    # Loans & Debt unchanged
    ("Loans & Debt", "Loan Repayment", "Loans & Debt", "Loan Repayment"),
    ("Loans & Debt", "Interest",       "Loans & Debt", "Interest"),
    ("Loans & Debt", "Credit Card",    "Loans & Debt", "Credit Card"),
    ("Loans & Debt", "Other Debt",     "Loans & Debt", "Other Debt"),
    # Other: software and taxes move to their own categories
    ("Other", "Subscriptions & Software", "Subscriptions & Software", "Subscriptions & Software"),
    ("Other", "Taxes & Fees",             "Fees & Taxes", "Taxes & Fees"),
    ("Other", "Charity & Donations",      "Other", "Charity & Donations"),
    ("Other", "Miscellaneous",            "Other", "Miscellaneous"),
    ("Other", "",                         "Other", "Miscellaneous"),
]

_TAXONOMY_LOOKUP = {(oc, os): (nc, ns) for oc, os, nc, ns in TAXONOMY_MIGRATION}

# Category-only renames for tables that store no subcategory (big_purchases).
CATEGORY_RENAMES = {
    "Housing": "Housing & Utilities",
    "Food & Dining": "Groceries",
    "Personal": "Shopping",
}


def remap_category_subcategory(category, subcategory=""):
    """Map an (old) category/subcategory pair to its new taxonomy name.

    Unknown pairs pass through unchanged, so re-running a migration is a
    natural no-op.
    """
    cat = category or ""
    sub = subcategory or ""
    return _TAXONOMY_LOOKUP.get((cat, sub), (cat, sub))


# Moved travel subcategories (used to collapse old travel pool entries).
_TRAVEL_SUBCATS = {"Vacation / Travel", "Hotels & Lodging", "Flights & Trains"}


def remap_fun_categories(entries):
    """Migrate a fun_categories list to the new taxonomy.

    "Food & Dining" -> "Dining Out"; "Groceries" is dropped; unknown entries
    are kept. Duplicates are removed while preserving order.
    """
    out = []
    for e in (entries or []):
        e = (e or "").strip()
        if e == "Food & Dining":
            out.append("Dining Out")
        elif e == "Groceries":
            continue
        elif e:
            out.append(e)
    return list(dict.fromkeys(out))


def remap_travel_categories(entries):
    """Migrate a travel_categories list to the new taxonomy.

    Any pair whose subcategory is one of the moved travel subcategories, or
    the whole "Entertainment" category, collapses to "Travel".
    """
    out = []
    for e in (entries or []):
        e = (e or "").strip()
        if not e:
            continue
        if " › " in e:
            _cat, sub = e.split(" › ", 1)
            if sub.strip() in _TRAVEL_SUBCATS:
                out.append("Travel")
                continue
        elif e == "Entertainment":
            out.append("Travel")
            continue
        out.append(e)
    return list(dict.fromkeys(out))

SUPPORTED_CURRENCIES = {
    "EUR": "€",  "RSD": "din", "USD": "$",   "GBP": "£",
    "CHF": "CHF","HRK": "kn",  "BAM": "KM",  "HUF": "Ft",
    "RON": "lei","BGN": "лв",  "PLN": "zł",  "CZK": "Kč",
}

# 1 EUR = X in that currency. These are editable fallbacks; the user's own
# values live in user_settings.currency_rates.
DEFAULT_RATES = {
    "EUR": 1.0,
    "RSD": 117.0,
    "USD": 1.08,
    "GBP": 0.85,
    "CHF": 0.94,
    "HRK": 7.5345,
    "BAM": 1.9558,
    "HUF": 400.0,
    "RON": 5.0,
    "BGN": 1.9558,
    "PLN": 4.3,
    "CZK": 25.0,
}

NEAR_LIMIT_THRESHOLD  = 0.85
SAVINGS_TARGET_PCT    = 15
SAVINGS_GOAL_PCT      = 20
BACKUP_RETENTION_DAYS = 30
APP_PORT              = 8501
# LAN access is plain HTTP by default; set EXPENSE_TRACKER_TLS=1 to serve
# HTTPS with a self-signed cert (run make_cert.py first).
TLS_ENABLED           = os.environ.get("EXPENSE_TRACKER_TLS") == "1"
MAX_AMOUNT            = 1_000_000.0
MAX_SAVINGS_TARGET    = 10_000_000.0

DEFAULT_FUN_CATEGORIES    = ["Entertainment","Dining Out"]
# "Category › Subcategory" pairs; a bare category name = whole category counts.
DEFAULT_TRAVEL_CATEGORIES = ["Travel"]


# ── Currency engine ───────────────────────────────────────────────────────────
#
# All amounts are stored twice: the original (amount, currency) and the EUR
# base value (amount_eur) snapshotted at entry time. Displaying the stored
# original amount whenever the display currency matches the row's currency
# means editing exchange rates later never rewrites history.

def get_currency_symbol(currency: str) -> str:
    return SUPPORTED_CURRENCIES.get(currency, currency)


def _valid_rate(v) -> float | None:
    """An exchange rate is usable only if it is finite and strictly positive.
    Zero, negative, NaN, and infinity are rejected — a zero rate must never
    be silently interpreted as a 1:1 conversion."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def get_rates(settings: dict) -> dict:
    """Return the per-currency rate table (1 EUR = X) for a settings dict.

    Stored values that are zero, negative, or non-finite are ignored so a
    corrupt manual entry can never poison conversions.
    """
    rates = dict(DEFAULT_RATES)
    stored = settings.get("currency_rates")
    if isinstance(stored, dict) and stored:
        for k, v in stored.items():
            f = _valid_rate(v)
            if f is not None:
                rates[k] = f
    else:
        # Legacy installs (no stored table, or an EMPTY dict): a single
        # exchange_rate column (EUR -> RSD).
        legacy = settings.get("exchange_rate")
        if legacy:
            f = _valid_rate(legacy)
            if f is not None:
                rates["RSD"] = f
    rates["EUR"] = 1.0
    return rates


def to_eur(amount: float, currency: str, rates: dict) -> float:
    """Convert a local-currency amount into its EUR base value."""
    if currency == "EUR":
        return round(float(amount), 4)
    r = _valid_rate(rates.get(currency, 1.0))
    if r is None:
        raise ValueError(f"Invalid exchange rate for {currency}: "
                         f"{rates.get(currency)!r} — must be > 0 and finite")
    return round(float(amount) / r, 4)


def to_display(eur: float, currency: str, rates: dict) -> float:
    """Convert a EUR-based aggregate into the display currency."""
    if currency == "EUR":
        return float(eur)
    r = _valid_rate(rates.get(currency, 1.0))
    if r is None:
        raise ValueError(f"Invalid exchange rate for {currency}: "
                         f"{rates.get(currency)!r} — must be > 0 and finite")
    return float(eur) * r


def to_display_row(eur: float, orig_amount: float, orig_currency: str,
                   currency: str, rates: dict) -> float:
    """Convert a stored row for display; the original amount wins when the
    row's currency equals the display currency (history never mutates)."""
    if orig_currency == currency:
        return float(orig_amount)
    return to_display(eur, currency, rates)


def effective_category_budgets(m_bud) -> dict:
    """Effective budget per category for a single month, applying budget-scope
    semantics: when subcategory-specific rows exist for a category they are
    authoritative and the entire-category row (subcategory == "") is ignored;
    otherwise the entire-category row applies. Overlapping rows are never
    summed together."""
    if m_bud is None or m_bud.empty:
        return {}
    df = m_bud.copy()
    df["_sub"] = df["subcategory"].fillna("").astype(str).str.strip()
    eff = {}
    for cat, g in df.groupby("category"):
        subs = g[g["_sub"] != ""]
        if not subs.empty:
            eff[cat] = float(subs["budgeted_eur"].sum())
        else:
            eff[cat] = float(g["budgeted_eur"].sum())
    return eff


def filter_started_templates(df, year: int, month: int):
    """Recurring templates whose start_month ("YYYY-MM") is on or before the
    given month. None/blank start_month = always active (legacy templates),
    and frames without the column pass through unchanged."""
    if df is None or df.empty or "start_month" not in df.columns:
        return df
    cur = f"{year:04d}-{month:02d}"
    started = df["start_month"].fillna("").astype(str).str.strip()
    return df[(started == "") | (started <= cur)]  # YYYY-MM compares lexically


def _fmt_number(v: float, currency: str) -> str:
    sym = get_currency_symbol(currency)
    if currency in ("RSD", "HUF", "HRK"):
        return f"{v:,.0f} {sym}"
    return f"{sym}{v:,.2f}"


def fmt(eur: float, currency: str, rates: dict) -> str:
    """Format a EUR-based aggregate in the display currency."""
    return _fmt_number(to_display(eur, currency, rates), currency)


def fmt_row(eur: float, orig_amount: float, orig_currency: str,
            currency: str, rates: dict) -> str:
    """Format a stored row in the display currency, preserving original values."""
    return _fmt_number(to_display_row(eur, orig_amount, orig_currency, currency, rates),
                       currency)


def fmt_dual(orig_amount: float, orig_currency: str, eur: float) -> str:
    """Show the original amount plus its EUR equivalent, e.g. '10,000 din / €85.47'."""
    if orig_currency == "EUR":
        return f"€{eur:,.2f}"
    return f"{_fmt_number(float(orig_amount), orig_currency)} / €{eur:,.2f}"


# ── Salary-cycle math (forecast) ──────────────────────────────────────────────

def compute_salary_cycle(today: _date, salary_day: int = 10,
                         latest_salary: _date | None = None) -> tuple[_date, _date]:
    """Return (period_start, period_end) for a salary cycle.

    period_end is the day before the next cycle start. Month-end salary days
    (29/30/31) are clamped with calendar.monthrange at EVERY construction so
    they never raise.
    """
    def _clamped(y, m):
        return _date(y, m, min(salary_day, calendar.monthrange(y, m)[1]))

    if latest_salary is not None:
        period_start = latest_salary
    elif today.day >= salary_day:
        period_start = _clamped(today.year, today.month)
    elif today.month > 1:
        period_start = _clamped(today.year, today.month - 1)
    else:
        period_start = _clamped(today.year - 1, 12)

    next_m  = period_start.month + 1 if period_start.month < 12 else 1
    next_y  = period_start.year if period_start.month < 12 else period_start.year + 1
    last_day = calendar.monthrange(next_y, next_m)[1]
    period_end = _date(next_y, next_m, min(period_start.day, last_day)) - _td(days=1)
    return period_start, period_end


# ── Formatting helpers ────────────────────────────────────────────────────────

def pbar(pct: float, color: str) -> str:
    """Return an HTML progress bar string."""
    width = min(max(pct, 0), 100)
    return (f'<div class="pw">'
            f'<div class="pb" style="width:{width:.1f}%;background:{color};"></div>'
            f'</div>')


# ── Fun money & travel pools ─────────────────────────────────────────────────

def _pool_members(entries) -> tuple[list, list]:
    """Split pool entries into (category_names, subcategory_names).

    Pool entries may be a category name (matches all its subcategories) or,
    for backward compatibility, a bare subcategory name.
    """
    cats, subs = [], []
    for e in (entries or []):
        e = (e or "").strip()
        if not e:
            continue
        if e in CATEGORIES:
            cats.append(e)
        elif e in ALL_SUBCATS:
            subs.append(e)
    return cats, subs


def fun_spent(expenses_df, categories, year: int, month: int) -> float:
    """EUR spent this month across the fun-money categories.

    A category name matches ALL of its subcategories; a bare subcategory name
    is still accepted for backward compatibility.
    """
    if expenses_df is None or expenses_df.empty or not categories:
        return 0.0
    m = expenses_df[(expenses_df["date"].dt.year == year) &
                    (expenses_df["date"].dt.month == month)]
    if m.empty:
        return 0.0
    cats, subs = _pool_members(categories)
    mask = m["category"].isin(cats) if cats else pd.Series(False, index=m.index)
    if subs and "subcategory" in m.columns:
        mask = mask | m["subcategory"].fillna("").isin(subs)
    return float(m[mask]["amount_eur"].sum())


def travel_spent(expenses_df, pairs, year: int) -> float:
    """EUR spent this year on travel pairs.

    Each entry may be a "Category › Subcategory" pair (empty subcategory =
    whole category counts), a bare category name (whole category), or a bare
    subcategory name (backward compatibility). Overlapping pairs (e.g.
    "Travel › (all)" plus "Travel › Flights") are unioned — an expense is
    never counted twice.
    """
    if expenses_df is None or expenses_df.empty or not pairs:
        return 0.0
    y = expenses_df[expenses_df["date"].dt.year == year]
    if y.empty:
        return 0.0
    mask = pd.Series(False, index=y.index)
    for pair in pairs:
        if not pair:
            continue
        # NB: "Category › " (trailing space) means the whole category, so the
        # " › " separator is checked BEFORE stripping trailing whitespace.
        if " › " in pair:
            cat, sub = pair.split(" › ", 1)
            cat, sub = cat.strip(), sub.strip()
            if sub:
                mask = mask | ((y["category"] == cat) &
                               (y["subcategory"] == sub))
            else:
                mask = mask | (y["category"] == cat)
        else:
            bare = pair.strip()
            if bare in CATEGORIES:
                mask = mask | (y["category"] == bare)
            elif bare in ALL_SUBCATS:
                mask = mask | (y["subcategory"].fillna("") == bare)
    return float(y[mask]["amount_eur"].sum())


# ── Big-purchase priority matrix ──────────────────────────────────────────────

QUADRANT_COLORS = {
    "Quick wins":   "#00B050",
    "Plan & save":  "#0F3460",
    "Maybe later":  "#A8A8A8",
    "Reconsider":   "#E94560",
}


def classify_quadrant(work_hours: float, usage_hours: float,
                      median_work: float, median_usage: float) -> str:
    """4-square priority matrix: expected usage vs work-hours needed to buy."""
    high_usage = usage_hours > median_usage
    high_work  = work_hours > median_work
    if high_usage and not high_work:
        return "Quick wins"
    if high_usage and high_work:
        return "Plan & save"
    if not high_usage and not high_work:
        return "Maybe later"
    return "Reconsider"


def sortable_grouped_ids(groups: dict, key: str) -> dict:
    """Return persisted-friendly IDs after optional category drag/drop.

    The sortable component only transports strings, so each visible label gets
    an invisible unique suffix. If the optional dependency is unavailable, the
    original order is returned and the page remains usable.
    """
    original = {str(category): [str(item_id) for item_id, _ in items]
                for category, items in groups.items()}
    try:
        from streamlit_sortables import sort_items
    except Exception:
        return original

    marker = "\u2063"
    payload = [{
        "header": str(category),
        "items": [f"{label}{marker}{item_id}" for item_id, label in items],
    } for category, items in groups.items()]
    try:
        sorted_payload = sort_items(payload, multi_containers=True,
                                    direction="vertical", key=key)
    except Exception:
        return original

    result = {}
    for container in sorted_payload or []:
        category = str(container.get("header", ""))
        ids = []
        for token in container.get("items", []) or []:
            token = str(token)
            if marker not in token:
                return original
            ids.append(token.rsplit(marker, 1)[1])
        result[category] = ids

    expected = {item_id for ids in original.values() for item_id in ids}
    actual = {item_id for ids in result.values() for item_id in ids}
    return result if expected == actual else original


# Cells starting with these characters are treated as formulas when the
# spreadsheet opens (or when a CSV is re-imported): a user-supplied value
# like "=HYPERLINK(...)" would execute. Prefixing with a quote makes
# openpyxl/pandas write them as literal text cells (data_type 's').
_XL_UNSAFE_PREFIXES = ("=", "+", "@")


def _xl_safe(v):
    if isinstance(v, str) and v.startswith(_XL_UNSAFE_PREFIXES):
        return "'" + v
    # "-" alone starts a formula only when followed by a digit (a plain
    # "-rebate" note is inert text and must not gain a stray quote).
    if isinstance(v, str) and len(v) > 1 and v[0] == "-" and v[1].isdigit():
        return "'" + v
    return v


def to_excel(df) -> bytes:
    buf = io.BytesIO()
    safe = df.copy()
    from pandas.api import types as pd_types
    for col in safe.columns:
        # pandas 3 uses "str" dtype for string columns, not object.
        if pd_types.is_string_dtype(safe[col]) or safe[col].dtype == object:
            safe[col] = safe[col].astype(object).map(_xl_safe)
    safe.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


# ── Error helpers ─────────────────────────────────────────────────────────────

def safe_error(msg: str):
    st.error(msg, icon=":material/error:")


def safe_warning(msg: str):
    st.warning(msg, icon=":material/warning:")


def try_or_error(fn, fallback, friendly_msg: str):
    try:
        return fn()
    except Exception as e:
        safe_error(f"{friendly_msg} (Detail: {e})")
        return fallback


def help_expander(title: str, content: str):
    with st.expander(title, icon=":material/help:"):
        st.markdown(content)


# ── Mobile & global CSS ───────────────────────────────────────────────────────

def inject_mobile_css():
    st.markdown("""
    <style>
    /* ── KPI cards ─────────────────────────────────────────────── */
    .kpi {
        background: var(--secondary-background-color);
        border-radius: 14px;
        padding: 18px 12px;
        text-align: center;
        border: 1px solid rgba(128,128,128,0.15);
        margin-bottom: 6px;
        transition: box-shadow 0.2s ease, transform 0.15s ease;
    }
    .kpi:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.10); transform: translateY(-2px); }
    .kpi-val { font-size: 22px; font-weight: 700; margin: 6px 0; }
    .kpi-sub { font-size: 11px; color: #888; margin-top: 3px; }
    .kpi-lbl { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: .7px; }

    /* ── Colours ────────────────────────────────────────────────── */
    .pos { color: #00B050; }
    .neg { color: #E94560; }
    .neu { color: #0F3460; }

    /* ── Progress bar ───────────────────────────────────────────── */
    .pw { background: #e0e0e0; border-radius: 8px; height: 14px; overflow: hidden; margin: 5px 0; }
    .pb { height: 100%; border-radius: 8px; transition: width 0.4s ease; }

    /* ── Badges (gamification) ──────────────────────────────────── */
    .badge {
        display: inline-block;
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.2);
        border-radius: 20px;
        padding: 4px 10px;
        font-size: 12px;
        margin: 2px;
    }

    /* ── Mobile ─────────────────────────────────────────────────── */
    @media (max-width: 768px) {
        .stButton > button {
            width: 100%;
            font-size: 16px;
            padding: 12px;
            border-radius: 10px;
        }
        /* Stack only KPI-column metrics on phones (a global min-width on
           every column broke tables and forms on every page). Pages render
           Streamlit's native st.metric, not the legacy .kpi divs. */
        div[data-testid="column"]:has([data-testid="stMetric"]) { min-width: 100% !important; }
        .stDataFrame { font-size: 13px; }
        h1 { font-size: 1.6rem !important; }
        h2 { font-size: 1.3rem !important; }
    }

    /* ── Forms ──────────────────────────────────────────────────── */
    div[data-testid="stForm"] {
        border: 1px solid rgba(128,128,128,0.15);
        border-radius: 12px;
        padding: 16px;
    }

    /* ── Sidebar ────────────────────────────────────────────────── */
    section[data-testid="stSidebar"] { min-width: 240px; }
    </style>
    """, unsafe_allow_html=True)


# ── Network (LAN phone access) ───────────────────────────────────────────────

def get_server_port() -> int:
    """Port the running Streamlit server listens on."""
    try:
        return int(st.get_option("server.port"))
    except Exception:
        pass
    try:
        return int(os.environ.get("STREAMLIT_SERVER_PORT", APP_PORT))
    except Exception:
        return APP_PORT


@st.cache_data(ttl=60, show_spinner=False)
def get_lan_urls(port: int):
    """Return (urls, hostname) for this machine's LAN addresses.

    Works without internet access: the UDP probe to 8.8.8.8 is a best-effort
    hint, and we always fall back to the hostname's own addresses.
    """
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    hostname = None
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    urls = []
    scheme = "https" if TLS_ENABLED else "http"
    for ip in sorted(ips):
        if ip.startswith(("127.", "169.254.")):
            continue
        urls.append(f"{scheme}://{ip}:{port}")
    return urls, hostname


def qr_png(url: str) -> bytes:
    """Return a PNG QR code for the given URL.

    PNG is used instead of SVG because qrcode's SVG uses namespace-prefixed
    elements (<svg:rect>) that the HTML parser can't map when injected into
    the page, which rendered as an invisible image.
    """
    import io
    import qrcode
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
