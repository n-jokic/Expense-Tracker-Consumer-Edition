"""
utils.py — compatibility shim (R6).

The canonical homes are now:
- domain.taxonomy  → CATEGORIES, CAT_LIST, ALL_SUBCATS, TAXONOMY_MIGRATION,
                     remap_category_subcategory, etc.
- domain.money     → SUPPORTED_CURRENCIES, DEFAULT_RATES, MAX_AMOUNT,
                     get_currency_symbol, get_rates, to_eur, to_display,
                     to_display_row, and related constants
- domain.periods   → filter_started_templates, compute_salary_cycle
- domain.validation/merchant → validation + merchant normalization
- ui.formatting    → fmt, fmt_row, fmt_dual
- ui.styles        → CHART_COLORS, QUADRANT_COLORS, draggable_card_board,
                     validate_grouped_order, safe_error, help_expander,
                     inject_mobile_css
- infra.exporting  → to_excel
- infra.networking → APP_PORT, TLS_ENABLED, get_server_port, get_lan_urls, qr_png

This file re-exports from those modules so existing imports keep working
without a 30-file PR. New code should import from the canonical locations.
"""

# ── domain/taxonomy ────────────────────────────────────────────────────────
from domain.taxonomy import (
    CATEGORIES, INCOME_SOURCES, INCOME_TYPES, SAVINGS_GOALS,
    CAT_LIST, ALL_SUBCATS,
    TAXONOMY_MIGRATION, CATEGORY_RENAMES, _TAXONOMY_LOOKUP, _TRAVEL_SUBCATS,
    remap_category_subcategory, remap_fun_categories, remap_travel_categories,
)
# ── domain/money ───────────────────────────────────────────────────────────
from domain.money import (
    SUPPORTED_CURRENCIES, DEFAULT_RATES,
    NEAR_LIMIT_THRESHOLD, SAVINGS_TARGET_PCT, SAVINGS_GOAL_PCT,
    BACKUP_RETENTION_DAYS, APP_PORT, TLS_ENABLED, MAX_AMOUNT, MAX_SAVINGS_TARGET,
    DEFAULT_FUN_CATEGORIES, DEFAULT_TRAVEL_CATEGORIES,
    get_currency_symbol, get_rates, to_eur, to_display, to_display_row,
)
# ── domain/periods ─────────────────────────────────────────────────────────
from domain.periods import (
    filter_started_templates, compute_salary_cycle,
    month_bounds, parse_date,
)
# ── ui/formatting ──────────────────────────────────────────────────────────
from ui.formatting import _fmt_number, fmt, fmt_row, fmt_dual  # noqa: F401
# ── ui/board + styles (canonical) + compat re-export
import ui.styles as _ui_styles  # ensure module loads without circular import
import ui.board as _ui_board
CHART_COLORS = _ui_styles.CHART_COLORS
QUADRANT_COLORS = _ui_styles.QUADRANT_COLORS
# Expose board types on utils for old imports: utils.ItemMove etc. remain resolvable
ItemMove = _ui_board.ItemMove
BoardResult = _ui_board.BoardResult
BoardAction = _ui_board.BoardAction
# Re-export effectful UI helpers from the real utils implementation via
# dynamic import so that stylistic functions still resolve to their callers.
# Keep the heavy board/export/network helpers here for now (no circular deps):
import io as _io  # noqa: F401
import math as _math  # noqa: F401
import socket as _socket  # noqa: F401
import calendar  # noqa: F401
from datetime import date as _date, timedelta as _td  # noqa: F401
import pandas as pd  # noqa: F401
import streamlit as st  # noqa: F401
import os as _os  # noqa: F401

# effective_category_budgets / pool helpers / board / export / ... stay here
# until Phase 2/3 cleanly extracts them — this shim intentionally keeps the
# legacy code path so existing callers (db.py, sync_core.py intra-function
# imports, tests) don't break. The canonical domain/money exports above are
# the source of truth; below are the remaining utils-only symbols.

# NOTE: the block below is the original utils.py implementation minus the
# symbols already re-exported above. Kept verbatim to avoid semantic drift.
# New code should NOT be added here — add it in domain/*, ui/*, or infra/*.

def effective_category_budgets(m_bud) -> dict:
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

def _pool_members(entries) -> tuple[list, list]:
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
    if expenses_df is None or expenses_df.empty or not pairs:
        return 0.0
    y = expenses_df[expenses_df["date"].dt.year == year]
    if y.empty:
        return 0.0
    mask = pd.Series(False, index=y.index)
    for pair in pairs:
        if not pair:
            continue
        if " \u203a " in pair:
            cat, sub = pair.split(" \u203a ", 1)
            cat, sub = cat.strip(), sub.strip()
            if sub:
                mask = mask | ((y["category"] == cat) & (y["subcategory"] == sub))
            else:
                mask = mask | (y["category"] == cat)
        else:
            bare = pair.strip()
            if bare in CATEGORIES:
                mask = mask | (y["category"] == bare)
            elif bare in ALL_SUBCATS:
                mask = mask | (y["subcategory"].fillna("") == bare)
    return float(y[mask]["amount_eur"].sum())


