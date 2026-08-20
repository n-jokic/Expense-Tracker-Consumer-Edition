"""
domain/money.py — canonical currency engine (Streamlit-free).

Single source of truth for SUPPORTED_CURRENCIES / DEFAULT_RATES /
get_currency_symbol / get_rates / to_eur / to_display / to_display_row and the
related constants. R6 will keep utils.py as a thin shim re-exporting from here.
"""

from __future__ import annotations

import math
import os

SUPPORTED_CURRENCIES: dict[str, str] = {
    "EUR": "\u20ac",  "RSD": "din", "USD": "$",   "GBP": "\u00a3",
    "CHF": "CHF","HRK": "kn",  "BAM": "KM",  "HUF": "Ft",
    "RON": "lei","BGN": "\u043b\u0432",  "PLN": "z\u0142",  "CZK": "K\u010d",
}

DEFAULT_RATES: dict[str, float] = {
    "EUR": 1.0, "RSD": 117.0, "USD": 1.08, "GBP": 0.85, "CHF": 0.94,
    "HRK": 7.5345, "BAM": 1.9558, "HUF": 400.0, "RON": 5.0, "BGN": 1.9558,
    "PLN": 4.3, "CZK": 25.0,
}

NEAR_LIMIT_THRESHOLD  = 0.85
SAVINGS_TARGET_PCT    = 15
SAVINGS_GOAL_PCT      = 20
BACKUP_RETENTION_DAYS = 30
APP_PORT              = 8501
TLS_ENABLED           = os.environ.get("EXPENSE_TRACKER_TLS") == "1"
MAX_AMOUNT            = 1_000_000.0
MAX_SAVINGS_TARGET    = 10_000_000.0


def get_currency_symbol(currency: str) -> str:
    return SUPPORTED_CURRENCIES.get(currency, currency)


def _valid_rate(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def get_rates(settings: dict) -> dict:
    rates = dict(DEFAULT_RATES)
    stored = settings.get("currency_rates")
    if isinstance(stored, dict) and stored:
        for k, v in stored.items():
            f = _valid_rate(v)
            if f is not None:
                rates[k] = f
    else:
        legacy = settings.get("exchange_rate")
        if legacy:
            f = _valid_rate(legacy)
            if f is not None:
                rates["RSD"] = f
    rates["EUR"] = 1.0
    return rates


def to_eur(amount: float, currency: str, rates: dict) -> float:
    if currency == "EUR":
        return round(float(amount), 4)
    r = _valid_rate(rates.get(currency, 1.0))
    if r is None:
        raise ValueError(f"Invalid exchange rate for {currency}: "
                         f"{rates.get(currency)!r} \u2014 must be > 0 and finite")
    return round(float(amount) / r, 4)


def to_display(eur: float, currency: str, rates: dict) -> float:
    if currency == "EUR":
        return float(eur)
    r = _valid_rate(rates.get(currency, 1.0))
    if r is None:
        raise ValueError(f"Invalid exchange rate for {currency}: "
                         f"{rates.get(currency)!r} \u2014 must be > 0 and finite")
    return float(eur) * r


def to_display_row(eur: float, orig_amount: float, orig_currency: str,
                   currency: str, rates: dict) -> float:
    if orig_currency == currency:
        return float(orig_amount)
    return to_display(eur, currency, rates)
