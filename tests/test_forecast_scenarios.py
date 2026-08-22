"""#17: pure projection helpers — band, fixed/discretionary split, scenarios."""

import pytest

from forecasting import (NAIVE_BAND_PCT, projection_band,
                         projection_breakdown, savings_scenario)


def test_projection_band_symmetric():
    lo, hi = projection_band(1000.0)
    assert lo == pytest.approx(1000 * (1 - NAIVE_BAND_PCT))
    assert hi == pytest.approx(1000 * (1 + NAIVE_BAND_PCT))


def test_projection_band_clamps_negative_input():
    lo, hi = projection_band(-50.0)
    assert lo == 0.0 and hi == 0.0


def test_breakdown_splits_fixed_and_discretionary():
    b = projection_breakdown(2000.0, 700.0)
    assert b["fixed"] == 700.0
    assert b["discretionary"] == 1300.0
    assert not b["under_fixed"]


def test_breakdown_flags_under_fixed():
    b = projection_breakdown(500.0, 700.0)
    assert b["discretionary"] == 0.0
    assert b["under_fixed"]


def test_scenario_recurring_cut_raises_savings():
    base = savings_scenario(3000.0, 900.0, 1100.0, 0.0)
    cut = savings_scenario(3000.0, 900.0, 1100.0, -25.0)
    assert cut["projected_spend"] < base["projected_spend"]
    assert cut["monthly_savings"] > base["monthly_savings"]
    assert base["savings_rate"] == pytest.approx((3000 - 2000) / 3000)


def test_scenario_zero_salary_gives_none_rate():
    s = savings_scenario(0.0, 900.0, 100.0, 0.0)
    assert s["savings_rate"] is None
    assert s["projected_spend"] == 1000.0


def test_scenario_negative_delta_never_below_zero_fixed():
    s = savings_scenario(3000.0, 400.0, 600.0, -150)  # -150% would flip sign
    assert s["fixed"] >= 0.0