def travel_spent_in_range(expenses_df, pairs, start, end) -> float:
    """#14 windowed twin of travel_spent: sum over [start, end] inclusive."""
    if expenses_df is None or expenses_df.empty or not pairs:
        return 0.0
    win = expenses_df[(expenses_df["date"].dt.date >= start)
                      & (expenses_df["date"].dt.date <= end)]
    if win.empty:
        return 0.0
    mask = pd.Series(False, index=win.index)
    for pair in pairs:
        if not pair:
            continue
        if " › " in pair:
            cat, sub = pair.split(" › ", 1)
            cat, sub = cat.strip(), sub.strip()
            if sub and sub != "(all)":
                mask = mask | ((win["category"] == cat) & (win["subcategory"] == sub))
            else:
                mask = mask | (win["category"] == cat)
        else:
            bare = pair.strip()
            if bare in CATEGORIES:
                mask = mask | (win["category"] == bare)
            elif bare in ALL_SUBCATS:
                mask = mask | (win["subcategory"].fillna("") == bare)
    return float(win[mask]["amount_eur"].sum())

QUADRANT_COLORS = _ui_styles.QUADRANT_COLORS  # keep canonical palette
CHART_COLORS = _ui_styles.CHART_COLORS

def classify_quadrant(work_hours: float, usage_hours: float,
                      median_work: float, median_usage: float) -> str:
    high_usage = usage_hours > median_usage
    high_work  = work_hours > median_work
    if high_usage and not high_work:
        return "Quick wins"
    if high_usage and high_work:
        return "Plan & save"
    if not high_usage and not high_work:
        return "Maybe later"
    return "Reconsider"

def validate_grouped_order(order: dict, expected: dict):
    if not isinstance(order, dict) or set(order) != set(expected):
        return None
    wanted = [str(item_id) for ids in expected.values() for item_id in ids]
    received = [str(item_id) for category in expected for item_id in order.get(category, [])]
    if len(received) != len(wanted) or len(set(received)) != len(received):
        return None
    if set(received) != set(wanted):
        return None
    return {str(category): [str(item_id) for item_id in order[category]]
            for category in expected}

def draggable_card_board(groups: dict, key: str):
    """Compat wrapper — delegates to ui.board.grouped_board (canonical).

    A3: the old copy kept its own CCv2 registration in a module global;
    when the active runtime's registry lacked it at mount time every call
    crashed with "Component not registered". The canonical board registers
    per render, so delegating is correct everywhere.
    """
    from ui.board import grouped_board
    res = grouped_board(key, groups)
    order = dict(getattr(res, "item_order", None) or {})
    act = getattr(res, "action", None)
    action = ({"id": str(act.id), "action": str(act.action),
               "value": act.value} if act is not None else None)
    return order, action

_XL_UNSAFE_PREFIXES = ("=", "+", "@")

def _xl_safe(v):
    if isinstance(v, str) and v.startswith(_XL_UNSAFE_PREFIXES):
        return "'" + v
    if isinstance(v, str) and len(v) > 1 and v[0] == "-" and v[1].isdigit():
        return "'" + v
    return v

def to_excel(df) -> bytes:
    buf = _io.BytesIO()
    safe = df.copy()
    from pandas.api import types as pd_types
    for col in safe.columns:
        if pd_types.is_string_dtype(safe[col]) or safe[col].dtype == object:
            safe[col] = safe[col].astype(object).map(_xl_safe)
    safe.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()

def progress_ratio(value, target) -> float:
    """value/target clamped to [0.0, 1.0] — safe for st.progress.

    st.progress rejects values outside [0.0, 1.0]; overdrawn/negative
    balances (intentionally unclamped in the ledger) must render as 0.
    Returns 0.0 when the target is missing or not positive.
    """
    try:
        v = float(value)
        t = float(target)
    except (TypeError, ValueError):
        return 0.0
    if t <= 0:
        return 0.0
    return min(max(v / t, 0.0), 1.0)


def safe_error(msg: str):
    st.error(msg, icon=":material/error:")

def safe_warning(msg: str):
    st.warning(msg, icon=":material/warning:")

def help_expander(title: str, content: str):
    with st.expander(title, icon=":material/help:"):
        st.markdown(content)

def inject_mobile_css():
    # research.md U1c/U6: dead .kpi*/.pw/.pb rules removed; full-width mobile
    # buttons now apply only where they read as primary actions (sidebar,
    # forms) so small icon buttons keep their natural size.
    st.markdown("""
    <style>
    .pos { color: #00B050; } .neg { color: #E94560; } .neu { color: #0F3460; }
    .badge { display: inline-block; background: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.2); border-radius: 20px; padding: 4px 10px; font-size: 12px; margin: 2px; }
    @media (max-width: 768px) {
        section[data-testid="stSidebar"] .stButton > button,
        div[data-testid="stForm"] .stButton > button { width: 100%; font-size: 16px; padding: 12px; border-radius: 10px; }
        div[data-testid="column"]:has([data-testid="stMetric"]) { min-width: 100% !important; }
        .stDataFrame { font-size: 13px; }
        h1 { font-size: 1.6rem !important; } h2 { font-size: 1.3rem !important; }
    }
    div[data-testid="stForm"] { border: 1px solid rgba(128,128,128,0.15); border-radius: 12px; padding: 16px; }
    section[data-testid="stSidebar"] { min-width: 240px; }
    </style>
    """, unsafe_allow_html=True)

def get_server_port() -> int:
    try:
        return int(st.get_option("server.port"))
    except Exception:
        pass
    try:
        return int(_os.environ.get("STREAMLIT_SERVER_PORT", APP_PORT))
    except Exception:
        return APP_PORT

@st.cache_data(ttl=60, show_spinner=False)
def get_lan_urls(port: int):
    ips = set()
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    hostname = None
    try:
        hostname = _socket.gethostname()
        for ip in _socket.gethostbyname_ex(hostname)[2]:
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
    import io as _io2, qrcode
    img = qrcode.make(url)
    buf = _io2.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
