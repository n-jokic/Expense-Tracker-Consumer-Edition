"""
Tests for live exchange-rate refresh (rates.py) with mocked network calls.
"""

import io
import json
from datetime import datetime, date, timedelta, timezone

import pytest

import rates
from rates import fetch_live_rates, rates_are_stale, RATES_MAX_AGE_DAYS


class FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _url_of(req) -> str:
    return getattr(req, "full_url", req)


def test_fetch_merges_frankfurter_and_er_api(monkeypatch):
    calls = {"f": 0, "e": 0}

    def fake_urlopen(req, timeout=3):
        if "frankfurter" in _url_of(req):
            calls["f"] += 1
            return FakeResponse({"rates": {"USD": 1.08, "GBP": 0.85, "RSD": None}})
        calls["e"] += 1
        return FakeResponse({"result": "success", "rates": {"RSD": 117.5, "BAM": 1.9558}})

    monkeypatch.setattr(rates.urllib.request, "urlopen", fake_urlopen)
    out = fetch_live_rates()
    assert out["EUR"] == 1.0
    assert out["USD"] == 1.08
    assert out["RSD"] == 117.5  # from the fallback provider
    assert out["BAM"] == 1.9558


def test_fetch_skips_fallback_when_all_currencies_present(monkeypatch):
    calls = {"e": 0}

    def fake_urlopen(req, timeout=3):
        if "frankfurter" in _url_of(req):
            full = {"USD": 1.08, "GBP": 0.85, "CHF": 0.94, "HRK": 7.53, "BAM": 1.9558,
                    "HUF": 400.0, "RON": 5.0, "BGN": 1.9558, "PLN": 4.3, "CZK": 25.0,
                    "RSD": 117.0}
            return FakeResponse({"rates": full})
        calls["e"] += 1
        return FakeResponse({"result": "success", "rates": {}})

    monkeypatch.setattr(rates.urllib.request, "urlopen", fake_urlopen)
    out = fetch_live_rates()
    assert out["USD"] == 1.08
    assert calls["e"] == 0  # fallback not needed


def test_fetch_returns_none_when_all_providers_fail(monkeypatch):
    def fake_urlopen(req, timeout=3):
        raise OSError("network down")

    monkeypatch.setattr(rates.urllib.request, "urlopen", fake_urlopen)
    assert fetch_live_rates() is None


def test_fetch_returns_none_when_rates_are_garbage(monkeypatch):
    def fake_urlopen(req, timeout=3):
        return FakeResponse({"unexpected": "shape"})

    monkeypatch.setattr(rates.urllib.request, "urlopen", fake_urlopen)
    assert fetch_live_rates() is None


def test_rates_are_stale_logic():
    now = datetime.now(timezone.utc)
    assert rates_are_stale({"rates_updated_at": None}) is True
    assert rates_are_stale({"rates_updated_at": now}) is False
    old = now - timedelta(days=RATES_MAX_AGE_DAYS + 2)
    assert rates_are_stale({"rates_updated_at": old}) is True
    # plain date objects and ISO strings also work
    assert rates_are_stale({"rates_updated_at": date.today()}) is False
    assert rates_are_stale({"rates_updated_at": "2020-01-01T00:00:00"}) is True
