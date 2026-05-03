"""
utils.py — Shared constants, formatting helpers, CSS, and UI utilities.
"""

import socket
import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORIES = {
    "Housing":       ["Rent / Mortgage","Electricity","Gas & Heating","Water",
                      "Internet & Phone","Home Insurance","Building Maintenance","Furniture & Appliances"],
    "Food & Dining": ["Groceries","Restaurants & Takeaway","Coffee & Snacks",
                      "Food Delivery","Work Lunch"],
    "Transport":     ["Fuel","Public Transit","Taxi / Uber","Car Insurance",
                      "Car Maintenance","Parking","Tolls"],
    "Health":        ["Gym & Fitness","Pharmacy","Doctor / Specialist","Dental",
                      "Supplements","Mental Health"],
    "Entertainment": ["Streaming Services","Cinema & Theater","Concerts & Events",
                      "Going Out","Hobbies","Books & Courses","Vacation / Travel"],
    "Personal":      ["Clothing & Accessories","Beauty & Skincare","Haircut & Grooming","Gifts"],
    "Other":         ["Subscriptions & Software","Taxes & Fees","Charity & Donations","Miscellaneous"],
}

INCOME_SOURCES  = ["Primary Salary","Freelance / Side Income","Investment Returns","Rental Income","Other"]
SAVINGS_GOALS   = ["Emergency Fund","Vacation / Travel","Investment Account","Down Payment","Other"]
CHART_COLORS    = ["#0F3460","#E94560","#00B050","#F4A261","#457B9D","#A8DADC","#E9C46A","#2A9D8F"]
CAT_LIST        = list(CATEGORIES.keys())

SUPPORTED_CURRENCIES = {
    "EUR": "€",  "RSD": "din", "USD": "$",   "GBP": "£",
    "CHF": "CHF","HRK": "kn",  "BAM": "KM",  "HUF": "Ft",
    "RON": "lei","BGN": "лв",  "PLN": "zł",  "CZK": "Kč",
}

NEAR_LIMIT_THRESHOLD  = 0.85
SAVINGS_TARGET_PCT    = 15
SAVINGS_GOAL_PCT      = 20
BACKUP_RETENTION_DAYS = 30
APP_PORT              = 8501
MAX_AMOUNT            = 1_000_000.0
MAX_SAVINGS_TARGET    = 10_000_000.0


# ── Formatting ────────────────────────────────────────────────────────────────

def get_currency_symbol(currency: str) -> str:
    return SUPPORTED_CURRENCIES.get(currency, currency)


def to_display(eur: float, currency: str, rate: float) -> float:
    """Convert EUR amount to display currency."""
    if currency == "RSD":
        return eur * rate
    return eur


def fmt(eur: float, currency: str, rate: float) -> str:
    """Format a EUR value for display in the chosen currency."""
    v = to_display(eur, currency, rate)
    sym = get_currency_symbol(currency)
    if currency in ("RSD", "HUF", "HRK"):
        return f"{v:,.0f} {sym}"
    return f"{sym}{v:,.2f}"


def fmt_both(eur: float, rate: float) -> str:
    """Show value in both EUR and RSD."""
    return f"€{eur:,.2f}  /  {eur * rate:,.0f} din"


def pbar(pct: float, color: str) -> str:
    """Return an HTML progress bar string."""
    width = min(max(pct, 0), 100)
    return (f'<div class="pw">'
            f'<div class="pb" style="width:{width:.1f}%;background:{color};"></div>'
            f'</div>')


# ── Error helpers ─────────────────────────────────────────────────────────────

def safe_error(msg: str):
    st.error(f"😕 {msg}\n\nIf this keeps happening, try refreshing the page.")


def safe_warning(msg: str):
    st.warning(f"⚠️ {msg}")


def try_or_error(fn, fallback, friendly_msg: str):
    try:
        return fn()
    except Exception as e:
        safe_error(f"{friendly_msg} (Detail: {e})")
        return fallback


# ── Help expander ─────────────────────────────────────────────────────────────

def help_expander(title: str, content: str):
    with st.expander(f"ℹ️ {title}"):
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
        .kpi { padding: 12px 8px; }
        .kpi-val { font-size: 18px; }
        .stButton > button {
            width: 100%;
            font-size: 16px;
            padding: 12px;
            border-radius: 10px;
        }
        div[data-testid="column"] { min-width: 100% !important; }
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


# ── Network ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "your-pc-ip"
