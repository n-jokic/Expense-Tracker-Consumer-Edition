"""
ai/charts.py — validated chart specs for safe AI answers (AI-04).

A chart spec is a tiny declarative dict {"type", "title", "x", "y"}. The
DATA always comes from the canonical tool result rows passed alongside —
the model may name fields and a chart type, never supply numbers. Anything
that fails validation returns None and the UI falls back to text/table.
No code execution, no HTML, no URLs — by construction.
"""

from __future__ import annotations

import re
from typing import Any

ALLOWED_TYPES = ("line", "bar", "pie")
MAX_TITLE_LEN = 120
MAX_DATA_ROWS = 60

_TAG_RE = re.compile(r"<[^>]*>")
_BRACE_RE = re.compile(r"[{}]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean_title(title: Any) -> str:
    """Plain-text titles only: strip tags/control chars/braces, cap length."""
    if not isinstance(title, str):
        return ""
    t = _TAG_RE.sub("", title)
    t = _BRACE_RE.sub("", t)
    t = _CTRL_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_TITLE_LEN]


def validate_chart_spec(spec: Any, rows: list[dict],
                        *, max_rows: int = MAX_DATA_ROWS) -> dict | None:
    """Validate a chart spec against the canonical data rows.

    Returns a normalized spec {"type","title","x","y","data"} where ``data``
    is the caller's OWN row list (values untouched), or None when anything
    is off — the caller must then fall back to text/table rendering.
    """
    if not isinstance(spec, dict):
        return None
    kind = spec.get("type")
    if not isinstance(kind, str) or kind.lower() not in ALLOWED_TYPES:
        return None
    x, y = spec.get("x"), spec.get("y")
    for field in (x, y):
        if not isinstance(field, str) or not field or len(field) > 60:
            return None
        # Field names are plain identifiers only — no paths, no expressions.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,59}", field):
            return None
    raw_title = spec.get("title")
    title = _clean_title(raw_title) if isinstance(raw_title, str) else ""
    if not title:
        title = f"{kind} chart"

    if not isinstance(rows, list) or not (1 <= len(rows) <= max_rows):
        return None
    for r in rows:
        if not isinstance(r, dict) or x not in r or y not in r:
            return None
        v = r[y]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
    return {"type": kind.lower(), "title": title, "x": x, "y": y,
            "data": [{x: r[x], y: r[y]} for r in rows]}
