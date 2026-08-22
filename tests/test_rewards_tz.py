"""#20: earned_at timestamps mix tz-aware (stored) and naive (legacy/NULL
fallback) values; sorting them crashed the Rewards page. utc_ts() must make
every value comparable without changing ordering semantics."""

import pandas as pd

from db import utc_ts


def test_utc_ts_handles_none_naive_and_aware():
    aware = pd.Timestamp("2024-01-01 10:00", tz="UTC")
    naive = pd.Timestamp("2024-01-01 10:00")
    assert utc_ts(None) == pd.Timestamp.min.tz_localize("UTC")
    assert utc_ts(float("nan")) == pd.Timestamp.min.tz_localize("UTC")
    for v in (aware, naive, "2024-01-01 10:00"):
        out = utc_ts(v)
        assert out.tz is not None


def test_mixed_aware_naive_none_sort_does_not_raise():
    rows = {"a": pd.Timestamp("2024-03-01", tz="UTC"),
            "ghost": None,
            "b": pd.Timestamp("2024-06-01"),          # naive
            "c": pd.Timestamp("2025-01-15", tz="UTC")}
    recent = sorted(rows.items(), key=lambda item: utc_ts(item[1]), reverse=True)
    assert [k for k, _ in recent][:3] == ["c", "b", "a"]
    assert recent[-1][0] == "ghost"


def test_datetime_with_tzinfo_roundtrips_through_sqlite_shape():
    # Mimics what SQLAlchemy returns after a SQLite round-trip of a stored
    # aware datetime: naive in some drivers, aware in others. Both compare.
    import datetime as dt
    aware = dt.datetime(2024, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    naive = dt.datetime(2024, 1, 1, 12, 0)
    assert utc_ts(aware) == utc_ts(naive)
