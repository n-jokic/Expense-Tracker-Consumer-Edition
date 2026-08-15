"""
rates.py — Live exchange-rate refresh from free public APIs.

Rates are always stored in user_settings.currency_rates together with a
rates_updated_at timestamp (last known value survives any network failure).
Refresh happens on login when the stored rates are older than
RATES_MAX_AGE_DAYS (or were never fetched); failures are cached for 30 min
per process so a broken network doesn't slow down every rerun.
"""

import json
import logging
import urllib.request
from datetime import datetime, date, timezone

import streamlit as st

import queries as q
from utils import SUPPORTED_CURRENCIES

logger = logging.getLogger(__name__)

RATES_MAX_AGE_DAYS = 3
FRANKFURTER_URL = "https://api.frankfurter.app/latest?from=EUR"
OPEN_ER_URL     = "https://open.er-api.com/v6/latest/EUR"


def _open(url: str, timeout: int):
    req = urllib.request.Request(
        url, headers={"User-Agent": "ExpenseTracker/1.0 (+local personal app)"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_live_rates(timeout: int = 3) -> dict | None:
    """Fetch current EUR-based rates for the app's supported currencies.

    Frankfurter (ECB data) is the primary source; open.er-api.com covers the
    currencies ECB doesn't publish (RSD, BAM, ...). Returns {code: rate} or
    None when nothing usable was retrieved.
    """
    rates = {}
    try:
        with _open(FRANKFURTER_URL, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        rates.update(data.get("rates", {}))
    except Exception as e:
        logger.warning("Frankfurter fetch failed: %s", e)

    missing = [c for c in SUPPORTED_CURRENCIES if c != "EUR"
               and not (isinstance(rates.get(c), (int, float)) and rates.get(c) > 0)]
    if missing:
        try:
            with _open(OPEN_ER_URL, timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("result") == "success":
                rates.update(data.get("rates", {}))
        except Exception as e:
            logger.warning("open.er-api fetch failed: %s", e)

    out = {"EUR": 1.0}
    for code in SUPPORTED_CURRENCIES:
        if code == "EUR":
            continue
        v = rates.get(code)
        if isinstance(v, (int, float)) and v > 0:
            out[code] = float(v)
    return out if len(out) > 1 else None


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_cached():
    """Cache fetch results for 30 min — including failures (None)."""
    return fetch_live_rates()


def rates_are_stale(settings: dict) -> bool:
    """True when rates were never fetched or are RATES_MAX_AGE_DAYS old."""
    updated = settings.get("rates_updated_at")
    if updated is None:
        return True
    if isinstance(updated, datetime):
        d = updated.date()
    elif isinstance(updated, date):
        d = updated
    else:
        try:
            d = datetime.fromisoformat(str(updated)).date()
        except Exception:
            return True
    return (date.today() - d).days >= RATES_MAX_AGE_DAYS


def refresh_rates_if_due(user_id: int, settings: dict,
                         force: bool = False) -> tuple[dict, bool]:
    """Refresh stored rates when due (or forced). Returns (settings, updated).

    On network failure the last known rates are kept untouched.
    """
    if not (force or rates_are_stale(settings)):
        return settings, False
    if force:
        _fetch_cached.clear()
    fresh = _fetch_cached()
    if not fresh:
        return settings, False

    current = dict(settings.get("currency_rates") or {})
    current.update(fresh)
    new_settings = q.save_settings(user_id, {
        "currency_rates": current,
        "rates_updated_at": datetime.now(timezone.utc),
    })
    logger.info("Exchange rates refreshed for user %s: %s", user_id,
                ", ".join(f"{c}={current[c]:.4g}" for c in sorted(current)))
    return new_settings, True
