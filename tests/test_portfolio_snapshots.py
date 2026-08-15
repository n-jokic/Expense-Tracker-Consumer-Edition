"""
Regression tests for portfolio price snapshots (db.HoldingPrice): snapshots
must record quantity/rate/value so the value-over-time chart uses exact
historical values instead of today's quantity multiplied by old prices.
"""

from datetime import date

import pytest
from sqlalchemy import text

from db import (
    init_db, create_user, delete_user_account, add_holding,
    add_holding_price, get_holding_prices, get_engine,
    username_exists, get_user_by_username,
)
from auth import hash_password

TEST_USERNAME = "snapshot_test_user"
TEST_EMAIL    = "snapshot_test@example.com"


@pytest.fixture()
def test_user():
    init_db()
    if username_exists(TEST_USERNAME):
        delete_user_account(get_user_by_username(TEST_USERNAME)["id"])
    uid = create_user(TEST_USERNAME, TEST_EMAIL, hash_password("test1234"),
                      "Snapshot Tester")
    yield uid
    delete_user_account(uid)


@pytest.fixture()
def holding_id(test_user):
    return add_holding(test_user, {
        "symbol": "AAPL", "name": "Apple Inc.", "quantity": 10.0,
        "currency": "USD", "cost_total": 1000.0, "cost_eur": 900.0,
    })


def test_snapshot_records_quantity_rate_and_value(test_user, holding_id):
    add_holding_price(holding_id, price=200.0, when=date(2025, 6, 1),
                      quantity=10.0, rate=1.08)
    df = get_holding_prices(test_user)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["quantity"] == 10.0
    assert row["rate"] == 1.08
    assert row["value_eur"] == pytest.approx(10 * 200 / 1.08, abs=1e-4)


def test_snapshot_value_survives_later_quantity_change(test_user, holding_id):
    """The EUR value of an old snapshot must NOT change when the user later
    edits the holding's quantity — it is frozen at snapshot time."""
    add_holding_price(holding_id, price=200.0, when=date(2025, 6, 1),
                      quantity=10.0, rate=1.0)
    df = get_holding_prices(test_user)
    assert df.iloc[0]["value_eur"] == pytest.approx(2000.0)

    # A later snapshot with a different quantity coexists independently.
    add_holding_price(holding_id, price=210.0, when=date(2025, 6, 2),
                      quantity=15.0, rate=1.0)
    df = get_holding_prices(test_user)
    assert len(df) == 2
    assert set(df["value_eur"].round(2)) == {2000.0, 3150.0}


def test_same_day_snapshot_updates_values(test_user, holding_id):
    add_holding_price(holding_id, price=200.0, when=date(2025, 6, 1),
                      quantity=10.0, rate=1.0)
    add_holding_price(holding_id, price=205.0, when=date(2025, 6, 1),
                      quantity=12.0, rate=1.1)
    df = get_holding_prices(test_user)
    assert len(df) == 1
    assert df.iloc[0]["price"] == 205.0
    assert df.iloc[0]["quantity"] == 12.0
    assert df.iloc[0]["value_eur"] == pytest.approx(12 * 205 / 1.1, abs=1e-4)


def test_legacy_rows_report_null_value_for_estimation(test_user, holding_id):
    # Rows written before the columns existed have no value_eur: the page
    # must fall back to today's quantity with an "estimated" label.
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO holding_prices (holding_id, date, price)"
            " VALUES (:h, '2025-05-01', 180.0)"), {"h": holding_id})
    df = get_holding_prices(test_user)
    row = df[df["date"] == "2025-05-01"].iloc[0]
    assert row["price"] == 180.0
    assert row["value_eur"] is None or row["value_eur"] == 0


def test_migration_adds_snapshot_columns():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS holding_prices"))
        conn.execute(text(
            "CREATE TABLE holding_prices ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " holding_id VARCHAR NOT NULL, date DATE, price FLOAT DEFAULT 0)"))
    init_db()
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(engine).get_columns("holding_prices")}
    assert {"quantity", "rate", "value_eur"} <= cols
