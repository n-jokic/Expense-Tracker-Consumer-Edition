"""
Regression tests for exchange-rate validation (utils.py): zero, negative, and
non-finite rates must never be accepted — and never silently become 1:1.
"""

import math

import pytest

from utils import get_rates, to_eur, to_display, DEFAULT_RATES


def test_get_rates_ignores_invalid_stored_values():
    settings = {"currency_rates": {
        "RSD": 0.0,        # zero -> fall back to default
        "USD": -1.5,       # negative -> fall back to default
        "GBP": float("nan"),  # NaN -> fall back to default
        "HUF": float("inf"),  # infinity -> fall back to default
        "CHF": 0.94,       # valid -> accepted
        "PLN": "junk",     # unparseable -> fall back to default
    }}
    rates = get_rates(settings)
    assert rates["CHF"] == 0.94
    assert rates["RSD"] == DEFAULT_RATES["RSD"]
    assert rates["USD"] == DEFAULT_RATES["USD"]
    assert rates["GBP"] == DEFAULT_RATES["GBP"]
    assert rates["HUF"] == DEFAULT_RATES["HUF"]
    assert rates["PLN"] == DEFAULT_RATES["PLN"]
    assert rates["EUR"] == 1.0


def test_get_rates_ignores_invalid_legacy_rate():
    assert get_rates({"exchange_rate": 0.0})["RSD"] == DEFAULT_RATES["RSD"]
    assert get_rates({"exchange_rate": -3})["RSD"] == DEFAULT_RATES["RSD"]
    assert get_rates({"exchange_rate": 118.5})["RSD"] == 118.5


def test_to_eur_rejects_invalid_rates_instead_of_1_to_1():
    with pytest.raises(ValueError):
        to_eur(1000, "RSD", {"RSD": 0.0})
    with pytest.raises(ValueError):
        to_eur(1000, "RSD", {"RSD": -1.0})
    with pytest.raises(ValueError):
        to_eur(1000, "RSD", {"RSD": float("nan")})


def test_to_display_rejects_invalid_rates():
    with pytest.raises(ValueError):
        to_display(10, "USD", {"USD": 0.0})


def test_missing_currency_still_defaults_to_1_to_1():
    # Unknown currencies were always assumed 1:1 — that behavior is kept,
    # but only for MISSING keys, never for explicitly invalid values.
    assert to_eur(50, "XYZ", {"EUR": 1.0}) == 50.0
    assert to_display(50, "XYZ", {"EUR": 1.0}) == 50.0


def test_sanitized_rates_convert_cleanly():
    rates = get_rates({"currency_rates": {"RSD": 117.0, "USD": 0.0}})
    assert to_eur(1170, "RSD", rates) == pytest.approx(10.0)
    assert to_display(10, "RSD", rates) == pytest.approx(1170.0)
    assert math.isfinite(to_eur(1, "USD", rates))
