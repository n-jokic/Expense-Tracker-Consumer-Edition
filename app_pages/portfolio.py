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
from market_data import refresh_prices_if_due, _fetch_cached
from utils import (
    SUPPORTED_CURRENCIES, MAX_SAVINGS_TARGET, CHART_COLORS,
    fmt, to_display, to_eur, get_currency_symbol,
    help_expander,
)

user_id  = st.session_state.user_id
DC       = st.session_state.dc
rates    = st.session_state.rates
settings = st.session_state.settings
today    = date.today()

st.title(":material/trending_up: Portfolio")
st.caption("Track stocks & ETFs — free daily prices, refreshed on login (or manually).")
help_expander("How portfolio tracking works",
              "Add a holding with its symbol, quantity and what you paid. Prices come from "
              "free public market data (Yahoo Finance with a Stooq fallback) once per day "
              "on login. If the network is down, the last known prices are kept. Value "
              "snapshots are stored daily so the value-over-time chart grows by itself.")

if (msg := st.session_state.pop("pf_flash", None)):
    st.success(msg, icon=":material/check_circle:")

# ── Refresh ───────────────────────────────────────────────────────────────────
df_hold = q.holdings(user_id)
if not df_hold.empty:
    rc1, rc2 = st.columns([3, 1.2])
    with rc1:
        last_dates = [d.strftime("%d %b %Y") for d in df_hold["last_price_date"].dropna()] \
            if "last_price_date" in df_hold.columns else []
        st.caption("Prices last updated: " + (", ".join(sorted(set(last_dates))) if last_dates else "never"))
    with rc2:
        if st.button("Refresh prices", icon=":material/refresh:", width="stretch", key="pf_refresh"):
            with st.spinner("Fetching prices..."):
                n, ok = refresh_prices_if_due(user_id, force=True)
            if ok:
                q.bump_db_version()
                st.success(f"✅ Updated {n} holding(s)")
                st.rerun()
            else:
                st.error("😕 Couldn't fetch prices — keeping the last known values.")

# ── Add / edit holdings ───────────────────────────────────────────────────────
with st.form("hold_form", clear_on_submit=True):
    st.markdown("**:material/add: Add holding**")
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
    if st.form_submit_button("Save holding", type="primary", width="stretch", icon=":material/save:"):
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
            st.session_state["pf_flash"] = (
                f"**{sym}** added"
                + (f" (price {price:,.2f})" if price else " (price will be fetched on refresh)")
            )
            st.rerun()
        else:
            st.error("Please enter a symbol.")

# ── Portfolio view ────────────────────────────────────────────────────────────
df_hold = q.holdings(user_id)
if df_hold.empty:
    st.info("No holdings yet — add one above")
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

with st.container(horizontal=True):
    st.metric("Market value", fmt(m["value"], DC, rates), border=True)
    st.metric("Invested", fmt(m["invested"], DC, rates), border=True)
    st.metric("Gain / loss", fmt(m["gain"], DC, rates), border=True)
    gain_pct_txt = (f"{m['gain_pct']:+.1f}%" if m["invested"] > 0 else "—")
    st.metric("Gain %", gain_pct_txt, border=True)

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
        st.caption("No live prices yet — refresh above.")

with r2:
    st.subheader("Value over time")
    prices = q.holding_prices(user_id)
    if not prices.empty:
        vhist = []
        estimated = False
        for _, p in prices.iterrows():
            hrow = view[view["symbol"] == p["symbol"]]
            if hrow.empty:
                continue
            pv = p.get("value_eur")
            if pv is not None and not pd.isna(pv) and float(pv) > 0:
                value = float(pv)  # exact snapshot value (qty/rate at the time)
            else:
                # Legacy snapshot rows: estimate from today's quantity/rates.
                cur = str(hrow.iloc[0]["currency"])
                qty = float(hrow.iloc[0]["quantity"])
                price_eur = float(p["price"])
                if cur != "EUR" and price_eur > 0:
                    price_eur = price_eur / (rates.get(cur, 1.0) or 1.0)
                value = qty * price_eur
                estimated = True
            vhist.append({"date": p["date"], "symbol": p["symbol"],
                          "value_eur": value})
        if vhist:
            vdf = pd.DataFrame(vhist)
            vsum = vdf.groupby("date")["value_eur"].sum().reset_index()
            vsum["d"] = vsum["value_eur"].apply(lambda x: to_display(x, DC, rates))
            figv = px.area(vsum, x="date", y="d",
                           labels={"d": f"Value ({get_currency_symbol(DC)})", "date": "Date"})
            figv.update_layout(plot_bgcolor="rgba(0,0,0,0)",
                               paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
            st.plotly_chart(figv, width="stretch")
            if estimated:
                st.caption("≈ Includes days estimated from today's quantity; "
                           "new snapshots record exact values.")
        else:
            st.info("Snapshots start accumulating after the first refresh.")
    else:
        st.info("Snapshots start accumulating after the first refresh.")

# Holdings table
st.subheader("Holdings")
csym = get_currency_symbol(DC)
tbl = []
for _, r in view.iterrows():
    gain = r["value_eur"] - r["cost_eur"]
    gain_pct = (gain / r["cost_eur"]) * 100 if r["cost_eur"] > 0 else None
    tbl.append({
        "Symbol": r["symbol"],
        "Name": r["name"] or r["symbol"],
        "Qty": f"{r['quantity']:,.4f}",
        "Price": r["last_price"] or None,
        "Value": to_display(r["value_eur"], DC, rates),
        "Invested": fmt(r["cost_eur"], DC, rates),
        "Gain": to_display(gain, DC, rates),
        "Gain %": gain_pct,
    })
st.dataframe(
    pd.DataFrame(tbl),
    column_config={
        "Price": st.column_config.NumberColumn("Price", format="%.2f"),
        "Value": st.column_config.NumberColumn("Value", format=f"{csym}%.2f"),
        "Gain": st.column_config.NumberColumn("Gain", format=f"{csym}%.2f"),
        "Gain %": st.column_config.NumberColumn("Gain %", format="%+.1f%%"),
    },
    hide_index=True,
)


@st.dialog("Remove holding?")
def remove_holding_dialog(uid, holding_id, symbol, quantity):
    """Confirm removing a holding (and its saved price history) from the portfolio."""
    st.write(f"Remove **{symbol}** from your holdings?")
    st.caption(f"Quantity: {quantity:,.4f} · This also removes its saved price history.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", key=f"hold_cancel_{holding_id}", width="stretch"):
            st.rerun()
    with c2:
        if st.button("Delete holding", key=f"hold_confirm_{holding_id}",
                     type="primary", width="stretch"):
            delete_holding(uid, holding_id)
            q.bump_db_version()
            st.toast(f"Holding **{symbol}** removed.", icon=":material/delete:")
            st.rerun()


# Manage holdings
with st.expander("Manage holdings", icon=":material/edit:"):
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
            if st.button("Save", icon=":material/save:", key=f"hold_s_{r['id']}", width="stretch"):
                update_holding(user_id, r["id"], {"quantity": float(nq)})
                q.bump_db_version()
                st.rerun()
        if st.button("Remove holding", icon=":material/delete:", key=f"hold_d_{r['id']}",
                     type="secondary", width="stretch"):
            remove_holding_dialog(user_id, r["id"], r["symbol"], float(r["quantity"]))
