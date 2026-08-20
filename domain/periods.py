"""
domain/periods.py — canonical period helpers (Streamlit-free).

Covers month_bounds / parse_date / compute_salary_cycle /
filter_started_templates and related period utilities. utils.py will shim
from here after R6.
"""

from __future__ import annotations

import calendar
from datetime import date as _date, datetime, timedelta as _td

import pandas as pd


def month_bounds(month: str) -> tuple[_date, _date]:
    """'current', 'last', or 'YYYY-MM' → (first_day, first_day_of_next)."""
    m = (month or "current").strip().lower()
    today = _date.today()
    if m in ("current", "this", "now"):
        first = today.replace(day=1)
    elif m in ("last", "previous"):
        prev = (today.replace(day=1) - _td(days=1))
        first = prev.replace(day=1)
    else:
        try:
            first = datetime.strptime(m, "%Y-%m").date().replace(day=1)
        except ValueError:
            raise ValueError("month must be 'current', 'last', or 'YYYY-MM'")
    nxt = (first.replace(day=28) + _td(days=4)).replace(day=1)
    return first, nxt


def parse_date(d: str | None) -> _date:
    if not d:
        return _date.today()
    d = d.strip().lower()
    if d in ("today", "now"):
        return _date.today()
    if d == "yesterday":
        return _date.today() - _td(days=1)
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("date must be 'YYYY-MM-DD', 'today', or 'yesterday'")


def filter_started_templates(df, year: int, month: int):
    """Recurring templates whose start_month ("YYYY-MM") is on or before the
    given month. None/blank start_month = always active (legacy templates),
    and frames without the column pass through unchanged."""
    if df is None or df.empty or "start_month" not in df.columns:
        return df
    cur = f"{year:04d}-{month:02d}"
    started = df["start_month"].fillna("").astype(str).str.strip()
    return df[(started == "") | (started <= cur)]


def compute_salary_cycle(today: _date, salary_day: int = 10,
                         latest_salary: _date | None = None) -> tuple[_date, _date]:
    """Return (period_start, period_end) for a salary cycle."""
    def _clamped(y, m):
        return _date(y, m, min(salary_day, calendar.monthrange(y, m)[1]))

    if latest_salary is not None:
        period_start = latest_salary
    elif today.day >= salary_day:
        period_start = _clamped(today.year, today.month)
    elif today.month > 1:
        period_start = _clamped(today.year, today.month - 1)
    else:
        period_start = _clamped(today.year - 1, 12)

    next_m  = period_start.month + 1 if period_start.month < 12 else 1
    next_y  = period_start.year if period_start.month < 12 else period_start.year + 1
    last_day = calendar.monthrange(next_y, next_m)[1]
    period_end = _date(next_y, next_m, min(period_start.day, last_day)) - _td(days=1)
    return period_start, period_end
