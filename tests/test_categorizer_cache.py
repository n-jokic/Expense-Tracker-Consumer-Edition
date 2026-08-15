"""
Regression tests for categorizer cache invalidation (forecasting): the model
must be keyed by a dataset fingerprint so category CORRECTIONS and deletions
retrain immediately instead of serving stale suggestions from a user-keyed
cache_resource.
"""

import pandas as pd

from forecasting import (
    suggest_category, get_categorizer, clear_categorizers,
    _dataset_fingerprint, CATEGORIZER_MODEL_VERSION,
)


def _labels(n_groceries: int = 12, n_streams: int = 12,
            grocery_cat: str = "Food & Dining") -> pd.DataFrame:
    rows = [{"description": f"lidl {i}", "category": grocery_cat}
            for i in range(n_groceries)]
    rows += [{"description": f"netflix {i}", "category": "Entertainment"}
             for i in range(n_streams)]
    return pd.DataFrame(rows)


def test_fingerprint_changes_on_category_edit():
    df1 = _labels()
    df2 = df1.copy()
    df2.loc[df2["description"].str.startswith("lidl"), "category"] = "Other"
    assert _dataset_fingerprint(df1) != _dataset_fingerprint(df2)


def test_fingerprint_changes_on_row_delete():
    df1 = _labels()
    assert _dataset_fingerprint(df1) != _dataset_fingerprint(df1.iloc[:-1])


def test_edited_labels_retrain_immediately():
    clear_categorizers()
    df1 = _labels()
    cat1, _ = suggest_category(df1, "lidl supermarket", user_id=42)
    assert cat1 == "Food & Dining"

    # The user corrects every "lidl" row to "Other" — the next suggestion
    # must reflect the correction, not the stale cached model.
    df2 = df1.copy()
    df2.loc[df2["description"].str.startswith("lidl"), "category"] = "Other"
    cat2, _ = suggest_category(df2, "lidl supermarket", user_id=42)
    assert cat2 == "Other"


def test_cache_keyed_by_fingerprint_and_user():
    clear_categorizers()
    fp = _dataset_fingerprint(_labels())
    m1 = get_categorizer(1, CATEGORIZER_MODEL_VERSION, fp)
    m2 = get_categorizer(1, CATEGORIZER_MODEL_VERSION, fp)
    assert m1 is m2  # same key -> cached instance

    other = get_categorizer(1, CATEGORIZER_MODEL_VERSION, "different")
    assert other is not m1  # different fingerprint -> fresh instance

    other_user = get_categorizer(2, CATEGORIZER_MODEL_VERSION, fp)
    assert other_user is not m1  # different user -> no cross-account leakage


def test_clear_drops_all_models():
    fp = _dataset_fingerprint(_labels())
    m1 = get_categorizer(7, CATEGORIZER_MODEL_VERSION, fp)
    clear_categorizers()
    m2 = get_categorizer(7, CATEGORIZER_MODEL_VERSION, fp)
    assert m1 is not m2
