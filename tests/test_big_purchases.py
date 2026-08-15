"""
Tests for the big-purchase priority matrix (utils.classify_quadrant).
"""

from utils import classify_quadrant


def test_quadrant_quick_win():
    # high usage, low work-hours -> buy soon
    assert classify_quadrant(work_hours=5, usage_hours=40,
                             median_work=20, median_usage=10) == "Quick wins"


def test_quadrant_plan_and_save():
    assert classify_quadrant(work_hours=60, usage_hours=40,
                             median_work=20, median_usage=10) == "Plan & save"


def test_quadrant_maybe_later():
    assert classify_quadrant(work_hours=5, usage_hours=2,
                             median_work=20, median_usage=10) == "Maybe later"


def test_quadrant_reconsider():
    assert classify_quadrant(work_hours=60, usage_hours=2,
                             median_work=20, median_usage=10) == "Reconsider"


def test_quadrant_boundary_values_fall_to_low_side():
    # exactly at the median counts as "not high"
    assert classify_quadrant(work_hours=20, usage_hours=10,
                             median_work=20, median_usage=10) == "Maybe later"
