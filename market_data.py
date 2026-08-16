"""
market_data.py — Free key-less market price fetching for portfolio holdings.

Yahoo Finance's chart endpoint is primary; Stooq's CSV endpoint is the
fallback. Last known prices persist in the DB (holdings.last_price +
last_price_date), so any network failure keeps the previous value. Refresh
happens on login when prices are older than PRICES_MAX_AGE_DAYS (default 1),
and failures are cached for 30 min per process.
"""

import csv
import io
import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from db import get_holdings, update_holding, add_holding_price

logger = logging.getLogger(__name__)

_refresh_lock = threading.Lock()   # prevents overlapping background refreshes

PRICES_MAX_AGE_DAYS = 1
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
STOOQ_URL = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"


def _open(url: str, timeout: int):
    req = urllib.request.Request(
        url, headers={"User-Agent": "ExpenseTracker/1.0 (+local personal app)"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_price_yahoo(symbol: str, timeout: int = 4) -> float | None:
    try:
        with _open(YAHOO_URL.format(sym=symbol), timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is None:
            closes = data.get("chart", {}).get("result", [{}])[0] \
                         .get("indicators", {}).get("quote", [{}])[0].get("close", [])
            price = next((c for c in reversed(closes) if c), None)
        return float(price) if price and price > 0 else None
    except Exception as e:
        logger.warning("Yahoo fetch failed for %s: %s", symbol, e)
        return None


def fetch_price_stooq(symbol: str, timeout: int = 4) -> float | None:
    try:
        with _open(STOOQ_URL.format(sym=symbol), timeout) as r:
            text = r.read().decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            return None
        close = rows[0].get("Close")
        return float(close) if close not in (None, "", "N/D") else None
    except Exception as e:
        logger.warning("Stooq fetch failed for %s: %s", symbol, e)
        return None


def fetch_price(symbol: str, timeout: int = 4) -> float | None:
    """Best-effort price for one symbol: Yahoo first, Stooq fallback."""
    price = fetch_price_yahoo(symbol, timeout)
    if price is None:
        price = fetch_price_stooq(symbol, timeout)
    return price


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_cached(symbol: str):
    """Per-symbol cache including failures (None) for 30 min."""
    return fetch_price(symbol)


def prices_are_stale(holdings_df: pd.DataFrame) -> bool:
    """True when any holding's price is missing or older than the max age.

    All comparisons happen in UTC: refresh timestamps are written in UTC and
    the SQLite column reads back timezone-naive, so comparing against local
    date.today() would drift by the local offset around midnight.
    """
    if holdings_df is None or holdings_df.empty:
        return False
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    for _, h in holdings_df.iterrows():
        d = h.get("last_price_date")
        if d is None or pd.isna(d):
            return True
        try:
            ts = pd.Timestamp(d)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            if (now_utc - ts).days >= PRICES_MAX_AGE_DAYS:
                return True
        except Exception:
            return True
    return False


def refresh_prices_if_due(user_id: int, force: bool = False,
                          cached: bool = True) -> tuple[int, bool]:
    """Refresh all holdings' prices when stale (or forced).

    Updates holdings.last_price/last_price_date and appends a daily
    holding_prices snapshot. Returns (updated_count, success).
    """
    holdings = get_holdings(user_id)
    if holdings.empty:
        return 0, False
    if not (force or prices_are_stale(holdings)):
        return 0, False

    get = _fetch_cached if cached else fetch_price
    from db import get_settings as _db_get_settings
    from utils import get_rates
    settings = _db_get_settings(user_id) or {}
    rates = get_rates(settings)
    updated = 0
    for _, h in holdings.iterrows():
        symbol = str(h["symbol"])
        price = get(symbol)
        if price is None:
            continue
        update_holding(user_id, str(h["id"]), {
            "last_price": price,
            "last_price_date": datetime.now(timezone.utc),
        })
        # Record quantity + rate so the snapshot's EUR value stays exact even
        # if the user later edits the quantity or the rates change.
        cur = str(h.get("currency") or "EUR").upper()
        qty = float(h.get("quantity") or 0.0)
        rate = float(rates.get(cur, 1.0) or 1.0)
        add_holding_price(str(h["id"]), price, quantity=qty, rate=rate)
        updated += 1
    return updated, updated > 0


def maybe_refresh_in_background(user_id: int):
    """Kick off a daily price refresh in a daemon thread (never blocks the
    UI). A process-wide lock prevents overlapping refreshes; new prices are
    picked up by the cached readers on their next TTL expiry (2 min)."""
    try:
        holdings = get_holdings(user_id)
        if holdings.empty or not prices_are_stale(holdings):
            return
    except Exception:
        return
    if not _refresh_lock.acquire(blocking=False):
        return

    def _worker():
        try:
            refresh_prices_if_due(user_id, force=False, cached=False)
        finally:
            _refresh_lock.release()

    threading.Thread(target=_worker, daemon=True).start()
