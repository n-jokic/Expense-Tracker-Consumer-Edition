"""#14 travel adapters: destination search (Nominatim) + forecast
(Open-Meteo). Free/no-key endpoints, cached per session, and every failure
degrades to None so the trip planner stays fully usable offline. Tests
monkeypatch _fetch_json."""
from __future__ import annotations

import json
import urllib.parse

import streamlit as st

_UA = "expense-tracker-consumer-edition/1.0 (local personal finance app)"
_TIMEOUT = 8


def _fetch_json(url: str):
    """One GET returning parsed JSON; any error -> None (offline-safe)."""
    try:
        import requests
        r = requests.get(url, timeout=_TIMEOUT,
                         headers={"User-Agent": _UA})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def geocode_destination(query: str):
    """Nominatim search -> list of {name, lat, lon} (max 5); [] on failure."""
    q = (query or "").strip()
    if not q:
        return []
    url = ("https://nominatim.openstreetmap.org/search?"
           + urllib.parse.urlencode({"q": q, "format": "json", "limit": 5}))
    data = _fetch_json(url) or []
    out = []
    for d in data[:5]:
        try:
            out.append({"name": str(d.get("display_name", q)),
                        "lat": float(d["lat"]), "lon": float(d["lon"])})
        except Exception:
            continue
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def destination_forecast(lat: float, lon: float, start_date: str,
                         end_date: str):
    """Open-Meteo daily forecast for the trip window.

    Returns {"days": [{date, t_max, t_min, precip_mm, code}], "source":
    "open-meteo"} or None when the window is out of range / offline.
    """
    url = ("https://api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode({
               "latitude": lat, "longitude": lon,
               "daily": "temperature_2m_max,temperature_2m_min,"
                        "precipitation_sum,weather_code",
               "timezone": "auto",
               "start_date": start_date, "end_date": end_date}))
    data = _fetch_json(url)
    if not data or "daily" not in data:
        return None
    daily = data["daily"]
    days = []
    for i, dstr in enumerate(daily.get("time", [])):
        def _num(key):
            try:
                v = daily[key][i]
                return None if v is None else round(float(v), 1)
            except Exception:
                return None
        days.append({"date": dstr,
                     "t_max": _num("temperature_2m_max"),
                     "t_min": _num("temperature_2m_min"),
                     "precip_mm": _num("precipitation_sum"),
                     "code": _num("weather_code")})
    return {"days": days, "source": "open-meteo"}
