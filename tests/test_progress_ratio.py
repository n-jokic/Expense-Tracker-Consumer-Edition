"""A2: utils.progress_ratio — clamped, st.progress-safe ratios."""

import pytest

from utils import progress_ratio


def test_normal_fraction():
    assert progress_ratio(50, 100) == 0.5
    assert progress_ratio(0, 100) == 0.0
    assert progress_ratio(100, 100) == 1.0


def test_overdrawn_negative_clamps_to_zero():
    # savings.py crash case: negative goal balance must not reach st.progress.
    assert progress_ratio(-329.58, 100) == 0.0


def test_overshoot_clamps_to_one():
    assert progress_ratio(150, 100) == 1.0


def test_bad_target_returns_zero():
    assert progress_ratio(10, 0) == 0.0
    assert progress_ratio(10, -5) == 0.0
    assert progress_ratio(10, None) == 0.0
    assert progress_ratio(None, 10) == 0.0


@pytest.mark.parametrize("v,t", [(1, 3), (2.5, 5)])
def test_result_always_in_unit_range(v, t):
    r = progress_ratio(v, t)
    assert 0.0 <= r <= 1.0
