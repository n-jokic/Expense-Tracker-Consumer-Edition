"""
Tests for market price fetching (market_data.py) with mocked network calls.
"""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import market_data
from market_data import fetch_price_yahoo, fetch_price_stooq, prices_are_stale


class FakeResponse:
    def __init__(self, payload):
        self._data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_yahoo_parses_regular_market_price(monkeypatch):
    def fake_urlopen(req, timeout=4):
        return FakeResponse({"chart": {"result": [{
            "meta": {"regularMarketPrice": 123.45},
            "indicators": {"quote": [{"close": [None]}]},
        }]}})

    monkeypatch.setattr(market_data.urllib.request, "urlopen", fake_urlopen)
    assert fetch_price_yahoo("AAPL") == 123.45


def test_yahoo_falls_back_to_close_prices(monkeypatch):
    def fake_urlopen(req, timeout=4):
        return FakeResponse({"chart": {"result": [{
            "meta": {},
            "indicators": {"quote": [{"close": [10.0, 11.0, 12.5]}]},
        }]}})

    monkeypatch.setattr(market_data.urllib.request, "urlopen", fake_urlopen)
    assert fetch_price_yahoo("AAPL") == 12.5


def test_yahoo_returns_none_on_garbage(monkeypatch):
    def fake_urlopen(req, timeout=4):
        return FakeResponse({"nope": True})

    monkeypatch.setattr(market_data.urllib.request, "urlopen", fake_urlopen)
    assert fetch_price_yahoo("AAPL") is None


def test_stooq_parses_csv(monkeypatch):
    csv_text = ("Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                "AAPL.US,2025-01-01,22:00:01,1,2,3,250.5,1000\n").encode()

    def fake_urlopen(req, timeout=4):
        return FakeResponse(csv_text)

    monkeypatch.setattr(market_data.urllib.request, "urlopen", fake_urlopen)
    assert fetch_price_stooq("AAPL.US") == 250.5


def test_stooq_handles_n_d(monkeypatch):
    def fake_urlopen(req, timeout=4):
        return FakeResponse(b"Symbol,Date,Close\nXXX.US,2025-01-01,N/D\n")

    monkeypatch.setattr(market_data.urllib.request, "urlopen", fake_urlopen)
    assert fetch_price_stooq("XXX.US") is None


def _holdings_df(dates):
    return pd.DataFrame([{"last_price_date": d} for d in dates])


def test_prices_are_stale():
    now = datetime.now(timezone.utc)
    assert prices_are_stale(pd.DataFrame()) is False
    assert prices_are_stale(_holdings_df([None])) is True
    assert prices_are_stale(_holdings_df([now])) is False
    old = now - timedelta(days=market_data.PRICES_MAX_AGE_DAYS + 1)
    assert prices_are_stale(_holdings_df([old])) is True
