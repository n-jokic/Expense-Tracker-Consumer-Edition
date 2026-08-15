"""
Tests for the currency engine (utils.py).
"""

import pytest

from utils import (
    DEFAULT_RATES, SUPPORTED_CURRENCIES,
    get_rates, to_eur, to_display, to_display_row, fmt, fmt_row,
)


def test_default_rates_cover_all_supported_currencies():
    assert set(SUPPORTED_CURRENCIES.keys()) <= set(DEFAULT_RATES.keys())
    assert DEFAULT_RATES["EUR"] == 1.0


def test_get_rates_seeds_legacy_exchange_rate():
    rates = get_rates({"exchange_rate": 120.0, "currency_rates": None})
    assert rates["RSD"] == 120.0
    assert rates["EUR"] == 1.0


def test_get_rates_prefers_stored_table_and_fills_gaps():
    rates = get_rates({
        "exchange_rate": 120.0,
        "currency_rates": {"RSD": 118.0, "USD": 1.1},
    })
    assert rates["RSD"] == 118.0
    assert rates["USD"] == 1.1
    # untouched currencies fall back to defaults
    assert rates["GBP"] == DEFAULT_RATES["GBP"]


def test_to_eur_converts_with_rate_and_keeps_eur():
    rates = get_rates({"currency_rates": {"RSD": 117.0, "USD": 1.08}})
    assert to_eur(1170, "RSD", rates) == pytest.approx(10.0)
    assert to_eur(10.8, "USD", rates) == pytest.approx(10.0)
    assert to_eur(10, "EUR", rates) == pytest.approx(10.0)


def test_to_display_converts_aggregates():
    rates = get_rates({"currency_rates": {"RSD": 117.0}})
    assert to_display(10, "RSD", rates) == pytest.approx(1170.0)
    assert to_display(10, "EUR", rates) == pytest.approx(10.0)


def test_to_display_row_uses_original_amount_when_currency_matches():
    """History must not mutate when rates change later (bug 4)."""
    rates = get_rates({"currency_rates": {"RSD": 117.0}})
    # stored: 1000 RSD originally, snapshot as 8.5470 EUR at the old rate
    shown = to_display_row(8.5470, 1000.0, "RSD", "RSD", rates)
    assert shown == 1000.0
    # even at a wildly different current rate the original wins
    rates2 = get_rates({"currency_rates": {"RSD": 200.0}})
    assert to_display_row(8.5470, 1000.0, "RSD", "RSD", rates2) == 1000.0


def test_to_display_row_converts_when_currencies_differ():
    rates = get_rates({"currency_rates": {"RSD": 117.0, "USD": 1.08}})
    # a USD row shown in RSD converts via EUR
    assert to_display_row(10.0, 10.8, "USD", "RSD", rates) == pytest.approx(1170.0)


def test_fmt_formatting_per_currency():
    rates = get_rates({"currency_rates": {"RSD": 117.0, "USD": 1.08}})
    assert fmt(10, "EUR", rates) == "€10.00"
    assert fmt(10, "USD", rates) == "$10.80"
    assert fmt(10, "RSD", rates) == "1,170 din"


def test_fmt_row_uses_original_when_matching():
    rates = get_rates({"currency_rates": {"USD": 1.08}})
    assert fmt_row(10.0, 10.8, "USD", "USD", rates) == "$10.80"
