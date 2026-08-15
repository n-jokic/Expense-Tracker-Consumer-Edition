"""
Portfolio page: track stocks/ETF holdings with free market prices
(Yahoo Finance primary, Stooq fallback). Prices refresh on login when
older than a day; last known prices survive network failures.
"""

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import queries as q
from db import add_holding, update_holding, delete_holding
from finance import portfolio_metrics
from market_data import refresh_prices_if_due, fetch_price, _fetch_cached
from utils import (
    SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET, CHART_COLORS,
    fmt, fmt_row, to_display, to_eur, get_currency_symbol, get_rates,
    help_expander,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()

st.title("📈 Portfolio")
st.caption("Track stocks & ETFs — free daily prices, refreshed on login (or manually).")
help_expander("How portfolio tracking works",
              "Add a holding with its symbol, quantity and what you paid. Prices come from "
              "free public market data (Yahoo Finance with a Stooq fallback) once per day "
              "on login. If the network is down, the last known prices are kept. Value "
              "snapshots are stored daily so the value-over-time chart grows by itself.")

# ── Refresh ───────────────────────────────────────────────────────────────────
df_hold = q.holdings(user_id)
if not df_hold.empty:
    rc1, rc2 = st.columns([3, 1.2])
    with rc1:
        last_dates = [d.strftime("%d %b %Y") for d in df_hold["last_price_date"].dropna()] \
            if "last_price_date" in df_hold.columns else []
        st.caption("Prices last updated: " + (", ".join(sorted(set(last_dates))) if last_dates else "never"))
    with rc2:
        if st.button("🔄 Refresh prices", width="stretch", key="pf_refresh"):
            with st.spinner("Fetching prices..."):
                n, ok = refresh_prices_if_due(user_id, force=True)
            if ok:
                q.bump_db_version()
                st.success(f"✅ Updated {n} holding(s)")
                st.rerun()
            else:
                st.error("😕 Couldn't fetch prices — keeping the last known values.")

# ── Add / edit holdings ───────────────────────────────────────────────────────
with st.form("hold_form", clear_on_submit=False):
    st.markdown("**➕ Add holding**")
    c1, c2 = st.columns(2)
    with c1:
        h_symbol = st.text_input("Symbol", placeholder="e.g. AAPL, VWCE.DE, MSFT")
        h_name   = st.text_input("Name (optional)", placeholder="e.g. Apple Inc.")
        h_qty    = st.number_input("Quantity", min_value=0.0, step=0.01, format="%.4f")
    with c2:
        h_cur    = st.selectbox("Currency", list(SUPPORTED_CURRENCIES.keys()), key="hold_cur")
        h_cost   = st.number_input(f"Total invested ({get_currency_symbol(h_cur)})",
                                   min_value=0.0, max_value=MAX_SAVINGS_TARGET,
                                   step=100.0, format="%.2f")
        st.caption("Include fees — this is your cost basis.")
    if st.form_submit_button("💾 Save holding", type="primary"):
        if h_symbol.strip():
            sym = h_symbol.strip().upper()
            cost_eur = to_eur(h_cost, h_cur, rates)
            # try to fetch a starting price right away
            price = _fetch_cached(sym)
            import datetime as _dt
            add_holding(user_id, {
                "symbol": sym, "name": h_name.strip(),
                "quantity": float(h_qty), "currency": h_cur,
                "cost_total": float(h_cost), "cost_eur": cost_eur,
                "last_price": price if price else 0.0,
                "last_price_date": _dt.datetime.now(_dt.timezone.utc) if price else None,
            })
            q.bump_db_version()
            st.success(f"✅ **{sym}** added" + (f" (price {price:,.2f})" if price else " (price will be fetched on refresh)"))
            st.rerun()
        else:
            st.error("Please enter a symbol.")

# ── Portfolio view ────────────────────────────────────────────────────────────
df_hold = q.holdings(user_id)
if df_hold.empty:
    st.info("No holdings yet — add one above 👆")
    st.stop()

# Compute per-holding EUR values using current rates
rows = []
for _, h in df_hold.iterrows():
    cur = str(h["currency"] or "EUR")
    price_eur = float(h["last_price"] or 0.0)
    if cur != "EUR" and price_eur > 0:
        price_eur = price_eur / (rates.get(cur, 1.0) or 1.0)
    value_eur = float(h["quantity"] or 0.0) * price_eur
    rows.append({
        "id": str(h["id"]), "symbol": str(h["symbol"]), "name": str(h.get("name") or ""),
        "quantity": float(h["quantity"] or 0.0), "currency": cur,
        "last_price": float(h["last_price"] or 0.0), "price_eur": price_eur,
        "value_eur": value_eur, "cost_eur": float(h["cost_eur"] or 0.0),
        "last_price_date": h.get("last_price_date"),
    })
view = pd.DataFrame(rows)
m = portfolio_metrics(view.rename(columns={"price_eur": "last_price_eur"}).to_dict("records"))

st.divider()
k1, k2, k3, k4 = st.columns(4)
for col, lbl, val, cls in [
    (k1, "Market value",  m["value"],    "pos"),
    (k2, "Invested",      m["invested"], "neu"),
    (k3, "Gain / loss",   m["gain"],     "pos" if m["gain"] >= 0 else "neg"),
    (k4, "Gain %",        None,          "pos" if m["gain"] >= 0 else "neg"),
]:
    with col:
        v = f"{m['gain_pct']:+.1f}%" if lbl == "Gain %" else fmt(val, DC, rates)
        st.markdown(
            f'<div class="kpi"><div class="kpi-lbl">{lbl}</div>'
            f'<div class="kpi-val {cls}">{v}</div></div>', unsafe_allow_html=True)

# Allocation pie
r1, r2 = st.columns(2)
with r1:
    st.subheader("Allocation")
    alloc = view[view["value_eur"] > 0]
    if not alloc.empty:
        fig = px.pie(alloc, values="value_eur", names="symbol", hole=0.45,
                     color_discrete_sequence=CHART_COLORS)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=0,b=0,l=0,r=0),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No live prices yet — refresh above.")

