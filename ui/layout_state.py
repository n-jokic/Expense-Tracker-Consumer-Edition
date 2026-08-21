"""
ui/layout_state.py — persistent layout state (Phase 2 U2 / FIN-02).

Layout state lives in user_settings as a JSON blob (key: "ui_layout").
It is NOT domain state — panel order/collapse never touches budgets/savings.

Namespaced schema (version 1):
  {"version": 1,
   "dashboard": {"order": [...], "collapsed": [...]},
   "loans":     {"collapsed": [...]},
   "savings":   {"collapsed": [...]},
   "recurring": {"collapsed_groups": [...], "group_order": [...]}}

Rules:
- Every WRITE goes through a read-modify-write on ONE area namespace
  (db.atomic_update_setting_json) so two pages toggling different areas can
  never clobber each other's state.
- Values are sanitized before persisting: lists of non-empty unique strings,
  order preserved; when known_ids is given, unknown ids are dropped.
- load_layout NEVER raises: malformed stored JSON logs a warning and falls
  back to defaults.
- save_layout / update_layout_area raise LayoutSaveError on persistence
  failure — they never swallow errors. UI callers catch it and warn.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Key used inside user_settings (persisted via db.UserSettings as JSON).
LAYOUT_SETTINGS_KEY = "ui_layout"

DEFAULT_LAYOUT: dict[str, Any] = {
    "version": 1,
    "dashboard": {"order": [], "collapsed": []},
    "loans": {"collapsed": []},
    "savings": {"collapsed": []},
    "recurring": {"collapsed_groups": [], "group_order": []},
}

# Known areas → their list-of-ids keys. Unknown areas are preserved verbatim.
_AREA_KEYS: dict[str, tuple[str, ...]] = {
    "dashboard": ("order", "collapsed"),
    "loans": ("collapsed",),
    "savings": ("collapsed",),
    "recurring": ("collapsed_groups", "group_order"),
}

# Areas whose every id list is filtered by known_ids (recurring.group_order is
# filtered separately — collapsed_groups may reference panels, not groups).
_KNOWN_IDS_FILTERS_ALL = {"dashboard", "loans", "savings"}


class LayoutSaveError(RuntimeError):
    """Raised when a layout persistence attempt fails.

    Carries the user_id and (when known) the area namespace so callers can
    log/report context without parsing the message.
    """

    def __init__(self, message: str, *, user_id: int | None = None,
                 area: str | None = None) -> None:
        self.user_id = user_id
        self.area = area
        super().__init__(message)


# ── Sanitization ──────────────────────────────────────────────────────────────

def _clean_id_list(value: Any) -> list[str]:
    """Coerce to a list of non-empty unique strings, preserving order."""
    out: list[str] = []
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if not isinstance(item, str):
            continue  # drop non-strings
        item = item.strip()
        if not item or item in out:
            continue  # drop empties and duplicates
        out.append(item)
    return out


def sanitize_area(area: str, value: Any,
                  known_ids: Iterable[str] | None = None) -> dict:
    """Coerce one layout area into its persisted shape.

    - Lists are coerced to lists of strings; non-strings/empties dropped,
      duplicates removed preserving first-seen order.
    - When known_ids is provided, ids not in it are dropped (for recurring,
      only group_order is filtered — it may only contain known group ids).
    - Unknown areas get a best-effort {key: [str]} coercion of dict values.
    """
    keys = _AREA_KEYS.get(area)
    if keys is None:
        # Forward compat: unknown area — sanitize any list values we find.
        if isinstance(value, dict):
            return {k: _clean_id_list(v) for k, v in value.items()}
        return {}
    out: dict[str, list[str]] = {k: [] for k in keys}
    if isinstance(value, dict):
        for k in keys:
            out[k] = _clean_id_list(value.get(k))
    if known_ids is not None:
        known = {i for i in known_ids if isinstance(i, str)}
        if area == "recurring":
            out["group_order"] = [g for g in out["group_order"] if g in known]
        elif area in _KNOWN_IDS_FILTERS_ALL:
            for k in keys:
                out[k] = [i for i in out[k] if i in known]
    return out


def _normalize(layout: dict | None) -> dict:
    """Tolerant normalizer: missing keys defaulted, unknown areas verbatim."""
    if not isinstance(layout, dict):
        layout = {}
    out: dict = {"version": int(layout.get("version", 1) or 1)}
    for area, keys in _AREA_KEYS.items():
        val = layout.get(area)
        if isinstance(val, dict):
            out[area] = {k: list(val[k]) if isinstance(val.get(k), (list, tuple)) else []
                         for k in keys}
        else:
            # Missing or malformed area → default shape.
            out[area] = {k: [] for k in keys}
    # Preserve any other area keys verbatim for forward compatibility.
    for k, v in layout.items():
        if k not in out and k != "version":
            out[k] = v
    return out


# ── Read path (never raises) ──────────────────────────────────────────────────

def load_layout(user_id: int) -> dict:
    """Load layout from user_settings JSON (fallback to default).

    Never raises: malformed stored values log a warning (with user/area
    context) and fall back to DEFAULT_LAYOUT.
    """
    try:
        from db import get_settings
        settings = get_settings(user_id) or {}
        raw = settings.get(LAYOUT_SETTINGS_KEY)
        if isinstance(raw, dict):
            return _normalize(raw)
        if raw is None:
            return dict(DEFAULT_LAYOUT)
        import json
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                logger.warning(
                    "load_layout: malformed %s JSON for user %s (area=all); "
                    "falling back to defaults (%s)",
                    LAYOUT_SETTINGS_KEY, user_id, exc)
                return dict(DEFAULT_LAYOUT)
            if isinstance(parsed, dict):
                return _normalize(parsed)
        logger.warning(
            "load_layout: unexpected %s payload type %s for user %s "
            "(area=all); falling back to defaults",
            LAYOUT_SETTINGS_KEY, type(raw).__name__, user_id)
        return dict(DEFAULT_LAYOUT)
    except Exception as exc:
        logger.warning(
            "load_layout: could not read %s for user %s (area=all); "
            "falling back to defaults (%s)", LAYOUT_SETTINGS_KEY, user_id, exc)
        return dict(DEFAULT_LAYOUT)


# ── Write paths (raise LayoutSaveError on failure) ────────────────────────────

def update_layout_area(user_id: int, area: str,
                       mutate: Callable[[dict], dict]) -> dict:
    """Atomically read-modify-write ONE area namespace inside ui_layout.

    mutate receives the current sanitized area dict and returns the new one;
    the result is sanitized again before persisting. Built on
    db.atomic_update_setting_json so concurrent page writes serialize instead
    of clobbering each other's namespaces.

    Raises LayoutSaveError (user_id + area attached) on failure.
    """
    from db import atomic_update_setting_json

    def _updater(current: Any) -> dict:
        layout = _normalize(current if isinstance(current, dict) else {})
        area_val = layout.get(area)
        base = dict(area_val) if isinstance(area_val, dict) else {}
        new_area = mutate(base)
        layout[area] = sanitize_area(area, new_area)
        layout["version"] = 1
        return layout

    try:
        return atomic_update_setting_json(user_id, LAYOUT_SETTINGS_KEY, _updater)
    except Exception as exc:
        raise LayoutSaveError(
            f"Failed to persist layout area {area!r} for user {user_id}: {exc}",
            user_id=user_id, area=area) from exc


def save_layout(user_id: int, layout: dict) -> dict:
    """Validate, persist, and return normalized layout (full-blob write).

    Atomic-safe: the whole normalized blob replaces the stored value inside a
    single read-modify-write transaction. Raises LayoutSaveError on failure.
    """
    normalized = _normalize(layout)
    try:
        from db import atomic_update_setting_json
        return atomic_update_setting_json(
            user_id, LAYOUT_SETTINGS_KEY, lambda _current: normalized)
    except Exception as exc:
        raise LayoutSaveError(
            f"Failed to persist layout for user {user_id}: {exc}",
            user_id=user_id, area=None) from exc


def set_area_ids(user_id: int, area: str, key: str, ids: list[str],
                 known_ids: Iterable[str] | None = None) -> dict:
    """Replace one id list (e.g. loans.collapsed) inside its namespace."""
    if key not in _AREA_KEYS.get(area, ()):  # guard against typo'd keys
        raise ValueError(f"Unknown layout list {area}.{key}")

    def _mutate(area_val: dict) -> dict:
        area_val[key] = list(ids)
        return area_val

    return update_layout_area(user_id, area, _mutate)


# ── Convenience readers/writers (backward-compatible signatures) ─────────────

def get_dashboard_order(user_id: int) -> list[str]:
    return list(load_layout(user_id).get("dashboard", {}).get("order", []))


def set_dashboard_order(user_id: int, order: list[str]) -> dict:
    def _mutate(area_val: dict) -> dict:
        area_val["order"] = list(order)
        return area_val

    return update_layout_area(user_id, "dashboard", _mutate)


def is_collapsed(user_id: int, panel_id: str, area: str = "dashboard") -> bool:
    layout = load_layout(user_id)
    area_state = layout.get(area, {})
    if isinstance(area_state, dict):
        return panel_id in area_state.get("collapsed", [])
    return False


def toggle_collapsed(user_id: int, panel_id: str, area: str = "dashboard") -> dict:
    def _mutate(area_val: dict) -> dict:
        collapsed: list = list(area_val.get("collapsed", []))
        if panel_id in collapsed:
            collapsed = [c for c in collapsed if c != panel_id]
        else:
            collapsed.append(panel_id)
        area_val["collapsed"] = collapsed
        return area_val

    return update_layout_area(user_id, area, _mutate)
