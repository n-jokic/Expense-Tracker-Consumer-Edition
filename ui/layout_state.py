"""
ui/layout_state.py — persistent layout state (Phase 2 U2).

Layout state lives in user_settings as a JSON blob (key: "ui_layout").
It is NOT domain state — panel order/collapse never touches budgets/savings.

Schema:
  {"version": 1, "dashboard": {"order": [...], "collapsed": [...]}, ...}

Each area (dashboard, recurring, etc.) gets optional keys; unknown keys are
preserved for forward compatibility. Collapsed is a set-ish list of panel ids.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LAYOUT: dict[str, Any] = {
    "version": 1,
    "dashboard": {"order": [], "collapsed": []},
}

# Key used inside user_settings (persisted via db.UserSettings as JSON).
LAYOUT_SETTINGS_KEY = "ui_layout"


def _normalize(layout: dict | None) -> dict:
    if not isinstance(layout, dict):
        return dict(DEFAULT_LAYOUT)
    out: dict = {"version": int(layout.get("version", 1) or 1)}
    # Preserve known areas; ensure collapsed/order are lists.
    for area in ("dashboard", "recurring", "wishlist", "savings", "loans", "budgets", "portfolio"):
        val = layout.get(area)
        if isinstance(val, dict):
            order = val.get("order")
            collapsed = val.get("collapsed")
            out[area] = {
                "order": list(order) if isinstance(order, (list, tuple)) else [],
                "collapsed": list(collapsed) if isinstance(collapsed, (list, tuple)) else [],
            }
        elif val is None:
            continue
        else:
            out[area] = val
    # Preserve any other area keys verbatim for forward compat
    for k, v in layout.items():
        if k not in out and k != "version":
            out[k] = v
    return out


def load_layout(user_id: int) -> dict:
    """Load layout from user_settings JSON (fallback to default)."""
    try:
        from db import get_settings
        settings = get_settings(user_id) or {}
        raw = settings.get(LAYOUT_SETTINGS_KEY)
        if isinstance(raw, dict):
            return _normalize(raw)
        if raw is None:
            return dict(DEFAULT_LAYOUT)
        # Stored as JSON string? try parse
        import json
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return _normalize(parsed)
            except Exception:
                pass
        return dict(DEFAULT_LAYOUT)
    except Exception:
        return dict(DEFAULT_LAYOUT)


def save_layout(user_id: int, layout: dict) -> dict:
    """Validate, persist, and return normalized layout. Bumps data revision."""
    normalized = _normalize(layout)
    try:
        from db import save_settings  # type: ignore
        save_settings(user_id, {LAYOUT_SETTINGS_KEY: normalized})
    except Exception:
        try:
            # Fallback to queries wrapper if direct db helper unavailable
            from queries import save_settings as qs  # type: ignore
            qs(user_id, {LAYOUT_SETTINGS_KEY: normalized})
        except Exception:
            pass
    return normalized


def get_dashboard_order(user_id: int) -> list[str]:
    return list(load_layout(user_id).get("dashboard", {}).get("order", []))


def set_dashboard_order(user_id: int, order: list[str]) -> dict:
    layout = load_layout(user_id)
    layout.setdefault("dashboard", {})["order"] = list(order)
    return save_layout(user_id, layout)


def is_collapsed(user_id: int, panel_id: str, area: str = "dashboard") -> bool:
    layout = load_layout(user_id)
    return panel_id in layout.get(area, {}).get("collapsed", [])


def toggle_collapsed(user_id: int, panel_id: str, area: str = "dashboard") -> dict:
    layout = load_layout(user_id)
    area_state = layout.setdefault(area, {"order": [], "collapsed": []})
    collapsed: list = list(area_state.get("collapsed", []))
    if panel_id in collapsed:
        collapsed = [c for c in collapsed if c != panel_id]
    else:
        collapsed.append(panel_id)
    area_state["collapsed"] = collapsed
    return save_layout(user_id, layout)
