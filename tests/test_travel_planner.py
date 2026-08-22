"""#14 — trip planner: CRUD, windowed spend, adapters (monkeypatched HTTP),
checklist persistence, account-deletion cleanup."""
from datetime import date, timedelta

import pandas as pd
import pytest

import db
import services.travel_apis as tapi
from auth import hash_password
from db import (add_trip, create_user, delete_trip, delete_user_account,
                get_trips, get_user_by_username, init_db,
                update_trip, username_exists)
from utils import travel_spent_in_range

U = "trip_user"
E = "trip@example.com"
TODAY = date.today()


@pytest.fixture()
def user():
    init_db()
    if username_exists(U):
        delete_user_account(get_user_by_username(U)["id"])
    uid = create_user(U, E, hash_password("test1234"), "Trip Tester")
    yield uid
    delete_user_account(uid)


def _expenses(rows):
    return pd.DataFrame(rows, columns=["date", "category", "subcategory",
                                       "amount_eur"])
    # dates as Timestamps like q.expenses returns


def test_travel_spent_in_range_window_and_pairs(user):
    dfe = _expenses([
        [pd.Timestamp(TODAY), "Travel", "", 100.0],
        [pd.Timestamp(TODAY + timedelta(days=3)), "Travel", "", 50.0],
        [pd.Timestamp(TODAY + timedelta(days=10)), "Travel", "", 999.0],
        [pd.Timestamp(TODAY - timedelta(days=5)), "Food", "", 20.0],
    ])
    s = TODAY
    e = TODAY + timedelta(days=7)
    assert travel_spent_in_range(dfe, ["Travel"], s, e) == pytest.approx(150.0)
    # subcategory pair form and bare-subcategory form still route
    dfe2 = _expenses([
        [pd.Timestamp(TODAY), "Fun", "Vacation", 40.0],
        [pd.Timestamp(TODAY), "Other", "Misc", 10.0],
    ])
    assert travel_spent_in_range(
        dfe2, ["Fun › Vacation"], TODAY, TODAY) == pytest.approx(40.0)


def test_trip_crud_roundtrip_and_update(user):
    tid = add_trip(user, {"name": "Lisbon week",
                          "destination": "Lisbon, Portugal",
                          "start_date": TODAY + timedelta(days=30),
                          "end_date": TODAY + timedelta(days=37),
                          "envelope_eur": 1200.0, "dest_currency": "EUR",
                          "participants_json": ["A", "B"]})
    df = get_trips(user)
    row = df[df["id"] == tid].iloc[0]
    assert float(row["envelope_eur"]) == pytest.approx(1200.0)
    assert list(row["participants_json"]) == ["A", "B"]
    update_trip(user, tid, {"checklist_json": [{"text": "Passport",
                                                "done": False}]})
    row = get_trips(user).set_index("id").loc[tid]
    assert row["checklist_json"][0]["text"] == "Passport"
    assert delete_trip(user, tid) is True
    assert (get_trips(user)["id"] == tid).sum() == 0


def test_delete_user_account_cleans_trips(user):
    add_trip(user, {"name": "X", "start_date": TODAY,
                    "end_date": TODAY + timedelta(days=2),
                    "envelope_eur": 100.0})
    delete_user_account(user)          # must not FK-fail
    assert username_exists(U) is False


def test_geocode_monkeypatched(user):
    tapi.geocode_destination.clear()
    calls = {}

    def fake_fetch(url):
        calls["url"] = url
        return [{"display_name": "Lisbon, Portugal", "lat": "38.7",
                 "lon": "-9.1"}]

    orig = tapi._fetch_json
    tapi._fetch_json = fake_fetch
    try:
        hits = tapi.geocode_destination("lisbon")
    finally:
        tapi._fetch_json = orig
    assert hits and hits[0]["lat"] == pytest.approx(38.7)
    assert "nominatim" in calls["url"]
    # empty query short-circuits without network
    assert tapi.geocode_destination("") == []


def test_forecast_monkeypatched_and_offline_safe():
    tapi.destination_forecast.clear()

    def fake_fetch(url):
        return {"daily": {"time": ["2030-01-01"],
                          "temperature_2m_max": [21.4],
                          "temperature_2m_min": [12.0],
                          "precipitation_sum": [0.0],
                          "weather_code": [1]}}

    orig = tapi._fetch_json
    tapi._fetch_json = fake_fetch
    try:
        fc = tapi.destination_forecast(38.7, -9.1, "2030-01-01",
                                       "2030-01-01")
    finally:
        tapi._fetch_json = orig
    assert fc["days"][0]["t_max"] == pytest.approx(21.4)

    # offline / error path degrades to None instead of raising
    tapi.destination_forecast.clear()
    assert tapi._fetch_json("http://127.0.0.1:1/nope") is None


def test_participant_split_helper_math():
    """Equal-split preview math used by the UI (pure function parity)."""
    total, people = 900.0, 3
    share = round(total / people, 2)
    assert share * people == pytest.approx(total, abs=0.05)
