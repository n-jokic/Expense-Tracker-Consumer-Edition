"""
utils.py — Shared constants, currency engine, formatting helpers, CSS, and network utilities.
"""

import io
import os
import socket
import calendar
from datetime import date as _date, timedelta as _td

import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────

CATEGORIES = {
    "Housing":       ["Rent / Mortgage","Electricity","Gas & Heating","Water",
                      "Internet & Phone","Home Insurance","Building Maintenance","Furniture & Appliances"],
    "Food & Dining": ["Groceries","Restaurants & Takeaway","Coffee & Snacks",
                      "Food Delivery","Work Lunch"],
    "Transport":     ["Fuel","Public Transit","Taxi / Uber","Car Insurance",
                      "Car Maintenance","Parking","Tolls","Flights & Trains"],
    "Health":        ["Gym & Fitness","Pharmacy","Doctor / Specialist","Dental",
                      "Supplements","Mental Health"],
    "Entertainment": ["Streaming Services","Cinema & Theater","Concerts & Events",
                      "Going Out","Hobbies","Books & Courses","Vacation / Travel",
                      "Hotels & Lodging"],
    "Personal":      ["Clothing & Accessories","Beauty & Skincare","Haircut & Grooming","Gifts"],
    "Loans & Debt":  ["Loan Repayment","Interest","Credit Card","Other Debt"],
    "Other":         ["Subscriptions & Software","Taxes & Fees","Charity & Donations","Miscellaneous"],
}

INCOME_SOURCES  = ["Primary Salary","Freelance / Side Income","Investment Returns","Rental Income","Other"]
INCOME_TYPES    = ["Salary","Hourly","Bonus / Raise","Freelance","Investment","Rental","Other"]
SAVINGS_GOALS   = ["Emergency Fund","Vacation / Travel","Investment Account","Down Payment","Other"]
CHART_COLORS    = ["#0F3460","#E94560","#00B050","#F4A261","#457B9D","#A8DADC","#E9C46A","#2A9D8F"]
CAT_LIST        = list(CATEGORIES.keys())
ALL_SUBCATS     = sorted({s for subs in CATEGORIES.values() for s in subs})

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
MAX_AMOUNT            = 1_000_000.0
MAX_SAVINGS_TARGET    = 10_000_000.0

DEFAULT_FUN_CATEGORIES    = ["Entertainment"]
# "Category › Subcategory" pairs; empty subcategory = whole category counts.
DEFAULT_TRAVEL_CATEGORIES = [
    "Entertainment › Vacation / Travel",
    "Entertainment › Hotels & Lodging",
    "Transport › Flights & Trains",
]


# ── Currency engine ───────────────────────────────────────────────────────────
#
# All amounts are stored twice: the original (amount, currency) and the EUR
# base value (amount_eur) snapshotted at entry time. Displaying the stored
# original amount whenever the display currency matches the row's currency
# means editing exchange rates later never rewrites history.

def get_currency_symbol(currency: str) -> str:
    return SUPPORTED_CURRENCIES.get(currency, currency)


def get_rates(settings: dict) -> dict:
    """Return the per-currency rate table (1 EUR = X) for a settings dict."""
    rates = dict(DEFAULT_RATES)
    stored = settings.get("currency_rates")
    if isinstance(stored, dict):
        for k, v in stored.items():
            try:
                rates[k] = float(v)
            except (TypeError, ValueError):
                continue
    else:
        # Legacy installs: a single exchange_rate column (EUR -> RSD).
        legacy = settings.get("exchange_rate")
        if legacy:
            try:
                rates["RSD"] = float(legacy)
            except (TypeError, ValueError):
                pass
    rates["EUR"] = 1.0
    return rates


def to_eur(amount: float, currency: str, rates: dict) -> float:
    """Convert a local-currency amount into its EUR base value."""
    if currency == "EUR":
        return round(float(amount), 4)
    r = rates.get(currency, 1.0) or 1.0
    return round(float(amount) / r, 4)


def to_display(eur: float, currency: str, rates: dict) -> float:
    """Convert a EUR-based aggregate into the display currency."""
    if currency == "EUR":
        return float(eur)
    return float(eur) * (rates.get(currency, 1.0) or 1.0)


def to_display_row(eur: float, orig_amount: float, orig_currency: str,
                   currency: str, rates: dict) -> float:
    """Convert a stored row for display; the original amount wins when the
    row's currency equals the display currency (history never mutates)."""
    if orig_currency == currency:
        return float(orig_amount)
    return to_display(eur, currency, rates)


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

def fun_spent(expenses_df, categories, year: int, month: int) -> float:
    """EUR spent this month across the fun-money categories."""
    if expenses_df is None or expenses_df.empty or not categories:
        return 0.0
    m = expenses_df[(expenses_df["date"].dt.year == year) &
                    (expenses_df["date"].dt.month == month)]
    return float(m[m["category"].isin(categories)]["amount_eur"].sum())


def travel_spent(expenses_df, pairs, year: int) -> float:
    """EUR spent this year on travel pairs like 'Entertainment › Vacation / Travel'.
    An empty subcategory means the whole category counts."""
    if expenses_df is None or expenses_df.empty or not pairs:
        return 0.0
    y = expenses_df[expenses_df["date"].dt.year == year]
    total = 0.0
    for pair in pairs:
        if " › " in pair:
            cat, sub = pair.split(" › ", 1)
        else:
            cat, sub = pair, ""
        if sub:
            total += float(y[(y["category"] == cat) &
                             (y["subcategory"] == sub)]["amount_eur"].sum())
        else:
            total += float(y[y["category"] == cat]["amount_eur"].sum())
    return total


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


def to_excel(df) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


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
    for ip in sorted(ips):
        if ip.startswith(("127.", "169.254.")):
            continue
        urls.append(f"http://{ip}:{port}")
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
