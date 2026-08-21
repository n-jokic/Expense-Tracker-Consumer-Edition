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
# ── ui/board + styles (legacy lives in utils.py until extracted boards land)
import ui.styles as _ui_styles  # ensure module loads without circular import
CHART_COLORS = _ui_styles.CHART_COLORS
QUADRANT_COLORS = _ui_styles.QUADRANT_COLORS
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

_CARD_BOARD = None

def draggable_card_board(groups: dict, key: str):
    global _CARD_BOARD
    original = {str(category): [str(card["id"]) for card in cards]
                for category, cards in groups.items()}
    if _CARD_BOARD is None:
        _CARD_BOARD = st.components.v2.component(
            "expense_tracker_draggable_cards",
            html="<div id='board'></div>",
            css="""
                .board{display:grid;gap:1rem}
                .group{border:1px solid var(--st-border-color);border-radius:.5rem;padding:.75rem;background:var(--st-secondary-background-color)}
                .group h3{margin:0 0 .5rem;font-size:1rem}
                .drop{min-height:3rem;display:grid;gap:.5rem}
                .card{display:grid;grid-template-columns:auto 1fr auto;gap:.75rem;align-items:start;padding:.75rem;border:1px solid var(--st-border-color);border-radius:.4rem;background:var(--st-background-color);color:var(--st-text-color)}
                .card:focus{outline:2px solid var(--st-primary-color)}
                .handle{cursor:grab;border:0;background:transparent;color:var(--st-text-color);font-size:1.1rem}
                .meta{color:var(--st-secondary-text-color);font-size:.85rem}
                .amount{font-weight:600;white-space:nowrap}
                .actions{grid-column:2 / -1;display:flex;gap:.4rem;flex-wrap:wrap}
                .actions button,.actions select{font:inherit;color:inherit;background:var(--st-secondary-background-color);border:1px solid var(--st-border-color);border-radius:.25rem;padding:.25rem .5rem}
                @media(max-width:600px){.card{grid-template-columns:auto 1fr}.amount{grid-column:2}.actions{grid-column:1 / -1}}
            """,
            js="""
export default function({data,parentElement,setStateValue,setTriggerValue}) {
 const root=parentElement.querySelector('#board'); root.replaceChildren(); root.className='board';
 let drag=null; const groups=data.groups || {};
 const emit=()=>setStateValue('order',Object.fromEntries([...root.querySelectorAll('.group')].map(g=>[g.dataset.category,[...g.querySelectorAll('.card')].map(c=>c.dataset.id)])));
 const move=(card,delta)=>{const cards=[...card.parentElement.children],i=cards.indexOf(card),to=i+delta;if(to<0||to>=cards.length)return; card.parentElement.insertBefore(card,delta<0?cards[to]:cards[to].nextSibling);emit();card.focus();};
 for(const [category,cards] of Object.entries(groups)){
  const group=document.createElement('section');group.className='group';group.dataset.category=category;const title=document.createElement('h3');title.textContent=category;const drop=document.createElement('div');drop.className='drop';group.append(title,drop);
  drop.ondragover=e=>e.preventDefault();drop.ondrop=e=>{e.preventDefault();if(drag){drop.append(drag);emit();}};
  for(const dataCard of cards){const card=document.createElement('article');card.className='card';card.dataset.id=dataCard.id;card.tabIndex=0;card.draggable=true;card.ondragstart=()=>drag=card;card.ondragend=()=>drag=null;
   card.onkeydown=e=>{if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();move(card,e.key==='ArrowUp'?-1:1);}};
   const handle=document.createElement('button');handle.className='handle';handle.type='button';handle.textContent='\u2195';handle.title='Drag, or Alt+Up / Alt+Down to move';handle.setAttribute('aria-label','Move '+dataCard.title);
   const body=document.createElement('div');const name=document.createElement('strong');name.textContent=dataCard.title;const meta=document.createElement('div');meta.className='meta';meta.textContent=dataCard.details;body.append(name,meta);
   const amount=document.createElement('div');amount.className='amount';amount.textContent=dataCard.amount;const actions=document.createElement('div');actions.className='actions';
   for(const action of dataCard.actions||[]){if(action.type==='select'){const select=document.createElement('select');select.setAttribute('aria-label',action.label);for(const value of action.options){const option=document.createElement('option');option.value=value;option.textContent=value;option.selected=value===action.value;select.append(option);}select.onchange=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:select.value});actions.append(select);}else{const button=document.createElement('button');button.type='button';button.textContent=action.label;button.onclick=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:action.value||null});actions.append(button);}}
   card.append(handle,body,amount,actions);drop.append(card);
  } root.append(group);
 } return ()=>{};
}""",
        )
    result = _CARD_BOARD(data={"groups": groups}, key=key,
                         default={"order": original}, on_order_change=lambda: None)
    order = validate_grouped_order(getattr(result, "order", None), original) or original
    action = getattr(result, "action", None)
    if not isinstance(action, dict) or set(action) != {"id", "action", "value"}:
        action = None
    elif str(action["id"]) not in {item_id for ids in original.values() for item_id in ids}:
        action = None
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

def safe_error(msg: str):
    st.error(msg, icon=":material/error:")

def safe_warning(msg: str):
    st.warning(msg, icon=":material/warning:")

def help_expander(title: str, content: str):
    with st.expander(title, icon=":material/help:"):
        st.markdown(content)

def inject_mobile_css():
    st.markdown("""
    <style>
    .kpi { background: var(--secondary-background-color); border-radius: 14px; padding: 18px 12px; text-align: center; border: 1px solid rgba(128,128,128,0.15); margin-bottom: 6px; transition: box-shadow 0.2s ease, transform 0.15s ease; }
    .kpi:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.10); transform: translateY(-2px); }
    .kpi-val { font-size: 22px; font-weight: 700; margin: 6px 0; }
    .kpi-sub { font-size: 11px; color: #888; margin-top: 3px; }
    .kpi-lbl { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: .7px; }
    .pos { color: #00B050; } .neg { color: #E94560; } .neu { color: #0F3460; }
    .pw { background: #e0e0e0; border-radius: 8px; height: 14px; overflow: hidden; margin: 5px 0; }
    .pb { height: 100%; border-radius: 8px; transition: width 0.4s ease; }
    .badge { display: inline-block; background: var(--secondary-background-color); border: 1px solid rgba(128,128,128,0.2); border-radius: 20px; padding: 4px 10px; font-size: 12px; margin: 2px; }
    @media (max-width: 768px) {
        .stButton > button { width: 100%; font-size: 16px; padding: 12px; border-radius: 10px; }
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
