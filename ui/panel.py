"""
ui/panel.py — Panel primitive (Phase 2 U1).

Reusable panel shell: header, collapse/expand, summary/badge, actions slot,
drag handle when enabled, stable ID, layout-state integration. Content is
rendered by the caller (shell only — no DB writes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st

try:
    from ui.layout_state import is_collapsed as _is_collapsed, toggle_collapsed as _toggle
except Exception:  # pragma: no cover - import guard
    _is_collapsed = None  # type: ignore
    _toggle = None  # type: ignore

from ui.layout_state import DEFAULT_LAYOUT  # for type reference


@dataclass(frozen=True)
class PanelSpec:
    id: str
    title: str
    icon: str | None = None
    collapsible: bool = True
    default_expanded: bool = True
    reorderable: bool = False
    # Optional summary/badge text shown in header
    summary: str | None = None
    badge: str | None = None


def _expanded_for(spec: PanelSpec, user_id: int | None, area: str) -> bool:
    if not spec.collapsible:
        return True
    if user_id is not None and _is_collapsed is not None:
        try:
            collapsed = _is_collapsed(user_id, spec.id, area=area)
            return not collapsed
        except Exception:
            pass
    return bool(spec.default_expanded)


def panel(
    spec: PanelSpec,
    *,
    user_id: int | None = None,
    area: str = "dashboard",
    actions: list[tuple[str, Callable[[], None]]] | None = None,
    border: bool = True,
) -> tuple[bool, Any]:
    """Render a panel shell and return (expanded, container).

    - Header row: [drag handle?] [icon+title] [summary/badge] [actions] [collapse chevron]
    - If expanded, yields a content container (caller renders inside it).
    - No DB writes. If collapsible and user_id provided, click handler calls
      toggle_collapsed (layout state) and reruns.
    - actions: list of (label, on_click) rendered as small buttons in header.
    """
    expanded = _expanded_for(spec, user_id, area)

    # Use a bordered container as the panel chrome
    outer = st.container(border=border)

    with outer:
        # Header row
        cols = []
        # Use horizontal container for responsive header
        header = st.container(horizontal=True, vertical_alignment="center")  # type: ignore[call-arg]
        with header:
            if spec.reorderable:
                st.markdown("`↕`", help="Drag to reorder")
            label = f"{spec.icon} {spec.title}" if spec.icon else spec.title
            st.markdown(f"**{label}**")
            if spec.summary:
                st.caption(spec.summary)
            if spec.badge:
                st.badge(spec.badge)
            if actions:
                for lbl, cb in actions:
                    if st.button(lbl, key=f"panel_{spec.id}_{lbl}"):
                        cb()
            if spec.collapsible:
                chevron = "▼" if expanded else "▶"
                if st.button(chevron, key=f"panel_toggle_{spec.id}", help="Collapse/expand"):
                    if user_id is not None and _toggle is not None:
                        try:
                            _toggle(user_id, spec.id, area=area)
                        except Exception:
                            pass
                    st.rerun()

        if not expanded:
            return False, None

        content = st.container()
        return True, content