with r2:
    st.subheader("Value over time")
    prices = q.holding_prices(user_id)
    if not prices.empty:
        vhist = []
        for _, p in prices.iterrows():
            hrow = view[view["symbol"] == p["symbol"]]
            if hrow.empty:
                continue
            cur = str(hrow.iloc[0]["currency"])
            qty = float(hrow.iloc[0]["quantity"])
            price_eur = float(p["price"])
            if cur != "EUR" and price_eur > 0:
                price_eur = price_eur / (rates.get(cur, 1.0) or 1.0)
            vhist.append({"date": p["date"], "symbol": p["symbol"],
                          "value_eur": qty * price_eur})
        if vhist:
            vdf = pd.DataFrame(vhist)
            vsum = vdf.groupby("date")["value_eur"].sum().reset_index()
            vsum["d"] = vsum["value_eur"].apply(lambda x: to_display(x, DC, rates))
            figv = px.area(vsum, x="date", y="d",
                           labels={"d": f"Value ({get_currency_symbol(DC)})", "date": "Date"})
            figv.update_layout(plot_bgcolor="rgba(0,0,0,0)",
                               paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(figv, width="stretch")
        else:
            st.info("Snapshots start accumulating after the first refresh.")
    else:
        st.info("Snapshots start accumulating after the first refresh.")

# Holdings table
st.subheader("Holdings")
tbl = []
for _, r in view.iterrows():
    gain = r["value_eur"] - r["cost_eur"]
    tbl.append({
        "Symbol": r["symbol"],
        "Name": r["name"] or r["symbol"],
        "Qty": f"{r['quantity']:,.4f}",
        "Price": f"{r['last_price']:,.2f} {get_currency_symbol(r['currency'])}" if r["last_price"] else "—",
        "Value": fmt(r["value_eur"], DC, rates),
        "Invested": fmt(r["cost_eur"], DC, rates),
        "Gain": fmt(gain, DC, rates),
        "Gain %": f"{((gain / r['cost_eur']) * 100):+.1f}%" if r["cost_eur"] > 0 else "—",
    })
st.dataframe(pd.DataFrame(tbl), hide_index=True)

# Manage holdings
with st.expander("✏️ Manage holdings"):
    for _, r in view.iterrows():
        mc1, mc2, mc3 = st.columns([3, 1.4, 1])
        with mc1:
            st.write(f"**{r['symbol']}** — {r['name'] or ''} · qty {r['quantity']:,.4f} "
                     f"· invested {fmt(r['cost_eur'], DC, rates)}")
        with mc2:
            nq = st.number_input("Quantity", value=float(r["quantity"]), min_value=0.0,
                                 step=0.01, format="%.4f",
                                 key=f"hold_q_{r['id']}", label_visibility="collapsed")
        with mc3:
            if st.button("💾", key=f"hold_s_{r['id']}", width="stretch",
                         help="Save quantity"):
                update_holding(user_id, r["id"], {"quantity": float(nq)})
                q.bump_db_version()
                st.rerun()
        st.caption("")
        if st.button("🗑️ Remove holding", key=f"hold_d_{r['id']}", type="secondary"):
            delete_holding(user_id, r["id"])
            q.bump_db_version()
            st.rerun()
        st.divider()
